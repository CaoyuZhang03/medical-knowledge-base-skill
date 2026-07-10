"""Open-access-only PDF task creation and execution."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import storage


USER_AGENT = "medical-knowledge-base-skill/2.0"


def allowed_pdf_url(payload: dict[str, Any]) -> str | None:
    pmcid = str(payload.get("pmcid") or "").strip().upper()
    if pmcid:
        if not pmcid.startswith("PMC"):
            pmcid = "PMC" + pmcid
        if not re.fullmatch(r"PMC\d+", pmcid):
            return None
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
    explicit = str(payload.get("open_access_pdf_url") or "").strip()
    if explicit:
        parsed = urllib.parse.urlparse(explicit)
        if parsed.scheme == "https" and parsed.netloc:
            return explicit
    return None


def download_open_access_pdf(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type", "")
    if b"%PDF" not in data[:1024] and "pdf" not in content_type.lower():
        raise RuntimeError("open-access endpoint did not return a PDF")
    return data


def lookup_pmcid_for_pmid(pmid: str) -> str | None:
    params = urllib.parse.urlencode({"db": "pubmed", "id": pmid, "retmode": "json"})
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + params
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    item = data.get("result", {}).get(str(pmid), {})
    for article_id in item.get("articleids", []) or []:
        if article_id.get("idtype") == "pmc" and article_id.get("value"):
            value = str(article_id["value"]).upper()
            return value if value.startswith("PMC") else f"PMC{value}"
    return None


def queue_pdf_task(
    kb_path: str | Path,
    paper_id: int,
    *,
    pmid: str | None = None,
    pmcid: str | None = None,
    open_access_pdf_url: str | None = None,
) -> int:
    return storage.create_task(
        kb_path,
        "pdf_fetch",
        {
            "paper_id": paper_id,
            "pmid": pmid,
            "pmcid": pmcid,
            "open_access_pdf_url": open_access_pdf_url,
            "allowed_sources": ["pubmed-central", "open-access-link"],
        },
    )


def _paper(kb_path: str | Path, paper_id: int) -> dict[str, Any] | None:
    with storage.connect(kb_path) as conn:
        row = conn.execute("select * from papers where id = ?", (paper_id,)).fetchone()
    return storage.row_to_dict(row) if row else None


def run_pdf_task(kb_path: str | Path, task_id: int) -> dict[str, Any]:
    task = storage.get_task(kb_path, task_id)
    if not task or task.get("task_type") != "pdf_fetch":
        raise ValueError(f"PDF task not found: {task_id}")
    payload = dict(task.get("payload") or {})
    paper_id = int(payload["paper_id"])
    paper = _paper(kb_path, paper_id)
    if not paper:
        result = {"paper_id": paper_id, "status": "not_found", "reason": "paper not found"}
        storage.finish_task(kb_path, task_id, status="completed", result=result)
        return result
    if paper.get("has_pdf") and paper.get("pdf_path") and Path(paper["pdf_path"]).is_file():
        result = {"paper_id": paper_id, "status": "already_present", "path": paper["pdf_path"]}
        storage.finish_task(kb_path, task_id, status="completed", result=result)
        return result

    try:
        url = allowed_pdf_url(payload)
        if not url and payload.get("pmid"):
            pmcid = lookup_pmcid_for_pmid(str(payload["pmid"]))
            if pmcid:
                payload["pmcid"] = pmcid
                url = allowed_pdf_url(payload)
        if not url:
            result = {
                "paper_id": paper_id,
                "status": "not_found",
                "reason": "no PubMed Central or explicit open-access PDF source",
            }
            storage.finish_task(kb_path, task_id, status="completed", result=result)
            return result
        data = download_open_access_pdf(url)
        if b"%PDF" not in data[:1024]:
            raise RuntimeError("downloaded content is not a PDF")
        root = Path(kb_path)
        pdf_dir = root / "files" / "pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        stem = str(payload.get("pmcid") or f"paper-{paper_id}").upper()
        destination = pdf_dir / f"{stem}.pdf"
        destination.write_bytes(data)
        with storage.connect(kb_path) as conn:
            conn.execute(
                "update papers set has_pdf = 1, pdf_path = ?, updated_at = ? where id = ?",
                (str(destination), storage.utc_now(), paper_id),
            )
            conn.commit()
        result = {
            "paper_id": paper_id,
            "status": "downloaded",
            "url": url,
            "path": str(destination),
        }
        storage.finish_task(kb_path, task_id, status="completed", result=result)
        return result
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        storage.finish_task(kb_path, task_id, status="failed", error=error)
        return {"paper_id": paper_id, "status": "failed", "error": error}


def run_pending_tasks(
    kb_path: str | Path,
    *,
    task_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    with storage.connect(kb_path) as conn:
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            rows = conn.execute(
                f"select id from tasks where task_type = 'pdf_fetch' and status = 'pending' and id in ({placeholders})",
                task_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "select id from tasks where task_type = 'pdf_fetch' and status = 'pending' order by id"
            ).fetchall()
    return [run_pdf_task(kb_path, int(row["id"])) for row in rows]


def fetch_open_access_pdfs(
    kb_path: str | Path,
    *,
    doc_id: int | None = None,
    all_docs: bool = False,
) -> list[dict[str, Any]]:
    if not doc_id and not all_docs:
        raise ValueError("Provide doc_id or all_docs=True")
    with storage.connect(kb_path) as conn:
        if doc_id:
            rows = conn.execute("select id, pmid from papers where id = ?", (doc_id,)).fetchall()
        else:
            rows = conn.execute("select id, pmid from papers order by id").fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        paper_id = int(row["id"])
        if not row["pmid"]:
            results.append(
                {
                    "paper_id": paper_id,
                    "status": "skipped",
                    "reason": "missing PMID; upload PDF manually or add a PMCID-capable record",
                }
            )
            continue
        task_id = queue_pdf_task(kb_path, paper_id, pmid=row["pmid"])
        results.append(run_pdf_task(kb_path, task_id))
    return results
