#!/usr/bin/env python3
"""Local biomedical knowledge base toolkit for the medical-knowledge-base skill."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = SKILL_ROOT / "assets"
DEFAULT_JCR_PATH = ASSETS_DIR / "data" / "JCR2025-UTF8.csv"
SCHEMA_VERSION = "1.0.0"


class SafetyGateError(RuntimeError):
    """Raised when a workflow would cross a human-approval gate."""


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: str | None) -> str:
    value = value or ""
    value = value.strip().lower()
    value = re.sub(r"^the\s+", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_issn(value: str | None) -> str:
    return re.sub(r"[^0-9Xx]", "", value or "").upper()


def kb_db_path(kb_path: str | Path) -> Path:
    return Path(kb_path) / "kb.sqlite"


def connect(kb_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(kb_db_path(kb_path), factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def init_kb(kb_path: str | Path) -> dict[str, Any]:
    root = Path(kb_path)
    root.mkdir(parents=True, exist_ok=True)
    for rel in [
        "files/uploads",
        "files/pdfs",
        "files/extracted-text",
        "logs",
    ]:
        (root / rel).mkdir(parents=True, exist_ok=True)

    if not (root / "config.yaml").exists():
        (root / "config.yaml").write_text(
            textwrap.dedent(
                f"""\
                schema_version: "{SCHEMA_VERSION}"
                created_at: "{utc_now()}"
                pdf_fetch:
                  enabled: true
                  allowed_sources:
                    - pubmed-central
                    - open-access-link
                pubmed:
                  email: null
                  api_key: null
                defaults:
                  candidate_auto_ingest: false
                  require_search_confirmation: true
                """
            ),
            encoding="utf-8",
        )

    if not (root / "kb-passport.yaml").exists():
        (root / "kb-passport.yaml").write_text(
            textwrap.dedent(
                f"""\
                schema_version: "{SCHEMA_VERSION}"
                created_at: "{utc_now()}"
                kb_path: "{root}"
                jcr_table:
                  bundled_path: "{DEFAULT_JCR_PATH}"
                  version: "JCR2025"
                prompt_assets:
                  search_query_generation: "assets/prompts/search-query-generation.md"
                policy:
                  pubmed_topic_search_requires_confirmation: true
                  candidates_require_user_approval: true
                  scheduled_updates_write_candidates_only: true
                  pdf_fetch_open_access_only: true
                """
            ),
            encoding="utf-8",
        )

    with connect(root) as conn:
        create_schema(conn)
    return {"kb_path": str(root), "schema_version": SCHEMA_VERSION}


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists papers (
            id integer primary key autoincrement,
            pmid text unique,
            doi text,
            title text not null,
            authors_json text not null default '[]',
            journal text,
            issn text,
            eissn text,
            publication_year integer,
            study_type text,
            if_2025 real,
            jcr_quartiles_json text not null default '[]',
            has_pdf integer not null default 0,
            pdf_path text,
            source text not null default 'manual',
            created_at text not null,
            updated_at text not null
        );

        create table if not exists candidate_papers (
            id integer primary key autoincrement,
            pmid text,
            doi text,
            title text not null,
            authors_json text not null default '[]',
            journal text,
            issn text,
            eissn text,
            publication_year integer,
            study_type text,
            if_2025 real,
            jcr_quartiles_json text not null default '[]',
            raw_json text not null default '{}',
            status text not null default 'pending'
                check (status in ('pending', 'approved', 'rejected')),
            rejection_reason text,
            search_id integer,
            created_at text not null,
            decided_at text
        );

        create table if not exists saved_searches (
            id integer primary key autoincrement,
            name text not null,
            topic text,
            query text not null,
            filters_json text not null default '{}',
            frequency text not null default 'weekly',
            enabled integer not null default 1,
            last_run_at text,
            created_at text not null
        );

        create table if not exists tasks (
            id integer primary key autoincrement,
            task_type text not null,
            payload_json text not null default '{}',
            status text not null default 'pending',
            created_at text not null,
            updated_at text not null
        );

        create table if not exists chunks (
            id integer primary key autoincrement,
            paper_id integer not null references papers(id) on delete cascade,
            text text not null,
            source_locator text,
            created_at text not null
        );
        """
    )
    try:
        conn.execute(
            "create virtual table if not exists chunks_fts using fts5(text, content='chunks', content_rowid='id')"
        )
    except sqlite3.OperationalError:
        # Some embedded SQLite builds lack FTS5. Retrieval falls back to LIKE/scored scan.
        pass
    conn.commit()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("authors_json", "jcr_quartiles_json", "raw_json", "filters_json", "payload_json"):
        if key in data:
            out_key = key.removesuffix("_json")
            try:
                data[out_key] = json.loads(data.pop(key) or "null")
            except json.JSONDecodeError:
                data[out_key] = None
    for key in ("has_pdf", "enabled"):
        if key in data and data[key] is not None:
            data[key] = bool(data[key])
    return data


