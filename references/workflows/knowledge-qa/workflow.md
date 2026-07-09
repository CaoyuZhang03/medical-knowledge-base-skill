# Workflow: knowledge-qa

## Trigger
Use when the user asks a question based on the local knowledge base, either across the whole KB or selected papers.

## Inputs
- KB path.
- User question.
- Optional paper IDs.

## Required Decisions
- Ask which KB to use if the path is unknown.
- Ask for paper IDs only if the user wants a selected-document answer but has not identified documents.

## Data Access Level
Answers use `evidence_only` packets retrieved from `approved` records.

## Steps
1. Run `kb qa retrieve <kb_path> --question "<question>"`.
2. If scoped, pass `--doc-ids`.
3. Read the evidence packet.
4. Answer only from retrieved evidence.
5. Cite title plus PMID/DOI when available.

## CLI/API Calls
- `kb qa retrieve <kb_path> --question "..." --doc-ids <ids...>`

## Outputs
- Evidence packet.
- Codex answer with source citations.

## Quality Gates
- Empty evidence means say evidence is insufficient.
- Do not use model memory for biomedical claims.

## Failure Handling
- If no chunks exist, tell the user to import/reindex documents before QA.

## Related Contracts
- `shared/contracts/evidence_packet.schema.json`
- `shared/protocols/evidence-qa.md`
