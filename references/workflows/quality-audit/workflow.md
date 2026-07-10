# Workflow: quality-audit

## Trigger
Use when the user asks to validate this skill, audit a durable KB, check consistency, or gather completion evidence.

## Inputs
- Skill root.
- Optional KB path.

## Required Decisions
- Read-only checks need no confirmation.
- Route actual repair, deletion, or reindex execution through the guarded library workflow.

## Data Access Level
May read all levels for validation but does not mutate KB state.

## Steps
1. Validate JSON schemas, workflow headings, assets, routing fixtures, and documented CLI contracts.
2. Run deterministic evals for query prompt, JCR, study type, candidate state, scheduling, import, retrieval, and routing.
3. Run the complete scripts test suite.
4. Audit an optional KB path and inspect reported counts/issues.
5. Run the system skill validator against the actual installation/worktree path.
6. For UI changes, inspect fresh desktop/mobile screenshots, browser console, and a real interaction.

## CLI/API Calls
- `kb audit <kb_path>`
- `scripts/validate_contracts.py`
- `scripts/run_evals.py`
- `python -m pytest scripts -q -p no:cacheprovider`
- `GET /api/audit`

## Outputs
- Contract, eval, test, validator, and optional KB audit reports.
- Requirement-by-requirement evidence for completion.

## Quality Gates
- Do not claim completion from narrow tests when broader requirements exist.
- Any missing or indirect evidence remains incomplete until verified.
- Keep validation deterministic and network-independent except explicit PubMed integration tests.

## Failure Handling
- Fix schema, command-contract, fixture, test, or runtime failures before completion.
- Report environmental blockers separately from product failures.

## Related Contracts
- All files under `shared/contracts/`.
- `shared/protocols/quality-gates.md`
