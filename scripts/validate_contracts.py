#!/usr/bin/env python3
"""Validate bundled schemas and skill wiring without network access."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    errors: list[str] = []

    contracts = sorted((ROOT / "shared" / "contracts").glob("*.schema.json"))
    if not contracts:
        errors.append("no contract schemas found")
    for path in contracts:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path}: invalid JSON: {exc}")
            continue
        for key in ("$schema", "title", "type", "properties"):
            if key not in data:
                errors.append(f"{path}: missing {key}")

    workflows = [
        "init-kb",
        "import-local-files",
        "pubmed-discovery",
        "candidate-review",
        "scheduled-updates",
        "library-management",
        "knowledge-qa",
        "pdf-acquisition",
        "quality-audit",
    ]
    for name in workflows:
        path = ROOT / "references" / "workflows" / name / "workflow.md"
        if not path.is_file():
            errors.append(f"missing workflow: {name}")
            continue
        text = path.read_text(encoding="utf-8")
        for heading in (
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
        ):
            if heading not in text:
                errors.append(f"{path}: missing {heading}")

    required_assets = [
        ROOT / "assets" / "data" / "JCR2025-UTF8.csv",
        ROOT / "assets" / "prompts" / "search-query-generation.md",
        ROOT / "assets" / "web-ui" / "index.html",
    ]
    for path in required_assets:
        if not path.is_file():
            errors.append(f"missing asset: {path}")

    if errors:
        print(json.dumps({"passed": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"passed": True, "schemas": len(contracts), "workflows": len(workflows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
