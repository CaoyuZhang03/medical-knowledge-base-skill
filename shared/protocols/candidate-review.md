# Candidate Review Protocol

Candidate review is the only boundary that moves PubMed-discovered `screened` records into `approved` papers.

Required behavior:

- Use the local UI for multi-record inspection; provide its URL when one has not already been provided.
- Display ID, title, authors, journal, year, IF, JCR quartile, study type, and PDF availability/prospect.
- Approve only explicit selected IDs. Scheduled and PubMed results are never auto-approved.
- Approval writes the paper and abstract index transactionally, then creates optional open-access PDF tasks. PDF failure cannot roll back approval.
- Leave duplicate PMID/normalized DOI candidates pending and report them in `skipped`.
- Rejection preserves the candidate, reason, and decision timestamp.
- Rejection is protected: preview IDs/counts and reason, then execute with the matching fresh token.

Refreshing the page after each action must show authoritative candidate, library, and task counts.
