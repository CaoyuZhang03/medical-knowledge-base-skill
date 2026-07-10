---
name: medical-knowledge-base
description: Use when the user explicitly wants to create, import into, screen, maintain, update, open, audit, or ask evidence-grounded questions from a durable local biomedical knowledge base, including PubMed candidate discovery, recurring searches, IF/JCR enrichment, study-type filtering, open-access PDF tasks, and library management.
---

# Medical Knowledge Base

Use this skill as a lightweight orchestrator for a durable, user-selected local biomedical knowledge base. SQLite and preserved local files are the system of record. Codex routes the request, loads the relevant workflow, enforces confirmation gates, invokes deterministic scripts, and synthesizes answers only from retrieved evidence.

## Intent Boundary

Trigger only when the user expresses durable local-KB intent: create or open a KB, import into it, screen candidates, maintain its library, configure recurring updates, monitor its tasks, acquire PDFs for its records, audit it, or answer from it.

Do not trigger for ordinary one-off PubMed research, a standalone PDF summary, general medical QA, or an explanation of IF/JCR when the user has not asked to persist or use a local KB. Route those requests to the appropriate research, PDF, or general workflow.

## Routing Discipline

1. Classify the request into one primary mode from `references/mode-registry.md`.
2. Read that mode's `workflow.md` before acting.
3. Read an auxiliary workflow only when the primary workflow explicitly depends on it or the request clearly spans sequential phases.
4. When a request combines upload, PubMed discovery, and QA without a clear immediate goal, ask which phase to perform first.

Explicit tags override fuzzy routing:

- `[kb:init-kb]`
- `[kb:import-local-files]`
- `[kb:pubmed-discovery]`
- `[kb:candidate-review]`
- `[kb:scheduled-updates]`
- `[kb:library-management]`
- `[kb:knowledge-qa]`
- `[kb:pdf-acquisition]`
- `[kb:quality-audit]`

## Workflow Map

- Create or configure a KB: `references/workflows/init-kb/workflow.md`.
- Import local files or bibliography data: `references/workflows/import-local-files/workflow.md`.
- Discover PubMed candidates: `references/workflows/pubmed-discovery/workflow.md`.
- Approve or reject candidates: `references/workflows/candidate-review/workflow.md`.
- Configure or run recurring updates: `references/workflows/scheduled-updates/workflow.md`.
- Inspect, delete, or reindex library records: `references/workflows/library-management/workflow.md`.
- Answer from all or selected approved papers: `references/workflows/knowledge-qa/workflow.md`.
- Fetch or inspect open-access PDFs: `references/workflows/pdf-acquisition/workflow.md`.
- Validate the skill or a KB: `references/workflows/quality-audit/workflow.md`.

## UI Escalation

Use the local Web UI for candidate review, library management, local upload, saved-search configuration, and task monitoring. When entering any of these operational surfaces and no URL has been provided in the current task:

1. Start `python scripts/kb.py serve <kb_path> --host 127.0.0.1 --port 0` as a hidden background process.
2. Read the actual loopback URL printed by the server.
3. Immediately provide its URL as a clickable link before asking the user to select, upload, approve, reject, delete, configure, or retry anything.

Reuse a live URL already provided for the same KB; do not start duplicate servers. PubMed discovery and scheduled runs that produce new candidates should escalate to the candidate-review UI when user selection is the next step. Pure evidence retrieval and read-only CLI audits do not require opening the UI unless the user asks.

The UI is local-only and must remain bound to `127.0.0.1` by default. Read `shared/protocols/ui-escalation.md` for handoff details.

## Global Gates

Apply these gates in every mode:

1. A natural-language topic must be converted with `assets/prompts/search-query-generation.md`; show the exact PubMed expression and obtain user confirmation before retrieval.
2. PubMed and scheduled-search results enter `candidate_papers`; never auto-approve them.
3. Approval requires explicit candidate IDs selected by the user.
4. Delete, reject, reindex, scheduler install, and scheduler uninstall default to a preview. Execute only by repeating the exact command with the fresh state-bound `--confirm` token.
5. Automatic PDF acquisition is limited to PubMed Central and explicit HTTPS open-access PDF links. Never bypass a paywall or authenticated access.
6. Knowledge-base QA must run `kb qa retrieve`; use only the returned evidence packet. Empty or insufficient evidence requires an explicit insufficiency statement.
7. Before any bulk operation, display affected IDs/counts and report skipped or missing IDs.

## Data Access Levels

- `raw`: uploads, raw PubMed XML, preserved source files, extracted text, and downloaded PDFs.
- `screened`: filtered candidate records awaiting review.
- `approved`: user-approved candidates and user-imported local documents.
- `evidence_only`: retrieved chunks supplied to Codex for answer synthesis.

Read `shared/protocols/data-access-levels.md`; do not silently answer approved-KB questions from raw or screened material.

## Script Entry Point

Run from the skill root:

```bash
python scripts/kb.py <command>
```

Command families:

- `kb init`
- `kb import`
- `kb pubmed search`
- `kb candidates list|approve|reject`
- `kb schedule create|list|enable|disable|run|install|uninstall`
- `kb library list|delete|reindex`
- `kb pdf fetch`
- `kb qa retrieve`
- `kb audit`
- `kb serve`

Use each workflow's documented options rather than inferring syntax. The Web UI JSON endpoints are implemented in `scripts/kb_core/web.py`; the UI does not perform LLM answer synthesis.

## Durable Artifacts

- Main database: `<kb_path>/kb.sqlite`.
- Configuration: `<kb_path>/config.yaml`.
- Audit passport: `<kb_path>/kb-passport.yaml`.
- Preserved sources: `<kb_path>/files/uploads/`.
- Open PDFs: `<kb_path>/files/pdfs/`.
- Extracted text: `<kb_path>/files/extracted-text/`.
- Bundled JCR table: `assets/data/JCR2025-UTF8.csv`.
- Query-generation prompt: `assets/prompts/search-query-generation.md`.

Never expose `<kb_path>/.confirmation-secret`; initialization adds it to the KB's `.gitignore`.

## Validation

Before claiming this skill is ready, run all of:

```bash
python scripts/validate_contracts.py
python scripts/run_evals.py
python -m pytest scripts -q -p no:cacheprovider
python C:\Users\15468\.codex\skills\.system\skill-creator\scripts\quick_validate.py <skill_root>
```

For UI changes, additionally verify the live loopback page at desktop and mobile viewports, check browser console errors, and exercise at least one real mutation plus one confirmation preview.
