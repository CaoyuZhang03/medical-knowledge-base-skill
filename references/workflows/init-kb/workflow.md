# Workflow: init-kb

## Trigger
Use when the user wants to create, configure, inspect, or prepare a new local biomedical knowledge base.

## Inputs
- Target knowledge-base path.
- Optional default PDF policy and PubMed settings.

## Required Decisions
- If no path is provided, ask for the KB path.
- Do not initialize inside a directory that obviously belongs to another project unless the user confirms.

## Data Access Level
Creates an empty `approved` store and a `kb-passport.yaml` audit snapshot.

## Steps
1. Run `python scripts/kb.py init <kb_path>`.
2. Confirm `kb.sqlite`, `config.yaml`, `kb-passport.yaml`, `files/`, and `logs/` exist.
3. If the user wants the local UI, run `python scripts/kb.py serve <kb_path>`.

## CLI/API Calls
- `kb init <kb_path>`
- `kb audit <kb_path>`
- `kb serve <kb_path>`

## Outputs
- Initialized KB directory.
- SQLite schema with paper, candidate, task, saved-search, and chunk tables.
- Passport recording bundled JCR and prompt assets.

## Quality Gates
- Preserve existing KB files; do not delete or reinitialize without explicit confirmation.

## Failure Handling
- If schema creation fails, report the SQLite error and do not continue to import/search workflows.

## Related Contracts
- `shared/contracts/kb_config.schema.json`
- `shared/contracts/kb_passport.schema.json`
