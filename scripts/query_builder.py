#!/usr/bin/env python3
"""Print the bundled PubMed query-generation prompt for Codex use."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    print((ROOT / "assets" / "prompts" / "search-query-generation.md").read_text(encoding="utf-8"))
