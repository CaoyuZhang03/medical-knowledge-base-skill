"""Local HTTP API and static operational UI for a medical knowledge base."""

from __future__ import annotations

import json
import tempfile
import urllib.parse
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import approval, imports, library, pdfs, retrieval, safety, scheduling, storage


MAX_REQUEST_BYTES = 128 * 1024 * 1024
SKILL_ROOT = Path(__file__).resolve().parents[2]
PAGE_PATH = SKILL_ROOT / "assets" / "web-ui" / "index.html"


def render_page() -> bytes:
    return PAGE_PATH.read_bytes()


def _items(kb_path: Path, table: str, where: str = "", args: list[Any] | None = None) -> list[dict[str, Any]]:
    with storage.connect(kb_path) as conn:
        rows = conn.execute(f"select * from {table} {where}", args or []).fetchall()
    return [storage.row_to_dict(row) for row in rows]


def overview(kb_path: Path) -> dict[str, Any]:
    with storage.connect(kb_path) as conn:
        counts = {
            "pending_candidates": int(
                conn.execute("select count(*) from candidate_papers where status = 'pending'").fetchone()[0]
            ),
            "approved_papers": int(conn.execute("select count(*) from papers").fetchone()[0]),
            "saved_searches": int(conn.execute("select count(*) from saved_searches").fetchone()[0]),
            "active_tasks": int(
                conn.execute("select count(*) from tasks where status in ('pending', 'running')").fetchone()[0]
            ),
            "pdfs": int(conn.execute("select count(*) from papers where has_pdf = 1").fetchone()[0]),
        }
    return {
        "kb_path": str(kb_path.resolve()),
        "schema_version": storage.SCHEMA_VERSION,
        "counts": counts,
        "local_only": True,
    }


