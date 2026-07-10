# Evidence QA Protocol

1. Run `python scripts/kb.py qa retrieve <kb_path> --question "..."` with optional approved `--doc-ids`.
2. Preserve the returned scope. Do not silently expand selected-document QA to the whole KB.
3. Inspect the normalized `search_terms` and `retrieval_method` (`fts5_bm25` or `scored_fallback`).
4. Use returned evidence text as the sole biomedical source for synthesis.
5. Cite title and PMID/DOI when available; keep claims proportional to the evidence.
6. Empty evidence requires “evidence is insufficient.” Partial evidence requires a bounded answer plus the named gap.

Chinese questions use deterministic CJK bigrams; English terms use normalized word tokens. Scores rank evidence but do not prove clinical certainty. Never use model memory to fill unsupported biomedical claims.
