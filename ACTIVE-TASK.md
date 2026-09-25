# ACTIVE-TASK.md — GH-192 REM-2

## Mode: IMPLEMENT

### Authorized base and workspace
- BASE SHA: 332527722b77900287e0c07ce1861c76cad36483 (`origin/main` after fetch)
- Worktree: /home/dumbo/projects/amiga-adf-library-builder-wt
- Initial HEAD matched BASE; working tree was clean.
- Git action planned: create a task branch, then one local implementation commit after verification. No push.

### Scope
Repair the four GH-192 REM-2 production defects: packaged cache resolution, DAT provider diagnostics, generic online metadata matching and provider-level attempt observability. Preserve the existing release-key diagnostic association in `pipeline.py`.

### Validation
- `pytest tests/test_gh192_rem1.py -v`: 36 passed on implementation commit 1aebc1d9b2d44e522cb4199d531dd626f1a2acb5.
- Related suite: 150 passed, 2 deselected; the deselected artwork tests fail identically on BASE 3325277.
- Broader full-suite attempt was stopped after 10m at 44% with four failures observed. All four exact test failures were rerun from an archive of BASE 3325277 and reproduced there; no new regressions established. Full suite remainder is unverified.
- `git diff --check BASE..implementation-commit`: passed.
- Release-key association remains `_enrich_by_key` based; positional zip regression was not introduced.

### Candidate
- Implementation commit: 1aebc1d9b2d44e522cb4199d531dd626f1a2acb5
- Parent / BASE: 332527722b77900287e0c07ce1861c76cad36483
- Branch: `dev/gh192-rem2-packaged-gui-production-regressions`
- No push; final checkpoint documentation commit will follow.

## Mode: COMPLETE

Source and test changes are committed. Focused acceptance tests pass; pre-existing baseline failures and the incomplete full-suite run are recorded above. QA handoff must verify the final branch HEAD and should treat full-suite coverage beyond the recorded related suites as outstanding.
