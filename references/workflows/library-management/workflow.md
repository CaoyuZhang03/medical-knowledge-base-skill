# Workflow: library-management

## Trigger
Use when the user wants to view, filter, delete, inspect PDF status for, or rebuild indexes for approved records in a durable local KB.

## Inputs
- KB path.
- Paper IDs or an explicit all-record scope.

## Required Decisions
- Use the UI when the user needs to browse or select records.
- Delete and reindex require preview plus the exact fresh confirmation token.

## Data Access Level
Operates on `approved` papers, their chunks, and local file-status metadata.

## Steps
1. If no URL has been provided, start the local UI and provide its URL for library management.
2. List records with title, authors, journal, IF, JCR, study type, year, and PDF state.
3. For deletion, run without `--confirm`, show affected records, then repeat with the returned token after confirmation.
4. For reindex, choose `--ids` or `--all`; the default call previews current chunks, source availability, missing IDs, and token. Repeat with `--confirm` to rebuild.
5. Audit and refresh the library after execution. Preserved source/PDF files are not deleted by a record deletion.

## CLI/API Calls
- `kb library list <kb_path>`
- `kb library delete <kb_path> <ids...>`
- `kb library delete <kb_path> <ids...> --confirm <token>`
- `kb library reindex <kb_path> --ids <ids...>`
- `kb library reindex <kb_path> --ids <ids...> --confirm <token>`
- `kb library reindex <kb_path> --all`
- `kb audit <kb_path>`
- `POST /api/library/delete`
- `POST /api/library/reindex`

## Outputs
- Library list or guarded mutation result.
- Reindex summary with rebuilt, skipped, missing IDs, and chunk count.
- A local library-management URL when interactive selection is required.

## Quality Gates
- Never execute a destructive action from an expired, stale, mismatched, or cross-KB token.
- Show IDs/counts before mutation and preserve managed source files by default.
- Reindex only from stored abstract, extracted text, or a readable preserved source.

## Failure Handling
- Missing IDs are reported and do not expand scope.
- Source-less papers are skipped without deleting their current chunks.
- State changes after preview require a new token.

## Related Contracts
- `shared/contracts/paper_record.schema.json`
- `shared/contracts/evidence_packet.schema.json`
- `shared/protocols/confirmation-safety.md`
- `shared/protocols/quality-gates.md`
