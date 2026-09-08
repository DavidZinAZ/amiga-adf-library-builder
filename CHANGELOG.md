# Changelog

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
