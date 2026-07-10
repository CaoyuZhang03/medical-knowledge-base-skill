#!/usr/bin/env python3
"""Optional hook helper for destructive KB actions.

This script is not required for Codex skill correctness. It exists as a reference
guard for environments that support pre-tool hooks. Core safety gates live in
SKILL.md workflows and scripts.
"""

from __future__ import annotations

import json
import sys

DESTRUCTIVE_TERMS = (
    " library delete ",
    " candidates reject ",
    " library reindex ",
    " schedule install ",
    " schedule uninstall ",
)


def main() -> int:
    payload = sys.stdin.read()
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse"}}))
        return 0
    command = f" {data.get('tool_input', {}).get('command', '')} ".casefold()
    has_confirmation = " --confirm " in command or " --confirm=" in command
    if any(term in command for term in DESTRUCTIVE_TERMS) and not has_confirmation:
        print(
            json.dumps(
                {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Destructive KB command requires a preview confirmation token.",
                }
            )
        )
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse"}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
