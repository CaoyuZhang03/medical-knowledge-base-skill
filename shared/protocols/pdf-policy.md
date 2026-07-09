# PDF Acquisition Policy

Automatically fetch only PDFs from:

- PubMed Central.
- Publisher pages that clearly expose an open-access PDF URL.
- User-uploaded local files.

Do not design or run workflows that bypass paywalls, institutional authentication, robots restrictions, or copyright limitations. When no open source is found, record `has_pdf=false` and leave acquisition to user upload.
