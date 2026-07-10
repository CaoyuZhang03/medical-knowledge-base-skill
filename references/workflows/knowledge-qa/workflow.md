# Workflow: knowledge-qa

## Trigger
Use when the user explicitly asks a question grounded in all approved papers or selected paper IDs from a durable local KB.

## Inputs
- KB path.
- Question.
- Optional approved paper IDs.

## Required Decisions
- Ask which KB to use if the path is unknown.
- Ask for IDs only when selected-paper scope is requested but no documents are identified.

## Data Access Level
Codex consumes `evidence_only` packets retrieved from `approved` chunks.

## Steps
1. Run evidence retrieval with whole-KB scope or explicit `--doc-ids`.
2. Inspect `retrieval_method`, `search_terms`, and returned chunks.
3. If evidence is empty or insufficient, state that limitation and stop; do not fill biomedical claims from model memory.
4. Synthesize only supported statements and cite paper title plus PMID or DOI when present.
5. Keep selected-document scope exact.

## CLI/API Calls
- `kb qa retrieve <kb_path> --question "..."`
- `kb qa retrieve <kb_path> --question "..." --doc-ids <ids...>`

## Outputs
- Evidence packet with scope, method, terms, evidence, scores, provenance, and answer policy.
- Evidence-grounded answer or explicit insufficiency response.

## Quality Gates
- Never answer this workflow directly from raw uploads, pending candidates, general model memory, or an empty packet.
- Do not broaden selected IDs without user approval.

## Failure Handling
- If chunks are absent, recommend guarded reindex or importing extractable source text.
- If only partial evidence is available, answer the supported portion and name the gap.

## Related Contracts
- `shared/contracts/evidence_packet.schema.json`
- `shared/protocols/evidence-qa.md`
- `shared/protocols/data-access-levels.md`
