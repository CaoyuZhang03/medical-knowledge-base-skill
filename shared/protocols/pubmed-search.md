# PubMed Search Protocol

Natural-language topic workflow:

1. Read `assets/prompts/search-query-generation.md`.
2. Generate a PubMed expression using MeSH + free terms.
3. Show the exact expression and ask for confirmation.
4. Search only after confirmation.
5. Write results to `candidate_papers`.

Direct PubMed query workflow:

1. Treat the input as the query when it already uses PubMed syntax or the user says it is a search expression.
2. Search directly unless the user asks for refinement.
3. Write results to `candidate_papers`.

Never auto-ingest PubMed search results.
