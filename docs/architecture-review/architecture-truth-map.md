# Architecture Truth Map — amiga-adf-library-builder

GH-141 architecture review, final synthesis. All statements verified at
commit `9d20b804158f4c5662dffd81bb0307bc2af978ca` (v0.2.16, `main`, clean tree).
Line citations are `file:line` relative to `src/amiga_adf_library_builder/`
unless prefixed `tests/`.

## 1. Entry points and the single orchestrator

```
CLI  cli.py (751 L)  ──┐
                       ├──► pipeline.run_pipeline (pipeline.py:114)
GUI  gui/worker.py  ───┘      28 keyword-only params, ~850 L orchestrator,
    PipelineWorker on QThread  cooperative cancel_event + activity hook
    gui/state.py builds PathConfig + kwargs
    parity enforced by tests/test_gui_equivalence.py (297 L, 10 tests)
```

`run_pipeline` is the only execution path; the GUI does not reimplement the
pipeline. The 28 params: `cfg, online, refresh_metadata, require_artwork,
include_artwork, include_manuals_rtfm, upstream_task_closed, run_id, export,
verify_only, verified_artwork_width, verified_artwork_height,
local_media_config_path, rtfm_config_path, playmatch_config_path,
hasheous_config_path, one_per_game, operator_decisions_path,
selection_manifest_path, igdb_config_path, screenscraper_config_path,
retroachievements_config_path, retrokit_config_path, cancel_event, activity,
convert_progressive_jpeg, progressive_prompt_callback, library_state_path`.

**Asymmetry (verified):** neither `cli.py` run_pipeline call site
(cli.py:541, cli.py:571) ever passes `library_state_path`; only the GUI
export path (GH-136) supplies it. On CLI, `_apply_curation` therefore returns
`(None, decisions)` unchanged (pipeline.py:78-79).

## 2. Pipeline phases (export run)

```
scan/parse/group (scanner, catalog, metadata, metadata_source)
  -> quarantine
  -> _apply_curation (pipeline.py:507; curation state via library_state_path;
     ACCEPTED clears quarantine + seeds 1G1R decisions, REJECTED sets
     quarantine_reason; mutates groups IN PLACE)
  -> 1G1R selection (selection.py select_one_per_game; region/language scored
     from canonical.db via canon.resolve_field)
     fresh DB? _load_canonical_library (canonical_naming.py:495) -> None
     -> _ensure_canonical_library (pipeline.py:992) CREATES from
        post-curation groups: all entries stamped PENDING, release_key +
        title claims written as authority=curation / source="operator"
        (canonical.py:645-654), disks UNHASHED (no identity_store)
  -> enrich (provider chain) -> rtfm/retrokit/local_media (optional)
  -> exporter: writes work/staging/<run-id>/ + manifest
     (publish to SD root is a separate gated Phase-10)
```

The GUI additionally runs, after any pipeline run, the GH-86 block
(gui/main_window.py:2336-2337 -> pipeline.py:776
`build_staged_library_from_result`): restores prior curation via
`carry_over`, then `_persist_canonical_library` (pipeline.py:906/934)
upserts the FULL staged library into canonical.db **with** identity_store
(hashed, identity-linked disks) and real operator curation claims. Failures
are swallowed (best-effort, pipeline.py:909-912).

## 3. Persistent state — six stores, no single owner

| # | Store | Location / format | Version mechanism | Owner code |
|---|-------|-------------------|-----------------|------------|
| 1 | Scan/parse/group catalog | `catalog/` JSONL | none | catalog.py |
| 2 | Curation state | `curation/library_state_<run>.json` | `schema_version=1`, strict reject on mismatch (library_state.py:99-100), no migration | library_state.py CurationStateManager |
| 3 | File identity + memory | `curation/identity.db` SQLite | `PRAGMA user_version`, real forward migrations (file_identity.py:100-157) | file_identity.py FileIdentityStore |
| 4 | Canonical model | `curation/canonical.db` SQLite | `SCHEMA_VERSION=1` (canonical.py:40) + `PRAGMA user_version` + `_migrate()` (canonical.py:350) | canonical.py CanonicalLibrary |
| 5 | DAT source index | SQLite | `SCHEMA_VERSION = 1` declared once (metadata_source.py:197), **never used** — dead constant, no migration path | metadata_source.py |
| 6 | Manual approvals | `config/manual-approvals/` + `config/local.manual-approvals/` JSON | `SCHEMA_VERSION: int = 1` (manual_approvals.py:60) written into records (line 167) but **never checked on load** (`ApprovalRecord.from_dict`, manual_approvals.py:187) | manual_approvals.py |

