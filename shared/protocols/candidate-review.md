# Candidate Review Protocol

Candidate review is the boundary between `screened` and `approved`.

Required behavior:

- Show candidate IDs, title, journal, publication year, IF, JCR quartile, study type, and PDF status when available.
- Approve only IDs selected by the user.
- Reject only IDs selected by the user, with an optional reason.
- Preserve rejected rows for audit; do not delete them by default.
- Do not auto-approve candidates created by scheduled updates.