def audit(kb_path: Path) -> dict[str, Any]:
    issues = []
    for filename in ("kb.sqlite", "config.yaml", "kb-passport.yaml"):
        if not (kb_path / filename).is_file():
            issues.append(f"missing {filename}")
    counts = {}
    if not issues:
        with storage.connect(kb_path) as conn:
            for table in ("papers", "candidate_papers", "saved_searches", "tasks", "chunks"):
                counts[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
        passport = storage.refresh_passport(kb_path)
    else:
        passport = None
    return {
        "kb_path": str(kb_path),
        "issues": issues,
        "counts": counts,
        "passport_refreshed_at": passport.get("refreshed_at") if passport else None,
        "passed": not issues,
    }


def _json_body(headers: Any, body: bytes) -> dict[str, Any]:
    if "application/json" not in (headers.get("Content-Type") or "").lower():
        raise ValueError("Content-Type must be application/json")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("request body must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    return value


def _ids(payload: dict[str, Any], *, required: bool = True) -> list[int]:
    raw = payload.get("ids")
    if raw is None and not required:
        return []
    if not isinstance(raw, list) or not raw:
        raise ValueError("ids must be a non-empty array")
    try:
        values = list(dict.fromkeys(int(value) for value in raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("ids must contain integers") from exc
    if any(value <= 0 for value in values):
        raise ValueError("ids must contain positive integers")
    return values


def _confirmation_preview(value: dict[str, Any]) -> dict[str, Any]:
    return {"requires_confirmation": True, **value}


def _multipart_files(headers: Any, body: bytes, destination: Path) -> list[Path]:
    content_type = headers.get("Content-Type") or ""
    if "multipart/form-data" not in content_type.lower():
        raise ValueError("Content-Type must be multipart/form-data")
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    files = []
    for part in message.iter_parts():
        filename = part.get_filename()
        if not filename:
            continue
        safe_name = Path(filename.replace("\\", "/")).name.replace("\x00", "")
        if not safe_name:
            continue
        target = destination / safe_name
        target.write_bytes(part.get_payload(decode=True) or b"")
        files.append(target)
    if not files:
        raise ValueError("multipart request contains no files")
    return files


def _api_get(kb_path: Path, path: str, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    if path == "/api/overview":
        return 200, overview(kb_path)
    if path == "/api/candidates":
        status = (query.get("status") or ["pending"])[0]
        if status not in {"pending", "approved", "rejected", "all"}:
            raise ValueError("invalid candidate status")
        where = "order by created_at desc, id desc"
        args: list[Any] = []
        if status != "all":
            where = "where status = ? " + where
            args.append(status)
        return 200, {"items": _items(kb_path, "candidate_papers", where, args)}
    if path == "/api/library":
        return 200, {"items": _items(kb_path, "papers", "order by id desc")}
    if path == "/api/searches":
        return 200, {"items": scheduling.list_saved_searches(kb_path)}
    if path == "/api/tasks":
        status = (query.get("status") or [None])[0]
        where = "order by id desc"
        args = []
        if status:
            where = "where status = ? " + where
            args.append(status)
        return 200, {"items": _items(kb_path, "tasks", where, args)}
    if path == "/api/audit":
        return 200, audit(kb_path)
    return 404, {"error": "not_found", "message": f"No API route for {path}"}


def _retry_tasks(kb_path: Path, task_ids: list[int]) -> list[dict[str, Any]]:
    results = []
    for task_id in task_ids:
        task = storage.get_task(kb_path, task_id)
        if not task:
            results.append({"task_id": task_id, "status": "not_found"})
            continue
        if task["task_type"] == "pdf_fetch":
            payload = task.get("payload") or {}
            new_id = storage.create_task(kb_path, "pdf_fetch", payload)
            result = pdfs.run_pdf_task(kb_path, new_id)
            results.append({"task_id": task_id, "retry_task_id": new_id, **result})
        elif task["task_type"] == "scheduled_update":
            search_id = int((task.get("payload") or {}).get("search_id"))
            runs = scheduling.run_saved_searches(kb_path, search_ids=[search_id])
            results.append({"task_id": task_id, "runs": runs})
        else:
            results.append({"task_id": task_id, "status": "unsupported"})
    return results


def _api_post(
    kb_path: Path,
    path: str,
    headers: Any,
    body: bytes,
) -> tuple[int, dict[str, Any]]:
    if path == "/api/import":
        with tempfile.TemporaryDirectory(prefix="medical-kb-upload-") as temp_dir:
            paths = _multipart_files(headers, body, Path(temp_dir))
            return 200, imports.import_paths(kb_path, paths)

    payload = _json_body(headers, body)
    if path == "/api/candidates/approve":
        return 200, approval.approve_candidates(kb_path, _ids(payload))
    if path == "/api/candidates/reject":
        candidate_ids = _ids(payload)
        reason = str(payload.get("reason") or "")
        if payload.get("confirm"):
            return 200, library.reject_candidates(
                kb_path,
                candidate_ids,
                reason=reason,
                confirm=str(payload["confirm"]),
            )
        return 200, _confirmation_preview(
            safety.preview_action(
                kb_path,
                "candidate.reject",
                candidate_ids,
                details={"reason": reason},
            )
        )
    if path == "/api/library/delete":
        paper_ids = _ids(payload)
        if payload.get("confirm"):
            return 200, library.delete_papers(
                kb_path,
                paper_ids,
                confirm=str(payload["confirm"]),
            )
        return 200, _confirmation_preview(
            safety.preview_action(kb_path, "library.delete", paper_ids)
        )
    if path == "/api/library/reindex":
        paper_ids = (
            [item["id"] for item in storage.list_papers(kb_path)]
            if payload.get("all")
            else _ids(payload)
        )
        if payload.get("confirm"):
            return 200, library.reindex_library(
                kb_path,
                paper_ids,
                confirm=str(payload["confirm"]),
            )
        preview = retrieval.preview_reindex(kb_path, paper_ids)
        preview.update(safety.preview_action(kb_path, "library.reindex", paper_ids))
        return 200, _confirmation_preview(preview)
    if path == "/api/searches":
        if payload.get("id") is not None and "enabled" in payload:
            changed = scheduling.set_saved_search_enabled(
                kb_path,
                int(payload["id"]),
                bool(payload["enabled"]),
            )
            return 200, {"search_id": int(payload["id"]), "changed": changed}
        query = str(payload.get("query") or "").strip()
        if not query:
            raise ValueError("a confirmed PubMed query is required")
        search_id = scheduling.create_saved_search(
            kb_path,
            name=str(payload.get("name") or "").strip() or "Saved search",
            query=query,
            topic=str(payload.get("topic") or "").strip() or None,
            frequency=str(payload.get("frequency") or "weekly"),
            filters=payload.get("filters") if isinstance(payload.get("filters"), dict) else {},
            run_first=bool(payload.get("run_first", True)),
        )
        return 200, {"saved_search_id": search_id, "saved_search": storage.get_saved_search(kb_path, search_id)}
    if path == "/api/searches/run":
        search_ids = _ids(payload, required=False) or None
        return 200, {
            "runs": scheduling.run_saved_searches(
                kb_path,
                search_ids=search_ids,
                force=bool(payload.get("force")),
            )
        }
    if path == "/api/tasks/retry":
        return 200, {"results": _retry_tasks(kb_path, _ids(payload))}
    if path == "/api/pdfs/fetch":
        doc_id = int(payload["doc_id"]) if payload.get("doc_id") is not None else None
        return 200, {
            "results": pdfs.fetch_open_access_pdfs(
                kb_path,
                doc_id=doc_id,
                all_docs=bool(payload.get("all")),
            )
        }
    return 404, {"error": "not_found", "message": f"No API route for {path}"}


def create_handler(kb_path: str | Path) -> type[BaseHTTPRequestHandler]:
    root = Path(kb_path)

    class Handler(BaseHTTPRequestHandler):
        server_version = "MedicalKnowledgeBase/3"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            if content_type.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:",
                )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(
                status,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _dispatch(self, method: str) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if method == "GET" and parsed.path in {"/", "/index.html"}:
                self._send(200, render_page(), "text/html; charset=utf-8")
                return
            try:
                if method == "GET":
                    status, payload = _api_get(root, parsed.path, urllib.parse.parse_qs(parsed.query))
                else:
                    raw_length = self.headers.get("Content-Length")
                    if raw_length is None:
                        raise ValueError("Content-Length is required")
                    length = int(raw_length)
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        self._json(413, {"error": "request_too_large"})
                        return
                    status, payload = _api_post(root, parsed.path, self.headers, self.rfile.read(length))
                self._json(status, payload)
            except safety.SafetyGateError as exc:
                self._json(409, {"error": "safety_gate", "message": str(exc)})
            except (KeyError, TypeError, ValueError) as exc:
                self._json(400, {"error": "invalid_request", "message": str(exc)})
            except Exception as exc:
                self.log_error("Unhandled API error: %s", exc)
                self._json(500, {"error": "internal_error", "message": "The request could not be completed."})

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def create_server(
    kb_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    storage.init_kb(kb_path)
    return ThreadingHTTPServer((host, port), create_handler(kb_path))


def serve(kb_path: str | Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = create_server(kb_path, host=host, port=port)
    actual_host, actual_port = server.server_address
    print(f"Serving {Path(kb_path).resolve()} at http://{actual_host}:{actual_port}", flush=True)
    server.serve_forever()
