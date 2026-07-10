"""Due-aware saved PubMed searches and Windows scheduler commands."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import pubmed, storage


FREQUENCY_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def _as_datetime(value: dt.datetime | str | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).replace(microsecond=0)


def next_run_at(last_run: dt.datetime | str, frequency: str) -> dt.datetime:
    if frequency not in FREQUENCY_DAYS:
        raise ValueError("frequency must be daily, weekly, or monthly")
    return _as_datetime(last_run) + dt.timedelta(days=FREQUENCY_DAYS[frequency])


def list_saved_searches(kb_path: str | Path) -> list[dict[str, Any]]:
    with storage.connect(kb_path) as conn:
        return [
            storage.row_to_dict(row)
            for row in conn.execute("select * from saved_searches order by id")
        ]


def create_saved_search(
    kb_path: str | Path,
    *,
    name: str,
    query: str,
    topic: str | None = None,
    frequency: str = "weekly",
    filters: dict[str, Any] | None = None,
    run_first: bool = True,
    now: dt.datetime | str | None = None,
) -> int:
    root = Path(kb_path)
    storage.init_kb(root)
    created = _as_datetime(now)
    upcoming = next_run_at(created, frequency)
    with storage.connect(root) as conn:
        cur = conn.execute(
            """
            insert into saved_searches
                (name, topic, query, filters_json, frequency, enabled, next_run_at, created_at)
            values (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                name.strip(),
                topic,
                query.strip(),
                json.dumps(filters or {}, ensure_ascii=False),
                frequency,
                upcoming.isoformat(),
                created.isoformat(),
            ),
        )
        search_id = int(cur.lastrowid)
        conn.commit()
    if run_first:
        search = storage.get_saved_search(root, search_id)
        if search:
            run_one_search(root, search, now=created)
    return search_id


def set_saved_search_enabled(kb_path: str | Path, search_id: int, enabled: bool) -> bool:
    with storage.connect(kb_path) as conn:
        cur = conn.execute(
            "update saved_searches set enabled = ? where id = ?",
            (1 if enabled else 0, search_id),
        )
        conn.commit()
    return bool(cur.rowcount)


def run_one_search(
    kb_path: str | Path,
    search: dict[str, Any],
    *,
    now: dt.datetime | str | None = None,
) -> dict[str, Any]:
    run_at = _as_datetime(now)
    task_id = storage.create_task(
        kb_path,
        "scheduled_update",
        {"search_id": search["id"], "query": search["query"]},
    )
    try:
        filters = dict(search.get("filters") or {})
        max_results = int(filters.pop("max_results", 20))
        candidate_ids = pubmed.search_to_candidates(
            kb_path,
            query=search["query"],
            max_results=max_results,
            filters=filters,
            search_id=search["id"],
        )
        upcoming = next_run_at(run_at, search["frequency"])
        with storage.connect(kb_path) as conn:
            conn.execute(
                "update saved_searches set last_run_at = ?, next_run_at = ? where id = ?",
                (run_at.isoformat(), upcoming.isoformat(), search["id"]),
            )
            conn.commit()
        result = {
            "search_id": search["id"],
            "task_id": task_id,
            "candidate_ids": candidate_ids,
            "next_run_at": upcoming.isoformat(),
            "status": "completed",
        }
        storage.finish_task(kb_path, task_id, status="completed", result=result)
        return result
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        with storage.connect(kb_path) as conn:
            conn.execute(
                "update saved_searches set next_run_at = ? where id = ?",
                (run_at.isoformat(), search["id"]),
            )
            conn.commit()
        storage.finish_task(kb_path, task_id, status="failed", error=error)
        return {
            "search_id": search["id"],
            "task_id": task_id,
            "status": "failed",
            "error": error,
        }


def run_saved_searches(
    kb_path: str | Path,
    *,
    search_ids: list[int] | None = None,
    force: bool = False,
    now: dt.datetime | str | None = None,
) -> list[dict[str, Any]]:
    run_at = _as_datetime(now)
    selected = set(search_ids or [])
    results: list[dict[str, Any]] = []
    for search in list_saved_searches(kb_path):
        if not search.get("enabled"):
            continue
        explicitly_selected = bool(selected) and search["id"] in selected
        if selected and not explicitly_selected:
            continue
        due_at = search.get("next_run_at")
        due = due_at is None or _as_datetime(due_at) <= run_at
        if force or explicitly_selected or due:
            results.append(run_one_search(kb_path, search, now=run_at))
    return results


def scheduler_task_name(kb_path: str | Path) -> str:
    resolved = str(Path(kb_path).resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:10]
    return f"MedicalKnowledgeBaseUpdate-{digest}"


def windows_task_scheduler_command(kb_path: str | Path) -> str:
    args = windows_task_scheduler_args(kb_path)
    task_name = args[args.index("/TN") + 1]
    action = args[args.index("/TR") + 1]
    powershell_action = action.replace("'", "''")
    return f'schtasks /Create /F /SC DAILY /TN "{task_name}" /TR \'{powershell_action}\''


def windows_task_scheduler_args(kb_path: str | Path) -> list[str]:
    root = Path(kb_path).resolve()
    script = Path(__file__).resolve().parents[1] / "kb.py"
    task_name = scheduler_task_name(root)
    action = subprocess.list2cmdline([sys.executable, str(script), "schedule", "run", str(root)])
    return [
        "schtasks.exe",
        "/Create",
        "/F",
        "/SC",
        "DAILY",
        "/TN",
        task_name,
        "/TR",
        action,
    ]


def scheduler_uninstall_command(kb_path: str | Path) -> str:
    return f'schtasks /Delete /F /TN "{scheduler_task_name(kb_path)}"'


def scheduler_uninstall_args(kb_path: str | Path) -> list[str]:
    return ["schtasks.exe", "/Delete", "/F", "/TN", scheduler_task_name(kb_path)]


def apply_windows_schedule(kb_path: str | Path, *, install: bool) -> dict[str, Any]:
    args = windows_task_scheduler_args(kb_path) if install else scheduler_uninstall_args(kb_path)
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    return {
        "status": "installed" if install else "uninstalled",
        "task_name": scheduler_task_name(kb_path),
        "output": completed.stdout.strip(),
    }
