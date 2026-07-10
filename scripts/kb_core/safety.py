"""State-bound confirmation tokens for destructive knowledge-base actions."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from . import storage


SUPPORTED_ACTIONS = {
    "library.delete",
    "candidate.reject",
    "library.reindex",
    "schedule.install",
    "schedule.uninstall",
}
SECRET_FILE = ".confirmation-secret"
DEFAULT_TTL_SECONDS = 600


class SafetyGateError(RuntimeError):
    """Raised when a protected action lacks a valid, fresh confirmation."""


def _now(value: dt.datetime | None = None) -> dt.datetime:
    value = value or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _ids(values: list[int]) -> list[int]:
    try:
        normalized = sorted({int(value) for value in values})
    except (TypeError, ValueError) as exc:
        raise SafetyGateError("confirmation target IDs must be integers") from exc
    if any(value <= 0 for value in normalized):
        raise SafetyGateError("confirmation target IDs must be positive")
    return normalized


def _secret(kb_path: str | Path) -> bytes:
    path = Path(kb_path) / SECRET_FILE
    if not path.exists():
        value = secrets.token_bytes(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(value)
            try:
                path.chmod(0o600)
            except OSError:
                pass
    value = path.read_bytes()
    if len(value) < 32:
        raise SafetyGateError("knowledge-base confirmation secret is invalid")
    return value


def _rows_for_ids(kb_path: str | Path, table: str, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with storage.connect(kb_path) as conn:
        rows = conn.execute(
            f"select * from {table} where id in ({placeholders}) order by id",
            ids,
        ).fetchall()
    return [storage.row_to_dict(row) for row in rows]


def _file_state(path_value: str | None) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_file():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _state_material(
    kb_path: str | Path,
    action: str,
    ids: list[int],
    details: dict[str, Any],
) -> dict[str, Any]:
    if action == "library.delete":
        rows = _rows_for_ids(kb_path, "papers", ids)
        return {"rows": rows, "details": details}
    if action == "candidate.reject":
        rows = _rows_for_ids(kb_path, "candidate_papers", ids)
        return {"rows": rows, "details": details}
    if action == "library.reindex":
        rows = _rows_for_ids(kb_path, "papers", ids)
        with storage.connect(kb_path) as conn:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                chunks = [
                    dict(row)
                    for row in conn.execute(
                        f"select * from chunks where paper_id in ({placeholders}) order by id",
                        ids,
                    )
                ]
            else:
                chunks = []
        files = {
            str(row["id"]): {
                "source": _file_state(row.get("source_path")),
                "full_text": _file_state(row.get("full_text_path")),
            }
            for row in rows
        }
        return {"rows": rows, "chunks": chunks, "files": files, "details": details}
    if action in {"schedule.install", "schedule.uninstall"}:
        return {"details": details}
    raise SafetyGateError(f"unsupported protected action: {action}")


def _state_digest(
    kb_path: str | Path,
    action: str,
    ids: list[int],
    details: dict[str, Any],
) -> str:
    material = _state_material(kb_path, action, ids, details)
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _items(kb_path: str | Path, action: str, ids: list[int]) -> list[dict[str, Any]]:
    if action in {"schedule.install", "schedule.uninstall"}:
        return []
    table = "candidate_papers" if action == "candidate.reject" else "papers"
    rows = _rows_for_ids(kb_path, table, ids)
    keys = ("id", "title", "status") if table == "candidate_papers" else ("id", "title", "pmid", "has_pdf")
    return [{key: row.get(key) for key in keys} for row in rows]


def preview_action(
    kb_path: str | Path,
    action: str,
    ids: list[int],
    *,
    details: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    if action not in SUPPORTED_ACTIONS:
        raise SafetyGateError(f"unsupported protected action: {action}")
    if ttl_seconds <= 0 or ttl_seconds > 3600:
        raise SafetyGateError("confirmation token lifetime must be between 1 and 3600 seconds")
    target_ids = _ids(ids)
    action_details = details or {}
    issued_at = _now(now)
    expires_at = issued_at + dt.timedelta(seconds=ttl_seconds)
    payload = {
        "kb": str(Path(kb_path).resolve()),
        "action": action,
        "ids": target_ids,
        "details": action_details,
        "state": _state_digest(kb_path, action, target_ids, action_details),
        "exp": int(expires_at.timestamp()),
    }
    payload_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_secret(kb_path), payload_bytes, hashlib.sha256).hexdigest().encode("ascii")
    token = base64.urlsafe_b64encode(payload_bytes + b"." + signature).decode("ascii")
    items = _items(kb_path, action, target_ids)
    affected = (
        [item for item in items if item.get("status") == "pending"]
        if action == "candidate.reject"
        else items
    )
    affected_count = 1 if action.startswith("schedule.") else len(affected)
    return {
        "action": action,
        "ids": target_ids,
        "details": action_details,
        "affected_count": affected_count,
        "items": items,
        "expires_at": expires_at.isoformat(),
        "confirmation_token": token,
    }


def verify_confirmation(
    kb_path: str | Path,
    action: str,
    ids: list[int],
    token: str | None,
    *,
    details: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    if not token:
        raise SafetyGateError("protected action requires a confirmation token")
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii"))
        payload_bytes, supplied_signature = decoded.rsplit(b".", 1)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SafetyGateError("confirmation token is invalid") from exc
    expected_signature = hmac.new(
        _secret(kb_path), payload_bytes, hashlib.sha256
    ).hexdigest().encode("ascii")
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise SafetyGateError("confirmation token is invalid")
    expected_ids = _ids(ids)
    expected_details = details or {}
    expected_kb = str(Path(kb_path).resolve())
    if (
        str(payload.get("kb", "")).casefold() != expected_kb.casefold()
        or payload.get("action") != action
        or payload.get("ids") != expected_ids
        or payload.get("details") != expected_details
    ):
        raise SafetyGateError("confirmation token does not match this action")
    if int(payload.get("exp", 0)) <= int(_now(now).timestamp()):
        raise SafetyGateError("confirmation token has expired")
    current_state = _state_digest(kb_path, action, expected_ids, expected_details)
    if not hmac.compare_digest(str(payload.get("state", "")), current_state):
        raise SafetyGateError("confirmation token is stale because target state changed")
    return payload
