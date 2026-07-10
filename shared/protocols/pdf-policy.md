# PDF Acquisition Policy

Allowed automatic sources:

- A syntactically valid PubMed Central identifier.
- An explicit HTTPS URL identified as an open-access PDF.
- A user-uploaded local PDF.

Disallowed behavior:

- Treating an ordinary article page as a PDF source.
- Bypassing a paywall, authentication, institutional access, robots controls, or copyright restrictions.
- Marking `has_pdf=true` before validated PDF bytes exist locally.

Every automatic attempt is an auditable `pdf_fetch` task. `not_found` is a completed result, network/content errors are failed results, and retries create new task history. Approval remains committed even when its follow-up PDF task fails. `pdf_fetch.enabled=false` prevents automatic task creation during approval.
