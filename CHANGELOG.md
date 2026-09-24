# Changelog

## 0.2.26 — 2026-09-22

### GH-183 Release

- Merge GH-183 QA-qualified candidate (831849f) and CI provenance fix (74c47a5) into main.
- Unify metadata_sources DB path and fix source_id mismatch.
- Real document association to physical RTFM oracle (Lemon typed docs).
- Manual Lookup persist -> Preview -> Export lifecycle.
- Fix Hall of Light parsing and sequel rejection.
- Fix RTFM JSON serialization and pipeline production failures.
- CI: preserve exact Windows QA candidate artifact.
- Version identity: 0.2.25 → 0.2.26 in pyproject.toml, AmigaADFGui.spec, tests/test_version_identity.py.

## 0.2.25 — 2026-09-17

### Windows User Test Release (post-Canonical-Identity-RTFM)

- Packaging-only release of PR #181 merged main for real operator Windows testing.
- Version identity: 0.2.24 → 0.2.25 in pyproject.toml, AmigaADFGui.spec, tests/test_version_identity.py.
- No substantive application changes beyond the canonical identity/RTFM already-merged work.

## 0.2.24 — 2026-09-17

### Windows User Test Release (post-RTFM-FIX)

- Packaging-only release of GH-176 merged main for real operator Windows testing (post-RTFM-FIX).
- Version identity: 0.2.23 → 0.2.24 in pyproject.toml, AmigaADFGui.spec, tests/test_version_identity.py.
- No substantive application changes beyond the GH-176 already-merged work.

## 0.2.23 — 2026-09-17

### Windows User Test Release (post-GH-176)

- Packaging-only release of GH-176 merged main for real operator Windows testing.
- Version identity: 0.2.22 → 0.2.23 in pyproject.toml, AmigaADFGui.spec, tests/test_version_identity.py.
- No substantive application changes beyond the GH-176 already-merged work.

## 0.2.22 — 2026-09-16

### Windows User Test Release (post-GH-173)

- Packaging-only release of GH-173 merged main for real operator Windows testing.
- Version identity: 0.2.21 → 0.2.22 in pyproject.toml, AmigaADFGui.spec, tests/test_version_identity.py.
- No substantive application changes beyond the GH-173 already-merged work.

## 0.2.21 — 2026-09-16

- GH-170: Unified Lookup + Provider persistence.

## 0.2.20 — 2026-09-16

- GH-167: Fix four root causes (RC-A, RC-B, RC-C, RC-D).

## 0.2.19 — 2026-09-16

- GH-164: Canonical title normalization, provider diagnostics, DAT/local metadata, review persistence.

## 0.2.18 — 2026-09-15

## 0.2.17 — 2026-09-15

## 0.2.16 — 2026-09-14

- GH-73: PreviewWidget detail pane wrapped in QScrollArea.
- GH-84: Cooperative cancellation and close lifecycle.
- GH-119: Release identity enforcement and frozen Windows versioning (correction + unification).
- GH-93: Fix stale Move/Merge selection after GHOST.
- GH-107: Canonical Game/Release/Disk model, manual lookup UI, canonical naming/export policy, 1G1R selection, persistent hash identity, curation memory.
- GH-106: GHOST curation state for emptied releases.
- GH-76: GUI discovers default config when no provider-config path set.

## 0.2.13 — 2026-09-10

- GH-99: Coherent Preview/Curation workflow repair (8 defects).
  - Defect 1 (identity): table rows carry release_key in Qt::UserRole so selection, detail, and actions survive redraw/sort/filter.
  - Defect 2 (canonical display): Edition/Group/Confidence columns show parsed + provider-resolved values; ADFs count column added.
  - Defect 3 (persistence): state auto-saved on every curation change; pipeline carry_over restores prior decisions keyed by release_key.
  - Defect 4 (edit): inline Edition/Group editing with METADATA_EDIT undo/redo.
  - Defect 5 (multi-select): ExtendedSelection (Ctrl/Shift) enabled.
  - Defect 6 (redundant control): Multi-Select toggle removed.
  - Defect 7 (notes): line-oriented notes built in pipeline; detail pane is a capped read-only QTextEdit.
  - Defect 8 (needs_review): context-menu 'Review...' opens a dialog with reason list and full candidate; approve/reject recorded as REVIEW_RESOLVED with undo/redo.
  - Independently qualified on real packaged Windows (Windows-R3 Actions run 34466500597; QA PASS); released with Windows assets `amiga-adf-gui-portable.zip` and `amiga-adf-gui.exe`.

