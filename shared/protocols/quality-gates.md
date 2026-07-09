# Quality Gates

Blocking gates:

- Search confirmation: topic-derived PubMed queries require user confirmation.
- Candidate approval: candidates require explicit approval before library insertion.
- Scheduled updates: recurring jobs can only create candidates.
- PDF policy: only open-access or user-provided PDFs.
- Evidence QA: answer from retrieved evidence only.
- Destructive actions: show affected IDs/counts before delete or bulk reject.

Validation gates:

- `scripts/validate_contracts.py` validates bundled JSON Schemas and fixtures.
- `scripts/run_evals.py` runs deterministic gold-set checks.
- `scripts/test_kb_core.py` covers core SQLite behavior.
