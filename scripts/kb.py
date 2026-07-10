#!/usr/bin/env python3
"""Local biomedical knowledge base toolkit for the medical-knowledge-base skill."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from kb_core.storage import (
    ClosingConnection,
    SCHEMA_VERSION,
    connect,
    create_schema,
    get_saved_search as storage_get_saved_search,
    init_kb,
    kb_db_path,
    refresh_passport,
    utc_now,
)
from kb_core.metadata import (
    classify_study_type,
    load_jcr_catalog,
    match_jcr,
    normalize_issn,
    normalize_text,
    parse_jcr_row,
)
from kb_core.pubmed import fetch_records as _fetch_pubmed_records
from kb_core.pubmed import search_to_candidates
from kb_core.imports import chunk_text, extract_text as extract_text_best_effort
from kb_core.imports import import_paths
from kb_core import approval as approval_service
from kb_core import pdfs as pdf_service
from kb_core import scheduling as scheduling_service
from kb_core import retrieval as retrieval_service
from kb_core import library as library_service
from kb_core import safety as safety_service
from kb_core.safety import SafetyGateError
from kb_core import web as web_service

SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = SKILL_ROOT / "assets"
DEFAULT_JCR_PATH = ASSETS_DIR / "data" / "JCR2025-UTF8.csv"
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
    return approval_service.approve_candidates(kb_path, candidate_ids)["approved"]


def approve_candidates_result(kb_path: str | Path, candidate_ids: list[int]) -> dict[str, Any]:
    return approval_service.approve_candidates(kb_path, candidate_ids)


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
    chunk_ids = retrieval_service.add_chunks(
        kb_path,
        paper_id,
        chunk_text,
        source_locator=source_locator,
    )
    if not chunk_ids:
        raise ValueError("chunk text must not be empty")
    return chunk_ids[0]


def tokenize(value: str) -> list[str]:
    return retrieval_service.search_terms(value)


def retrieve_evidence(
    kb_path: str | Path,
    *,
    question: str,
    doc_ids: list[int] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    return retrieval_service.retrieve_evidence(
        kb_path,
        question=question,
        doc_ids=doc_ids,
        limit=limit,
    )


def import_files(kb_path: str | Path, files: list[str | Path]) -> list[int]:
    return import_result(kb_path, files)["paper_ids"]


def import_result(kb_path: str | Path, files: list[str | Path]) -> dict[str, Any]:
    return import_paths(kb_path, files)


def pubmed_search(
    kb_path: str | Path,
    *,
    topic: str | None = None,
    query: str | None = None,
    confirmed: bool = False,
    max_results: int = 20,
    filters: dict[str, Any] | None = None,
    search_id: int | None = None,
) -> list[int]:
    return pubmed_search_result(
        kb_path,
        topic=topic,
        query=query,
        confirmed=confirmed,
        max_results=max_results,
        filters=filters,
        search_id=search_id,
    )["candidate_ids"]


def pubmed_search_result(
    kb_path: str | Path,
    *,
    topic: str | None = None,
    query: str | None = None,
    confirmed: bool = False,
    max_results: int = 20,
    filters: dict[str, Any] | None = None,
    search_id: int | None = None,
) -> dict[str, Any]:
    if topic and not confirmed:
        raise SafetyGateError(
            "Topic-based PubMed searches must be confirmed before retrieval; generate the query first and ask the user to confirm."
        )
    search_query = query or topic
    if not search_query:
        raise ValueError("Provide topic or query")
    return search_to_candidates(
        kb_path,
        query=search_query,
        max_results=max_results,
        filters=filters,
        search_id=search_id,
    )


def fetch_pubmed_records(query: str, *, max_results: int = 20) -> list[dict[str, Any]]:
    return _fetch_pubmed_records(query, max_results=max_results)


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
    return scheduling_service.create_saved_search(
        kb_path,
        name=name,
        query=query,
        topic=topic,
        frequency=frequency,
        filters=filters,
        run_first=False,
    )


def run_saved_searches(kb_path: str | Path) -> list[dict[str, Any]]:
    return scheduling_service.run_saved_searches(kb_path)


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
    return pdf_service.fetch_open_access_pdfs(kb_path, doc_id=doc_id, all_docs=all_docs)


def lookup_pmcid_for_pmid(pmid: str) -> str | None:
    return pdf_service.lookup_pmcid_for_pmid(pmid)


def download_pmc_pdf(pmcid: str, dest: Path) -> None:
    dest.write_bytes(pdf_service.download_open_access_pdf(
        f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
    ))


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
        passport = refresh_passport(root)
    else:
        passport = None
    return {
        "kb_path": str(root),
        "issues": issues,
        "counts": counts,
        "passport_refreshed_at": passport.get("refreshed_at") if passport else None,
        "passed": not issues,
    }


def generate_html_dashboard(kb_path: str | Path) -> str:
    return web_service.render_page().decode("utf-8")


def serve(kb_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    web_service.serve(kb_path, host=host, port=port)


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parse_ids(values: list[str]) -> list[int]:
    return [int(value) for value in values]


def finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("value must be finite")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb", description="Local biomedical knowledge base toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("kb_path")

    p_import = sub.add_parser(
        "import",
        help="Import local documents, bibliography tables, or PMID lists",
        description=(
            "Import PDF, DOCX, Markdown, TXT, CSV, XLSX, RIS, BibTeX, "
            "or PMID-list files into an approved local library."
        ),
    )
    p_import.add_argument("kb_path")
    p_import.add_argument("files", nargs="+", help="One or more local source files")

    p_pubmed = sub.add_parser("pubmed")
    pubmed_sub = p_pubmed.add_subparsers(dest="pubmed_command", required=True)
    p_search = pubmed_sub.add_parser("search")
    p_search.add_argument("kb_path")
    group = p_search.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic")
    group.add_argument("--query")
    p_search.add_argument("--confirmed", action="store_true")
    p_search.add_argument("--max-results", type=int, default=20)
    p_search.add_argument("--min-if", type=finite_float)
    p_search.add_argument(
        "--jcr-quartile",
        dest="jcr_quartiles",
        action="append",
        choices=("Q1", "Q2", "Q3", "Q4"),
    )
    p_search.add_argument("--study-type", dest="study_types", action="append")
    p_search.add_argument("--year-from", type=int)
    p_search.add_argument("--year-to", type=int)

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
    p_cand_reject.add_argument("--confirm", help="Token returned by the preview call")

    p_library = sub.add_parser("library")
    lib_sub = p_library.add_subparsers(dest="library_command", required=True)
    p_lib_list = lib_sub.add_parser("list")
    p_lib_list.add_argument("kb_path")
    p_lib_delete = lib_sub.add_parser("delete")
    p_lib_delete.add_argument("kb_path")
    p_lib_delete.add_argument("ids", nargs="+")
    p_lib_delete.add_argument("--confirm", help="Token returned by the preview call")
    p_lib_reindex = lib_sub.add_parser("reindex")
    p_lib_reindex.add_argument("kb_path")
    reindex_scope = p_lib_reindex.add_mutually_exclusive_group(required=True)
    reindex_scope.add_argument("--ids", nargs="+", type=int)
    reindex_scope.add_argument("--all", action="store_true")
    p_lib_reindex.add_argument("--preview", action="store_true")
    p_lib_reindex.add_argument("--confirm", help="Token returned by the preview call")

    p_schedule = sub.add_parser("schedule")
    sched_sub = p_schedule.add_subparsers(dest="schedule_command", required=True)
    p_sched_create = sched_sub.add_parser("create")
    p_sched_create.add_argument("kb_path")
    p_sched_create.add_argument("--name", required=True)
    p_sched_create.add_argument("--query", required=True)
    p_sched_create.add_argument("--topic")
    p_sched_create.add_argument(
        "--frequency", choices=("daily", "weekly", "monthly"), default="weekly"
    )
    p_sched_create.add_argument("--max-results", type=int, default=20)
    p_sched_create.add_argument("--min-if", type=finite_float)
    p_sched_create.add_argument(
        "--jcr-quartile",
        dest="jcr_quartiles",
        action="append",
        choices=("Q1", "Q2", "Q3", "Q4"),
    )
    p_sched_create.add_argument("--study-type", dest="study_types", action="append")
    p_sched_create.add_argument("--year-from", type=int)
    p_sched_create.add_argument("--year-to", type=int)
    p_sched_create.add_argument("--no-first-run", action="store_true")
    p_sched_list = sched_sub.add_parser("list")
    p_sched_list.add_argument("kb_path")
    p_sched_enable = sched_sub.add_parser("enable")
    p_sched_enable.add_argument("kb_path")
    p_sched_enable.add_argument("search_id", type=int)
    p_sched_disable = sched_sub.add_parser("disable")
    p_sched_disable.add_argument("kb_path")
    p_sched_disable.add_argument("search_id", type=int)
    p_sched_run = sched_sub.add_parser("run")
    p_sched_run.add_argument("kb_path")
    p_sched_run.add_argument("--search-id", dest="search_ids", action="append", type=int)
    p_sched_run.add_argument("--force", action="store_true")
    p_sched_install = sched_sub.add_parser("install")
    p_sched_install.add_argument("kb_path")
    p_sched_install.add_argument("--confirm", help="Token returned by the preview call")
    p_sched_uninstall = sched_sub.add_parser("uninstall")
    p_sched_uninstall.add_argument("kb_path")
    p_sched_uninstall.add_argument("--confirm", help="Token returned by the preview call")

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
            print_json(import_result(args.kb_path, args.files))
        elif args.command == "pubmed":
            print_json(
                pubmed_search_result(
                    args.kb_path,
                    topic=args.topic,
                    query=args.query,
                    confirmed=args.confirmed,
                    max_results=args.max_results,
                    filters={
                        "min_if": args.min_if,
                        "jcr_quartiles": args.jcr_quartiles or [],
                        "study_types": args.study_types or [],
                        "year_from": args.year_from,
                        "year_to": args.year_to,
                    },
                )
            )
        elif args.command == "candidates":
            if args.candidate_command == "list":
                print_json(list_candidates(args.kb_path, status=args.status))
            elif args.candidate_command == "approve":
                print_json(approve_candidates_result(args.kb_path, parse_ids(args.ids)))
            elif args.candidate_command == "reject":
                candidate_ids = parse_ids(args.ids)
                if args.confirm:
                    print_json(
                        library_service.reject_candidates(
                            args.kb_path,
                            candidate_ids,
                            reason=args.reason,
                            confirm=args.confirm,
                        )
                    )
                else:
                    print_json(
                        safety_service.preview_action(
                            args.kb_path,
                            "candidate.reject",
                            candidate_ids,
                            details={"reason": args.reason},
                        )
                    )
        elif args.command == "library":
            if args.library_command == "list":
                print_json(list_papers(args.kb_path))
            elif args.library_command == "delete":
                paper_ids = parse_ids(args.ids)
                if args.confirm:
                    print_json(
                        library_service.delete_papers(
                            args.kb_path,
                            paper_ids,
                            confirm=args.confirm,
                        )
                    )
                else:
                    print_json(
                        safety_service.preview_action(
                            args.kb_path,
                            "library.delete",
                            paper_ids,
                        )
                    )
            elif args.library_command == "reindex":
                paper_ids = (
                    [paper["id"] for paper in list_papers(args.kb_path)]
                    if args.all
                    else args.ids
                )
                if args.preview or not args.confirm:
                    preview = retrieval_service.preview_reindex(args.kb_path, paper_ids)
                    preview.update(
                        safety_service.preview_action(
                            args.kb_path,
                            "library.reindex",
                            paper_ids,
                        )
                    )
                    print_json(preview)
                else:
                    print_json(
                        library_service.reindex_library(
                            args.kb_path,
                            paper_ids,
                            confirm=args.confirm,
                        )
                    )
        elif args.command == "schedule":
            if args.schedule_command == "create":
                search_id = scheduling_service.create_saved_search(
                    args.kb_path,
                    name=args.name,
                    query=args.query,
                    topic=args.topic,
                    frequency=args.frequency,
                    filters={
                        "max_results": args.max_results,
                        "min_if": args.min_if,
                        "jcr_quartiles": args.jcr_quartiles or [],
                        "study_types": args.study_types or [],
                        "year_from": args.year_from,
                        "year_to": args.year_to,
                    },
                    run_first=not args.no_first_run,
                )
                print_json(
                    {
                        "saved_search_id": search_id,
                        "saved_search": storage_get_saved_search(args.kb_path, search_id),
                        "first_run": not args.no_first_run,
                    }
                )
            elif args.schedule_command == "list":
                print_json(scheduling_service.list_saved_searches(args.kb_path))
            elif args.schedule_command == "enable":
                print_json(
                    {
                        "search_id": args.search_id,
                        "enabled": scheduling_service.set_saved_search_enabled(
                            args.kb_path, args.search_id, True
                        ),
                    }
                )
            elif args.schedule_command == "disable":
                changed = scheduling_service.set_saved_search_enabled(
                    args.kb_path, args.search_id, False
                )
                print_json({"search_id": args.search_id, "disabled": changed})
            elif args.schedule_command == "run":
                print_json(
                    {
                        "runs": scheduling_service.run_saved_searches(
                            args.kb_path,
                            search_ids=args.search_ids,
                            force=args.force,
                        )
                    }
                )
            elif args.schedule_command == "install":
                if args.confirm:
                    print_json(
                        library_service.change_schedule(
                            args.kb_path,
                            install=True,
                            confirm=args.confirm,
                        )
                    )
                else:
                    print_json(library_service.preview_schedule_change(args.kb_path, install=True))
            elif args.schedule_command == "uninstall":
                if args.confirm:
                    print_json(
                        library_service.change_schedule(
                            args.kb_path,
                            install=False,
                            confirm=args.confirm,
                        )
                    )
                else:
                    print_json(library_service.preview_schedule_change(args.kb_path, install=False))
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
    return scheduling_service.windows_task_scheduler_command(kb_path)


if __name__ == "__main__":
    raise SystemExit(main())
