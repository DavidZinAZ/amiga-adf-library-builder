# ACTIVE-TASK.md — t_e40f31ab: GH-183 Real Document Association → Physical RTFM Oracle

## Mode: IMPLEMENT

### BASE
- SHA: 62e4b683255d5328463ab91fed1b3118e9686a1e (origin/main)
- Worktree base: 7aedfc1c42db0bafefc813e6a34f05768a261336 (parent task t_f1143b2b)

### CANDIDATE
- SHA: (will be recorded on commit)

### Changes Made
1. **`src/amiga_adf_library_builder/manual_lookup.py`**:
   - Added `ManualDocument` dataclass with `doc_type`, `provider`, `url`, `title`, `content`
   - Added `apply_manual_document()` — persists document via `canonical.claim_field(curation_override=True)` and `provenance` table
   - Added `get_document_associations()` — retrieves persisted associations via `canonical.claims_for()`
   - Added `document_to_rtfm_sources()` — reconstructs `RtfmSource` list from provenance records
   - Fixed `fields_for_entity()` to include `rtfm_document` field
   - Fixed return type annotation to `list[tuple[Provenance, str]]`
   - Fixed `document_to_rtfm_sources` tuple unpacking order to `(prov, content)`

2. **`src/amiga_adf_library_builder/rtfm.py`**:
   - Fixed `_compose_sections` filter: `not s.path` → `(not s.path or str(s.path) in ("", "."))` to handle online sources (Path("") is truthy)
   - Fixed size safety check: `str(s.path) == ""` → `str(s.path) in ("", ".")` to correctly skip file stat for online sources

3. **`tests/test_gh183_manual_lookup_lifecycle.py`**:
   - Replaced broken `_make_opener` patching with `_fake_text_get` function returning `(html, url)` tuples
   - Fixed tuple unpacking in test assertions from `(content, prov)` to `(prov, content)` to match `get_document_associations` return type
   - Fixed `claims_reopen[0][0]` → `claims_reopen[0][1]` for content access
   - Updated `_make_release_group` to set `title` attribute and use `ReleaseGroup` properly

### Test Results
- All 3 tests in `test_gh183_manual_lookup_lifecycle.py`: **PASS**
- All 33 tests in `test_gh183_corrective.py` + `test_gh183_manual_lookup_lifecycle.py`: **PASS**
- All 120 tests across related test files: **PASS**

### Status
COMPLETE — ready for commit