# ACTIVE-TASK.md — GH-192 REM-2

## Mode: IMPLEMENT

### Authorized base and workspace
- BASE SHA: 332527722b77900287e0c07ce1861c76cad36483 (`origin/main` after fetch)
- Worktree: /home/dumbo/projects/amiga-adf-library-builder-wt
- Initial HEAD matched BASE; working tree was clean.
- Git action planned: create a task branch, then one local implementation commit after verification. No push.

### Scope
Repair the four GH-192 REM-2 production defects: packaged cache resolution, DAT provider diagnostics, generic online metadata matching and provider-level attempt observability. Preserve the existing release-key diagnostic association in `pipeline.py`.

### Verified progress
- Focused `pytest tests/test_gh192_rem1.py -v`: 36 passed.
- Related regression suites: 150 passed, 2 deselected; the two deselected tests fail identically on BASE 3325277 (artwork `resized` expectation mismatch).
- Full `pytest -q` is running as background process `proc_1b823fb7b37f`; result pending.
- `git diff --check` passed before latest test-only changes; rerun before commit.

### Candidate
Pending full validation, diff review, and local commit.
