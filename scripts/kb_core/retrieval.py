"""Evidence indexing, FTS5 retrieval, and deterministic fallback search."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from . import imports, storage


def search_terms(question: str) -> list[str]:
    english = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", question.lower())
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", question)
    cjk = [run[index : index + 2] for run in cjk_runs for index in range(len(run) - 1)]
    return list(dict.fromkeys([*english, *cjk]))


def normalized_search_text(text: str) -> str:
    return " ".join(search_terms(text))


def _insert_chunk(
    conn: sqlite3.Connection,
    *,
    paper_id: int,
    text: str,
    source_locator: str | None,
) -> int:
    cur = conn.execute(
        "insert into chunks (paper_id, text, source_locator, created_at) values (?, ?, ?, ?)",
        (paper_id, text, source_locator, storage.utc_now()),
    )
    chunk_id = int(cur.lastrowid)
    try:
        conn.execute(
            "insert into chunks_fts(rowid, text) values (?, ?)",
            (chunk_id, normalized_search_text(text)),
        )
    except sqlite3.OperationalError:
        pass
    return chunk_id


def add_chunks(
    kb_path: str | Path,
    paper_id: int,
    text: str,
    *,
    source_locator: str | None = None,
) -> list[int]:
    chunks = imports.chunk_text(text)
    with storage.connect(kb_path) as conn:
        chunk_ids = [
            _insert_chunk(
                conn,
                paper_id=paper_id,
                text=chunk,
                source_locator=source_locator,
            )
            for chunk in chunks
        ]
        conn.commit()
    return chunk_ids


def _base_select() -> str:
    return """
        select c.id as chunk_id, c.paper_id, c.text, c.source_locator,
               p.pmid, p.doi, p.title, p.journal, p.publication_year
        from chunks c join papers p on p.id = c.paper_id
    """


def _fallback_rows(
    conn: sqlite3.Connection,
    terms: list[str],
    doc_ids: list[int] | None,
    limit: int,
) -> list[dict[str, Any]]:
    where = ""
    args: list[Any] = []
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        where = f"where c.paper_id in ({placeholders})"
        args.extend(doc_ids)
    rows = conn.execute(f"{_base_select()} {where}", args).fetchall()
    scored: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        searchable = item["text"].lower()
        score = float(sum(searchable.count(term) for term in terms))
        if score > 0:
            item["score"] = score
            scored.append(item)
    scored.sort(key=lambda item: (-item["score"], item["chunk_id"]))
    return scored[:limit]


def _fts_rows(
    conn: sqlite3.Connection,
    terms: list[str],
    doc_ids: list[int] | None,
    limit: int,
) -> list[dict[str, Any]]:
    match_query = " OR ".join(f'"{term}"' for term in terms)
    scope = ""
    args: list[Any] = [match_query]
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        scope = f"and c.paper_id in ({placeholders})"
        args.extend(doc_ids)
    args.append(limit)
    rows = conn.execute(
        f"""
        select c.id as chunk_id, c.paper_id, c.text, c.source_locator,
               p.pmid, p.doi, p.title, p.journal, p.publication_year,
               bm25(chunks_fts) as rank
        from chunks_fts
        join chunks c on c.id = chunks_fts.rowid
        join papers p on p.id = c.paper_id
        where chunks_fts match ? {scope}
        order by rank asc, c.id asc
        limit ?
        """,
        args,
    ).fetchall()
    evidence = []
    for row in rows:
        item = dict(row)
        item["score"] = float(-item.pop("rank"))
        evidence.append(item)
    return evidence


def retrieve_evidence(
    kb_path: str | Path,
    *,
    question: str,
    doc_ids: list[int] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    terms = search_terms(question)
    if not terms:
        raise ValueError("question must contain searchable terms")
    if limit <= 0:
        raise ValueError("limit must be positive")
    method = "fts5_bm25"
    with storage.connect(kb_path) as conn:
        try:
            evidence = _fts_rows(conn, terms, doc_ids, limit)
        except sqlite3.OperationalError:
            evidence = []
        if not evidence:
            method = "scored_fallback"
            evidence = _fallback_rows(conn, terms, doc_ids, limit)
    return {
        "schema_version": storage.SCHEMA_VERSION,
        "question": question,
        "scope": {"doc_ids": doc_ids or "all"},
        "retrieved_at": storage.utc_now(),
        "retrieval_method": method,
        "search_terms": terms,
        "evidence": evidence,
        "answer_policy": (
            "Use only this evidence. If evidence is empty or insufficient, "
            "say evidence is insufficient."
        ),
    }


def _selected_papers(
    kb_path: str | Path,
    paper_ids: list[int] | None,
) -> list[dict[str, Any]]:
    with storage.connect(kb_path) as conn:
        if paper_ids is None:
            rows = conn.execute("select * from papers order by id").fetchall()
        elif not paper_ids:
            return []
        else:
            placeholders = ",".join("?" for _ in paper_ids)
            rows = conn.execute(
                f"select * from papers where id in ({placeholders}) order by id",
                paper_ids,
            ).fetchall()
    return [storage.row_to_dict(row) for row in rows]


def _paper_sources(paper: dict[str, Any]) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    abstract = str(paper.get("abstract") or "").strip()
    if abstract:
        sources.append(("abstract", abstract))
    full_text_path = Path(paper["full_text_path"]) if paper.get("full_text_path") else None
    if full_text_path and full_text_path.is_file():
        text = full_text_path.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            sources.append((str(full_text_path), text))
    elif paper.get("source_path"):
        source_path = Path(paper["source_path"])
        if source_path.is_file():
            text = imports.extract_text(source_path)
            if text.strip():
                sources.append((str(source_path), text))
    return sources


def _available_source_count(paper: dict[str, Any]) -> int:
    count = 1 if str(paper.get("abstract") or "").strip() else 0
    full_text_path = Path(paper["full_text_path"]) if paper.get("full_text_path") else None
    if full_text_path and full_text_path.is_file():
        return count + 1
    source_path = Path(paper["source_path"]) if paper.get("source_path") else None
    if source_path and source_path.is_file():
        return count + 1
    return count


def preview_reindex(
    kb_path: str | Path,
    paper_ids: list[int] | None = None,
) -> dict[str, Any]:
    papers = _selected_papers(kb_path, paper_ids)
    selected_ids = [paper["id"] for paper in papers]
    missing_ids = [
        paper_id for paper_id in dict.fromkeys(paper_ids or []) if paper_id not in selected_ids
    ]
    with storage.connect(kb_path) as conn:
        if selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            current_chunks = int(
                conn.execute(
                    f"select count(*) from chunks where paper_id in ({placeholders})",
                    selected_ids,
                ).fetchone()[0]
            )
        else:
            current_chunks = 0
    source_counts = {paper["id"]: _available_source_count(paper) for paper in papers}
    return {
        "paper_ids": selected_ids,
        "paper_count": len(selected_ids),
        "current_chunks": current_chunks,
        "source_documents": sum(source_counts.values()),
        "without_sources": [
            paper_id for paper_id, count in source_counts.items() if count == 0
        ],
        "missing_ids": missing_ids,
    }


def reindex_library(
    kb_path: str | Path,
    paper_ids: list[int] | None = None,
) -> dict[str, Any]:
    papers = _selected_papers(kb_path, paper_ids)
    selected_ids = [paper["id"] for paper in papers]
    missing_ids = [
        paper_id for paper_id in dict.fromkeys(paper_ids or []) if paper_id not in selected_ids
    ]
    source_map = {paper["id"]: _paper_sources(paper) for paper in papers}
    rebuildable = {paper_id: items for paper_id, items in source_map.items() if items}
    rebuilt: list[int] = []
    chunks_created = 0
    with storage.connect(kb_path) as conn:
        for paper_id, sources in rebuildable.items():
            old_ids = [
                int(row["id"])
                for row in conn.execute("select id from chunks where paper_id = ?", (paper_id,))
            ]
            for chunk_id in old_ids:
                try:
                    conn.execute("delete from chunks_fts where rowid = ?", (chunk_id,))
                except sqlite3.OperationalError:
                    break
            conn.execute("delete from chunks where paper_id = ?", (paper_id,))
            for locator, text in sources:
                for chunk in imports.chunk_text(text):
                    _insert_chunk(
                        conn,
                        paper_id=paper_id,
                        text=chunk,
                        source_locator=locator,
                    )
                    chunks_created += 1
            rebuilt.append(paper_id)
        conn.commit()
    return {
        "rebuilt": rebuilt,
        "skipped": [paper["id"] for paper in papers if paper["id"] not in rebuildable],
        "chunks_created": chunks_created,
        "missing_ids": missing_ids,
    }
