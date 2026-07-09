# Workflow: scheduled-updates

## Trigger
Use when the user wants recurring PubMed monitoring for a topic or query.

## Inputs
- KB path.
- Confirmed PubMed query or topic that has been converted and confirmed.
- Frequency and optional filters.

## Required Decisions
- Confirm generated query before creating the saved search.
- Confirm whether to install a system scheduler task.

## Data Access Level
Scheduled results are `screened` candidates only.

## Steps
1. Generate and confirm query if the user supplied a topic.
2. Run `kb schedule create <kb_path> --name ... --query ... --frequency ...`.
3. First run executes immediately and writes candidates.
4. For background execution, present the generated Windows Task Scheduler command.

## CLI/API Calls
- `kb schedule create <kb_path> --name "<name>" --query "<query>" --frequency weekly`
- `kb schedule run <kb_path>`
- `kb schedule install <kb_path>`

## Outputs
- Saved search row.
- Candidate rows from first and future runs.
- Optional system scheduler command.

## Quality Gates
- Scheduled updates never auto-approve papers.
- User must confirm system scheduler installation separately.

## Failure Handling
- If scheduling cannot be installed, keep the saved search and give the manual run command.

## Related Contracts
- `shared/contracts/saved_search.schema.json`
- `shared/contracts/task_record.schema.json`
