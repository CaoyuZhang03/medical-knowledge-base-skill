# Workflow: init-kb

## Trigger
Use when the user explicitly wants to create, configure, migrate, inspect, or open a durable local biomedical KB.

## Inputs
- Target KB path.
- Optional PDF-fetch and PubMed configuration preferences.

## Required Decisions
- Ask for a path when none is known.
- Confirm before placing a KB inside a directory that clearly belongs to another project.
- Ask whether the user wants the local UI only when they have not already requested to open it.

## Data Access Level
Creates an empty `approved` store plus raw-file directories and an auditable KB passport.

## Steps
1. Run initialization; forward migrations are applied without deleting existing records.
2. Verify `kb.sqlite`, `config.yaml`, `kb-passport.yaml`, `.gitignore`, `files/`, and `logs/`.
3. Explain that SQLite is authoritative and `kb-passport.yaml` is an exportable audit snapshot.
4. If the user wants an operational page, start the local server on loopback and provide its actual URL.

## CLI/API Calls
- `kb init <kb_path>`
- `kb audit <kb_path>`
- `kb serve <kb_path> --host 127.0.0.1 --port 0`

## Outputs
- Versioned SQLite schema and schema-migration history.
- Configuration, passport, file directories, and secret-exclusion `.gitignore` rule.
- Optional local Web UI URL.

## Quality Gates
- Preserve existing files and rows; initialization is idempotent.
- Never reveal `.confirmation-secret`.
- Bind the UI to `127.0.0.1` unless the user explicitly accepts a different exposure model.

## Failure Handling
- On migration failure, report the SQLite error and stop before import/search work.
- If a preferred port is occupied, use `--port 0` or another loopback port and report the actual URL.

## Related Contracts
- `shared/contracts/kb_config.schema.json`
- `shared/contracts/kb_passport.schema.json`
- `shared/protocols/confirmation-safety.md`
- `shared/protocols/ui-escalation.md`
