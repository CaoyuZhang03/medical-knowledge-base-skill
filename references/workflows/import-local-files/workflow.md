# Workflow: import-local-files

## Trigger
Use when the user uploads or points to local PDFs, DOCX, Markdown, TXT, CSV/Excel, RIS, BibTeX, or PMID lists.

## Inputs
- Existing or new KB path.
- One or more local file paths.

## Required Decisions
- Ask whether to initialize the KB if the path does not exist.
- Ask before importing a very large folder recursively.

## Data Access Level
Input files are `raw`; successfully inserted records become `approved` because local import is user-provided.

## Steps
1. Ensure the KB exists with `kb init`.
2. Run `python scripts/kb.py import <kb_path> <files...>`.
3. For PDFs/DOCX/TXT/MD, extract text and add chunks.
4. For tables/bibliographies/PMID lists, create paper records and enrich later when possible.
5. Run `kb audit` after import.

## CLI/API Calls
- `kb import <kb_path> <files...>`
- `kb library list <kb_path>`
- `kb audit <kb_path>`

## Outputs
- Paper records in `papers`.
- Copied files under `files/uploads/`.
- Extracted text under `files/extracted-text/`.
- Retrieval chunks in `chunks`.

## Quality Gates
- Never discard source files after import.
- If text extraction fails, still preserve the file and mark the record as imported without indexed text.

## Failure Handling
- Missing files should fail loudly with the path.
- Unsupported formats should be preserved as uploads but may have no extracted text.

## Related Contracts
- `shared/contracts/paper_record.schema.json`
- `shared/contracts/kb_passport.schema.json`
