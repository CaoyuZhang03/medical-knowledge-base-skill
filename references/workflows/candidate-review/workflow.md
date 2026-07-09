# Workflow: candidate-review

## Trigger
Use when the user wants to approve, reject, screen, filter, or ingest candidate papers.

## Inputs
- KB path.
- Candidate IDs or filter criteria.
- Optional rejection reason.

## Required Decisions
- Ask for exact IDs before approving or rejecting if the user has not selected them.
- For bulk actions, show affected count and representative titles first.

## Data Access Level
Moves records from `screened` to `approved` only after user approval.

## Steps
1. List pending candidates with `kb candidates list`.
2. Apply user-selected filters in the display layer.
3. Run approve or reject only on explicit IDs.
4. Confirm resulting counts with `kb library list` or `kb candidates list`.

## CLI/API Calls
- `kb candidates list <kb_path> --status pending`
- `kb candidates approve <kb_path> <ids...>`
- `kb candidates reject <kb_path> <ids...> --reason "..."`

## Outputs
- Approved papers inserted into `papers`.
- Rejected candidates retained with `rejection_reason`.

## Quality Gates
- No candidate can become a paper without explicit approval.
- Rejected candidates remain auditable.

## Failure Handling
- Ignore already decided IDs and report which IDs changed.
- If duplicate PMID/DOI blocks approval, report the conflict and leave candidate pending.

## Related Contracts
- `shared/contracts/candidate_record.schema.json`
- `shared/contracts/paper_record.schema.json`
- `shared/protocols/candidate-review.md`
