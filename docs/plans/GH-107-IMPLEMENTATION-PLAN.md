# GH-107 Implementation Plan

**EPIC:** Metadata Source Manager, persistent hash curation, canonical naming and 1G1R export engine.

**Repository:** https://github.com/DavidZinAZ/amiga-adf-library-builder

**GH-107 status: OPEN** (EPIC — must stay open until all slices land).

## Current origin/main SHA

`8963ebb4ec15a3f15bda861e2089c4b933c10dcb` (post-repair merge of PR #113;
pre-repair main was `05c77b79318ddd73f4011e3ee44d13f8cdb3d872`).

## Architecture Principles & Invariants

- **Identity Layer:** Content hashes (SHA-256 primary, SHA1/MD5/CRC32 secondary) are immutable identifiers.
- **Knowledge Layer:** DAT sources (TOSEC, No‑Intro, Fresh1G1R, custom) provide canonical metadata.
- **Curation Layer:** User decisions (move, rename, grouping) outrank automated matches and persist across sessions.
- **Export Layer:** TOSEC‑style naming and 1G1R export are policies, not destructive actions.
- **Persistence:** All hash‑based identity and curation records are stored in a local SQLite index; the raw DAT files remain read‑only.
- **Incremental Indexing:** Only changed DAT files are re‑indexed.
- **Windows Qualification:** Slice 1 must be runnable on Windows GUI with the new metadata source manager UI.

## Ordered Slices (proposed)

| Slice | Description | Status |
|------|-------------|--------|
| **1** | **Metadata Source Manager + DAT indexing/storage foundation** — GUI tab "Metadata Sources" (Add DAT / Add Folder / Rescan / Reindex Changed / Remove), local SQLite index `metadata_source_entries`, source enable/disable controls, raw DAT read-only, synthetic `tests/fixtures/sample.dat` parser tests. | **DONE** (semantic PASS after provenance repair — see Slice 1 closeout below) |
| **2** | Persistent hash‑based file identity & curation memory. | **DONE** (semantic PASS — see Slice 2 closeout below; APPLICATION_SHA `f0365799d0f551c30a9870150d0a27e7e35a2772`, merge `f204c2f...`) |
| **3** | Canonical game/release data model & provenance. | **DONE** — PUBLISHED (see Slice 3 closeout below; APPLICATION_SHA `85fd0136733dd2a826e3f8f85e33cc61c48471f3`, merge `8e85a44...`) |
| **4** | Unified manual lookup UI. | PLANNED |
| **5** | Canonical naming / export policy layer. | PLANNED |
| **6** | 1G1R selection & export engine. | PLANNED |

## Slice 1 closeout (semantic, after repair t_9503c6de)

Provenance chain (all verified, not commit-message claims):

