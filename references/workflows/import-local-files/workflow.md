# Workflow: import-local-files

## Trigger
Use when the user explicitly wants local files added to a durable biomedical KB, including PDF, DOCX, Markdown, TXT, CSV, XLSX, RIS, BibTeX, or PMID-list inputs.

## Inputs
- KB path.
- One or more user-selected local files.

## Required Decisions
- Initialize the KB first if it does not exist.
- Ask before recursively importing a large directory; the CLI accepts explicit files, not an implicit recursive crawl.

## Data Access Level
Source files are `raw`. User-selected imports become `approved` records. Extracted text is indexed for evidence retrieval.

## Steps
1. Ensure the KB exists.
2. When the user wants upload controls and no URL has been provided, start the local UI and provide its URL with the local-upload view.
3. Import explicit files through the CLI or multipart UI endpoint.
4. Preserve each source under `files/uploads/`; extract supported document text to `files/extracted-text/`.
5. Normalize structured metadata, enrich against the bundled JCR table, deduplicate, and index abstracts/full text.
6. Report created, duplicate, failed, paper IDs, per-item states, and warnings; then audit the KB.

## CLI/API Calls
- `kb init <kb_path>`
- `kb import <kb_path> <files...>`
- `kb library list <kb_path>`
- `kb audit <kb_path>`
- `kb serve <kb_path> --host 127.0.0.1 --port 0`
- `POST /api/import`

## Outputs
- Preserved source files and optional extracted-text files.
- Approved paper records and retrieval chunks.
- Structured import summary and, when needed, a local upload URL.

## Quality Gates
- Never discard a user source because parsing failed.
- Deduplicate by preserved source, PMID, normalized DOI, or bibliographic key as applicable.
- Do not claim indexed text when extraction returned no content.

## Failure Handling
- Missing paths are recorded as failures with the original path.
- Unsupported formats are preserved and reported with `unsupported_format`.
- Malformed structured files produce warnings without aborting unrelated files.

## Related Contracts
- `shared/contracts/paper_record.schema.json`
- `shared/contracts/kb_passport.schema.json`
- `shared/protocols/data-access-levels.md`
- `shared/protocols/ui-escalation.md`
