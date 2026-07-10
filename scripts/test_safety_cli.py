import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import kb  # noqa: E402
from kb_core import library, safety, scheduling, storage  # noqa: E402


UTC = dt.timezone.utc


def paper(tmp_path):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    paper_id = kb.insert_paper(
        kb_path,
        {"title": "Protected paper", "pmid": "123", "source": "test"},
    )
    return kb_path, paper_id


def test_init_excludes_confirmation_secret_without_overwriting_gitignore(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / ".gitignore").write_text("custom-rule\n", encoding="utf-8")

    storage.init_kb(kb_path)
    storage.init_kb(kb_path)

    rules = (kb_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "custom-rule" in rules
    assert rules.count(".confirmation-secret") == 1


def test_delete_requires_matching_preview_token(tmp_path):
    kb_path, paper_id = paper(tmp_path)
    preview = safety.preview_action(kb_path, "library.delete", [paper_id])

    with pytest.raises(safety.SafetyGateError):
        library.delete_papers(kb_path, [paper_id], confirm="wrong")
    assert storage.get_paper(kb_path, paper_id)

    result = library.delete_papers(
        kb_path,
        [paper_id],
        confirm=preview["confirmation_token"],
    )

    assert result["deleted"] == [paper_id]
    assert storage.get_paper(kb_path, paper_id) is None


def test_token_becomes_stale_when_target_state_changes(tmp_path):
    kb_path, paper_id = paper(tmp_path)
    preview = safety.preview_action(kb_path, "library.delete", [paper_id])
    storage.touch_paper(kb_path, paper_id)

    with pytest.raises(safety.SafetyGateError, match="stale"):
        library.delete_papers(
            kb_path,
            [paper_id],
            confirm=preview["confirmation_token"],
        )


def test_confirmation_token_is_bound_to_action_ids_and_details(tmp_path):
    kb_path, paper_id = paper(tmp_path)
    candidate_id = kb.add_candidate(kb_path, {"title": "Candidate"})
    preview = safety.preview_action(
        kb_path,
        "candidate.reject",
        [candidate_id],
        details={"reason": "out of scope"},
    )

    with pytest.raises(safety.SafetyGateError, match="does not match"):
        library.reject_candidates(
            kb_path,
            [candidate_id],
            reason="different reason",
            confirm=preview["confirmation_token"],
        )
    with pytest.raises(safety.SafetyGateError, match="does not match"):
        library.delete_papers(
            kb_path,
            [paper_id],
            confirm=preview["confirmation_token"],
        )


def test_expired_confirmation_token_is_rejected(tmp_path):
    kb_path, paper_id = paper(tmp_path)
    issued = dt.datetime(2026, 7, 10, 8, 0, tzinfo=UTC)
    preview = safety.preview_action(
        kb_path,
        "library.delete",
        [paper_id],
        now=issued,
        ttl_seconds=60,
    )

    with pytest.raises(safety.SafetyGateError, match="expired"):
        safety.verify_confirmation(
            kb_path,
            "library.delete",
            [paper_id],
            preview["confirmation_token"],
            now=issued + dt.timedelta(seconds=60),
        )


@pytest.mark.parametrize("token", ["%%%", "a", "not-a-token"])
def test_malformed_confirmation_tokens_are_rejected_cleanly(tmp_path, token):
    kb_path, paper_id = paper(tmp_path)

    with pytest.raises(safety.SafetyGateError, match="invalid"):
        safety.verify_confirmation(kb_path, "library.delete", [paper_id], token)


def test_candidate_reject_cli_previews_then_executes_with_token(tmp_path):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    candidate_id = kb.add_candidate(kb_path, {"title": "Review me"})
    command = [
        sys.executable,
        str(SCRIPT_DIR / "kb.py"),
        "candidates",
        "reject",
        str(kb_path),
        str(candidate_id),
        "--reason",
        "not relevant",
    ]

    preview = json.loads(subprocess.run(command, capture_output=True, check=True, text=True).stdout)
    with storage.connect(kb_path) as conn:
        assert conn.execute(
            "select status from candidate_papers where id = ?", (candidate_id,)
        ).fetchone()[0] == "pending"

    result = json.loads(
        subprocess.run(
            [*command, "--confirm", preview["confirmation_token"]],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    )

    assert result["rejected"] == [candidate_id]
    with storage.connect(kb_path) as conn:
        assert conn.execute(
            "select status from candidate_papers where id = ?", (candidate_id,)
        ).fetchone()[0] == "rejected"


def test_library_delete_cli_help_and_default_preview(tmp_path):
    kb_path, paper_id = paper(tmp_path)
    help_text = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "kb.py"), "library", "delete", "--help"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    assert "--confirm" in help_text

    preview = json.loads(
        subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "kb.py"),
                "library",
                "delete",
                str(kb_path),
                str(paper_id),
            ],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    )

    assert preview["action"] == "library.delete"
    assert preview["affected_count"] == 1
    assert storage.get_paper(kb_path, paper_id) is not None


def test_optional_guard_requires_confirm_flag_for_destructive_commands():
    hook = SCRIPT_DIR.parent / "hooks" / "optional_guard.py"
    denied = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_input": {"command": "python kb.py library delete kb 1"}}),
        capture_output=True,
        check=True,
        text=True,
    )
    allowed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {"tool_input": {"command": "python kb.py library delete kb 1 --confirm token"}}
        ),
        capture_output=True,
        check=True,
        text=True,
    )
    denied_uppercase = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_input": {"command": "PYTHON KB.PY LIBRARY DELETE kb 1"}}),
        capture_output=True,
        check=True,
        text=True,
    )
    allowed_equals = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {"tool_input": {"command": "python kb.py library delete kb 1 --confirm=token"}}
        ),
        capture_output=True,
        check=True,
        text=True,
    )

    assert json.loads(denied.stdout)["permissionDecision"] == "deny"
    assert "permissionDecision" not in json.loads(allowed.stdout)
    assert json.loads(denied_uppercase.stdout)["permissionDecision"] == "deny"
    assert "permissionDecision" not in json.loads(allowed_equals.stdout)


def test_reindex_token_is_consumed_by_changed_chunk_state(tmp_path):
    kb_path, paper_id = paper(tmp_path)
    with storage.connect(kb_path) as conn:
        conn.execute("update papers set abstract = ? where id = ?", ("Evidence text", paper_id))
        conn.commit()
    preview = safety.preview_action(kb_path, "library.reindex", [paper_id])

    result = library.reindex_library(
        kb_path,
        [paper_id],
        confirm=preview["confirmation_token"],
    )

    assert result["rebuilt"] == [paper_id]
    with pytest.raises(safety.SafetyGateError, match="stale"):
        library.reindex_library(
            kb_path,
            [paper_id],
            confirm=preview["confirmation_token"],
        )


def test_scheduler_install_requires_token_and_uses_argument_array(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb with spaces"
    storage.init_kb(kb_path)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="SUCCESS", stderr="", returncode=0)

    monkeypatch.setattr(scheduling.subprocess, "run", fake_run)
    preview = library.preview_schedule_change(kb_path, install=True)

    with pytest.raises(safety.SafetyGateError):
        library.change_schedule(kb_path, install=True, confirm="wrong")
    assert calls == []

    result = library.change_schedule(
        kb_path,
        install=True,
        confirm=preview["confirmation_token"],
    )

    assert result["status"] == "installed"
    assert calls[0][0][0].lower().endswith("schtasks.exe")
    assert calls[0][0][-2] == "/TR"
    assert str(kb_path.resolve()) in calls[0][0][-1]
    assert calls[0][1]["shell"] is False
