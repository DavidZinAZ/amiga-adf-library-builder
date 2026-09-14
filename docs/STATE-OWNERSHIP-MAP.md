# State Ownership Map

**Document:** `docs/STATE-OWNERSHIP-MAP.md`
**Repo:** amiga-adf-library-builder · **Base commit:** `2cac1936702a632b1d27ebebb3eebce620affd5a`
**Date:** 2026-09-14 · **Scope:** AR-003 (no universal source of truth), AR-004 (dead version markers)

> Every shared fact is assigned the store that already owns it in code — the one whose module writes it. Every other reader treats that store as authoritative. The map is **descriptive** (matching current code behavior) before it is **prescriptive** (what should change).

---

## 1. Store Inventory

| # | Store | Module | Location | Format | Owner code | Version mechanism | Read by |
|---|-------|--------|----------|--------|------------|-------------------|---------|
| 1 | Scan/parse/group catalog | `catalog.py` | `<root>/catalog/*.jsonl` | JSONL | `catalog.py` | none | `catalog.read_*` |
| 2 | Curation state (per run) | `library_state.py` | `<root>/curation/library_state_<run_id>.json` | JSON | `library_state.py` `_validate_schema` | `schema_version=1`, strict reject | pipeline `_apply_curation` |
| 3 | File identity + curation memory | `file_identity.py` | `data_dir/identity.db` | SQLite | `file_identity.py` `FileIdentityStore` | `PRAGMA user_version` + `_migrate()` | `selection.py`, `library_state.carry_over` |
| 4 | Canonical model | `canonical.py` | `<root>/curation/canonical.db` | SQLite | `canonical.py` `CanonicalLibrary` | `SCHEMA_VERSION=2`, `_migrate()`, `PRAGMA user_version` | `selection.py`, `canonical_naming.py`, `exporter.py`, `manual_lookup.py` |
| 5 | DAT source index | `metadata_source.py` | `data_dir/metadata_sources.db` | SQLite | `metadata_source.py` `MetadataSourceManager` | `SCHEMA_VERSION` + `_migrate()` (GH-145 R1) | pipeline enrich path |
| 6 | Manual approvals | `manual_approvals.py` | `config/manual-approvals/*.json` + `config/local.manual-approvals/*.json` | JSON | `manual_approvals.py` | `SCHEMA_VERSION` stamp, validated on load (GH-145 R1) | pipeline quarantine-clear |

## 2. Physical Layout

```
<library_root>/            (paths.py: library_root)
  catalog/                 scan.jsonl parse.jsonl groups.jsonl metadata-cache/ metadata-curated/
  curation/               library_state_<run>.json, canonical.db, identity.db
  config/                  manual-approvals/*.json, local.manual-approvals/*.json
<data_dir>/                (configurable)
  identity.db              (file_identity.py:8)
  metadata_sources.db      (metadata_source.py:7-11)
```

`identity.db` and `metadata_sources.db` share a configurable `data_dir`. `canonical.db` and per-run `library_state` live under `curation/`. Manual approvals live under `config/`. The catalog lives under `catalog/`. There is no single database.

## 3. Per-Store Ownership Summary

### canonical.db — Claim funnel + release-level descriptor authority

`CanonicalLibrary` owns **release-level descriptor facts** (`edition`, `region`, `language`, `publisher`, `release_key`, `title`, `folder`, `notes`, `curation_state`) with full per-field, per-source provenance. `SourceAuthority` tiers: `PARSER=10 < SEED=15 < DAT=20 < CURATION_MEMORY=30 < CURATION=40`. The 6-column discriminator is `(entity_type, entity_id, field_name, source, record_key, value)`. Claims accumulate by design; conflicts are never silently overwritten.

### identity.db — Content-hash-keyed identity + curation memory

`FileIdentityStore` owns **file-level facts** (`sha256`, `size`, `sha1`, `md5`, `crc32`, `first_seen`, `last_seen`) keyed by `sha256`, plus `path_observation` (transient locations) and `curation_memory` (decisions keyed by content hash). Speaks *files/bytes*; canonical.db speaks *releases*.

### metadata_sources.db — DAT source vocabulary

`MetadataSourceManager` owns the **DAT source registry + indexed source entries** (`title`, `year`, `publisher`, `region`, `language`, `sha1`, `md5`, `crc32`, `size`, `disk_number`). Never claims authority over release identity — raw DAT vocabulary that canonical.db consumes by re-claiming each field at DAT authority.

### library_state — Per-run staged curation decisions

`CurationStateManager` owns the **staged library's release_key curation decisions** (title/folder/notes/curation_state) for one run. Strict schema reject on mismatch (`CURATION_STATE_SCHEMA_VERSION=1`). Input to canonical.db via `migrate_staged_library`.

