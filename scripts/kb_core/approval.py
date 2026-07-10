"""Candidate approval and post-approval task orchestration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import metadata, pdfs, retrieval, storage


def _index_abstract(conn: sqlite3.Connection, paper_id: int, text: str) -> None:
    cur = conn.execute(
        "insert into chunks (paper_id, text, source_locator, created_at) values (?, ?, 'abstract', ?)",
        (paper_id, text, storage.utc_now()),
    )
    try:
        conn.execute(
            "insert into chunks_fts(rowid, text) values (?, ?)",
            (int(cur.lastrowid), retrieval.normalized_search_text(text)),
        )
    except sqlite3.OperationalError:
        pass


def approve_candidates(
    kb_path: str | Path,
    candidate_ids: list[int],
    *,
    run_pdf_tasks: bool = True,
) -> dict[str, Any]:
    """Approve pending candidates, index abstracts, then run optional PDF tasks."""
    root = Path(kb_path)
    now = storage.utc_now()
    approved: list[int] = []
    paper_ids: list[int] = []
    pdf_sources: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    with storage.connect(root) as conn:
        for candidate_id in candidate_ids:
            row = conn.execute(
                "select * from candidate_papers where id = ? and status = 'pending'",
                (candidate_id,),
            ).fetchone()
            if row is None:
                continue
            candidate = storage.row_to_dict(row)
            raw = candidate.get("raw") or {}
            abstract = str(raw.get("abstract") or "").strip() or None
            pmid = str(candidate.get("pmid") or "").strip() or None
            doi = metadata.normalize_doi(candidate.get("doi")) or None
            duplicate_pmid = (
                conn.execute("select id from papers where pmid = ? limit 1", (pmid,)).fetchone()
                if pmid
                else None
            )
            duplicate_doi = doi and any(
                metadata.normalize_doi(row["doi"]) == doi
                for row in conn.execute("select doi from papers where doi is not null")
            )
            if duplicate_pmid is not None or duplicate_doi:
                skipped.append({"candidate_id": candidate_id, "reason": "already in library"})
                continue
            cur = conn.execute(
                """
                insert into papers
                    (pmid, doi, title, authors_json, journal, issn, eissn, publication_year,
                     study_type, if_2025, jcr_quartiles_json, abstract, source,
                     created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate-review', ?, ?)
                """,
                (
                    pmid,
                    doi,
                    candidate["title"],
                    json.dumps(candidate.get("authors") or [], ensure_ascii=False),
                    candidate.get("journal"),
                    candidate.get("issn"),
                    candidate.get("eissn"),
                    candidate.get("publication_year"),
                    candidate.get("study_type"),
                    candidate.get("if_2025"),
                    json.dumps(candidate.get("jcr_quartiles") or [], ensure_ascii=False),
                    abstract,
                    now,
                    now,
                ),
            )
            paper_id = int(cur.lastrowid)
            if abstract:
                _index_abstract(conn, paper_id, abstract)
            conn.execute(
                "update candidate_papers set status = 'approved', decided_at = ? where id = ?",
                (now, candidate_id),
            )
            approved.append(candidate_id)
            paper_ids.append(paper_id)
            pdf_sources.append(
                {
                    "paper_id": paper_id,
                    "pmid": candidate.get("pmid"),
                    "pmcid": raw.get("pmcid"),
                    "open_access_pdf_url": raw.get("open_access_pdf_url"),
                }
            )
        conn.commit()

    config = storage.load_config(root)
    pdf_enabled = bool((config.get("pdf_fetch") or {}).get("enabled", True))
    pdf_task_ids: list[int] = []
    if pdf_enabled:
        pdf_task_ids = [
            pdfs.queue_pdf_task(root, **source)
            for source in pdf_sources
        ]
    pdf_results = (
        pdfs.run_pending_tasks(root, task_ids=pdf_task_ids)
        if run_pdf_tasks and pdf_task_ids
        else []
    )
    return {
        "approved": approved,
        "paper_ids": paper_ids,
        "pdf_task_ids": pdf_task_ids,
        "pdf_results": pdf_results,
        "skipped": skipped,
    }
