# Data and State Layout

**Current baseline:** Amiga ADF Library Builder v0.3.13<br>
**Reviewed:** 2026-10-16

The repository contains code. User library data and portable GUI application
state live outside Git-tracked source.

## Library Root

A typical library layout is:

```text
<library_root>/
├── original/                  preservation ADF/DSK source; never ordinary write target
├── catalog/
│   ├── metadata-cache/        cached metadata/provider records
│   └── metadata-curated/      operator-maintained curated records
├── assets/
│   ├── artwork-original/      retained source/master artwork
│   ├── artwork-processed/     processed export artwork
│   ├── nfo/                   generated NFO/provenance files
│   └── rtfm/                  generated manual/provenance files
├── data/
│   └── metadata_sources.db    authoritative DAT source index
├── curation/                  canonical/library-state data when configured there
├── unknown/                   unresolved/quarantined material
├── work/
│   └── staging/<run-id>/      run-owned export preparation
├── output/                    configured generated/final output
├── config/
│   └── manual-approvals/      operator approval records
├── reports/                   run/qualification reports
└── logs/                      application/pipeline logs
```

The GUI creates `Library-Root` under its application base automatically. Original and export folders are selectable. CLI locations are resolved through `PathConfig` and can be overridden.

## Canonical and indexed metadata databases

Important durable state includes:

- `canonical.db` — canonical Game / Release / Disk identity and claims;
- `metadata_sources.db` — indexed DAT/local metadata-source database;
- file identity database — durable file identity and curation carry-over.

The authoritative path is the one resolved by the current path configuration;
workflows should not create competing copies of the same database.

## GUI portable application state

The Windows GUI has a separate portable application base:

```text
<app-base>/
├── config/
│   ├── gui-settings.toml
│   └── secrets.vault
├── data/
├── logs/
├── cache/
└── themes/
```

When frozen, `<app-base>` normally resolves next to the executable. It can be
overridden with `AMIGA_ADF_GUI_BASE`.

The application base must not be confused with the Library Root.

## Export

Build prepares managed working data. Export writes a run-owned staging tree via the **Build & Export** tab. The GUI has no Check only or second acknowledgement checkbox; CLI `--verify-only` and acknowledgement flags remain separate supported controls.

Curation files under `curation/` preserve shared `media_choices`, `disk_media_choices` keyed by original disk filename, `media_scopes`, and action history. Keep these with the rest of Library-Root when backing up or upgrading.

## Preservation rule

Do not commit personal collections, caches, downloaded artwork, generated
manuals, portable vaults, staging output, or exported media to Git.

See:

- [USER-GUIDE.md](USER-GUIDE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [STATE-OWNERSHIP-MAP.md](STATE-OWNERSHIP-MAP.md)
