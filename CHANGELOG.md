# Changelog

## Unreleased

- GH-43: Duplicate export controls clarified (Run / Export Settings)
  - 'Export the library (writes the final files)' is now the ONE obvious
    primary choice that determines whether the run will export files.
  - The ambiguous 'Allow export' checkbox is replaced by the unmistakable
    safety acknowledgement 'I understand this run will write files'.
    Internal settings key `export_gate_acknowledged` and the CLI
    `--export-gate-acknowledged` flag are unchanged.
  - The acknowledgement is disabled (and never holds a stale checked value)
    while build-only mode is selected, preventing contradictory combinations;
    a run that writes files always gets a fresh, explicit confirmation.
  - The pre-Run state label now explains exactly why the run will or will not
    write files (build-only / export pending acknowledgement / check-only /
    files will be exported) before Run is pressed.
  - Regression coverage: tests/test_gui_issue43_export_controls.py.

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
  provenance-aware persistent cache and bundled curated records.
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
