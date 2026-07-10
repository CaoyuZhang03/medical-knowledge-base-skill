# Workflow: pdf-acquisition

## Trigger
Use when the user wants to fetch, inspect, retry, or audit PDFs associated with records in a durable local KB.

## Inputs
- KB path.
- One paper ID or an explicit all-record scope.
- `pdf_fetch.enabled` policy from `config.yaml`.

## Required Decisions
- Confirm a large all-library fetch because it may make many network requests.
- Use the local UI for PDF scope selection, status, task monitoring, and retry.

## Data Access Level
Downloaded PDFs are `raw` local evidence sources; successful existence updates `approved` paper metadata.

## Steps
1. If no live URL has been provided, start the UI and provide its library/task URL before asking the user to select records or inspect outcomes.
2. Read the PDF policy and KB configuration.
3. Queue a `pdf_fetch` task for selected records.
4. Resolve only a PMCID or explicit HTTPS open-access PDF URL.
5. Validate PDF bytes, save under `files/pdfs/`, then set `has_pdf=true` and `pdf_path`.
6. Record completed, not-found, already-present, or failed task results and refresh the UI state.

## CLI/API Calls
- `kb pdf fetch <kb_path> --doc-id <id>`
- `kb pdf fetch <kb_path> --all`
- `kb serve <kb_path> --host 127.0.0.1 --port 0`
- `POST /api/pdfs/fetch`
- `POST /api/tasks/retry`

## Outputs
- Auditable task records and optional local PDF files.
- Updated paper PDF status only after validated local persistence.
- A local library/task-monitoring URL.

## Quality Gates
- Accept only PubMed Central, explicit open-access HTTPS PDF links, or user uploads.
- Never use ordinary article URLs as proof of an open PDF.
- Never bypass paywalls, institutional login, robots restrictions, or copyright controls.

## Failure Handling
- No allowed source completes as `not_found` without changing `has_pdf`.
- Network or content validation failures mark the task failed without rolling back an approved paper.
- Retry creates a new auditable task rather than rewriting prior history.

## Related Contracts
- `shared/contracts/paper_record.schema.json`
- `shared/contracts/task_record.schema.json`
- `shared/protocols/pdf-policy.md`
- `shared/protocols/ui-escalation.md`
