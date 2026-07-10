"""Versioned SQLite storage and local knowledge-base metadata."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JCR_PATH = SKILL_ROOT / "assets" / "data" / "JCR2025-UTF8.csv"
SCHEMA_VERSION = "3"
LATEST_MIGRATION_VERSION = 3

MIGRATIONS = {
    1: ("alter table papers add column source_path text",),
    2: (
        "alter table papers add column abstract text",
        "alter table papers add column full_text_path text",
        "alter table candidate_papers add column dedupe_key text",
        "alter table tasks add column result_json text not null default '{}'",
        "alter table tasks add column error_json text not null default '{}'",
        "alter table tasks add column started_at text",
        "alter table tasks add column finished_at text",
    ),
    3: ("alter table saved_searches add column next_run_at text",),
}


class ClosingConnection(sqlite3.Connection):
    """A connection that closes when its context manager exits."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def kb_db_path(kb_path: str | Path) -> Path:
    return Path(kb_path) / "kb.sqlite"


def connect(kb_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(kb_db_path(kb_path), factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the original schema so incremental migrations work for new KBs too."""
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


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"pragma table_info({table_name})")}


def _apply_migration(conn: sqlite3.Connection, version: int) -> None:
    for statement in MIGRATIONS[version]:
        table_name, column_name = _migration_target(statement)
        if column_name in _table_columns(conn, table_name):
            continue
        conn.execute(statement)


def _migration_target(statement: str) -> tuple[str, str]:
    parts = statement.split()
    return parts[2], parts[5]


def migrate(conn: sqlite3.Connection) -> None:
    """Apply each missing schema migration once and record its version."""
    create_schema(conn)
    conn.execute(
        """
        create table if not exists schema_migrations (
            version integer primary key,
            applied_at text not null
        )
        """
    )
    applied_versions = {
        row["version"] for row in conn.execute("select version from schema_migrations")
    }
    for version in sorted(MIGRATIONS):
        if version in applied_versions:
            continue
        _apply_migration(conn, version)
        conn.execute(
            "insert into schema_migrations (version, applied_at) values (?, ?)",
            (version, utc_now()),
        )
    conn.commit()


def _default_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "pdf_fetch": {
            "enabled": True,
            "allowed_sources": ["pubmed-central", "open-access-link"],
        },
        "pubmed": {"email": None, "api_key": None},
        "defaults": {
            "candidate_auto_ingest": False,
            "require_search_confirmation": True,
        },
    }


def _default_passport(kb_path: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "kb_path": str(kb_path),
        "jcr_table": {"bundled_path": str(DEFAULT_JCR_PATH), "version": "JCR2025"},
        "prompt_assets": {"search_query_generation": "assets/prompts/search-query-generation.md"},
        "policy": {
            "pubmed_topic_search_requires_confirmation": True,
            "candidates_require_user_approval": True,
            "scheduled_updates_write_candidates_only": True,
            "pdf_fetch_open_access_only": True,
            "evidence_qa_uses_retrieved_chunks_only": True,
            "destructive_actions_require_state_bound_confirmation": True,
        },
    }


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"null", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            # Legacy passports used quoted Windows paths without escaping
            # backslashes. Preserve those values and normalize on the next write.
            return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if (value.startswith("[") and value.endswith("]")) or (
        value.startswith("{") and value.endswith("}")
    ):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    try:
        return int(value)
    except ValueError:
        return value


def _load_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None
    current_list: list[Any] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        line = raw_line.strip()
        if indent == 0:
            key, _, value = line.partition(":")
            if value.strip():
                data[key] = _parse_scalar(value)
                current_section = None
                current_list = None
            else:
                current_section = {}
                data[key] = current_section
                current_list = None
        elif line.startswith("- ") and current_list is not None:
            current_list.append(_parse_scalar(line[2:]))
        elif current_section is not None:
            key, _, value = line.partition(":")
            if value.strip():
                current_section[key] = _parse_scalar(value)
                current_list = None
            else:
                current_list = []
                current_section[key] = current_list
    return data


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _dump_yaml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for nested_key, nested_value in value.items():
                if isinstance(nested_value, list):
                    lines.append(f"  {nested_key}:")
                    lines.extend(f"    - {_yaml_scalar(item)}" for item in nested_value)
                else:
                    lines.append(f"  {nested_key}: {_yaml_scalar(nested_value)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def load_config(kb_path: str | Path) -> dict[str, Any]:
    config_path = Path(kb_path) / "config.yaml"
    if not config_path.is_file():
        return {}
    return _load_yaml(config_path)


def refresh_passport(kb_path: str | Path) -> dict[str, Any]:
    root = Path(kb_path)
    passport_path = root / "kb-passport.yaml"
    passport = _load_yaml(passport_path) if passport_path.is_file() else _default_passport(root)
    defaults = _default_passport(root)
    passport["schema_version"] = SCHEMA_VERSION
    passport["kb_path"] = str(root)
    passport.setdefault("created_at", defaults["created_at"])
    passport.setdefault("jcr_table", defaults["jcr_table"])
    passport.setdefault("prompt_assets", defaults["prompt_assets"])
    passport["policy"] = {**defaults["policy"], **(passport.get("policy") or {})}

    if kb_db_path(root).is_file():
        with connect(root) as conn:
            candidate_counts = {
                row["status"]: int(row["count"])
                for row in conn.execute(
                    "select status, count(*) as count from candidate_papers group by status"
                )
            }
            task_counts = {
                (row["task_type"], row["status"]): int(row["count"])
                for row in conn.execute(
                    "select task_type, status, count(*) as count from tasks group by task_type, status"
                )
            }
            paper_count = int(conn.execute("select count(*) from papers").fetchone()[0])
            chunk_count = int(conn.execute("select count(*) from chunks").fetchone()[0])
            indexed_paper_count = int(
                conn.execute("select count(distinct paper_id) from chunks").fetchone()[0]
            )
            available_paper_count = int(
                conn.execute("select count(*) from papers where has_pdf = 1").fetchone()[0]
            )
            import_sources = [
                {
                    "paper_id": int(row["id"]),
                    "source": row["source"],
                    "source_path": row["source_path"],
                    "created_at": row["created_at"],
                }
                for row in conn.execute(
                    """
                    select id, source, source_path, created_at
                    from papers
                    where source_path is not null or source like 'local-%'
                    order by id
                    """
                )
            ]
            scheduled_searches = [
                {
                    "id": int(row["id"]),
                    "name": row["name"],
                    "topic": row["topic"],
                    "query": row["query"],
                    "filters": _decode_json(row["filters_json"]) or {},
                    "frequency": row["frequency"],
                    "enabled": bool(row["enabled"]),
                    "last_run_at": row["last_run_at"],
                    "next_run_at": row["next_run_at"],
                }
                for row in conn.execute("select * from saved_searches order by id")
            ]
            latest_decision = conn.execute(
                "select max(decided_at) from candidate_papers where decided_at is not null"
            ).fetchone()[0]
            migration_versions = [
                int(row["version"])
                for row in conn.execute("select version from schema_migrations order by version")
            ]

        config = load_config(root)
        pdf_config = config.get("pdf_fetch") or {}
        passport["refreshed_at"] = utc_now()
        passport["schema_migrations"] = migration_versions
        passport["data_summary"] = {
            "paper_count": paper_count,
            "pending_candidate_count": candidate_counts.get("pending", 0),
            "approved_candidate_count": candidate_counts.get("approved", 0),
            "rejected_candidate_count": candidate_counts.get("rejected", 0),
            "saved_search_count": len(scheduled_searches),
            "task_count": sum(task_counts.values()),
        }
        passport["candidate_decisions"] = {
            "approved_count": candidate_counts.get("approved", 0),
            "rejected_count": candidate_counts.get("rejected", 0),
            "last_decided_at": latest_decision,
        }
        passport["import_sources"] = import_sources
        passport["scheduled_searches"] = scheduled_searches
        passport["pdf_status"] = {
            "enabled": bool(pdf_config.get("enabled", True)),
            "allowed_sources": pdf_config.get("allowed_sources") or [],
            "available_paper_count": available_paper_count,
            "pending_task_count": task_counts.get(("pdf_fetch", "pending"), 0),
            "completed_task_count": task_counts.get(("pdf_fetch", "completed"), 0),
            "failed_task_count": task_counts.get(("pdf_fetch", "failed"), 0),
        }
        passport["index_status"] = {
            "chunk_count": chunk_count,
            "indexed_paper_count": indexed_paper_count,
            "unindexed_paper_count": max(0, paper_count - indexed_paper_count),
        }
    passport_path.write_text(_dump_yaml(passport), encoding="utf-8")
    return passport


def init_kb(kb_path: str | Path) -> dict[str, Any]:
    root = Path(kb_path)
    root.mkdir(parents=True, exist_ok=True)
    for rel_path in ("files/uploads", "files/pdfs", "files/extracted-text", "logs"):
        (root / rel_path).mkdir(parents=True, exist_ok=True)

    gitignore_path = root / ".gitignore"
    existing_gitignore = (
        gitignore_path.read_text(encoding="utf-8", errors="replace")
        if gitignore_path.is_file()
        else ""
    )
    if ".confirmation-secret" not in existing_gitignore.splitlines():
        separator = "" if not existing_gitignore or existing_gitignore.endswith("\n") else "\n"
        with gitignore_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{separator}.confirmation-secret\n")

    config_path = root / "config.yaml"
    if not config_path.exists():
        config_path.write_text(_dump_yaml(_default_config()), encoding="utf-8")

    with connect(root) as conn:
        migrate(conn)
    passport = refresh_passport(root)
    return {"kb_path": str(root), "schema_version": passport["schema_version"]}


def _decode_json(value: str | None) -> Any:
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        return None


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("authors_json", "jcr_quartiles_json", "raw_json", "filters_json", "payload_json"):
        if key in data:
            data[key.removesuffix("_json")] = _decode_json(data.pop(key))
    for key in ("result_json", "error_json"):
        if key in data:
            data[key.removesuffix("_json")] = _decode_json(data.pop(key))
    for key in ("has_pdf", "enabled"):
        if key in data and data[key] is not None:
            data[key] = bool(data[key])
    return data


def table_columns(kb_path: str | Path, table_name: str) -> set[str]:
    with connect(kb_path) as conn:
        return _table_columns(conn, table_name)


def list_papers(kb_path: str | Path) -> list[dict[str, Any]]:
    with connect(kb_path) as conn:
        return [row_to_dict(row) for row in conn.execute("select * from papers order by id")]


def create_task(kb_path: str | Path, task_type: str, payload: dict[str, Any]) -> int:
    now = utc_now()
    with connect(kb_path) as conn:
        cur = conn.execute(
            """
            insert into tasks (task_type, payload_json, status, created_at, updated_at)
            values (?, ?, 'pending', ?, ?)
            """,
            (task_type, json.dumps(payload, ensure_ascii=False), now, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def finish_task(
    kb_path: str | Path,
    task_id: int,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    now = utc_now()
    with connect(kb_path) as conn:
        conn.execute(
            """
            update tasks
            set status = ?, result_json = ?, error_json = ?,
                started_at = coalesce(started_at, ?), finished_at = ?, updated_at = ?
            where id = ?
            """,
            (
                status,
                json.dumps(result or {}, ensure_ascii=False),
                json.dumps(error or {}, ensure_ascii=False),
                now,
                now,
                now,
                task_id,
            ),
        )
        conn.commit()


def get_task(kb_path: str | Path, task_id: int) -> dict[str, Any] | None:
    with connect(kb_path) as conn:
        row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
    return row_to_dict(row) if row is not None else None


def get_saved_search(kb_path: str | Path, search_id: int) -> dict[str, Any] | None:
    with connect(kb_path) as conn:
        row = conn.execute("select * from saved_searches where id = ?", (search_id,)).fetchone()
    return row_to_dict(row) if row is not None else None


def get_paper(kb_path: str | Path, paper_id: int) -> dict[str, Any] | None:
    with connect(kb_path) as conn:
        row = conn.execute("select * from papers where id = ?", (paper_id,)).fetchone()
    return row_to_dict(row) if row is not None else None


def touch_paper(kb_path: str | Path, paper_id: int) -> bool:
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    with connect(kb_path) as conn:
        cur = conn.execute(
            "update papers set updated_at = ? where id = ?",
            (timestamp, paper_id),
        )
        conn.commit()
    return bool(cur.rowcount)
