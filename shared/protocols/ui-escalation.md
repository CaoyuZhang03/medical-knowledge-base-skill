# UI Escalation Protocol

The local UI is the preferred surface for:

- Candidate review and batch selection.
- Library inspection, deletion preview, reindex, and PDF status.
- Local multi-file upload.
- Saved-search creation, enable/disable, and manual run.
- Background task monitoring and retry.

When one of these becomes the user's next action and no live URL has already been provided for the same KB:

1. Start `python scripts/kb.py serve <kb_path> --host 127.0.0.1 --port 0` as a hidden background process.
2. Read the actual URL from server output.
3. Provide a clickable URL immediately, before asking the user to operate the page.
4. Reuse that URL for later surfaces in the same task.

Default binding is loopback-only. Do not represent the UI as a hosted or multi-user service. The page calls only same-origin local APIs and performs no LLM chat; Codex handles evidence-grounded answer synthesis separately.
