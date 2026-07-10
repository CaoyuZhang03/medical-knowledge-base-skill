"""Guarded destructive operations for approved and candidate literature."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import retrieval, safety, scheduling, storage


def delete_papers(
    kb_path: str | Path,
    paper_ids: list[int],
    *,
    confirm: str | None,
) -> dict[str, Any]:
    with storage.connect(kb_path) as conn:
        conn.execute("begin immediate")
        safety.verify_confirmation(kb_path, "library.delete", paper_ids, confirm)
        deleted = []
        for paper_id in sorted(set(paper_ids)):
            cur = conn.execute("delete from papers where id = ?", (paper_id,))
            if cur.rowcount:
                deleted.append(paper_id)
        conn.commit()
    return {"deleted": deleted, "preserved_files": True}


def reject_candidates(
    kb_path: str | Path,
    candidate_ids: list[int],
    *,
    reason: str = "",
    confirm: str | None,
) -> dict[str, Any]:
    details = {"reason": reason}
    with storage.connect(kb_path) as conn:
        conn.execute("begin immediate")
        safety.verify_confirmation(
            kb_path,
            "candidate.reject",
            candidate_ids,
            confirm,
            details=details,
        )
        rejected = []
        now = storage.utc_now()
        for candidate_id in sorted(set(candidate_ids)):
            cur = conn.execute(
                """
                update candidate_papers
                set status = 'rejected', rejection_reason = ?, decided_at = ?
                where id = ? and status = 'pending'
                """,
                (reason, now, candidate_id),
            )
            if cur.rowcount:
                rejected.append(candidate_id)
        conn.commit()
    return {"rejected": rejected, "reason": reason}


def reindex_library(
    kb_path: str | Path,
    paper_ids: list[int],
    *,
    confirm: str | None,
) -> dict[str, Any]:
    safety.verify_confirmation(kb_path, "library.reindex", paper_ids, confirm)
    return retrieval.reindex_library(kb_path, paper_ids)


def _schedule_details(kb_path: str | Path, install: bool) -> dict[str, Any]:
    command = (
        scheduling.windows_task_scheduler_command(kb_path)
        if install
        else scheduling.scheduler_uninstall_command(kb_path)
    )
    return {
        "task_name": scheduling.scheduler_task_name(kb_path),
        "command": command,
    }


def preview_schedule_change(kb_path: str | Path, *, install: bool) -> dict[str, Any]:
    action = "schedule.install" if install else "schedule.uninstall"
    return safety.preview_action(
        kb_path,
        action,
        [],
        details=_schedule_details(kb_path, install),
    )


def change_schedule(
    kb_path: str | Path,
    *,
    install: bool,
    confirm: str | None,
) -> dict[str, Any]:
    action = "schedule.install" if install else "schedule.uninstall"
    safety.verify_confirmation(
        kb_path,
        action,
        [],
        confirm,
        details=_schedule_details(kb_path, install),
    )
    return scheduling.apply_windows_schedule(kb_path, install=install)