## 0.2.9 — 2026-09-07

- GH-93: Add Move ADF(s) and Merge Release with explicit selection safety and single audit trail.
  - Move ADF(s) now moves only the explicitly selected ADF filenames from one release
    to another, preserving source order and blocking empty or self-target moves.
  - Merge Release combines all ADFs from a source release into a destination release,
    promotes blank metadata from source only when destination fields are empty, and
    empties the source release into NEEDS_REVIEW rather than carrying hidden state.
  - Decision logging is now single-source: the model records one MOVE/MERGE entry per
    side, and undo/redo uses the stored payload to restore exact pre/post file sets
    and curation state instead of fragile string parsing.
  - The duplicate logging defect in move/merge operations is fixed; undo/redo no longer
    injects extra duplicate actions into the audit trail.
  - Focused regression coverage for explicit ADF selection, move/merge identity, empty
    source handling, undo/redo dispatch, and manual approval enforcement.
  - Independently qualified on real packaged Windows (Windows-R3 Actions run
    34126061118; QA PASS); released with Windows assets
    `amiga-adf-gui-portable.zip` and `amiga-adf-gui.exe`.

## 0.2.10 — 2026-09-08

- GH-85: Fix Windows GUI progress calculation/updates.
  - The Windows packaged GUI no longer miscounts processed versus total items during
    enrichment, preventing progress from stalling or miscounting before completion.
  - Focused regression coverage proves monotonic progress, enrichment-count consistency,
    and 100% only at completion.
  - Independently qualified on real packaged Windows (Windows-R3 Actions run
    34182674167; QA PASS); released with Windows assets
    `amiga-adf-gui-portable.zip` and `amiga-adf-gui.exe`.

## 0.2.11 — 2026-09-08

- GH-91: Fix Preview/Curation state filtering.
  - The Preview/Curation workspace now preserves the correct curation state filter
    and detail-binding behavior when switching selections, preventing filtered views
    from losing their applied state and hidden status.
  - Focused regression coverage proves case-safe combo-item identity, filter-change
    stability, and detail binding after state transitions.
  - Independently qualified on real packaged Windows (Windows-R3 Actions run
    34194129782; QA PASS); released with Windows assets
    `amiga-adf-gui-portable.zip` and `amiga-adf-gui.exe`.

## 0.2.12 — 2026-09-08

- GH-88+GH-89: Shared Online/Offline lookup workflow.
  - Online Lookup routes through the shared lookup workflow to online metadata providers, preserving existing release identity and metadata provenance.
  - Offline Lookup routes through the same workflow with offline-only provider selection, preventing online providers from appearing in offline lookup results.
  - Preview/Curation uses the shared provider-selection logic, so offline mode no longer lists Hall of Light/LaunchBox or other online providers.
  - Offline lookup remains network-absent when no local-media source is configured, with explicit local-source state reported rather than placeholder behavior.
  - Apply remains staged-only: it mutates staged release metadata and records curation actions without exporting or changing ADF files.
  - Focused regression coverage covers online/offline routing, offline provider selection, staged-only apply behavior, release-identity preservation, and preview-detail binding.
  - Independently qualified on real packaged Windows (Windows-R3 Actions run 34234564787; QA PASS); released with Windows assets
    `amiga-adf-gui-portable.zip` and `amiga-adf-gui.exe`.

## 0.2.7 — 2026-09-06

