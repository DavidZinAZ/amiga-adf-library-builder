# ACTIVE-TASK — GH-119 CORRECTION-DEV

## Task
Implement ONLY the three Q Branch correction items for GH-119:
1. Release workflow hard-fails when tag version != canonical app version (Gap A)
2. Frozen Windows EXE receives real FileVersion/ProductVersion PE metadata (Gap B)
3. Runtime About/version lookup works inside frozen onedir/onefile builds (Gap C)

## Authority Mode
IMPLEMENT

## Repository
/path: /home/dumbo/projects/amiga-adf-library-builder/.worktrees/t_37e4a94c
Branch: wt/t_37e4a94c
HEAD: c7c35aa982f884efde61b53820a25d8ebab25a44 (matches origin/main)
BASE_SHA: c7c35aa982f884efde61b53820a25d8ebab25a44

## Applicable Standards
- Research report: /archive01/dumbo/project-planner/technical-advisor/gh119-correction-research.md (exact spec in Appendix A)
- pyproject.toml [project].version = "0.2.13"
- Existing test: tests/test_version_identity.py
- Existing workflow: .github/workflows/build-windows.yml

## Confirmed Decisions
- Implementation order: C (frozen-runtime) → B (PE metadata) → A (tag equality)
- All three fixes in one PR
- Generated files: `_frozen_version.py` and `_version_info.txt` must be gitignored
- `skip_tag_version_check` workflow_dispatch bypass input included for Gap A

## Current Phase
Phase 1: Implement Gap C (frozen-runtime version safety)

## Last Verified Completed Action
None yet — implementation starting now.

## Exact Next Safe Action
1. Create `src/amiga_adf_library_builder/_frozen_version.py` (generated, gitignored)
2. Refactor `src/amiga_adf_library_builder/_version.py` to add `sys.frozen` guard
3. Modify `tools/build_windows.py` to generate `_frozen_version.py`
4. Add `_frozen_version.py` to `.gitignore`
5. Add test `test_frozen_version_fallback_when_sys_frozen` to `tests/test_version_identity.py`
6. Fix `test_no_stale_hardcoded_versions` to skip `_frozen_version.py`

## Files Intended to Change
- src/amiga_adf_library_builder/_version.py (Gap C)
- tools/build_windows.py (Gap C + Gap B)
- .gitignore (Gap C + Gap B)
- tests/test_version_identity.py (Gap C + Gap B tests)
- .github/workflows/build-windows.yml (Gap A)

## Files Independently Verified as Changed
None yet

## Tests Completed and Outstanding
- pytest tests/test_version_identity.py (baseline, then after each gap)

## Artifact and Rollback Locations
- Active task state: ACTIVE-TASK.md (this file)
- Research report: /archive01/dumbo/project-planner/technical-advisor/gh119-correction-research.md

## Blockers, Risks, and Uncertainties
- `test_no_stale_hardcoded_versions` regex `r'__version__\s*=\s*"[0-9]"'` will match `_frozen_version.py` — must skip it in the scan
- The test for frozen path uses `sys.frozen = True` injection which requires careful cleanup
- Gap A workflow YAML change is untestable locally — qualification by inspection only

## Timestamp
2026-09-13T18:10:00 America/Phoenix
