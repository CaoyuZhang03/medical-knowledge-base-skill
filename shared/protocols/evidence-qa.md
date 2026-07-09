# Evidence QA Protocol

For knowledge-base QA:

1. Retrieve evidence with `python scripts/kb.py qa retrieve <kb_path> --question "..."`
2. Use the returned evidence packet as the sole source for the answer.
3. Cite paper title plus PMID or DOI when available.
4. If the evidence packet is empty, say evidence is insufficient.
5. If evidence is partial, answer only what the evidence supports and state the gap.

Do not use model memory to fill biomedical claims.
