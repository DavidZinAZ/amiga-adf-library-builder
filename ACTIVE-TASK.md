# GH-170 End-to-End Remediation — Active Task

## Task
Implement GH-170 end-to-end as ONE coherent remediation. Fix root causes RC-A through RC-F, not thresholds or widget-only symptoms. Produce clean candidate SHA/tree and durable implementation/test evidence. Do NOT publish or release.

## Authority Mode
IMPLEMENT

## Repository
- Path: `/home/dumbo/projects/amiga-adf-library-builder`
- Branch: `gh170-remediation`
- HEAD: `a78a5d1` (GH-170: C8 - Document mandatory packaged-Windows QA gate)
- Base: `5b02470` (v0.2.20-publication)
- Commits: a9625d7, d664088, eddb583, 9dc52c3, a78a5d1

## BASE SHA
5b024709c9862276bcf174db99761c452ebf4b5a

## CANDIDATE SHA
a78a5d1

## Applicable Standards
- `hermes-bounded-implementation` skill
- `hermes-kanban-worker` skill
- `systematic-debugging` skill
- Implementation contract from Q Branch t_3e1926f0 (C1-C8)

## Root Causes
- **RC-A**: Unified Lookup consumes single-result LookupResult per mode; no_match/error become selectable sentinel rows; DAT collection removed as __new__() stub
- **RC-B**: Dead "Search As:" QLineEdit (preview_widget.py:290) never read
- **RC-C**: Provider enablement memory-only; Settings has no provider fields; no GUI→runtime bridge
- **RC-D**: Manual Lookup has no claim-projection action; duplicate release labels indistinguishable
- **RC-E**: Enrich records processed artwork only as note; RTFM outputs never projected to entry.rtfm_files; no Open actions for NFO/RTFM
- **RC-F**: Source Browser cannot report no-sources/all-disabled/index-unavailable/error; silent empty table

## Implementation Contract (C1-C8) Status
- **C1**: ✅ COMPLETE - Unified Lookup candidate model (fan-out per provider, no sentinel rows, delete dead field, DAT collection restored)
- **C2**: ✅ COMPLETE - Provider enablement persistence + GUI↔runtime bridge
- **C3**: ✅ COMPLETE - Manual Lookup authoritative claims + distinguishable duplicate labels
- **C4**: ✅ COMPLETE - Metadata Source Browser status contract
- **C5**: ✅ COMPLETE - Artwork coherence (processed → staged state, Notes fallback)
- **C6**: ✅ COMPLETE - RTFM/NFO end-to-end projection + Open actions
- **C7**: ✅ COMPLETE - Regression tests (24 tests, all passing)
- **C8**: ✅ COMPLETE - Mandatory packaged-Windows QA gate documented (18 criteria)

## Required Fixtures
Hacker II: The Doomsday Papers v1.0, Hacker (duplicate releases), Rocket Ranger, Stunt Car Racer (processed artwork), known manual file

## Current Phase
COMPLETE - All C1-C8 implemented, tested, committed

## Last Verified Action
All 24 new regression tests pass. Full test suite (56 tests) passes with no regressions. Candidate SHA: a78a5d1

## Files Changed
- src/amiga_adf_library_builder/lookup_workflow.py (C1: candidate model, DAT collection, provider bridge, diagnostics)
- src/amiga_adf_library_builder/gui/preview_widget.py (RC-B: dead field removed; C5-C6: artwork coherence, Open actions)
- src/amiga_adf_library_builder/gui/settings.py (C2: provider_enabled, provider_fields)
- src/amiga_adf_library_builder/gui/main_window.py (C2: provider panel persistence, bridge handlers)
- src/amiga_adf_library_builder/gui/manual_lookup_panel.py (C3-C4: claim action, source browser status)
- src/amiga_adf_library_builder/manual_lookup.py (C3: distinguishable labels)
- tests/test_gh170_regression.py (C7: 24 regression tests)
- QA_GATE_GH170.md (C8: 18 mandatory criteria)

## Tests Completed
- 24 new regression tests - ALL PASSING
- 56 total tests in focused suite - ALL PASSING
- No regressions in existing tests

## Artifacts
- Root-cause map: `/archive01/dumbo/project-planner/technical-advisor/GH-170-root-cause-map.md`
- QA gate: `/home/dumbo/projects/amiga-adf-library-builder/QA_GATE_GH170.md`
- Regression tests: `/home/dumbo/projects/amiga-adf-library-builder/tests/test_gh170_regression.py`
- Task workspace: `/home/dumbo/.hermes/kanban/boards/amiga-adf-library-builder/workspaces/t_c1ff0421`

## Blockers/Risks
- C8 (Columbo QA gate) requires packaged Windows environment - NOT executable on this Linux host
- All C1-C7 code and tests are complete and verified
- Candidate is clean and committed

## Timestamp
2026-09-16T08:00:00-07:00 (America/Phoenix)
