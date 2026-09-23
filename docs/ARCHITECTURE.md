# Architecture

**Current baseline:** Amiga ADF Library Builder v0.2.26<br>
**Reviewed:** 2026-09-22

This document describes the current high-level architecture. Historical design,
issue, and architecture-review documents are retained under [`archive/`](archive/).

## Core rule: one shared engine

The CLI and Windows GUI are two front ends over the same core pipeline.

```text
CLI arguments ───────┐
                     ├─> resolved paths/config ─> shared core pipeline
Windows GUI state ───┘
```

The GUI must not independently reimplement scanning, parsing, grouping,
metadata reconciliation, export rules, or preservation checks.

## Main runtime layers

### Presentation

- `cli.py` — command-line interface.
- `gui/` — PySide6 Windows GUI.
- `gui/main_window.py` — main UI and run orchestration.
- `gui/state.py` — maps GUI controls into shared path/pipeline configuration.
- `gui/worker.py` — background pipeline execution and cooperative cancellation.

### Intake and identity

- `scanner.py` — read-only discovery and hashing.
- `parser.py` — filename/release token parsing.
- `grouper.py` — disk/release grouping.
- `file_identity.py` — durable file identity and curation memory.
- `canonical.py` — canonical Game / Release / Disk model.
- `canonical_naming.py` / `naming.py` — canonical/export naming.

### Metadata and enrichment

- `metadata.py` — shared metadata records, cache, relevance validation, and
  lower-level online metadata sources.
- `lookup_workflow.py` — shared online/offline/alternate lookup workflow.
- `metadata_source.py` — indexed DAT/local metadata sources.
- `local_media.py` — read-only local artwork/media matching.
- provider modules such as `igdb.py`, `screenscraper.py`,
  `retroachievements.py`, `playmatch.py`, `hasheous.py`, and `retrokit.py`.
- `rtfm.py` / `rtfm_docs.py` — manual/document pipeline.
- `artwork.py` — artwork processing.
- `nfo_render.py` — Gotek-facing NFO rendering.

### State, selection, and export

- `library_state.py` — staged Preview & Curation state.
- `manual_approvals.py` — operator approval records.
- `selection.py` — selection/1G1R logic.
- `exporter.py` — staging/final export mechanics.
- `exporter_guard.py` — export safety gate.
- `quarantine.py` — unresolved/ambiguous material.
- `activity_log.py` / `logging_utils.py` — activity logging and redaction.

## Canonical identity

The canonical hierarchy is:

```text
Game
└── Release
    └── Disk
```

Raw filenames are evidence, not the final identity model. Provider results,
indexed local metadata, hashes, and operator curation can all contribute claims.
Operator-authoritative decisions are intended to survive normal refreshes.

## Persistent state ownership

No single store owns every fact. Current major stores include:

| Store | Primary responsibility |
|---|---|
| catalog/cache files | scan/group/provider cache data |
| `library_state` | staged Preview & Curation state |
| file identity DB | durable file identity / curation memory |
| `canonical.db` | Game / Release / Disk identity and claims |
| `metadata_sources.db` | indexed DAT/local metadata sources |
| manual approval records | explicit operator approvals |
| GUI settings | non-sensitive GUI preferences |
| GUI secret vault | credentials only |

See [STATE-OWNERSHIP-MAP.md](STATE-OWNERSHIP-MAP.md) for the current operator/developer map.

## Pipeline shape

The shared pipeline is conceptually:

```text
Scan
  -> Parse
  -> Group
  -> Resolve canonical identity
  -> Enrich metadata/artwork/manuals
  -> Apply persistent curation
  -> Validate
  -> Stage/export
  -> Report/quarantine unresolved material
```

Ambiguous identity should be surfaced for review rather than guessed.

## Windows GUI

The released Windows GUI is a normal supported interface, not a future design.
Its current tabs are:

- Library
- Options
- Providers
- LaunchBox media
- Preview & Curation
- Diagnostics
- Metadata Sources
- Manual Lookup

For user-facing behavior see
[WINDOWS-GUI-WALKTHROUGH.md](WINDOWS-GUI-WALKTHROUGH.md).

## Portable application state vs library data

The frozen GUI keeps application state under its portable application base:

```text
config/
data/
logs/
cache/
themes/
```

The Amiga library root is separate. It contains preservation source data,
catalog/curation state, generated assets, staging/output, reports, and logs as
configured.

See [DATA-LAYOUT.md](DATA-LAYOUT.md).

## Safety invariants

- original ADF/DSK source files are never ordinary write targets;
- online access is explicit/configurable;
- ambiguous matches go to review;
- secrets remain separate from ordinary settings and are redacted from logs;
- final export is explicit and gated;
- run-owned staging is used before final publication;
- provenance is retained for metadata, artwork, manuals, and operator decisions;
- GUI and CLI behavior should converge on shared core code.

## Historical architecture material

Issue-era GUI design, remediation reviews, and older architecture snapshots are
preserved under [`archive/architecture/`](archive/architecture/) and
[`archive/architecture-review/`](archive/architecture-review/).
