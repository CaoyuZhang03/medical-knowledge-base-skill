# Workflow: candidate-review

## Trigger
Use when the user wants to view, filter, approve, reject, or batch-screen candidates already associated with a durable local KB.

## Inputs
- KB path.
- Candidate IDs or display filters.
- Optional rejection reason.

## Required Decisions
- If exact IDs are absent, open the candidate-review UI and let the user select them.
- Approval is explicit but non-destructive; rejection requires preview plus confirmation token.

## Data Access Level
Reads `screened` records. Approval creates `approved` papers; rejection keeps an auditable `screened` record with status `rejected`.

## Steps
1. If no live URL has been provided, follow global UI Escalation and provide the candidate-review URL.
2. Load pending candidates and display ID, title, authors, journal, IF, JCR, study type, year, and PDF availability.
3. Approve only selected IDs. Approval indexes abstracts and may create PDF tasks according to `config.yaml`.
4. For rejection, run the preview command, show `affected_count`, then repeat with its fresh token only after user confirmation.
5. Refresh candidate, library, and task counts. Report duplicate candidates left pending.

## CLI/API Calls
- `kb candidates list <kb_path> --status pending`
- `kb candidates approve <kb_path> <ids...>`
- `kb candidates reject <kb_path> <ids...> --reason "..."`
- `kb candidates reject <kb_path> <ids...> --reason "..." --confirm <token>`
- `POST /api/candidates/approve`
- `POST /api/candidates/reject`

## Outputs
- Structured approval result with `approved`, `paper_ids`, `pdf_task_ids`, `pdf_results`, and `skipped`.
- Rejected candidate IDs and retained rejection reason.
- A local UI URL when interactive review is needed.

## Quality Gates
- Never approve an unselected candidate or auto-approve scheduled results.
- Rejection preview and confirmation must use identical IDs and reason.
- Keep duplicates pending and explain the conflict.

## Failure Handling
- Already decided or missing IDs are unchanged and reported.
- PDF failure does not roll back an approved paper; inspect the failed task separately.
- A stale or mismatched token requires a new preview.

## Related Contracts
- `shared/contracts/candidate_record.schema.json`
- `shared/contracts/paper_record.schema.json`
- `shared/contracts/task_record.schema.json`
- `shared/protocols/candidate-review.md`
- `shared/protocols/confirmation-safety.md`
