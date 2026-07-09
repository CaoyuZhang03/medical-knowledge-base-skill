# Workflow: library-management

## Trigger
Use when the user wants to list, delete, inspect, repair, or reindex approved library records.

## Inputs
- KB path.
- Paper IDs or filters.

## Required Decisions
- Deletion and bulk rejection require explicit affected IDs/counts before execution.

## Data Access Level
Operates on `approved` paper records and indexed chunks.

## Steps
1. List papers with `kb library list`.
2. For destructive actions, show affected rows and ask for confirmation.
3. Run `kb library delete` or `kb library reindex`.
4. Run `kb audit` afterward.

## CLI/API Calls
- `kb library list <kb_path>`
- `kb library delete <kb_path> <ids...>`
- `kb library reindex <kb_path>`
- `kb audit <kb_path>`

## Outputs
- Updated library records.
- Audit result.

## Quality Gates
- Never delete without explicit user confirmation.
- Delete should cascade chunks through SQLite foreign keys.

## Failure Handling
- Missing IDs are skipped and reported.

## Related Contracts
- `shared/contracts/paper_record.schema.json`
- `shared/protocols/quality-gates.md`