- Original DEV candidate: `45ac790d80e2c093c9a15b6420b1e2727c298aee` (tree `7c2d3b5f2bf031393619199fbc197111ed4e47e6`).
- PR #111 (squash-merged as `8210bd6d550569a29138a60801ce7f27684fbd55`): head `7029798` = candidate + 3 blanket test-skip lines added by QA after the candidate — suppressing 26 real GH-88/89/93 regression tests. No CI lane runs pytest, so the skips protected nothing.
- PR #112 (merged as `05c77b7`): empty content diff; a commit-message SHA reference only — not provenance.
- Repair commit / **FINAL_APPLICATION_SHA**: `cc7c6adb57f089f52397e4a7b7ee309b66ae9617` (branch `repair/gh-107-slice1-provenance`, PR #113) — removes the blanket skips so the tree is byte-identical to the original candidate: `git diff 45ac790 cc7c6ad` is empty; both trees hash to `7c2d3b5f2bf031393619199fbc197111ed4e47e6`.
- Merge: `8963ebb4ec15a3f15bda861e2089c4b933c10dcb` (PR #113, true merge commit —
  `cc7c6ad` is a DIRECT ancestor of origin/main: `git merge-base --is-ancestor
  cc7c6adb57f089f52397e4a7b7ee309b66ae9617 origin/main` exits 0). Full proof chain:
  SLICE1-REPAIR.md in the operator archive.

Verification evidence:

- Local (Linux, PySide6 6.11.2, pytest 9.1.1, `QT_QPA_PLATFORM=offscreen`):
  `tests/test_metadata_source.py` 23 passed; restored `test_gh88_89_lookup_workflow.py` 21 passed and `test_gh93_dev_r2_selection.py` 21 passed; GUI import + "Metadata Sources" tab construction verified headless; per-file full suite green except 4 failures reproduced byte-identically on pristine pre-repair main (pre-existing, unrelated to Slice 1).
- Raw DAT read-only, SQLite persistence/reopen, add/rescan/reindex/enable-disable/remove are covered by the passing `test_metadata_source.py` suite.
- Windows (PR #113 head `f30384446e6e9ebb4769892e0999603a72bef97d`): `Build Windows GUI` run 34647172197 SUCCESS (all steps incl. onedir+onefile build and GUI smoke test); `QA Windows real execution` run 34647172196 SUCCESS (all steps; downloaded `report.json` shows every real-Windows step `ok: true` — clean launch, portable layout under a path with spaces, settings persistence, theme switch, logs, no-crash failure path). Full evidence in `/archive01/dumbo/project-planner/amiga-adf-library-builder/GH-107/SLICE1-REPAIR.md`.

## Slice 2 — DONE (2026-09-11)

- Branch: `dev/gh-107-slice2-549da21` (BASE `549da214327ec1d51eb929e65aba17219a7c8a92`).
- QA PASS for APPLICATION_SHA `f0365799d0f551c30a9870150d0a27e7e35a2772`
  (QA task t_3e2bc459: store probes 17/17, real-flow integration 11/11,
  focused suites 144/144 green; only pre-existing BASE-reproduced failures;
  Windows packaged qualification deferred to publication CI Windows lanes).
- FileIdentityStore wired into rescan/carry_over: GUI instantiates
  FileIdentityStore(data_dir/identity.db) and threads it with original_dir through
  build_staged_library_from_result into StagedLibrary.carry_over's content-hash
  second pass; release_key-only fallback preserved (GH-99 behavior).


Persistent hash-based file identity and curation memory:
SHA-256/SHA-1/MD5/CRC32 file identity records persisted in the same SQLite store,
surviving rescan/reopen, with curation decisions bound to content hashes.

Do not start Slice 2 work until this plan's Slice 1 closeout above shows the
semantic PASS merge on origin/main.

Status: **DONE** — terminal closeout PASS (Hannibal, t_0ea7adb5, 2026-09-11).
Evidence block: APPLICATION_SHA `f0365799d0f551c30a9870150d0a27e7e35a2772`;
QA verdict PASS (t_3e2bc459); PR #115 (head `6f429d8180c7b5fc29eb9aaf6149115ae8a5cdd6`,
docs-only); merge/final main `f204c2f296552f6af8015be4dca4f31a1332bf7f`;
ancestry `merge-base --is-ancestor f0365799... origin/main` exit 0 (verified live at closeout).
Next planned slice: **Slice 4** (unified manual lookup UI) — NOT STARTED.

## Slice 3 — DONE (published 2026-09-12)

Canonical Game / Release / Disk(/File) domain model with per-field provenance,
conflict preservation, deterministic precedence, migration, and production
integration. DEV candidate t_d8615948; QA bound to the exact APPLICATION_SHA
reported at DEV terminalization.

- Module: `src/amiga_adf_library_builder/canonical.py` —
  `Game` (slug-anchored abstract title identity), `Release` (deterministic
  `release_id = game_id + descriptor hash` for edition/region/language/
  publisher), `Disk` (content-anchored `disk_id = sha256:...`, multi-disk
  ordering, optional Slice 2 `FileIdentityStore` linkage), `Provenance` /
  `CanonicalField` (conflicting claims preserved, never overwritten),
  `CanonicalLibrary` (SQLite `canonical.db`, PRAGMA user_version v1
  deterministic migration), `migrate_staged_library()` (backwards-compatible
  import of existing staged state; staged JSON untouched).
- Documented precedence rule (module docstring, binding): curation (operator
  staged decisions) > curation_memory (hash-bound operator memory, Slice 2)
  > DAT knowledge sources (ranked by authority_rank, then confidence, then
  most recent observation; total stable tiebreak by source/record/url/value)
  > parser/filename derivations. Manual curation claims can never be
  outranked by later automated refreshes because the authority tier dominates
  the comparison.
- Production integration: `build_staged_library_from_result()` now persists
  the canonical model to `<library_root>/curation/canonical.db` on every
  staged build/rescan (best-effort; staged state file remains the curation
  authority). QA can reach the model by running a staged build and opening
  `curation/canonical.db`.
- Tests: `tests/test_canonical_model.py` (20 tests) — multi-release/multi-disk,
  conflict preservation, precedence, manual override vs. provider refresh
  (model and pipeline path), migration incl. idempotency and no-identity-store
  graceful degradation, real integration through
  `build_staged_library_from_result` (canonical.db created, survives rescan,
  failure isolation).
- Per-file suite evidence (QT_QPA_PLATFORM=offscreen): all
  `tests/test_*.py` files green except 3 failures byte-identical on pristine
  BASE (`test_gui_window_geometry`, `test_gui_wording_plain_language`,
  `test_qa_issue15_equiv`) — pre-existing, unrelated to Slice 3. Single-process
  full-suite runs abort in PySide6 offscreen (reproduced byte-equivalently on
  BASE; environmental). Focused suites green: canonical 20, file_identity 24,
  metadata_source 23, gh80 29, gh93 17+21, gh88_89 21, pipeline 9.
- Publication evidence block: APPLICATION_SHA `85fd0136733dd2a826e3f8f85e33cc61c48471f3`
  (identical to QA-tested candidate; QA verdict PASS t_d14601b9); PR #117
  (head `85fd0136733dd2a826e3f8f85e33cc61c48471f3`, no docs-only commit —
  PR head == QA SHA); merge/final main `8e85a44ddadb0b3538eb9d5bf61935c223aea9c8`;
  ancestry `merge-base --is-ancestor 85fd013... origin/main` exit 0 (verified
  live at closeout); required CI green including both Windows lanes
  (Build Windows standalone / PyInstaller, Real Windows GUI qualification).
- No Slice 4 work started (no lookup UX, no source-browser redesign, no
  filename/export policy, no 1G1R).

## Governance notes

- GH-107 remains OPEN after Slice 1.
- The lesson from this repair: an APPLICATION_SHA is proven by ancestry or byte-for-byte
  tree identity — never by a commit message; and QA may not weaken regression coverage
  to force a green lane.

---
*This plan is authoritative; both the repository copy (`docs/plans/GH-107-IMPLEMENTATION-PLAN.md`) and the archive copy (`/archive01/dumbo/project-planner/amiga-adf-library-builder/GH-107/IMPLEMENTATION-PLAN.md`) must stay in sync.*
