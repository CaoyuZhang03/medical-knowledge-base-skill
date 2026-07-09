# Workflow: quality-audit

## Trigger
Use when validating this skill, checking a KB for consistency, running evals, or preparing to claim the skill works.

## Inputs
- Skill root.
- Optional KB path.

## Required Decisions
- None for read-only validation.
- Ask before repairing or deleting data.

## Data Access Level
Audits can read all levels but should not mutate unless a repair action is explicitly requested.

## Steps
1. Run `python scripts/validate_contracts.py`.
2. Run `python scripts/run_evals.py`.
3. Run `python -m pytest scripts/test_kb_core.py -q`.
4. If a KB path is provided, run `python scripts/kb.py audit <kb_path>`.
5. Run the system `quick_validate.py` against the skill folder before finalizing.

## CLI/API Calls
- `kb audit <kb_path>`
- `scripts/validate_contracts.py`
- `scripts/run_evals.py`
- `scripts/test_kb_core.py`

## Outputs
- Contract validation report.
- Eval report.
- Test results.
- Optional KB audit.

## Quality Gates
- Do not claim completion without fresh validation output.

## Failure Handling
- Fix schema or fixture failures before reporting the skill ready.

## Related Contracts
- All files in `shared/contracts/`.
