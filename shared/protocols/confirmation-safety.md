# Confirmation Safety Protocol

Protected actions default to preview:

- `candidate.reject`
- `library.delete`
- `library.reindex`
- `schedule.install`
- `schedule.uninstall`

The preview returns affected IDs/counts, details, expiry, and a signed token. The token binds the resolved KB path, action, sorted IDs, action details (including rejection reason or scheduler command), target-state digest, and expiration.

Execution rules:

1. Show the preview to the user.
2. Repeat the identical command with `--confirm <token>` only after explicit approval.
3. Reject invalid signatures, different paths/actions/IDs/details, expired tokens, and stale target state.
4. Request a new preview after any mismatch or state change.

The random secret lives at `<kb_path>/.confirmation-secret`, is best-effort owner-restricted, is ignored by the KB's `.gitignore`, and must never enter passports, logs, API errors, or source control.
