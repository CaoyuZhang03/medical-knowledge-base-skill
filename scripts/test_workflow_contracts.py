import argparse
import json
import re
import shlex
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import kb  # noqa: E402


REQUIRED_WORKFLOW_HEADINGS = (
    "## Trigger",
    "## Inputs",
    "## Required Decisions",
    "## Data Access Level",
    "## Steps",
    "## CLI/API Calls",
    "## Outputs",
    "## Quality Gates",
    "## Failure Handling",
    "## Related Contracts",
)


def load_skill_description(path):
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter"
    description = re.search(r"^description:\s*(.+)$", match.group(1), re.MULTILINE)
    assert description, "frontmatter description is missing"
    return description.group(1).strip()


def enumerate_parser_commands(parser):
    result = {}

    def walk(current, path):
        options = {
            option
            for action in current._actions
            for option in action.option_strings
            if option.startswith("--")
        }
        subparsers = [
            action for action in current._actions if isinstance(action, argparse._SubParsersAction)
        ]
        if not subparsers:
            required_groups = []
            for group in current._mutually_exclusive_groups:
                if group.required:
                    required_groups.append(
                        {
                            option
                            for action in group._group_actions
                            for option in action.option_strings
                            if option.startswith("--")
                        }
                    )
            result[tuple(path)] = {"options": options, "required_groups": required_groups}
            return
        for action in subparsers:
            for name, child in action.choices.items():
                walk(child, [*path, name])

    walk(parser, [])
    return result


def documented_kb_calls(workflow_root):
    calls = []
    for path in sorted(workflow_root.glob("*/workflow.md")):
        text = path.read_text(encoding="utf-8")
        for command in re.findall(r"^- `kb ([^`]+)`", text, re.MULTILINE):
            tokens = shlex.split(command, posix=False)
            calls.append((path, tokens))
    return calls


def test_frontmatter_requires_explicit_durable_local_kb_intent():
    description = load_skill_description(ROOT / "SKILL.md")

    assert description.startswith("Use when")
    assert "durable local biomedical knowledge base" in description.lower()
    assert "standalone pdf" not in description.lower()


def test_ui_escalation_policy_is_global_and_names_all_operational_surfaces():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "## UI Escalation" in skill
    assert "provide its URL" in skill
    assert "no URL has been provided" in skill
    for surface in (
        "candidate review",
        "library management",
        "local upload",
        "saved-search configuration",
        "task monitoring",
    ):
        assert surface in skill


def test_all_workflows_have_required_sections():
    workflows = sorted((ROOT / "references" / "workflows").glob("*/workflow.md"))
    assert len(workflows) == 9
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        for heading in REQUIRED_WORKFLOW_HEADINGS:
            assert heading in text, f"{path} is missing {heading}"


def test_documented_cli_calls_exist_in_parser():
    parser_contract = enumerate_parser_commands(kb.build_parser())
    for path, tokens in documented_kb_calls(ROOT / "references" / "workflows"):
        command_path = None
        for candidate in sorted(parser_contract, key=len, reverse=True):
            if list(candidate) == tokens[: len(candidate)]:
                command_path = candidate
                break
        assert command_path is not None, f"{path}: unsupported command: {' '.join(tokens)}"
        documented_options = {
            token.split("=", 1)[0] for token in tokens if token.startswith("--")
        }
        contract = parser_contract[command_path]
        assert documented_options <= contract["options"], (
            f"{path}: unsupported options for {' '.join(command_path)}: "
            f"{sorted(documented_options - contract['options'])}"
        )
        for group in contract["required_groups"]:
            assert documented_options & group, (
                f"{path}: {' '.join(command_path)} must document one of {sorted(group)}"
            )


def test_routing_gold_set_covers_positive_negative_ui_and_qa_cases():
    cases = json.loads(
        (ROOT / "evals" / "gold" / "routing" / "cases.json").read_text(encoding="utf-8")
    )
    ids = [case["id"] for case in cases]

    assert len(cases) >= 12
    assert len(ids) == len(set(ids))
    assert {case["should_trigger"] for case in cases} == {True, False}
    assert any(case.get("expect_ui") for case in cases)
    assert any(case.get("expected_mode") == "knowledge-qa" for case in cases)
    assert all(case.get("reason") for case in cases)
