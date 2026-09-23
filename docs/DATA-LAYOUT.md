# Data and State Layout

**Current baseline:** Amiga ADF Library Builder v0.2.26<br>
**Reviewed:** 2026-09-22

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
│   └── nfo/                   generated NFO/provenance files
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

Exact locations are resolved through `PathConfig` and can be overridden.

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

Export uses run-owned staging before publication/final output. The GUI clearly
distinguishes:

- Build — scan/organize/prepare;
- Check only — validate without final writes;
- Export — writes final files after the explicit safety acknowledgement.

## Preservation rule

Do not commit personal collections, caches, downloaded artwork, generated
manuals, portable vaults, staging output, or exported media to Git.

See:

- [USER-GUIDE.md](USER-GUIDE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [STATE-OWNERSHIP-MAP.md](STATE-OWNERSHIP-MAP.md)
