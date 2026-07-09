# Data Access Levels

Use these labels in workflows, audit reports, and evidence packets.

| Level | Meaning | Allowed Consumers |
|---|---|---|
| `raw` | User uploads, raw PubMed responses, PDF text, extracted full text | Import, indexing, metadata enrichment |
| `screened` | Search results filtered by IF/JCR/study type but not approved | Candidate review only |
| `approved` | User-approved literature and local documents | Library management and retrieval |
| `evidence_only` | Chunks returned by retrieval for a question | Codex answer synthesis |

Do not answer from `raw` or `screened` material unless the user explicitly asks to analyze unapproved candidates. Default QA scope is `approved`.