def load_jcr_catalog(path: str | Path = DEFAULT_JCR_PATH) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    by_issn: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            entry = parse_jcr_row(raw)
            rows.append(entry)
            for value in (entry.get("issn"), entry.get("eissn")):
                if value:
                    by_issn[value] = entry
            if entry.get("journal_key"):
                by_title[entry["journal_key"]] = entry
    return {"rows": rows, "by_issn": by_issn, "by_title": by_title}


def parse_jcr_row(raw: dict[str, str]) -> dict[str, Any]:
    quartiles = []
    for index in range(1, 7):
        category = (raw.get(f"Category_{index}") or "").strip()
        quartile = (raw.get(f"IF Quartile(2025)_{index}") or "").strip()
        rank = (raw.get(f"IF Rank(2025)_{index}") or "").strip()
        if category or quartile or rank:
            quartiles.append({"category": category, "quartile": quartile, "rank": rank})
    if_value = None
    if_raw = (raw.get("IF(2025)") or "").strip()
    if if_raw:
        try:
            if_value = float(if_raw)
        except ValueError:
            if_value = None
    journal = (raw.get("Journal") or "").strip()
    return {
        "journal": journal,
        "journal_key": normalize_text(journal),
        "issn": normalize_issn(raw.get("ISSN")),
        "eissn": normalize_issn(raw.get("EISSN")),
        "web_of_science": (raw.get("Web of Science") or "").strip(),
        "if_2025": if_value,
        "quartiles": quartiles,
    }


def match_jcr(
    catalog: dict[str, Any],
    *,
    journal: str | None = None,
    issn: str | None = None,
    eissn: str | None = None,
) -> dict[str, Any] | None:
    for label, value in (("issn", issn), ("eissn", eissn)):
        key = normalize_issn(value)
        if key and key in catalog["by_issn"]:
            match = dict(catalog["by_issn"][key])
            match["match_method"] = label
            return match
    title_key = normalize_text(journal)
    if title_key and title_key in catalog["by_title"]:
        match = dict(catalog["by_title"][title_key])
        match["match_method"] = "journal"
        return match
    return None


STUDY_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("随机对照试验", ["randomized controlled trial", "randomised controlled trial"]),
    ("非随机对照试验", ["controlled clinical trial", "non-randomized", "nonrandomized"]),
    ("观察性研究", ["observational study", "cohort", "cross-sectional", "case-control"]),
    ("病例报告/病例系列报告", ["case reports", "case report", "case series"]),
    ("Meta分析", ["meta-analysis", "meta analysis"]),
    ("系统性综述", ["systematic review"]),
    ("指南/共识", ["practice guideline", "guideline", "consensus development conference", "consensus"]),
    ("信件/讲义", ["letter", "lecture"]),
    ("回顾性研究", ["retrospective studies", "retrospective study"]),
    ("期刊论文", ["journal article"]),
    ("社论/评论", ["editorial", "comment"]),
    ("文献综述", ["review"]),
    ("会议内容", ["congress", "conference"]),
    ("勘误", ["published erratum", "erratum", "correction"]),
    ("临床研究", ["clinical trial", "clinical study", "clinical research"]),
]


