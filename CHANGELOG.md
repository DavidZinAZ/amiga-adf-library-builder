# Changelog

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
