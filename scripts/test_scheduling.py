import datetime as dt
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from kb_core import scheduling, storage  # noqa: E402


UTC = dt.timezone.utc


def test_create_runs_only_new_saved_search(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    scheduling.create_saved_search(
        kb_path,
        name="old",
        query="old query",
        frequency="weekly",
        run_first=False,
    )
    seen = []
    monkeypatch.setattr(
        scheduling,
        "run_one_search",
        lambda _kb, row, now=None: seen.append(row["name"]) or {"search_id": row["id"]},
    )

    new_id = scheduling.create_saved_search(
        kb_path,
        name="new",
        query="new query",
        frequency="weekly",
        run_first=True,
    )

    assert seen == ["new"]
    assert storage.get_saved_search(kb_path, new_id)["next_run_at"] is not None


def test_due_runner_respects_frequency_and_explicit_selection(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    now = dt.datetime(2026, 7, 10, 8, 0, tzinfo=UTC)
    due_id = scheduling.create_saved_search(
        kb_path, name="due", query="q1", frequency="daily", run_first=False, now=now
    )
    future_id = scheduling.create_saved_search(
        kb_path, name="future", query="q2", frequency="weekly", run_first=False, now=now
    )
    disabled_id = scheduling.create_saved_search(
        kb_path, name="disabled", query="q3", frequency="daily", run_first=False, now=now
    )
    with storage.connect(kb_path) as conn:
        conn.execute(
            "update saved_searches set next_run_at = ? where id = ?",
            ((now - dt.timedelta(minutes=1)).isoformat(), due_id),
        )
        conn.execute(
            "update saved_searches set next_run_at = ? where id = ?",
            ((now + dt.timedelta(days=1)).isoformat(), future_id),
        )
        conn.execute("update saved_searches set enabled = 0 where id = ?", (disabled_id,))
        conn.commit()
    seen = []
    monkeypatch.setattr(
        scheduling,
        "run_one_search",
        lambda _kb, row, now=None: seen.append(row["id"]) or {"search_id": row["id"]},
    )

    scheduling.run_saved_searches(kb_path, now=now)
    scheduling.run_saved_searches(kb_path, search_ids=[future_id], now=now)

    assert seen == [due_id, future_id]


def test_next_run_at_uses_supported_frequency_and_rejects_unknown():
    start = dt.datetime(2026, 7, 10, tzinfo=UTC)

    assert scheduling.next_run_at(start, "daily") == start + dt.timedelta(days=1)
    assert scheduling.next_run_at(start, "weekly") == start + dt.timedelta(days=7)
    assert scheduling.next_run_at(start, "monthly") == start + dt.timedelta(days=30)
    try:
        scheduling.next_run_at(start, "hourly")
    except ValueError as exc:
        assert "frequency" in str(exc)
    else:
        raise AssertionError("unknown frequency should fail")


def test_windows_scheduler_names_are_unique_and_commands_are_scoped(tmp_path):
    first = scheduling.windows_task_scheduler_command(tmp_path / "a")
    second = scheduling.windows_task_scheduler_command(tmp_path / "b")

    assert first != second
    assert scheduling.scheduler_task_name(tmp_path / "a") in first
    assert str((tmp_path / "a").resolve()) in first
    assert "/Create" in first
    spaced = scheduling.windows_task_scheduler_command(tmp_path / "path with spaces")
    assert "/TR '" in spaced
    assert spaced.endswith("'")
    assert scheduling.scheduler_uninstall_command(tmp_path / "a") != scheduling.scheduler_uninstall_command(
        tmp_path / "b"
    )


def test_enable_disable_and_list_saved_searches(tmp_path):
    kb_path = tmp_path / "kb"
    search_id = scheduling.create_saved_search(
        kb_path, name="monitor", query="q", frequency="monthly", run_first=False
    )

    assert scheduling.set_saved_search_enabled(kb_path, search_id, False) is True
    assert scheduling.list_saved_searches(kb_path)[0]["enabled"] is False
    assert scheduling.set_saved_search_enabled(kb_path, search_id, True) is True
    assert scheduling.list_saved_searches(kb_path)[0]["enabled"] is True
    assert scheduling.set_saved_search_enabled(kb_path, 9999, False) is False


def test_schedule_help_lists_management_commands():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "kb.py"), "schedule", "--help"],
        capture_output=True,
        check=True,
        text=True,
    )

    for command in ("create", "list", "enable", "disable", "run", "install", "uninstall"):
        assert command in completed.stdout


def test_failed_search_is_audited_and_remains_due(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    now = dt.datetime(2026, 7, 10, 8, 0, tzinfo=UTC)
    search_id = scheduling.create_saved_search(
        kb_path,
        name="retry",
        query="q",
        frequency="weekly",
        run_first=False,
        now=now,
    )
    monkeypatch.setattr(
        scheduling.pubmed,
        "search_to_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary failure")),
    )

    result = scheduling.run_saved_searches(kb_path, search_ids=[search_id], now=now)[0]

    assert result["status"] == "failed"
    search = storage.get_saved_search(kb_path, search_id)
    assert dt.datetime.fromisoformat(search["next_run_at"]) <= now
    task = storage.get_task(kb_path, result["task_id"])
    assert task["status"] == "failed"
    assert task["error"]["message"] == "temporary failure"
