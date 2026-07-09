# Medical Knowledge Base Mode Registry

Single source of truth for workflow modes.

| Mode | Purpose | Oversight | Main Workflow |
|---|---|---:|---|
| `init-kb` | Create a local SQLite/file-based biomedical KB | High | `workflows/init-kb/workflow.md` |
| `import-local-files` | Import PDFs, DOCX, Markdown, TXT, tables, bibliographies, or PMID lists | Medium | `workflows/import-local-files/workflow.md` |
| `pubmed-discovery` | Generate or run PubMed searches and place results in candidate review | High | `workflows/pubmed-discovery/workflow.md` |
| `candidate-review` | Approve, reject, filter, or ingest candidate papers | High | `workflows/candidate-review/workflow.md` |
| `scheduled-updates` | Configure and run recurring PubMed updates | High | `workflows/scheduled-updates/workflow.md` |
| `library-management` | List, delete, repair, or reindex approved papers | High for destructive actions | `workflows/library-management/workflow.md` |
| `knowledge-qa` | Retrieve evidence for Codex answer synthesis | Medium | `workflows/knowledge-qa/workflow.md` |
| `pdf-acquisition` | Fetch or audit open-access PDFs | Medium | `workflows/pdf-acquisition/workflow.md` |
| `quality-audit` | Validate contracts, evals, schema shape, and KB consistency | Low | `workflows/quality-audit/workflow.md` |

When ambiguous, prefer a clarification question over routing to a destructive or ingesting workflow.
