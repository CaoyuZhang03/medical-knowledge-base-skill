# Workflow: scheduled-updates

## Trigger
Use when the user wants to create, list, enable, disable, run, install, or remove recurring PubMed monitoring for a durable local KB.

## Inputs
- KB path.
- Confirmed PubMed query, name, daily/weekly/monthly frequency, and optional filters.
- Optional saved-search IDs.

## Required Decisions
- Topic-derived expressions require confirmation before the saved search is created.
- Installing or uninstalling Windows Task Scheduler requires preview plus confirmation token.

## Data Access Level
Saved-search metadata is durable configuration; every run writes `screened` candidates only.

## Steps
1. If no URL has been provided, start the UI and provide its saved-search configuration URL.
2. Create a saved search from a confirmed expression. The default first run executes only the new search; `--no-first-run` defers it.
3. List, enable, disable, or explicitly run saved searches. Background runs execute only due enabled searches.
4. Inspect candidate and task results; never auto-approve.
5. For system installation/removal, call once for preview, show the unique task name and command, then repeat with the fresh token after confirmation.

## CLI/API Calls
- `kb schedule create <kb_path> --name "<name>" --query "<query>" --frequency weekly --min-if 10 --jcr-quartile Q1 --study-type "随机对照试验"`
- `kb schedule list <kb_path>`
- `kb schedule enable <kb_path> <search_id>`
- `kb schedule disable <kb_path> <search_id>`
- `kb schedule run <kb_path> --search-id <search_id>`
- `kb schedule run <kb_path> --force`
- `kb schedule install <kb_path>`
- `kb schedule install <kb_path> --confirm <token>`
- `kb schedule uninstall <kb_path>`
- `kb schedule uninstall <kb_path> --confirm <token>`
- `POST /api/searches`
- `POST /api/searches/run`

## Outputs
- Saved-search rows with `last_run_at` and `next_run_at`.
- Auditable `scheduled_update` tasks and pending candidate IDs.
- Optional installed/uninstalled unique Windows task result.
- Local saved-search/task-monitoring URL.

## Quality Gates
- Run only due enabled searches unless explicit IDs or `--force` are supplied.
- A failed run stays due for retry and records a failed task.
- System commands use an argument array with `shell=False` after confirmation.
- Scheduled results never bypass candidate review.

## Failure Handling
- A failed first run does not delete the saved search.
- Scheduler installation failure leaves the saved search usable through manual runs.
- Stale or mismatched install/uninstall tokens require a new preview.

## Related Contracts
- `shared/contracts/saved_search.schema.json`
- `shared/contracts/task_record.schema.json`
- `shared/protocols/confirmation-safety.md`
- `shared/protocols/ui-escalation.md`
