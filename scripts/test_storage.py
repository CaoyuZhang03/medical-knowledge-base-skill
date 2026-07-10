import sqlite3
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from kb_core import storage  # noqa: E402


def legacy_database(kb_path, *, title):
    kb_path.mkdir()
    db_path = kb_path / "kb.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            create table papers (
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
            create table candidate_papers (
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
                status text not null default 'pending',
                rejection_reason text,
                search_id integer,
                created_at text not null,
                decided_at text
            );
            create table saved_searches (
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
            create table tasks (
                id integer primary key autoincrement,
                task_type text not null,
                payload_json text not null default '{}',
                status text not null default 'pending',
                created_at text not null,
                updated_at text not null
            );
            create table chunks (
                id integer primary key autoincrement,
                paper_id integer not null references papers(id) on delete cascade,
                text text not null,
                source_locator text,
                created_at text not null
            );
            """
        )
        cur = conn.execute(
            """
            insert into papers (title, created_at, updated_at)
            values (?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (title,),
        )
        return int(cur.lastrowid)


def test_migration_preserves_existing_paper_and_adds_source_columns(tmp_path):
    kb_path = tmp_path / "kb"
    legacy_id = legacy_database(kb_path, title="Preserved")

    storage.init_kb(kb_path)

    assert storage.list_papers(kb_path)[0]["id"] == legacy_id
    assert {"source_path", "abstract", "full_text_path"} <= storage.table_columns(kb_path, "papers")
    assert {"dedupe_key", "search_id"} <= storage.table_columns(kb_path, "candidate_papers")
    assert {"result_json", "error_json", "started_at", "finished_at"} <= storage.table_columns(
        kb_path, "tasks"
    )
    with storage.connect(kb_path) as conn:
        versions = [
            row["version"]
            for row in conn.execute("select version from schema_migrations order by version")
        ]
        assert versions == [1, 2, 3]
    assert "next_run_at" in storage.table_columns(kb_path, "saved_searches")


def test_task_lifecycle_records_structured_result(tmp_path):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)

    task_id = storage.create_task(kb_path, "pdf_fetch", {"paper_id": 1})
    storage.finish_task(kb_path, task_id, status="completed", result={"status": "not_found"})

    task = storage.get_task(kb_path, task_id)
    assert task["payload"] == {"paper_id": 1}
    assert task["status"] == "completed"
    assert task["result"] == {"status": "not_found"}
    assert task["error"] == {}
    assert task["started_at"] is not None
    assert task["finished_at"] is not None


def test_load_config_and_refresh_passport_preserve_config_and_update_schema_version(tmp_path):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    config_path = kb_path / "config.yaml"
    config_path.write_text(
        "schema_version: \"1.0.0\"\npubmed:\n  email: user@example.com\n",
        encoding="utf-8",
    )

    config = storage.load_config(kb_path)
    passport = storage.refresh_passport(kb_path)

    assert config["pubmed"]["email"] == "user@example.com"
    assert config["schema_version"] == "1.0.0"
    assert passport["schema_version"] == storage.SCHEMA_VERSION
    assert passport["kb_path"] == str(kb_path)


def test_external_schema_versions_remain_strings(tmp_path):
    kb_path = tmp_path / "kb"

    result = storage.init_kb(kb_path)
    config = storage.load_config(kb_path)
    passport = storage.refresh_passport(kb_path)

    assert isinstance(result["schema_version"], str)
    assert isinstance(config["schema_version"], str)
    assert isinstance(passport["schema_version"], str)


def test_passport_refresh_exports_operational_audit_snapshot(tmp_path):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    now = storage.utc_now()
    with storage.connect(kb_path) as conn:
        paper = conn.execute(
            """
            insert into papers
                (title, authors_json, jcr_quartiles_json, source, source_path,
                 abstract, has_pdf, created_at, updated_at)
            values ('Snapshot paper', '[]', '[]', 'local-upload', 'source.pdf',
                    'Indexed evidence', 1, ?, ?)
            """,
            (now, now),
        )
        paper_id = int(paper.lastrowid)
        conn.execute(
            """
            insert into candidate_papers
                (title, authors_json, jcr_quartiles_json, raw_json, status, created_at)
            values ('Pending candidate', '[]', '[]', '{}', 'pending', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            insert into saved_searches
                (name, query, filters_json, frequency, enabled, next_run_at, created_at)
            values ('Weekly', 'asthma[Title]', '{}', 'weekly', 1, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            "insert into chunks (paper_id, text, source_locator, created_at) values (?, 'Indexed evidence', 'abstract', ?)",
            (paper_id, now),
        )
        conn.execute(
            "insert into tasks (task_type, payload_json, status, created_at, updated_at) values ('pdf_fetch', '{}', 'failed', ?, ?)",
            (now, now),
        )
        conn.commit()

    passport = storage.refresh_passport(kb_path)
    reloaded = storage._load_yaml(kb_path / "kb-passport.yaml")

    assert passport["data_summary"]["paper_count"] == 1
    assert passport["data_summary"]["pending_candidate_count"] == 1
    assert passport["import_sources"][0]["source"] == "local-upload"
    assert passport["scheduled_searches"][0]["query"] == "asthma[Title]"
    assert passport["pdf_status"]["available_paper_count"] == 1
    assert passport["pdf_status"]["failed_task_count"] == 1
    assert passport["index_status"]["chunk_count"] == 1
    assert passport["index_status"]["indexed_paper_count"] == 1
    assert reloaded["scheduled_searches"] == passport["scheduled_searches"]


def test_passport_refresh_round_trips_windows_paths_without_extra_escaping(tmp_path):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)

    first = storage.refresh_passport(kb_path)
    second = storage.refresh_passport(kb_path)

    assert first["jcr_table"]["bundled_path"] == str(storage.DEFAULT_JCR_PATH)
    assert second["jcr_table"]["bundled_path"] == first["jcr_table"]["bundled_path"]
