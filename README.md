# Amiga ADF Library Builder

**Amiga ADF Library Builder** is a preservation-first Windows application and Python CLI for organizing Amiga disk-image collections, grouping multi-disk releases, enriching them with metadata/artwork/manual information, curating ambiguous results, and producing reviewable Gotek-oriented output.

The project is designed as a companion utility for the [Gotek Touchscreen Interface (GTi)](https://github.com/mesarim/Gotek-Touchscreen-interface). It prepares and curates libraries for Gotek-oriented use; it is not a replacement for or fork of GTi.

> **Preservation rule:** source/original `.adf` and `.dsk` files are treated as immutable input. Review and curation happen in application-managed state before deliberate export.

## Download the Windows app

**No Python required.** The normal Windows release is available in two forms:

- **`amiga-adf-gui-portable.zip`** — recommended portable folder build.
- **`amiga-adf-gui.exe`** — single-file portable executable.

Download them from the project's **latest GitHub release**. Each release identifies the version and source commit used to build the artifacts.

## Windows workflow at a glance

The current GUI includes these primary workspaces:

- **Library** — choose the library/source and working locations.
- **Options** — control online lookup, metadata refresh, artwork/manual behavior, matching thresholds, and related run options.
- **Providers** — configure optional provider integrations.
- **LaunchBox media** — configure local artwork/manual folders.
- **Preview & Curation** — review releases, filter state, inspect details, accept/reject, move/merge ADFs, perform lookup, and persist curation decisions.
- **Diagnostics** — inspect runtime/provider/configuration state.
- **Metadata Sources** — review metadata-source configuration/state.
- **Manual Lookup** — explicitly search/browse sources and persist a selected result into the normal Preview → Export lifecycle.

Build/review first, then export deliberately using the Run / Export controls and safety acknowledgement.

## Core capabilities

- Read-only scanning of `.adf` and `.dsk` originals.
- Canonical Game → Release → Disk identity model with persistent hash identity.
- Multi-disk grouping and ambiguity/quarantine handling.
- Online and offline/local metadata lookup.
- Artwork discovery, preservation, processing, and provenance.
- RTFM/manual discovery and association.
- Persistent curation memory and review state.
- Multi-select, move, merge, accept/reject, undo/redo-oriented curation workflows.
- 1G1R selection for Gotek export when desired.
- Reviewable staging/export behavior with explicit safety gates.
- Portable Windows builds and a full Python CLI/core for automation and development.

## Quick start

For normal Windows use, start with **[docs/QUICKSTART.md](docs/QUICKSTART.md)**. Python is only required when you intentionally use the CLI from source or develop the project.

For CLI/development installation:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[artwork,dev]'
```

Then initialize a library root:

```bash
.venv/bin/amiga-adf-library-builder init \
  --library-root /path/to/my-amiga-library
```

## Data and safety model

A configured library root owns the application's working data. Typical locations include:

```text
<library_root>/
├── original/                 immutable ADF/DSK source collection
├── catalog/                  provider/cache/curated metadata state
├── assets/                   artwork and generated metadata/manual assets
├── unknown/                  unresolved/quarantined material
├── work/staging/             reviewable staging runs
├── output/                   generated output
├── config/                   application/operator configuration records
├── reports/                  reports
└── logs/                     logs
```

The exact state ownership is more detailed than this summary; see the documentation index and data-layout reference.

## Documentation

Start at **[docs/README.md](docs/README.md)** for the current documentation map.

Current reference material includes:

- [Quick Start](docs/QUICKSTART.md)
- [Command Reference](docs/COMMANDS.md)
- [Data Layout](docs/DATA-LAYOUT.md)
- [Migration Guide](docs/archive/migrations/portable-path-migration.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Windows Build Reference](docs/BUILD-Windows.md)
- [Architecture](docs/ARCHITECTURE.md)

The documentation refresh is adding a full User Guide, Installation Guide, Windows GUI Walkthrough, Metadata/Provider Guide, expanded Troubleshooting Guide, and consolidated Developer Guide.

## Project philosophy

The application favors reviewable, explainable behavior over aggressive guessing:

- originals are not rewritten;
- ambiguous identity or grouping is surfaced for review;
- online access is explicit/configurable;
- provider/cache/provenance state is kept separate from source disks;
- final export is deliberate and gated;
- curation decisions are intended to persist across reruns instead of forcing the operator to repeat the same work.

## Credits, development history, security, and contribution

The existing project acknowledgements, AI-assisted-development disclosure, security policy, contribution policy, license, and detailed command examples from the current README should be retained below the revised product/usage sections when this replacement is applied. This file intentionally replaces only the stale product-facing upper portion rather than deleting project-history content.

## Credits and acknowledgements

This project exists because of the excellent work behind the [Gotek Touchscreen Interface](https://github.com/mesarim/Gotek-Touchscreen-interface) — **“The GTi — The Floppy Flinger Thinger.”**

Special thanks to **Mez and Dimmy (Dimitri Hilverda) of OMEGAWARE** for creating and sharing GTi. Their project turns a Gotek-style floppy emulator into a much richer experience with touchscreen browsing, cover artwork, game information, multi-disk handling, and Amiga ADF support. Amiga ADF Library Builder is not intended to replace GTi; it is a companion utility created to make preparing, organizing, enriching, and exporting libraries for it easier.

If this tool is useful in your Gotek setup, please visit, support, and contribute to the original GTi project.

### Project team

A lot of people — and a lot of silicon — helped get this project across the finish line.

- **DavidZinAZ** — human project owner and operator; set the direction, tested real collections and hardware workflows, and made the final product decisions.
- **Dumbo** — AI operator-liaison and dispatcher profile; helped coordinate work across the project team and route tasks to the appropriate specialist agents.
- **Hannibal** — planning and coordination; helped turn broad goals into bounded implementation work.
- **Case** — software engineering; carried much of the implementation and remediation work.
- **Columbo** — independent QA; persistently checked behavior, regressions, and evidence.
- **Worf** — security review; challenged trust boundaries, path handling, secrets, network behavior, and release safety.
- **Gunny** — Git and release hygiene; helped keep commits, packaging, repository state, and publication work disciplined.
- **Dixie** — systems support; helped with runtime, deployment, storage, and environment-level work.
- **Q Branch** — technical research and evaluation; investigated tools, integrations, providers, and implementation options.
- **Scout (ChatGPT)** — technical advisor and project partner; assisted with architecture, debugging, review, documentation, release preparation, and the final clean-room public release.

Thank you to everyone who tested, reviewed, challenged assumptions, documented behavior, or helped turn rough ideas into something reproducible.

### AI-assisted development

This project was created with substantial AI assistance. AI was used throughout architecture, coding, testing, debugging, security review, documentation, research, release preparation, and project coordination.

The development workflow included **ChatGPT by OpenAI**, the **Hermes Project** multi-agent environment, locally hosted **Qwen3.6-35B-A3B**, and **Step 3.7 Flash** during portions of testing and evaluation.

AI output was treated as engineering input rather than unquestioned truth: changes were tested, reviewed, revised, and validated against the application, synthetic fixtures, and hardware-oriented workflows.

Open-source software is possible because people share their work, knowledge, libraries, documentation, testing, and time. Thank you to everyone whose work made this project possible.

## Security

Amiga ADF Library Builder is offline by default; online enrichment is opt-in via
`--online`, and all outbound HTTP(S) fetches are guarded against non-public
(loopback/link-local/RFC1918/IPv6-ULA) targets. See
[`SECURITY.md`](./SECURITY.md) for the vulnerability-reporting policy and the
documented security model, and [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the
development workflow.
