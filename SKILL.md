---
name: medical-knowledge-base
description: Build, maintain, screen, update, and query local biomedical knowledge bases. Use when Codex needs to initialize a local medical literature knowledge base, import PDFs/DOCX/Markdown/TXT/CSV/Excel/RIS/BibTeX/PMID lists, search PubMed from a topic or search expression, enrich papers with IF/JCR and study type, manage candidate screening, schedule recurring literature updates, fetch open-access PDFs, audit knowledge-base quality, or answer questions from retrieved local evidence.
---

# Medical Knowledge Base

Use this skill as a local orchestrator for biomedical knowledge bases. The skill stores the durable state in a user-selected local knowledge-base directory and uses bundled scripts for deterministic operations. Codex performs reasoning, confirmation, and answer synthesis only after loading the workflow that matches the user's request.

## Routing Discipline

First classify the request into one primary mode from `references/mode-registry.md`. Read exactly one primary workflow before taking action. Read an auxiliary workflow only when the primary workflow explicitly depends on it or the user request spans phases.

Explicit mode tags override fuzzy routing:

- `[kb:init-kb]`
- `[kb:import-local-files]`
- `[kb:pubmed-discovery]`
- `[kb:candidate-review]`
- `[kb:scheduled-updates]`
- `[kb:library-management]`
- `[kb:knowledge-qa]`
- `[kb:quality-audit]`
- `[kb:pdf-acquisition]`

Clarify before acting when the user provides cross-phase materials without a clear target, such as uploading files, requesting a PubMed search, and asking a knowledge-base question in the same message. Do not silently choose one workflow.

## Workflow Map

- Create or configure a knowledge base: read `references/workflows/init-kb/workflow.md`.
- Upload local files, PDFs, tables, or bibliographies: read `references/workflows/import-local-files/workflow.md`.
- Search PubMed by natural-language topic or PubMed query: read `references/workflows/pubmed-discovery/workflow.md`.
- Approve, reject, screen, or ingest candidate papers: read `references/workflows/candidate-review/workflow.md`.
- Create or run recurring literature updates: read `references/workflows/scheduled-updates/workflow.md`.
- Delete, inspect, reindex, or repair library records: read `references/workflows/library-management/workflow.md`.
- Answer from the knowledge base: read `references/workflows/knowledge-qa/workflow.md`.
- Fetch or audit open-access PDFs: read `references/workflows/pdf-acquisition/workflow.md`.
- Validate schemas, fixtures, workflow coverage, or KB consistency: read `references/workflows/quality-audit/workflow.md`.

## Global Gates

Follow these gates in every workflow:

1. Topic-based PubMed search requires user confirmation of the generated query before retrieval.
2. PubMed results enter `candidate_papers`; never ingest them into `papers` without approval.
3. Scheduled updates write candidates only; never auto-approve papers.
4. PDF acquisition is limited to PubMed Central and explicit open-access links.
5. Knowledge-base QA must use `kb qa retrieve` evidence. If retrieved evidence is empty or weak, say the evidence is insufficient.
6. Destructive actions must present affected IDs/counts before execution.

## Data Access Levels

Use `shared/protocols/data-access-levels.md` to preserve provenance:

- `raw`: uploaded files, raw PubMed responses, and downloaded PDFs.
- `screened`: filtered but not approved candidate papers.
- `approved`: user-approved library records.
- `evidence_only`: retrieved chunks used for answer synthesis.

## Script Entry Points

Run scripts from `scripts/` with Python. The main entry point is:

```bash
python scripts/kb.py <command>
```

Public commands:

- `kb init <kb_path>`
- `kb import <kb_path> <files...>`
- `kb pubmed search <kb_path> --topic/--query`
- `kb candidates list|approve|reject`
- `kb schedule create|run|install|uninstall`
- `kb library list|delete|reindex`
- `kb pdf fetch <kb_path> --doc-id/--all`
- `kb qa retrieve <kb_path> --question --doc-ids optional`
- `kb audit <kb_path>`
- `kb serve <kb_path>`

## Bundled Assets

- JCR/IF data: `assets/data/JCR2025-UTF8.csv`.
- PubMed query generation prompt: `assets/prompts/search-query-generation.md`.
- Local Web UI template: `assets/web-ui/index.html`.

## Validation

Before claiming the skill is ready or after changing it, run:

```bash
python scripts/validate_contracts.py
python scripts/run_evals.py
python -m pytest scripts/test_kb_core.py -q
```

Then run the system skill validator:

```bash
python C:\Users\15468\.codex\skills\.system\skill-creator\scripts\quick_validate.py C:\Users\15468\.codex\skills\medical-knowledge-base
```
