# ACTIVE-TASK.md

**Task**: t_f6d949c9 — P0 GH-183 Real Lemon Docs + Manual Lookup Workflow
**Status**: COMPLETE
**Candidate**: 81ea86858e0431e991eae95823920621f254d465
**BASE**: 62e4b683255d5328463ab91fed1b3118e9686a1e (origin/main)
**Parent**: 0bd0fa4ea5d36cd1001263fedb2bf20293ce2669

## Failure A — Real Lemon Typed-Document Acquisition ✅ FIXED

Changed files: `src/amiga_adf_library_builder/metadata.py`, `src/amiga_adf_library_builder/rtfm.py`

- Extended `_LemonAmigaGameParser` to discover typed-document links from the game page "Docs" section
- Added `lemonamiga_discover_docs()` — discovers which typed docs actually exist on Lemon Amiga
- Added `lemonamiga_fetch_doc()` — fetches actual document pages and extracts real body content
- Added `_LemonAmigaDocParser` — parses doc page content (code blocks, tables)
- Rewrote `lemonamiga_to_rtfm_sources()` — discovers and fetches real docs, never fabricates
- Added `content` field to `RtfmSource` for actual document body text
- Removed placeholder prose block from `_compose_sections()`
- Added content handler for sources with inline content

## Failure B — Real Manual Lookup Operator Workflow ✅ FIXED

Changed file: `src/amiga_adf_library_builder/gui/manual_lookup_panel.py`

- Added typed-document search section to ManualLookupPanel
- Added `_on_doc_search()` — discovers docs via `lemonamiga_discover_docs()`
- Added `_on_doc_override()` — persists operator selection as curation claims
- Doc table exposes: Provider/Source, Doc Type, Status, Match/Reason, Source URL, Selected
- Empty/no-result states explain why
- Apply Selection button for operator override

## Test Results

- GH-183 corrective tests: 29 passed
- RTFM tests: 147 passed
- Pipeline tests: 29 passed
- Manual approvals tests: 10 passed
- Total: 99+ passed, 0 failed
- `git diff --check` clean
