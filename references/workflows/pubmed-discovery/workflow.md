# Workflow: pubmed-discovery

## Trigger
Use when the user wants to search PubMed from a natural-language topic or an explicit PubMed search expression.

## Inputs
- KB path.
- Topic or PubMed query.
- Optional IF, JCR, study-type, and year filters.

## Required Decisions
- Natural-language topic: generate query from `assets/prompts/search-query-generation.md` and ask for confirmation before search.
- Explicit query: search directly unless the user asks to refine it.

## Data Access Level
Raw PubMed results are `raw`; filtered results saved to `candidate_papers` are `screened`.

## Steps
1. Classify input as topic or query.
2. For topic, generate PubMed expression and request confirmation.
3. Run `python scripts/kb.py pubmed search <kb_path> --query "<query>" --confirmed`.
4. Enrich candidates with IF/JCR and study type when data is available.
5. Show candidates for review; do not ingest automatically.

## CLI/API Calls
- `kb pubmed search <kb_path> --query "<query>" --confirmed`
- `kb candidates list <kb_path> --status pending`

## Outputs
- Pending candidate records.
- Search result summary with candidate IDs.

## Quality Gates
- Topic search confirmation is mandatory.
- PubMed search results never bypass candidate review.

## Failure Handling
- If PubMed is unavailable, preserve the query and report that no candidates were written.
- If JCR matching fails, keep the candidate with null IF/JCR fields.

## Related Contracts
- `shared/contracts/candidate_record.schema.json`
- `shared/protocols/pubmed-search.md`
