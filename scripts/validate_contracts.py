#!/usr/bin/env python3
"""Validate schemas, fixtures, workflow commands, assets, and live record shapes."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any

import kb
from kb_core import scheduling, storage


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    "init-kb",
    "import-local-files",
    "pubmed-discovery",
    "candidate-review",
    "scheduled-updates",
    "library-management",
    "knowledge-qa",
    "pdf-acquisition",
    "quality-audit",
)
WORKFLOW_HEADINGS = (
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


def parser_contract(parser: argparse.ArgumentParser) -> dict[tuple[str, ...], dict[str, Any]]:
    result: dict[tuple[str, ...], dict[str, Any]] = {}

    def walk(current: argparse.ArgumentParser, path: list[str]) -> None:
        options = {
            option
            for action in current._actions
            for option in action.option_strings
            if option.startswith("--")
        }
        subparsers = [
            action for action in current._actions if isinstance(action, argparse._SubParsersAction)
        ]
        if subparsers:
            for action in subparsers:
                for name, child in action.choices.items():
                    walk(child, [*path, name])
            return
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

    walk(parser, [])
    return result


def validate_workflow_commands(errors: list[str]) -> None:
    contract = parser_contract(kb.build_parser())
    for workflow in WORKFLOWS:
        path = ROOT / "references" / "workflows" / workflow / "workflow.md"
        if not path.is_file():
            errors.append(f"missing workflow: {workflow}")
            continue
        text = path.read_text(encoding="utf-8")
        for heading in WORKFLOW_HEADINGS:
            if heading not in text:
                errors.append(f"{path}: missing {heading}")
        for raw in re.findall(r"^- `kb ([^`]+)`", text, re.MULTILINE):
            tokens = shlex.split(raw, posix=False)
            command_path = next(
                (
                    candidate
                    for candidate in sorted(contract, key=len, reverse=True)
                    if list(candidate) == tokens[: len(candidate)]
                ),
                None,
            )
            if command_path is None:
                errors.append(f"{path}: unsupported command: kb {raw}")
                continue
            options = {token.split("=", 1)[0] for token in tokens if token.startswith("--")}
            unsupported = options - contract[command_path]["options"]
            if unsupported:
                errors.append(
                    f"{path}: unsupported options for {' '.join(command_path)}: {sorted(unsupported)}"
                )
            for group in contract[command_path]["required_groups"]:
                if not options & group:
                    errors.append(
                        f"{path}: {' '.join(command_path)} must document one of {sorted(group)}"
                    )


def live_contract_instances() -> dict[str, dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "kb"
        storage.init_kb(root)
        paper_id = kb.insert_paper(
            root,
            {"title": "Contract paper", "pmid": "9001", "source": "validation"},
        )
        kb.add_chunk(
            root,
            paper_id=paper_id,
            chunk_text="Contract evidence supports retrieval.",
            source_locator="abstract",
        )
        candidate_id = kb.add_candidate(root, {"title": "Contract candidate", "pmid": "9002"})
        search_id = scheduling.create_saved_search(
            root,
            name="Contract search",
            query="contract[Title]",
            frequency="weekly",
            run_first=False,
        )
        task_id = storage.create_task(root, "audit", {"scope": "contracts"})
        return {
            "kb_config.schema.json": storage.load_config(root),
            "kb_passport.schema.json": storage.refresh_passport(root),
            "paper_record.schema.json": storage.get_paper(root, paper_id),
            "candidate_record.schema.json": next(
                item for item in kb.list_candidates(root, status="pending") if item["id"] == candidate_id
            ),
            "saved_search.schema.json": storage.get_saved_search(root, search_id),
            "task_record.schema.json": storage.get_task(root, task_id),
            "evidence_packet.schema.json": kb.retrieve_evidence(
                root,
                question="contract evidence",
            ),
        }


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _instance_errors(schema: dict[str, Any], value: Any, location: str = "<root>") -> list[str]:
    errors = []
    if "oneOf" in schema:
        matches = [not _instance_errors(branch, value, location) for branch in schema["oneOf"]]
        if sum(matches) != 1:
            errors.append(f"{location}: expected exactly one oneOf branch")
        return errors
    expected = schema.get("type")
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, item) for item in allowed):
            return [f"{location}: expected type {allowed}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{location}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: value {value!r} is outside enum")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{location}: string is shorter than minLength")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{location}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{location}: value is above maximum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{location}: missing required property {key}")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                errors.extend(_instance_errors(child, value[key], f"{location}.{key}"))
    if isinstance(value, list):
        child = schema.get("items")
        if child:
            for index, item in enumerate(value):
                errors.extend(_instance_errors(child, item, f"{location}[{index}]"))
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{location}: array items are not unique")
    return errors


def _schema_shape_errors(schema: dict[str, Any], location: str) -> list[str]:
    errors = []
    for key in ("$schema", "title", "type", "properties"):
        if key not in schema:
            errors.append(f"{location}: missing {key}")
    if "properties" in schema and not isinstance(schema["properties"], dict):
        errors.append(f"{location}: properties must be an object")
    if "required" in schema and not isinstance(schema["required"], list):
        errors.append(f"{location}: required must be an array")
    return errors


def validate_schemas(errors: list[str]) -> int:
    contracts = sorted((ROOT / "shared" / "contracts").glob("*.schema.json"))
    if not contracts:
        errors.append("no contract schemas found")
        return 0
    schemas = {}
    for path in contracts:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            errors.extend(_schema_shape_errors(schema, str(path)))
            schemas[path.name] = schema
        except Exception as exc:
            errors.append(f"{path}: invalid schema: {exc}")
    if len(schemas) != len(contracts):
        return len(contracts)

    try:
        instances = live_contract_instances()
    except Exception as exc:
        errors.append(f"could not generate live contract instances: {exc}")
        return len(contracts)
    for name, instance in instances.items():
        if instance is None:
            errors.append(f"live instance missing for {name}")
            continue
        for error in _instance_errors(schemas[name], instance):
            errors.append(f"{name} live instance {error}")
    return len(contracts)


def validate_routing(errors: list[str]) -> int:
    path = ROOT / "evals" / "gold" / "routing" / "cases.json"
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"routing fixtures invalid: {exc}")
        return 0
    seen = set()
    for case in cases:
        case_id = case.get("id")
        if not case_id or case_id in seen:
            errors.append(f"routing fixture has missing/duplicate id: {case_id}")
        seen.add(case_id)
        if not case.get("reason") or not isinstance(case.get("should_trigger"), bool):
            errors.append(f"routing fixture {case_id} lacks reason/boolean trigger")
        mode = case.get("expected_mode")
        if case.get("should_trigger") and mode not in WORKFLOWS:
            errors.append(f"routing fixture {case_id} has unknown mode: {mode}")
        if not case.get("should_trigger") and mode is not None:
            errors.append(f"routing fixture {case_id} negative case must have null mode")
    return len(cases)


def validate_skill_wiring(errors: list[str]) -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = re.match(r"^---\s*\n(.*?)\n---", skill, re.DOTALL)
    if not frontmatter:
        errors.append("SKILL.md must begin with YAML frontmatter")
    else:
        description = re.search(r"^description:\s*(.+)$", frontmatter.group(1), re.MULTILINE)
        if not description or not description.group(1).startswith("Use when"):
            errors.append("SKILL.md description must start with 'Use when'")
    for phrase in (
        "## UI Escalation",
        "no URL has been provided",
        "provide its URL",
        "candidate review",
        "library management",
        "local upload",
        "saved-search configuration",
        "task monitoring",
    ):
        if phrase not in skill:
            errors.append(f"SKILL.md missing UI policy phrase: {phrase}")

    for path in (
        ROOT / "assets" / "data" / "JCR2025-UTF8.csv",
        ROOT / "assets" / "prompts" / "search-query-generation.md",
        ROOT / "assets" / "web-ui" / "index.html",
        ROOT / "shared" / "protocols" / "confirmation-safety.md",
        ROOT / "shared" / "protocols" / "ui-escalation.md",
    ):
        if not path.is_file():
            errors.append(f"missing required asset/protocol: {path}")


def main() -> int:
    errors: list[str] = []
    schema_count = validate_schemas(errors)
    validate_skill_wiring(errors)
    validate_workflow_commands(errors)
    routing_count = validate_routing(errors)
    report = {
        "passed": not errors,
        "schemas": schema_count,
        "workflows": len(WORKFLOWS),
        "routing_cases": routing_count,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
