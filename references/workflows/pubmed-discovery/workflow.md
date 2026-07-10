# Workflow: pubmed-discovery

## Trigger
Use when the user wants PubMed records discovered specifically for a durable local KB candidate queue, from either a natural-language topic or explicit PubMed expression.

## Inputs
- KB path.
- Topic or PubMed expression.
- Optional minimum IF, JCR quartiles, study types, year range, and maximum results.

## Required Decisions
- Topic input: generate an expression with the bundled prompt and obtain confirmation of the exact text.
- Explicit PubMed syntax: use it directly unless the user asks to refine it.
- Never interpret one-off literature research as durable-KB discovery without explicit intent.

## Data Access Level
PubMed XML is `raw`; enriched and filtered records written to `candidate_papers` are `screened`.

## Steps
1. Initialize or identify the target KB.
2. Classify topic versus explicit expression.
3. For a topic, read `assets/prompts/search-query-generation.md`, generate the expression, display it, and wait for user confirmation.
4. Search PubMed, normalize article/book records, enrich IF/JCR, classify study type, apply filters, and deduplicate by PMID, normalized DOI, or bibliographic key.
5. Persist only candidates and report inserted/duplicate counts plus filter provenance.
6. Because user selection is next, start or reuse the candidate-review UI and provide its URL.

## CLI/API Calls
- `kb pubmed search <kb_path> --query "<query>" --confirmed --max-results 20`
- `kb pubmed search <kb_path> --query "<query>" --confirmed --min-if 10 --jcr-quartile Q1 --study-type "随机对照试验" --year-from 2020 --year-to 2026`
- `kb candidates list <kb_path> --status pending`
- `kb serve <kb_path> --host 127.0.0.1 --port 0`

## Outputs
- Structured search result with confirmed query, filters, candidate IDs, and duplicate counts.
- Pending candidates containing PubMed provenance and enrichment.
- Candidate-review URL for selection.

## Quality Gates
- Never retrieve a topic-derived query before explicit confirmation.
- Never auto-ingest PubMed results into `papers`.
- Treat censored IF values such as `<0.1` conservatively for minimum-IF filtering.

## Failure Handling
- PubMed failure writes no partial candidate batch and preserves the query for retry.
- Missing JCR match leaves nullable enrichment fields; it does not discard the paper unless a filter requires them.
- Duplicate candidates are reported without creating repeated rows.

## Related Contracts
- `shared/contracts/candidate_record.schema.json`
- `shared/contracts/saved_search.schema.json`
- `shared/protocols/pubmed-search.md`
- `shared/protocols/candidate-review.md`
