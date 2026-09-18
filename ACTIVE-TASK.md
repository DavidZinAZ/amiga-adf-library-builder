# ACTIVE-TASK.md — GH-183 Fix Phantom RTFM, Doc Acquisition, Title Export + Manual Lookup

## Task
t_5351d6b7 — P0 — CASE — FIX PHANTOM RTFM DOC ACQUISITION TITLE EXPORT + MANUAL LOOKUP
GitHub issue: #183
Consumes Q task t_8774309f. Fixes five v0.2.25 production-path failures.

## Authority Mode
IMPLEMENT

## Repository Path
/home/dumbo/projects/amiga-adf-library-builder

## Branch
publish/canonical-identity-rtfm

## HEAD
d0b9f6633e69171be31dc20c719f1f3b1f623f4b

## BASE SHA
62e4b683255d5328463ab91fed1b3118e9686a1e (origin/main)

## Changes Made So Far
1. RC-2 (export naming): `exporter.py` - replaced deprecated `release_basename()` fallback with `_get_canonical_basename()`; added `library_root` param to `export_release`; passed `library_root` from `export_all`
2. RC-2 (export naming): `pipeline.py` - replaced `_release_basename_with_warn` to use `canonical_release_name`; updated call site to pass `library_root`; fixed `else` branch in preview path to use `canonical_release_name`
3. RC-2 (export naming): `enrich.py` - replaced `release_basename` in NFO and artwork paths with `canonical_release_name`; added `library_root` param to `enrich_group` and `enrich_all`; passed `library_root` from pipeline
4. RC-3 (test fix): `tests/test_manual_lookup.py` - updated `test_list_browse_releases_and_disks` to expect `[entity_id]` suffix per GH-170 RC-D
5. All 171+ tests pass (pre-existing failure in `test_cli_rtfm_integration.py` unrelated to changes)

## Remaining Deprecation Warnings (pre-existing, non-blocking)
- `rtfm.py:_group_identity` uses `release_basename` - needs `library_root` param pass-through
- `canonical_naming.py:export_name_for_release_group` fallback uses `release_basename` - intrinsic compatibility fallback
- `test_cli_rtfm_integration.py::test_cli_build_enables_rtfm_end_to_end` - pre-existing JSON serialization failure

## Tests Added/Updated
- `tests/test_manual_lookup.py::test_list_browse_releases_and_disks` - updated assertions for `[entity_id]` labels

## Required Next Steps
- Add Hacker/Hacker II/Hot Rod production pipeline regression tests
- Add Rocket Ranger/Stunt Car Racer/Ultima IV regression tests
- Add export Title naming tests
- Add observability logging for per-release canonical identity/provider IDs/doc candidates

## Timestamp
2026-09-17T19:15:00-07:00