- GH-86: Fix Preview/Curation population (P0 — released v0.2.6 Windows GUI showed an
  empty Preview/Curation tab).
  - Root cause: `FileNotFoundError` during source-scan + empty ADF/identity handling
    left the Preview/Curation workspace with zero populated rows from a real
    configured library.
  - The Preview/Curation workspace now populates from the configured **Original Disks
    (read only)** source with the complete discovered source inventory, original
    identity, planned processed/export name and destination path, and per-entry
    preview/details — before any final export is written.
  - Non-destructive contract preserved: source fixtures remain untouched and no
    export files are written until explicit Export.
  - Regression coverage uses a populated library fixture (curation-not-output
    regression + full `test_pipeline` population paths), not only lightweight object
    construction.
  - Independently qualified on real packaged Windows (Windows-R3 Actions run
    34025701908; QA-FINAL-R2 run #160 PASS): populated Preview/Curation rows,
    original identity, coherent planned export path, 7 source fixtures hash-unchanged,
    0 preview-only export writes.
  - Shipped with Windows assets: `amiga-adf-gui-portable.zip` and `amiga-adf-gui.exe`.

## 0.2.5 — 2026-08-21

- GH-49: Configurable local matching confidence and ambiguous review system
  - Three configurable thresholds in LocalMediaConfig:
    - `auto_match_threshold` (default 0.90): confidence ≥ this → Auto Match
    - `review_threshold` (default 0.70): confidence ≥ this → Needs Review, below → No Match
    - `near_tie_difference` (default 0.03): top two candidates within this → force Needs Review
  - Validation ensures `review_threshold < auto_match_threshold`
  - Three-outcome resolution logic: Auto Match / Needs Review / No Match
  - Near-tie detection within and across categories (artwork/manual)
  - Persistent review queue (ManualReviewItem) at cache_dir/review_queue.json
  - Manual-lock registry at cache_dir/manual_locks.json with provenance
  - Manual locks protect selections from auto-overwrite on refresh/re-scan
  - Explicit opt-in (remove_manual_lock) allows reconsideration
  - LocalMediaResult extended with outcome field and top_candidates
  - Comprehensive tests for thresholds, boundaries, near-ties, persistence, locking, review queue
  - All existing GH-45 behavior preserved

## 0.2.3 — 2026-08-07

- Preserve-first Amiga ADF catalogue and Gotek SD-card builder: scan, parse,
  group, enrich, quarantine, and stage a deterministic single-level Gotek
  `/ADF/<Game>/` + `/DSK/<Game>/` tree.
- Artwork discovery and processing for Gotek export: JPEG/PNG acceptance,
  verified upstream hard limits (file ≤ 500 KB, pixel ≤ 2000×2000), aspect-fit
  with no upscaling, and master preservation under `assets/artwork-original`.
- Online metadata enrichment (opt-in `--online`) from approved providers with a
  provenance-aware persistent metadata cache and bundled curated records.
- Gotek export safety gate (`exporter_guard.export_gate_open`): hard-gated by an
  explicit operator safety signal and verified artwork dimensions; never writes
  to the shared SD-card destination and refuses to silently overwrite staged
  output.
- Curated records and example/template configuration for end users.

## 0.2.1

- Add Amiga-specific artwork discovery from approved Lemon Amiga, Hall of Light, OpenRetro, and Lychesis pages using OpenGraph, Twitter image, JSON-LD, and scored image fallbacks.
- Seed Lemon Amiga artwork pages for the four current accepted releases.
- Preserve artwork-source provenance in sidecar JSON files and rich NFO output.
- Add `--require-artwork` preflight so staging writes are refused when any accepted release lacks a processed JPG.
- Merge newly bundled curated keys without overwriting operator edits.
- Remove duplicate SHA/size lines from generated NFO files.
- Accept WebP masters while continuing to emit Gotek-compatible JPEG derivatives.

## 0.2.0 — 2026-08-04

- Implemented real opt-in online metadata enrichment.
- Added Wikipedia provider and optional RAWG provider.
- Added provenance-aware persistent metadata cache and curated overrides.
- Added artwork download, master preservation, and 150×150 no-crop/no-upscale JPEG processing.
- Replaced skeletal exported NFO generation with rich cached enrichment output.
- Bundled curated records for the four currently accepted releases.
- Added online-provider and cache tests.

## Unreleased

### GH-187 — Simplify export folder configuration

- Removed redundant "Export destination" row from the Library tab; the GUI no longer exposes or persists `output_dir`.
- Renamed "Export work folder" to "ADF Library Export Folder" to accurately describe where finished export/library files are written.
- Existing configured export paths are preserved; `default_output_dir` and `Preset.output_dir` fields retained for backward compatibility.
- The CLI `--output-dir` flag and `output_dir` config key remain unchanged (valid-but-unused in the current pipeline).