**canonical.db internal truth (verified at schema level):**
- `release` table PK `release_id`, written with `INSERT OR IGNORE`
  (canonical.py:512-521) — edition/region/language/publisher columns are
  first-write-wins and never updated by any path.
- `disk` table PK `(disk_id, release_id)`, written `INSERT OR REPLACE`
  (canonical.py:533-543) — disk rows DO refresh; identity linkage upgrades
  from unhashed (CLI-created) to hashed (GUI-created).
- `field_claim` PK `(entity_type, entity_id, field_name, source,
  record_key, value)` (canonical.py:406), `INSERT OR IGNORE` (line 438) —
  distinct claims accumulate forever; no DELETE statement exists anywhere in
  canonical.py; no expiry/TTL/prune mechanism exists.
- Authority order (IntEnum canonical.py:57-63): PARSER 10 < DAT 20 <
  CURATION_MEMORY 30 < CURATION 40. Highest tier wins resolution; equal-tier
  ties break deterministically, never by recency.

**Identity facts overlap across stores:** sha256 lives in identity.db, the
DAT index, and canonical.db disk rows; `release_key` lives in curation JSON,
canonical.db claims, and identity.db curation_memory. No documented SSOT.

## 4. Naming regime — dual, load-bearing

```
canonical_naming.py (GH-107 intent)
   │  _slugify_title (line 482) — "mirrors canonical.slugify_title" (self-documented copy)
   │  _sanitize_component (line 490) — delegates to exporter._sanitize_component via
   │    private import at canonical_naming.py:42  (cross-module private reach)
   ▼
exporter._get_canonical_basename (exporter.py:48-74)
   ├─ canonical.db loads  -> canonical_naming.export_name_for_release_group
   └─ absent/unloadable   -> naming.release_basename  + provenance
                             "fallback: no canonical DB" (exporter.py:64,72)
```

`naming.release_basename` is referenced by 7 production modules:
exporter.py, pipeline.py, models.py, rtfm.py, enrich.py, retrokit.py and
**canonical_naming.py itself** (line 43). The legacy path remains the
operative default whenever canonical.db is absent or unloadable; the GH-107
migration cannot be declared complete while this is true.

## 5. Provider layer (enrich chain)

playmatch -> hasheous -> igdb -> screenscraper -> retroachievements,
confidence-based override, explicit conflict provenance recorded
(enrich.py ~1089-1103). Six provider modules, 5,988 lines (igdb 1009,
screenscraper 1359, hasheous 967, playmatch 909, retroachievements 743,
retrokit 1001), each carrying its own `*Config` dataclass, its own
`_cache_load`/`_cache_store` (5 copies), its own `_write_json_atomic`
(where present), and inline `__import__('..._version', ...)` User-Agent
construction (>=4 sites). LemonAmiga (GH-75) adds a 7th config dataclass
inside metadata.py:249.

Placeholder `.example` base URLs are CONSISTENT across GUI and provider
modules (playmatch.py:74, hasheous.py:111, gui/providers.py:136-137,284-285):
deliberate disabled-by-default governance (issue #12), not divergence.

## 6. Versioning and compatibility

Single version source: `_version.py` reads pyproject.toml at runtime; frozen
builds use generated `_frozen_version.py`. 0.2.16 everywhere at the pinned
commit. Migration surfaces: docs/MIGRATION.md (portable path config),
`canonical.migrate_staged_library`, `file_identity` user_version migrations,
`library_state` schema_version strict check, manual_approvals stamp-only
field. No legacy CLI flags retained.

## 7. Test-enforced invariants

- `tests/test_gui_equivalence.py` — GUI builds identical PathConfig +
  run_pipeline kwargs (the strongest invariant in the repo).
- Security lanes: test_gui_security_remediation, test_ssrf_guard,
  test_worf_high_findings.
- Curation-to-export: test_gh136_curation_export (accepted decisions survive
  export; provider results cannot erase them; multi-disk export).
- Canonical: test_canonical_model, test_canonical_naming,
  test_canonical_naming_production.
- Per-provider lanes including `*_real_fetch` (live services).

## 8. GUI boundaries and secrets (verified sound)

MainWindow (2,729 L) + preview_widget (2,687 L) are presentation hotspots,
not logic duplication. gui/secrets.py SecretBackend ABC
(PortableVault/Env/WinDpapi) + RedactingFilter: least-privilege, keep as-is.
