# GH-170 Mandatory Packaged-Windows QA Gate (Columbo)

## Overview

Per GitHub issue #170, Linux/unit/widget tests are **not sufficient**. Columbo must exercise the packaged Windows GUI with the real workflows listed below. Each mandatory criterion is PASS or FAIL with durable evidence (screenshots + log lines). **There is no PARTIAL PASS for a mandatory criterion.**

## Prerequisites

- Packaged Windows GUI build available (Columbo environment)
- Test fixtures: Hacker II: The Doomsday Papers v1.0, Hacker, Rocket Ranger, Stunt Car Racer, known manual file
- All C1-C7 code changes committed to the release branch

## Mandatory Criteria

### 1. Per-provider enablement persists across full GUI restart
- **Steps**: Enable online providers → Save settings → Close GUI → Reopen → Verify providers still enabled
- **Evidence**: Screenshot showing provider checkboxes checked after restart
- **FAIL if**: Providers revert to default state after restart

### 2. Effective runtime provider set matches saved GUI state
- **Steps**: Disable a provider in GUI → Run lookup → Verify disabled provider is not queried
- **Evidence**: Log lines showing provider skip for disabled providers
- **FAIL if**: All providers are queried regardless of GUI state

### 3. Provider-specific diagnostics prove what was queried and what happened
- **Steps**: Run Unified Lookup → Verify diagnostic bands show per-provider status
- **Evidence**: Screenshot showing provider status bands (disabled, zero_results, error, etc.)
- **FAIL if**: Only generic "no_match" or "(unknown)" shown

### 4. Unified Lookup has one obvious editable search query
- **Steps**: Open Unified Lookup → Verify single Search field exists
- **Evidence**: Screenshot of Unified Lookup dialog
- **FAIL if**: Two title/query-like fields present

### 5. Search button and Enter visibly rerun search
- **Steps**: Enter query → Press Search/Enter → Verify table updates
- **Evidence**: Screenshot showing results after rerun
- **FAIL if**: No visible rerun or results not replaced

### 6. Search returns named candidates or explicit zero-result/error status
- **Steps**: Search for "Hacker II" → Verify named candidates appear
- **Evidence**: Screenshot showing candidate table with named entries
- **FAIL if**: Only `(unknown) / no_match` row shown

### 7. `no_match` sentinel is not presented as a selectable candidate
- **Steps**: Search for nonexistent title → Verify no selectable sentinel row
- **Evidence**: Table shows empty or status band only, Apply button disabled
- **FAIL if**: `0.00 / fuzzy_title / (unknown) / no_match` row is selectable

### 8. Candidate selection shows details/provenance/reason and available media
- **Steps**: Select a candidate → Verify detail panel shows metadata
- **Evidence**: Screenshot showing candidate details
- **FAIL if**: Detail panel is blank or shows only title

### 9. Manual Lookup populates authoritative Game/Release/Disk identity
- **Steps**: Select a DAT record → Click "Record as Authoritative Identity" → Verify claims appear
- **Evidence**: Screenshow showing CURATION claims in the precedence table
- **FAIL if**: Canonical fields remain `(no claim recorded)`

### 10. Duplicate release labels are distinguishable
- **Steps**: Open Manual Lookup → Browse Hacker releases → Verify labels differ
- **Evidence**: Screenshot showing distinguishable release labels
- **FAIL if**: Duplicate Hacker releases have identical labels

### 11. Metadata Source Browser returns records or explicit source/index/search status
- **Steps**: Open Source Browser → Search → Verify status line shows explicit state
- **Evidence**: Screenshot showing status line ("N sources indexed", "Zero results", etc.)
- **FAIL if**: Empty table with no status line

### 12. Metadata-source persistence contract is documented and restart-tested
- **Steps**: Document state→store→restart map → Verify on restart
- **Evidence**: Documentation + screenshot
- **FAIL if**: State lost on restart

### 13. RTFM/manual generation works end-to-end for a known fixture
- **Steps**: Select a known manual fixture → Generate RTFM → Verify output exists
- **Evidence**: Screenshot showing RTFM file path in Preview
- **FAIL if**: RTFM/Manual shows "(none)"

### 14. Preview Curation exposes NFO and RTFM/manual inspection/open workflow
- **Steps**: Select entry with NFO/RTFM → Right-click → Verify Open actions available
- **Evidence**: Screenshot showing context menu with Open Artwork/NFO/RTFM
- **FAIL if**: No Open actions for NFO/RTFM

### 15. Existing working artwork remains working
- **Steps**: Load title with existing artwork → Verify Artwork field and preview render
- **Evidence**: Screenshot showing artwork preview
- **FAIL if**: Artwork shows "(none)" or blank

### 16. Processed-artwork state cannot silently contradict `Artwork: (none)`
- **Steps**: Process artwork for Stunt Car Racer → Verify Artwork field shows processed path
- **Evidence**: Screenshow showing processed artwork path
- **FAIL if**: Notes show processed path but Artwork shows "(none)"

### 17. State/provenance/notes refresh coherently after lookup Apply
- **Steps**: Apply lookup result → Verify state/provenance/notes update
- **Evidence**: Screenshow showing updated state
- **FAIL if**: State does not refresh after Apply

### 18. No regression to GH-164 matching/review routing, GH-78 live provider activity, or exact/manual authority precedence
- **Steps**: Run existing test suite → Verify all pass
- **Evidence**: Test output showing all tests pass
- **FAIL if**: Any existing test regresses

## Evidence Format

For each criterion, provide:
1. **PASS/FAIL** verdict
2. **Screenshot** (PNG) showing the relevant UI state
3. **Log lines** (text) showing relevant operations
4. **Timestamp** of test execution

## Pass Criteria

All 18 mandatory criteria must be PASS. Any single FAIL is a final QA FAIL. Do not publish/release until all gates pass.

## Automation Notes

This QA gate requires a Windows environment with the packaged GUI. The `qa_verify_gh83.py` script in the repo root demonstrates the pattern for automated verification scripts. A similar Columbo script should be created for GH-170.
