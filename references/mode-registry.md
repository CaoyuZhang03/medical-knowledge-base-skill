# Medical Knowledge Base Mode Registry

This is the single source of truth for primary routing after durable local-KB intent has been established.

| Mode | Trigger | Oversight | UI Default | Main Workflow |
|---|---|---:|---:|---|
| `init-kb` | Create, configure, or inspect a durable KB directory | High | No | `workflows/init-kb/workflow.md` |
| `import-local-files` | Import user-selected files into a KB | Medium | Yes | `workflows/import-local-files/workflow.md` |
| `pubmed-discovery` | Discover PubMed records for a KB candidate queue | High | After results | `workflows/pubmed-discovery/workflow.md` |
| `candidate-review` | Approve, reject, filter, or inspect candidates | High | Yes | `workflows/candidate-review/workflow.md` |
| `scheduled-updates` | Configure, run, enable, or disable recurring searches | High | Yes | `workflows/scheduled-updates/workflow.md` |
| `library-management` | List, delete, fetch PDFs for, or reindex approved papers | High | Yes | `workflows/library-management/workflow.md` |
| `knowledge-qa` | Retrieve approved evidence for all-KB or selected-paper QA | Medium | No | `workflows/knowledge-qa/workflow.md` |
| `pdf-acquisition` | Run or inspect open-access PDF tasks for KB papers | Medium | Yes | `workflows/pdf-acquisition/workflow.md` |
| `quality-audit` | Validate contracts, evals, schemas, or KB consistency | Low | No | `workflows/quality-audit/workflow.md` |

One-off literature lookup, standalone document summarization, and general medical questions are outside this registry unless the user explicitly ties them to a durable local KB.

When multiple modes are requested with a clear sequence, choose the first unresolved phase as primary and name the later phases. When the sequence is unclear, ask which outcome the user wants first.
