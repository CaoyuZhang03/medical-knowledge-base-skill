# PubMed Search Protocol

## Topic Input

1. Read `assets/prompts/search-query-generation.md`.
2. Generate a PubMed expression using MeSH plus free terms.
3. Display the exact expression and wait for user confirmation.
4. Retrieve only the confirmed expression.

## Explicit Expression

Use the text directly when it already contains PubMed syntax or the user identifies it as a search expression. Refine only when asked.

## Normalization And Persistence

- Normalize PubMed articles and book articles, all abstract sections, authors, DOI, PMCID, ISSN/EISSN, publication types, MeSH, year, OA link, and raw XML.
- Enrich IF/JCR by normalized journal/ISSN matching and classify the configured study-type taxonomy.
- Apply minimum IF, JCR, study type, and year filters before insertion. Treat censored IF conservatively.
- Deduplicate atomically by PMID, normalized DOI, and bibliographic key across approved and candidate tables.
- Persist the confirmed query, filters, and optional saved-search ID in candidate provenance.
- Write candidates only. Candidate selection and approval remain separate.
