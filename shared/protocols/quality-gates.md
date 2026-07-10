# Quality Gates

Blocking runtime gates:

- Topic-derived PubMed expressions require confirmation before retrieval.
- PubMed and scheduled records remain candidates until explicit approval.
- Scheduled runs create candidates only and honor due/enabled state.
- Automatic PDFs are open-access only and must validate before metadata changes.
- KB answers use retrieved approved evidence only.
- Reject, delete, reindex, scheduler install, and scheduler uninstall require a state-bound preview token.
- Operational candidate review, library management, local upload, saved-search configuration, and task monitoring provide a local UI URL when one is not already active.

Validation gates:

- `scripts/validate_contracts.py` checks schemas, workflow sections, assets, routing fixtures, and documented CLI options.
- `scripts/run_evals.py` checks prompt, JCR, study type, candidate state, scheduling, import, retrieval, and routing fixtures.
- `python -m pytest scripts -q -p no:cacheprovider` runs unit, integration, HTTP, safety, migration, and workflow-contract tests.
- The system skill validator must pass against the final skill path.
- UI changes require browser console, interaction, desktop, and mobile evidence.

Completion requires fresh evidence for every explicit requirement, not only green narrow tests.