### catalog — Scan/parse/group cache (append-only)

Pure JSONL. Records keyed by `(filename, sha256)` for scans, `source_filename` for parse, append-only groups. Feeds the pipeline; never consumed or updated by other stores. Projection of the `original/` directory.

### manual-approvals — Quarantine-clear policy

`manual_approvals.py` owns **pre-approved release_key overrides** used to clear quarantine. `config/`-rooted policy (git-tracked + local override). Read at pipeline entry to seed `library_state`.

## 4. Cross-Store Overlap Map

| Shared fact | Fact locations | Authoritative owner | Projection rule | Write / Read boundary | Sync contract | Migration | Failure semantic |
|---|---|---|---|---|---|---|---|
| **sha256** | catalog, identity.db, metadata_sources.db, canonical.db disk rows | **identity.db** | `identity.db.register_file` computes sha256 once; catalog records it; canonical.db `upsert_disk` copies it into `disk.sha256` as denormalized reference | Write: identity.db (hash source). canonical.db disk.sha256 is read-projected copy. Read: selection/naming read canonical.db disk row | catalog → identity.db → canonical.db unidirectional; `disk.sha256` must equal identity.db `sha256` | identity.db `_migrate()` (user_version); canonical.db `_migrate()` schema v2 | identity.db read error → skipped, canonical.db persists with NULL hash |
| **release_key** | library_state, canonical.db claims, identity.db curation_memory | **canonical.db** | `migrate_staged_library` records verbatim `release_key` as `CURATION_MEMORY`/`CURATION` claim; writes to release's `release_key` field | Write: canonical.db claim surface only via GUI. Read: selection/naming via `resolve_field` | library_state → canonical.db claim → identity.db backfill (`import_staged_decisions`) | migration-idempotent across re-imports | canonical.db persist is best-effort; failure → silent fallback |
| **curation_state** | library_state, canonical.db claims, identity.db curation_memory | **library_state** (per-run input) + **canonical.db** (authority projection) | library_state holds decisions per run. `migrate_staged_library` records `curation_state` claim ← library_state value stamped as `seed_authority` or `CURATION` | Write: library_state author; canonical.db absorbs; identity.db backfills | library_state → canonical.db → identity.db `curation_memory`; claims preserve conflict history | library_state strict-reject on schema mismatch; canonical.db additive | canonical.db failure → silent; library_state still authoritative |
| **title** | library_state, canonical.db claims, metadata_sources.db (DAT), parser | **canonical.db** | Parser/DAT write claims at PARSER/SEED/DAT tier. Operator title writes at CURATION. `resolve_field` picks winner | Write: parser, DAT, GUI. Read: `canonical_naming.py`, `exporter.py` via `resolve_field` | Same as curation_state | per-field authority; no title-ID joins | canonical.db failure → exporter fallback `release_basename` |
| **region / language / publisher / edition** | metadata_sources.db (DAT), canonical.db descriptors | **canonical.db** | `canonical._ensure_canonical_library` writes columns from parser; GUI runs write them as SEED; `resolve_descriptor_columns` re-resolves from winning claim | Write: parser (PARSER), migration (SEED/CURATION/CURATION_MEMORY), GUI (CURATION). Read: `selection.py` via `resolve_field` | canonical.db descriptor columns re-resolved from field_claim on write; DAT sources are only input | canonical.db migration v1→v2 landed (GH-143) | canonical.db failure → neutral 20.0 region/language scores |
| **disk_number / disk_total** | metadata_sources.db (SourceEntry), canonical.db disk row | **metadata_sources.db** (per-source) / **canonical.db** (projection) | SourceEntry records from DAT parse; canonical.db disk_number sourced from same canonical release ordering | Write: metadata_source during indexing; preserve to canonical disk row. Read: export ordering | metadata_sources.db → canonical.db disk (release ordering) | none in-flight (metadata_sources.db has dead version constant) | metadata_source DAT re-parse overwrites SourceEntry per source_id (idempotent) |
| **approval match decision** | manual-approvals, library_state (consumed by pipeline) | **manual-approvals** | Pipeline matches `release_key` against approval records; on match, seeds library_state as ACCEPTED | Write: manual_approvals `config/` (git + local override). Read: pipeline quarantine-clear | approval → library_state (ACCEPTED) → canonical.db (CURATION claim) via identity.db `_sha_for_entry_file` | manual_approvals schema_version is stamp-only (AR-004) + load-time check | — |
| **game_id (title identity)** | canonical.db (game/title), parser (naming), catalog (parse) | **canonical.db** | `slugify_title(title)` → `game_id`; canonical.db owns as PK of `game` table | Write: `canonical_naming.py` / canonical.db. Read: `selection.py`, `manual_lookup.py` | parser → canonical.db (single store of game_id, parsed title is input only) | none in-flight | — |
| **size** | catalog (scan.jsonl), identity.db, metadata_sources.db | **identity.db** | Identical provenance to sha256 (same file content) | Write: identity.db `register_file`. Read: catalog / metadata sources have their own copy from original | catalog → identity.db → canonical.db disk (denormalized) is consistent if the file hash matches | — | — |

