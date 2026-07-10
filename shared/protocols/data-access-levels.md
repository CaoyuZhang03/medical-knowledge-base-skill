# Data Access Levels

Use these labels in workflows, persistence, audits, and evidence packets.

| Level | Examples | Allowed Consumers |
|---|---|---|
| `raw` | User uploads, preserved bibliography files, PubMed XML, downloaded PDFs, extracted full text | Import, normalization, enrichment, indexing |
| `screened` | Enriched/filtered PubMed and scheduled-search candidates | Candidate display and review only |
| `approved` | Explicitly approved candidates and user-selected local imports | Library operations and retrieval |
| `evidence_only` | Ranked chunks returned for one question and scope | Codex answer synthesis |

Rules:

- Provenance moves forward; do not rewrite `raw` input as if it originated at a later level.
- Local user imports are `approved` because selection of the files is explicit ingest intent.
- PubMed and scheduled results remain `screened` until candidate approval.
- Normal KB QA retrieves only from `approved` chunks and exposes only `evidence_only` packets to synthesis.
- An explicit request to analyze pending candidates is a candidate-review analysis, not approved-KB QA.