def classify_study_type(publication_types: list[str] | None, mesh_terms: list[str] | None = None) -> str:
    haystack = " | ".join([*(publication_types or []), *(mesh_terms or [])]).lower()
    for label, needles in STUDY_TYPE_RULES:
        if any(needle in haystack for needle in needles):
            return label
    return "unknown"


def add_candidate(kb_path: str | Path, record: dict[str, Any]) -> int:
    now = utc_now()
    with connect(kb_path) as conn:
        cur = conn.execute(
            """
            insert into candidate_papers
                (pmid, doi, title, authors_json, journal, issn, eissn, publication_year,
                 study_type, if_2025, jcr_quartiles_json, raw_json, status, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                record.get("pmid"),
                record.get("doi"),
                record["title"],
                json.dumps(record.get("authors", []), ensure_ascii=False),
                record.get("journal"),
                record.get("issn"),
                record.get("eissn"),
                record.get("publication_year"),
                record.get("study_type"),
                record.get("if_2025"),
                json.dumps(record.get("jcr_quartiles", []), ensure_ascii=False),
                json.dumps(record, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_candidates(kb_path: str | Path, status: str | None = None) -> list[dict[str, Any]]:
    query = "select * from candidate_papers"
    args: list[Any] = []
    if status:
        query += " where status = ?"
        args.append(status)
    query += " order by created_at desc, id desc"
    with connect(kb_path) as conn:
        return [row_to_dict(row) for row in conn.execute(query, args)]


def insert_paper(kb_path: str | Path, record: dict[str, Any]) -> int:
    now = utc_now()
    with connect(kb_path) as conn:
        cur = conn.execute(
            """
            insert into papers
                (pmid, doi, title, authors_json, journal, issn, eissn, publication_year,
                 study_type, if_2025, jcr_quartiles_json, has_pdf, pdf_path, source,
                 created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("pmid"),
                record.get("doi"),
                record["title"],
                json.dumps(record.get("authors", []), ensure_ascii=False),
                record.get("journal"),
                record.get("issn"),
                record.get("eissn"),
                record.get("publication_year"),
                record.get("study_type"),
                record.get("if_2025"),
                json.dumps(record.get("jcr_quartiles", []), ensure_ascii=False),
                1 if record.get("has_pdf") else 0,
                record.get("pdf_path"),
                record.get("source", "manual"),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_papers(kb_path: str | Path) -> list[dict[str, Any]]:
    with connect(kb_path) as conn:
        return [row_to_dict(row) for row in conn.execute("select * from papers order by id")]


def approve_candidates(kb_path: str | Path, candidate_ids: list[int]) -> list[int]:
    approved: list[int] = []
    now = utc_now()
    with connect(kb_path) as conn:
        for candidate_id in candidate_ids:
            row = conn.execute(
                "select * from candidate_papers where id = ? and status = 'pending'",
                (candidate_id,),
            ).fetchone()
            if row is None:
                continue
            candidate = row_to_dict(row)
            conn.execute(
                """
                insert into papers
                    (pmid, doi, title, authors_json, journal, issn, eissn, publication_year,
                     study_type, if_2025, jcr_quartiles_json, source, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate-review', ?, ?)
                """,
                (
                    candidate.get("pmid"),
                    candidate.get("doi"),
                    candidate["title"],
                    json.dumps(candidate.get("authors", []), ensure_ascii=False),
                    candidate.get("journal"),
                    candidate.get("issn"),
                    candidate.get("eissn"),
                    candidate.get("publication_year"),
                    candidate.get("study_type"),
                    candidate.get("if_2025"),
                    json.dumps(candidate.get("jcr_quartiles", []), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute(
                "update candidate_papers set status = 'approved', decided_at = ? where id = ?",
                (now, candidate_id),
            )
            approved.append(candidate_id)
        conn.commit()
    return approved


def reject_candidates(kb_path: str | Path, candidate_ids: list[int], reason: str = "") -> list[int]:
    rejected: list[int] = []
    now = utc_now()
    with connect(kb_path) as conn:
        for candidate_id in candidate_ids:
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
    return rejected


def add_chunk(
    kb_path: str | Path,
    *,
    paper_id: int,
    chunk_text: str,
    source_locator: str | None = None,
) -> int:
    with connect(kb_path) as conn:
        cur = conn.execute(
            "insert into chunks (paper_id, text, source_locator, created_at) values (?, ?, ?, ?)",
            (paper_id, chunk_text, source_locator, utc_now()),
        )
        chunk_id = int(cur.lastrowid)
        try:
            conn.execute("insert into chunks_fts(rowid, text) values (?, ?)", (chunk_id, chunk_text))
        except sqlite3.OperationalError:
            pass
        conn.commit()
        return chunk_id


def tokenize(value: str) -> list[str]:
    return [token for token in re.findall(r"[\w\u4e00-\u9fff]+", value.lower()) if len(token) > 1]


def retrieve_evidence(
    kb_path: str | Path,
    *,
    question: str,
    doc_ids: list[int] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    terms = tokenize(question)
    where = ""
    args: list[Any] = []
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        where = f"where c.paper_id in ({placeholders})"
        args.extend(doc_ids)
    with connect(kb_path) as conn:
        rows = conn.execute(
            f"""
            select c.id as chunk_id, c.paper_id, c.text, c.source_locator,
                   p.pmid, p.doi, p.title, p.journal, p.publication_year
            from chunks c join papers p on p.id = c.paper_id
            {where}
            """,
            args,
        ).fetchall()
    scored = []
    for row in rows:
        data = dict(row)
        text_l = data["text"].lower()
        score = sum(text_l.count(term) for term in terms)
        if score or not terms:
            data["score"] = score
            scored.append(data)
    scored.sort(key=lambda item: (item["score"], item["chunk_id"]), reverse=True)
    evidence = scored[:limit]
    return {
        "schema_version": SCHEMA_VERSION,
        "question": question,
        "scope": {"doc_ids": doc_ids or "all"},
        "retrieved_at": utc_now(),
        "evidence": evidence,
        "answer_policy": "Use only this evidence. If evidence is empty or insufficient, say evidence is insufficient.",
    }


def import_files(kb_path: str | Path, files: list[str | Path]) -> list[int]:
    init_kb(kb_path)
    imported: list[int] = []
    uploads = Path(kb_path) / "files" / "uploads"
    extracted = Path(kb_path) / "files" / "extracted-text"
    for file in files:
        src = Path(file)
        if not src.is_file():
            raise FileNotFoundError(src)
        digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
        dest = uploads / f"{digest}-{src.name}"
        if not dest.exists():
            shutil.copy2(src, dest)
        title = src.stem
        paper_id = insert_paper(
            kb_path,
            {
                "title": title,
                "source": "local-upload",
                "has_pdf": src.suffix.lower() == ".pdf",
                "pdf_path": str(dest) if src.suffix.lower() == ".pdf" else None,
            },
        )
        text = extract_text_best_effort(dest)
        if text.strip():
            text_path = extracted / f"{paper_id}.txt"
            text_path.write_text(text, encoding="utf-8")
            for chunk in chunk_text(text):
                add_chunk(kb_path, paper_id=paper_id, chunk_text=chunk, source_locator=str(text_path))
        imported.append(paper_id)
    return imported


def extract_text_best_effort(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        try:
            import fitz  # type: ignore

            with fitz.open(path) as doc:
                return "\n".join(page.get_text() for page in doc)
        except Exception:
            return ""
    if suffix == ".docx":
        try:
            import zipfile
            import xml.etree.ElementTree as ET

            with zipfile.ZipFile(path) as zf:
                xml = zf.read("word/document.xml")
            root = ET.fromstring(xml)
            return "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
        except Exception:
            return ""
    return ""


def chunk_text(text: str, *, max_chars: int = 1200) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs or [text.strip()]:
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}".strip()
        else:
            if current:
                chunks.append(current)
            current = para[:max_chars]
    if current:
        chunks.append(current)
    return chunks


def pubmed_search(
    kb_path: str | Path,
    *,
    topic: str | None = None,
    query: str | None = None,
    confirmed: bool = False,
    max_results: int = 20,
) -> list[int]:
    if topic and not confirmed:
        raise SafetyGateError(
            "Topic-based PubMed searches must be confirmed before retrieval; generate the query first and ask the user to confirm."
        )
    search_query = query or topic
    if not search_query:
        raise ValueError("Provide topic or query")
    init_kb(kb_path)
    records = fetch_pubmed_records(search_query, max_results=max_results)
    candidate_ids = []
    for record in records:
        candidate_ids.append(add_candidate(kb_path, record))
    return candidate_ids


def fetch_pubmed_records(query: str, *, max_results: int = 20) -> list[dict[str, Any]]:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    params = urllib.parse.urlencode(
        {"db": "pubmed", "term": query, "retmode": "json", "retmax": str(max_results)}
    )
    with urllib.request.urlopen(base + "esearch.fcgi?" + params, timeout=30) as response:
        search_data = json.loads(response.read().decode("utf-8"))
    ids = search_data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    summary_params = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "json"})
    with urllib.request.urlopen(base + "esummary.fcgi?" + summary_params, timeout=30) as response:
        summary_data = json.loads(response.read().decode("utf-8"))
    result = summary_data.get("result", {})
    records = []
    for pmid in ids:
        item = result.get(pmid, {})
        pubtypes = item.get("pubtype", []) or []
        records.append(
            {
                "pmid": pmid,
                "title": strip_pubmed_markup(item.get("title") or ""),
                "journal": item.get("fulljournalname") or item.get("source"),
                "publication_year": parse_year(item.get("pubdate")),
                "authors": [
                    {"name": author.get("name")}
                    for author in item.get("authors", [])
                    if author.get("name")
                ],
                "study_type": classify_study_type(pubtypes, []),
                "raw_pubmed": item,
            }
        )
    return records


def strip_pubmed_markup(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def parse_year(value: str | None) -> int | None:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", value or "")
    return int(match.group(1)) if match else None


def create_saved_search(
    kb_path: str | Path,
    *,
    name: str,
    query: str,
    topic: str | None = None,
    frequency: str = "weekly",
    filters: dict[str, Any] | None = None,
) -> int:
    init_kb(kb_path)
    with connect(kb_path) as conn:
        cur = conn.execute(
            """
            insert into saved_searches (name, topic, query, filters_json, frequency, enabled, created_at)
            values (?, ?, ?, ?, ?, 1, ?)
            """,
            (name, topic, query, json.dumps(filters or {}, ensure_ascii=False), frequency, utc_now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def run_saved_searches(kb_path: str | Path) -> list[dict[str, Any]]:
    results = []
    with connect(kb_path) as conn:
        searches = conn.execute("select * from saved_searches where enabled = 1").fetchall()
    for row in searches:
        search = row_to_dict(row)
        ids = pubmed_search(kb_path, query=search["query"], confirmed=True)
        with connect(kb_path) as conn:
            conn.execute("update saved_searches set last_run_at = ? where id = ?", (utc_now(), search["id"]))
            conn.commit()
        results.append({"search_id": search["id"], "candidate_ids": ids})
    return results


def delete_papers(kb_path: str | Path, paper_ids: list[int]) -> list[int]:
    deleted = []
    with connect(kb_path) as conn:
        for paper_id in paper_ids:
            row = conn.execute("select * from papers where id = ?", (paper_id,)).fetchone()
            if row is None:
                continue
            conn.execute("delete from papers where id = ?", (paper_id,))
            deleted.append(paper_id)
        conn.commit()
    return deleted


def fetch_open_access_pdfs(
    kb_path: str | Path,
    *,
    doc_id: int | None = None,
    all_docs: bool = False,
) -> list[dict[str, Any]]:
    if not doc_id and not all_docs:
        raise ValueError("Provide doc_id or all_docs=True")
    root = Path(kb_path)
    (root / "files" / "pdfs").mkdir(parents=True, exist_ok=True)
    with connect(root) as conn:
        if doc_id:
            rows = conn.execute("select * from papers where id = ?", (doc_id,)).fetchall()
        else:
            rows = conn.execute("select * from papers order by id").fetchall()

    results = []
    for row in rows:
        paper = row_to_dict(row)
        paper_id = paper["id"]
        if paper.get("has_pdf") and paper.get("pdf_path") and Path(paper["pdf_path"]).is_file():
            results.append({"paper_id": paper_id, "status": "already_present", "path": paper["pdf_path"]})
            continue
        pmid = paper.get("pmid")
        if not pmid:
            results.append(
                {
                    "paper_id": paper_id,
                    "status": "skipped",
                    "reason": "missing PMID; upload PDF manually or add a PMCID-capable record",
                }
            )
            continue
        try:
            pmcid = lookup_pmcid_for_pmid(pmid)
        except Exception as exc:
            results.append({"paper_id": paper_id, "status": "failed", "reason": f"PMCID lookup failed: {exc}"})
            continue
        if not pmcid:
            results.append({"paper_id": paper_id, "status": "not_found", "reason": "no PubMed Central record"})
            continue
        dest = root / "files" / "pdfs" / f"{pmcid}.pdf"
        try:
            download_pmc_pdf(pmcid, dest)
        except Exception as exc:
            results.append({"paper_id": paper_id, "status": "failed", "reason": f"PMC PDF download failed: {exc}"})
            continue
        with connect(root) as conn:
            conn.execute(
                "update papers set has_pdf = 1, pdf_path = ?, updated_at = ? where id = ?",
                (str(dest), utc_now(), paper_id),
            )
            conn.commit()
        results.append({"paper_id": paper_id, "status": "downloaded", "pmcid": pmcid, "path": str(dest)})
    return results


def lookup_pmcid_for_pmid(pmid: str) -> str | None:
    params = urllib.parse.urlencode({"db": "pubmed", "id": pmid, "retmode": "json"})
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + params
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    item = data.get("result", {}).get(str(pmid), {})
    for article_id in item.get("articleids", []) or []:
        if article_id.get("idtype") == "pmc" and article_id.get("value"):
            value = article_id["value"]
            return value if value.startswith("PMC") else f"PMC{value}"
    return None


def download_pmc_pdf(pmcid: str, dest: Path) -> None:
    url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
    request = urllib.request.Request(url, headers={"User-Agent": "medical-knowledge-base-skill/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content_type = response.headers.get("Content-Type", "")
        data = response.read()
    if b"%PDF" not in data[:1024] and "pdf" not in content_type.lower():
        raise RuntimeError("PMC endpoint did not return a PDF")
    dest.write_bytes(data)


def audit_kb(kb_path: str | Path) -> dict[str, Any]:
    root = Path(kb_path)
    issues = []
    if not (root / "kb.sqlite").is_file():
        issues.append("missing kb.sqlite")
    if not (root / "config.yaml").is_file():
        issues.append("missing config.yaml")
    if not (root / "kb-passport.yaml").is_file():
        issues.append("missing kb-passport.yaml")
    counts: dict[str, int] = {}
    if not issues:
        with connect(root) as conn:
            for table in ("papers", "candidate_papers", "saved_searches", "tasks", "chunks"):
                counts[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
    return {"kb_path": str(root), "issues": issues, "counts": counts, "passed": not issues}


def generate_html_dashboard(kb_path: str | Path) -> str:
    papers = list_papers(kb_path)
    candidates = list_candidates(kb_path, status="pending")
    rows = "\n".join(
        f"<tr><td>{p['id']}</td><td>{html.escape(p.get('title') or '')}</td><td>{html.escape(p.get('journal') or '')}</td><td>{p.get('if_2025') or ''}</td><td>{html.escape(p.get('study_type') or '')}</td><td>{'yes' if p.get('has_pdf') else 'no'}</td></tr>"
        for p in papers
    )
    candidate_rows = "\n".join(
        f"<tr><td>{c['id']}</td><td>{html.escape(c.get('title') or '')}</td><td>{html.escape(c.get('journal') or '')}</td><td>{html.escape(c.get('study_type') or '')}</td></tr>"
        for c in candidates
    )
    template_path = ASSETS_DIR / "web-ui" / "index.html"
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else "{content}"
    content = f"""
    <section><h2>Library</h2><table><thead><tr><th>ID</th><th>Title</th><th>Journal</th><th>IF</th><th>Study type</th><th>PDF</th></tr></thead><tbody>{rows}</tbody></table></section>
    <section><h2>Candidate Queue</h2><table><thead><tr><th>ID</th><th>Title</th><th>Journal</th><th>Study type</th></tr></thead><tbody>{candidate_rows}</tbody></table></section>
    """
    return template.replace("{{content}}", content)


def serve(kb_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    init_kb(kb_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/api/audit"):
                body = json.dumps(audit_kb(kb_path), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = generate_html_dashboard(kb_path).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    print(f"Serving {kb_path} at http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parse_ids(values: list[str]) -> list[int]:
    return [int(value) for value in values]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb", description="Local biomedical knowledge base toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("kb_path")

    p_import = sub.add_parser("import")
    p_import.add_argument("kb_path")
    p_import.add_argument("files", nargs="+")

    p_pubmed = sub.add_parser("pubmed")
    pubmed_sub = p_pubmed.add_subparsers(dest="pubmed_command", required=True)
    p_search = pubmed_sub.add_parser("search")
    p_search.add_argument("kb_path")
    group = p_search.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic")
    group.add_argument("--query")
    p_search.add_argument("--confirmed", action="store_true")
    p_search.add_argument("--max-results", type=int, default=20)

    p_candidates = sub.add_parser("candidates")
    cand_sub = p_candidates.add_subparsers(dest="candidate_command", required=True)
    p_cand_list = cand_sub.add_parser("list")
    p_cand_list.add_argument("kb_path")
    p_cand_list.add_argument("--status")
    p_cand_approve = cand_sub.add_parser("approve")
    p_cand_approve.add_argument("kb_path")
    p_cand_approve.add_argument("ids", nargs="+")
    p_cand_reject = cand_sub.add_parser("reject")
    p_cand_reject.add_argument("kb_path")
    p_cand_reject.add_argument("ids", nargs="+")
    p_cand_reject.add_argument("--reason", default="")

    p_library = sub.add_parser("library")
    lib_sub = p_library.add_subparsers(dest="library_command", required=True)
    p_lib_list = lib_sub.add_parser("list")
    p_lib_list.add_argument("kb_path")
    p_lib_delete = lib_sub.add_parser("delete")
    p_lib_delete.add_argument("kb_path")
    p_lib_delete.add_argument("ids", nargs="+")
    p_lib_reindex = lib_sub.add_parser("reindex")
    p_lib_reindex.add_argument("kb_path")

    p_schedule = sub.add_parser("schedule")
    sched_sub = p_schedule.add_subparsers(dest="schedule_command", required=True)
    p_sched_create = sched_sub.add_parser("create")
    p_sched_create.add_argument("kb_path")
    p_sched_create.add_argument("--name", required=True)
    p_sched_create.add_argument("--query", required=True)
    p_sched_create.add_argument("--topic")
    p_sched_create.add_argument("--frequency", default="weekly")
    p_sched_run = sched_sub.add_parser("run")
    p_sched_run.add_argument("kb_path")
    p_sched_install = sched_sub.add_parser("install")
    p_sched_install.add_argument("kb_path")
    p_sched_uninstall = sched_sub.add_parser("uninstall")
    p_sched_uninstall.add_argument("kb_path")

    p_pdf = sub.add_parser("pdf")
    pdf_sub = p_pdf.add_subparsers(dest="pdf_command", required=True)
    p_pdf_fetch = pdf_sub.add_parser("fetch")
    p_pdf_fetch.add_argument("kb_path")
    p_pdf_fetch.add_argument("--doc-id", type=int)
    p_pdf_fetch.add_argument("--all", action="store_true")

    p_qa = sub.add_parser("qa")
    qa_sub = p_qa.add_subparsers(dest="qa_command", required=True)
    p_qa_retrieve = qa_sub.add_parser("retrieve")
    p_qa_retrieve.add_argument("kb_path")
    p_qa_retrieve.add_argument("--question", required=True)
    p_qa_retrieve.add_argument("--doc-ids", nargs="*", type=int)
    p_qa_retrieve.add_argument("--limit", type=int, default=8)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument("kb_path")

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("kb_path")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print_json(init_kb(args.kb_path))
        elif args.command == "import":
            print_json({"paper_ids": import_files(args.kb_path, args.files)})
        elif args.command == "pubmed":
            print_json(
                {
                    "candidate_ids": pubmed_search(
                        args.kb_path,
                        topic=args.topic,
                        query=args.query,
                        confirmed=args.confirmed,
                        max_results=args.max_results,
                    )
                }
            )
        elif args.command == "candidates":
            if args.candidate_command == "list":
                print_json(list_candidates(args.kb_path, status=args.status))
            elif args.candidate_command == "approve":
                print_json({"approved": approve_candidates(args.kb_path, parse_ids(args.ids))})
            elif args.candidate_command == "reject":
                print_json({"rejected": reject_candidates(args.kb_path, parse_ids(args.ids), args.reason)})
        elif args.command == "library":
            if args.library_command == "list":
                print_json(list_papers(args.kb_path))
            elif args.library_command == "delete":
                print_json({"deleted": delete_papers(args.kb_path, parse_ids(args.ids))})
            elif args.library_command == "reindex":
                print_json({"status": "no-op", "message": "Chunks are indexed during import; rerun import to rebuild."})
        elif args.command == "schedule":
            if args.schedule_command == "create":
                search_id = create_saved_search(
                    args.kb_path,
                    name=args.name,
                    query=args.query,
                    topic=args.topic,
                    frequency=args.frequency,
                )
                print_json({"saved_search_id": search_id, "first_run": run_saved_searches(args.kb_path)})
            elif args.schedule_command == "run":
                print_json({"runs": run_saved_searches(args.kb_path)})
            elif args.schedule_command == "install":
                print_json({"status": "manual", "message": windows_task_scheduler_command(args.kb_path)})
            elif args.schedule_command == "uninstall":
                print_json({"status": "manual", "message": "Delete the Windows Task Scheduler task named MedicalKnowledgeBaseUpdate."})
        elif args.command == "pdf":
            print_json(
                {
                    "policy": "PMC and explicit open-access links only",
                    "results": fetch_open_access_pdfs(
                        args.kb_path,
                        doc_id=args.doc_id,
                        all_docs=args.all,
                    ),
                }
            )
        elif args.command == "qa":
            print_json(
                retrieve_evidence(
                    args.kb_path,
                    question=args.question,
                    doc_ids=args.doc_ids,
                    limit=args.limit,
                )
            )
        elif args.command == "audit":
            print_json(audit_kb(args.kb_path))
        elif args.command == "serve":
            serve(args.kb_path, host=args.host, port=args.port)
        else:
            parser.error("unknown command")
    except SafetyGateError as exc:
        print_json({"error": "safety_gate", "message": str(exc)})
        return 2
    return 0


def windows_task_scheduler_command(kb_path: str | Path) -> str:
    script = Path(__file__).resolve()
    return (
        'schtasks /Create /SC DAILY /TN "MedicalKnowledgeBaseUpdate" '
        f'/TR "python \\"{script}\\" schedule run \\"{Path(kb_path)}\\""'
    )


if __name__ == "__main__":
    raise SystemExit(main())
