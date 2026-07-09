# Workflow: pdf-acquisition

## Trigger
Use when the user wants to fetch, attach, or audit PDFs for library or candidate papers.

## Inputs
- KB path.
- Paper IDs or `--all`.
- PDF policy setting.

## Required Decisions
- Confirm bulk PDF fetching if it may make many network requests.

## Data Access Level
Downloaded PDFs are `raw`; their existence updates `approved` metadata only after successful open-access retrieval or user upload.

## Steps
1. Read `shared/protocols/pdf-policy.md`.
2. For each paper, prefer PubMed Central and explicit open-access links.
3. Save PDFs under `files/pdfs/`.
4. Mark `has_pdf=true` only when the file exists locally.

## CLI/API Calls
- `kb pdf fetch <kb_path> --doc-id <id>`
- `kb pdf fetch <kb_path> --all`

## Outputs
- Local PDF files where legally available.
- Updated `has_pdf` and `pdf_path`.

## Quality Gates
- Do not bypass paywalls or institutional access.

## Failure Handling
- If no open PDF is found, leave `has_pdf=false` and report the reason.

## Related Contracts
- `shared/protocols/pdf-policy.md`
- `shared/contracts/task_record.schema.json`