## 5. Synchronization Contract

Per store, the synchronization contract:

- **catalog (store 1):** Append-only. Records keyed by `(filename, sha256)` never mutated by other stores. Sync is: catalog → pipeline read. No reverse write.
- **library_state (store 2):** Authoritative for *one run's* decisions. Writes are final for that run. `canonical.db` is a downstream projection; `library_state` is never re-projected back into a prior run.
- **identity.db (store 3):** Owns the canonical hash for a given file content. `canonical.db.disk.sha256` is a denormalized reference matching `identity.db.file_identity.sha256`. `curation_memory.release_key` is the backfill target from `library_state`.
- **canonical.db (store 4):** The claim funnel. Aggregates claims from all sources; `resolve_field` is the only authoritative read path for field-level descriptors. Descriptor columns are re-resolved from winning claims on write.
- **metadata_sources.db (store 5):** DAT source vocabulary. Consumed by the pipeline to produce DAT-authority claims in `canonical.db`. No reverse projection from `canonical.db` back into DAT sources.
- **manual-approvals (store 6):** Quarantine-clear policy. Read at pipeline entry to seed `library_state`; not consumed by `canonical.db` directly.

## 6. Migration & Version Semantics

| Store | Current version | Mechanism | Forward migration path |
|-------|----------------|-----------|------------------------|
| catalog | none | — | N/A (append-only; no schema) |
| library_state | 1 | `schema_version` field, strict reject | Deferred (R4) — a v2 needs a real migration |
| identity.db | 1 | `PRAGMA user_version` + `_migrate()` | Add columns via `ALTER TABLE`, bump `SCHEMA_VERSION` |
| canonical.db | 2 | `_migrate()` + `SCHEMA_VERSION=2` | Add columns via `ALTER TABLE`, bump to 3 |
| metadata_sources.db | 1 | `_migrate()` + `PRAGMA user_version` (GH-145 R1) | Add columns via `ALTER TABLE`, bump `SCHEMA_VERSION` |
| manual-approvals | 1 | `SCHEMA_VERSION` stamp, validated on load (GH-145 R1) | Bump `SCHEMA_VERSION` in `manual_approvals.py`; `from_dict` rejects mismatch |

## 7. Failure Semantics

- **canonical.db persist failure** (`pipeline.py:909-912`): silently swallowed; library_state remains authority; exporter falls back to `release_basename`.
- **library_state strict-reject** (`library_state.py:100`): returns empty library on schema mismatch. Safe but lossy for forward compatibility (flagged as deferred R4).
- **metadata_sources.db re-parse** (`metadata_source.py`): idempotent by `source_id`; changed file gets re-indexed, old SourceEntries updated.
- **manual_approvals silent skip of malformed JSON** (`manual_approvals.py:341-346`): never raises; returns `LoadedApprovals` without the bad record.
- **identity.db missing** (`file_identity.py:15-18`): store degrades to empty; canonical.db written with NULL hash; no crash.

## 8. Version Marker Hygiene (AR-004)

Two dead version markers were made real as part of GH-145:

1. **`metadata_source.py:197`**: `SCHEMA_VERSION = 1` was a dead constant with no `_migrate()`, no `PRAGMA user_version`, and no migration idiom. Now `_migrate()` checks `PRAGMA user_version` and creates tables if stale, mirroring `file_identity.py:100-158`.

2. **`manual_approvals.py:60,167,187`**: `SCHEMA_VERSION` was written to each record but `ApprovalRecord.from_dict` never validated it. Now `from_dict` raises `ValueError` if `schema_version` differs from `SCHEMA_VERSION`. The existing `_read_json_records` loader catches this and skips the record gracefully.

## 9. Ownership Rule (Architectural Constraint)

No store is treated as a universal "source of truth." Each shared fact is assigned the store that already owns it in code. Future contributors must consult this map before adding cross-store logic. The invariant test suite (`tests/test_state_ownership_invariants.py`) makes these relationships machine-checkable.

---

*Source: GH-145 Q Branch design (§6, §14). See `tests/test_state_ownership_invariants.py` for executable assertions.*
