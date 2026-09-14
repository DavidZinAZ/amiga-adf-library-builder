# ACTIVE-TASK.md — GH-143 Canonical Lifecycle Implementation

## Task
GH-143 — CASE CANONICAL LIFECYCLE IMPLEMENTATION
Implement P1 canonical.db lifecycle remediation end-to-end per approved Q Branch design.

## Authority Mode
IMPLEMENT

## Repository Path
/home/dumbo/projects/amiga-adf-library-builder

## Worktree Path
/home/dumbo/.hermes/kanban/boards/amiga-adf-library-builder/workspaces/t_50f99dfb

## Branch
gh143-implement-t50f99dfb

## HEAD
8186d8122031f96c0ec26ee2ad5fcef83b483221 (origin/main)

## BASE SHA
8186d8122031f96c0ec26ee2ad5fcef83b483221

## Applicable Standards
- hermes-bounded-implementation
- hermes-kanban-worker
- design doc: /home/dumbo/.hermes/kanban/boards/amiga-adf-library-builder/attachments/t_816862a5/GH143-QBRANCH-CANONICAL-LIFECYCLE-DESIGN.md

## Confirmed Decisions
- Alt C adopted: record-of-authority with scoped release retirement
- SEED=15 tier between PARSER(10) and DAT(20)
- Soft-delete `retired` flag on release rows
- CLI passes `library_state_path` when available
- Schema migration v1→v2 via existing `_migrate()` pattern
- 7-step implementation plan from design doc §17
- Stop conditions: release_id hash invariant, migrate_staged_library parameterization, soft-delete vs FK cascade

## Unresolved Assumptions
- U1: Descriptor columns not read directly by production code (only `game_id` from `release_row`), but must stay consistent
- U2: Performance of re-resolving on every migrate_staged_library call — bounded by small DAT source counts
- U3: `manual_lookup.py` should show retired releases with flag (design doc recommends this)

## Current Phase
Step 1 — Schema migration (SCHEMA_VERSION 1→2, add `retired` column + index)

## Last Verified Completed Action
None yet — implementation just starting

## Exact Next Safe Action
Implement Step 1: Add SEED tier, SCHEMA_VERSION=2, `retired` column + index to `_migrate()`

## Files Intended to Change
- src/amiga_adf_library_builder/canonical.py (Steps 1-6)
- src/amiga_adf_library_builder/pipeline.py (Steps 3, 5)
- src/amiga_adf_library_builder/cli.py (Step 7)
- tests/test_canonical_lifecycle.py (new test file)

## Files Independently Verified as Changed
(None)

## Files Attempted but Not Changed
(None)

## Tests Completed and Outstanding
- Existing: test_canonical_model.py, test_canonical_naming.py, test_canonical_naming_production.py
- New: tests/test_canonical_lifecycle.py (12+ tests)

## Commands or Processes Still Running
(None)

## Artifact and Rollback Locations
- /home/dumbo/.hermes/kanban/boards/amiga-adf-library-builder/workspaces/t_50f99dfb/

## Blockers, Risks, and Uncertainties
- Branch name `gh143-canonical-lifecycle` already existed in main repo; using `gh143-implement-t50f99dfb`
- Must verify release_id hash invariant is preserved throughout

## Timestamp
2026-09-14T05:14:00-07:00 (America/Phoenix)
