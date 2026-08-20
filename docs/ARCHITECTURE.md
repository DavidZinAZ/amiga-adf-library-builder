# Architecture

## Code repository

`<REPO_ROOT>`

## Persistent data root

`<LIBRARY_ROOT>`

```text
original/                      # IMMUTABLE intake (never written by app)
work/                          # temporary/scratch writes
catalog/                      # persistent catalogue (JSON or SQLite)
assets/
  artwork-original/           # masters as downloaded / sourced
  artwork-processed/          # resized Gotek-suitable copies
  nfo/                        # generated .nfo metadata
review/                       # human-review queue with explanations
unknown/                      # quarantined ambiguous/incomplete files + reasons
rejected/                     # explicitly rejected material
logs/                         # run logs, hashes, provenance
```

## Generated SD-card root

`<SD_CARD_ROOT>`

```text
ADF/
  <Unique Game or Release Name>/
    <Unique Game or Release Name>-1.adf
    <Unique Game or Release Name>-2.adf
    <Unique Game or Release Name>.jpg
    <Unique Game or Release Name>.nfo

DSK/
  <Unique Game or Release Name>/
    <Unique Game or Release Name>-1.dsk
    <Unique Game or Release Name>-2.dsk
    <Unique Game or Release Name>.jpg
    <Unique Game or Release Name>.nfo
```

Note: during a build, the exporter does **not** write here directly. It writes
to a run-owned staging directory `work/staging/<run-id>/ADF|DSK`, which is the
only directory a rollback may delete. The verified staging tree is copied to
this root **only at publish time (Phase 10)**, after qualification is green. This
keeps rollback cleanup scoped to a single run and protects the shared card roots
from any broad deletion.

## Domain hierarchy (internal model)

`Game → official version/edition → release group → dump variant → disk & role`

The internal model is rich; the **export** collapses it to a single folder per
unique release name so it never breaks the Gotek flat layout.

## Pipeline stages

1. **Scan** — read-only walk of `original/`; record name, path, size, SHA-256.
   No write to `original/`.
2. **Parse** — extract title, year, publisher, chipset, language, version,
   release group, trainer flag, alternate marker, disk number, total disks,
   special-disk role from the filename (and disk contents where needed).
3. **Group** — cluster files into release sets by normalized title + edition +
   group + variant; order disks by parsed ordinal; reject mixing incompatible
   releases.
4. **Enrich** (offline default; `--online` enables) — online-sourced metadata,
   cover art, provenance; cached for reuse.
5. **Export** — write the Gotek single-level tree; never overwrite existing
   output silently; idempotent.
6. **Quarantine** — unresolved/incomplete material routed to `review/`+`unknown/`
   with a human-readable explanation.

## Module layout (proposed, under `src/amiga_adf_library_builder/`)

- `cli.py` — argparse entry (`init`, `scan`, future `build`, `--online`, `--dry-run`)
- `initializer.py` — safe managed-directory creation (exists)
- `scanner.py` — intake walk + hashing
- `parser.py` — filename → structured record
- `grouper.py` — clustering / ordering
- `catalog.py` — persistent catalogue + cache
- `enrich.py` — metadata/artwork (offline NFO; `--online` hook to online providers)
- `igdb.py` — optional IGDB metadata/artwork provider (OPTIONAL, DISABLED by default)
- `exporter.py` — Gotek tree writer
- `quarantine.py` — review/unknown routing
- `playmatch.py` — optional Playmatch ROM-hash identity resolver (OPTIONAL, DISABLED by default)
- `hasheous.py` — optional Hasheous ROM-hash identity resolver (OPTIONAL, DISABLED by default)
- `local_media.py` — local-media artwork provider (offline, read-only)
- `rtfm.py` — deterministic manual sidecar build (offline, NO-AI)
- `metadata.py` — online metadata providers (Wikipedia, RAWG) + cache/provenance
- `artwork.py` — artwork processing / resize
- `nfo_render.py` — Gotek-facing NFO rendering
- `manual_approvals.py` — operator manual approvals
- `naming.py` — release basename generation
- `paths.py` — portable path configuration
- `models.py` — core data models
- `logging_utils.py` — structured logging / redaction
- `exporter_guard.py` — export safety gate
- `activity_log.py` — live activity log (issue #21)
- `gui/` — PySide6 Windows GUI (optional extra)

## Safety properties

- No application write targets `original/`.
- Initialization is idempotent.
- Existing output is never overwritten silently.
- Temporary writes use `work/`.
- Network operations require `--online`.
- Paths derived from filenames or online data are sanitized.
- Hashes are recorded and re-verifiable (integrity / preservation proof).
