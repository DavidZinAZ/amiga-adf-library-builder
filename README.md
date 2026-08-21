# Amiga ADF Library Builder

A preservation-first command-line tool that scans an Amiga disk-image collection, groups multi-disk releases, enriches them with metadata and artwork, and builds a reviewable Gotek staging tree.

This project is designed as a companion utility for the excellent [Gotek Touchscreen Interface (GTi)](https://github.com/mesarim/Gotek-Touchscreen-interface). It helps prepare, organize, enrich, and export libraries in a layout that GTi can use; it is not a replacement for or fork of GTi.

> **Important:** the application never writes directly to the SD-card output during `export`. It writes to a run-owned staging directory so the result can be reviewed before publishing.

## Download Windows App

**No Python required.** The standalone Windows application is distributed as a portable ZIP and a single EXE.

### Quick start
1. Go to the [latest release](https://github.com/DavidZinAZ/amiga-adf-library-builder/releases/latest).
2. Download either:
   - **`amiga-adf-gui-portable.zip`** — extract anywhere, run `AmigaADFLibraryBuilder/AmigaADFLibraryBuilder.exe`
   - **`amiga-adf-gui.exe`** — single portable executable, run directly
3. No installation, no Python, no dependencies. All runtime state (config, cache, logs) stays under the portable folder.

Each release page shows the version tag, release notes, and the exact source commit the artifacts were built from.

## What it does

- scans immutable `.adf` and `.dsk` originals;
- parses title, disk number, edition, chipset, crack group, and related release markers;
- groups complete disk sets and quarantines ambiguous/incomplete groups;
- enriches accepted releases with online metadata and artwork when `--online` is used;
- caches metadata, provenance, artwork masters, and processed JPGs;
- writes concise Gotek-compatible `.nfo` files while preserving detailed provenance separately;
- creates a Gotek-compatible staging tree under `<library_root>/work/staging/<run-id>`;
- preserves source disk images byte-for-byte.

## Configuration

The tool no longer hard-codes any host path. You point it at a **library root** and it derives every working directory beneath it (`original/`, `work/staging/`, `output/`, `unknown/`, `config/manual-approvals/`, `reports/`, `logs/`). The cache defaults to the XDG cache (`~/.cache/amiga-adf-library-builder`).

Configuration is discovered in this order (highest → lowest precedence):

1. an explicit CLI flag (`--library-root`, `--original-dir`, …);
2. an environment variable (`AMIGA_ADF_LIBRARY_ROOT`, `AMIGA_ADF_ORIGINAL_DIR`, …);
3. an explicit `--config <file.toml>`;
4. the XDG per-user config `~/.config/amiga-adf-library-builder/config.toml`;
5. an optional system-wide config `/etc/amiga-adf-library-builder/config.toml`;
6. safe built-in defaults (only once a library root is supplied).

If no library configuration is found, commands print:

```text
No library configuration found.

Run:
  amiga-adf-library-builder init

Or provide:
  --config /path/to/config.toml
```

### Quick start

#### 1. Install for development

From the repository root (the directory containing this README):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[artwork,dev]'
```

#### 2. Initialize a library

Choose a directory to own all library data (originals, staging, output, approvals, logs). It need not live on the same host as the checkout.

```bash
.venv/bin/amiga-adf-library-builder init \
  --library-root /path/to/my-amiga-library
```

This writes a TOML config (per-user by default) and prints the resolved layout. To write to an explicit file, add `--config /path/to/config.toml`. Place immutable source images in `<library_root>/original`.

#### 3. Build an enriched staging tree

```bash
RUN_ID="enriched-$(date +%Y%m%d-%H%M%S)"

.venv/bin/amiga-adf-library-builder export \
  --library-root /path/to/my-amiga-library \
  --online \
  --refresh-metadata \
  --require-artwork \
  --export-gate-acknowledged \
  --run-id "$RUN_ID" \
  --json
```

The output appears under `<library_root>/work/staging/<run-id>`. Review the generated `.nfo` and `.jpg` files before publishing anything to the SD card.

#### Inspect the resolved configuration

```bash
.venv/bin/amiga-adf-library-builder config show
.venv/bin/amiga-adf-library-builder config validate
```

## Common commands

Show help:

```bash
.venv/bin/amiga-adf-library-builder --help
.venv/bin/amiga-adf-library-builder export --help
```

Offline build using cached metadata only:

```bash
.venv/bin/amiga-adf-library-builder build \
  --library-root /path/to/my-amiga-library \
  --json
```

Verify an existing staging run without writing:

```bash
.venv/bin/amiga-adf-library-builder export \
  --library-root /path/to/my-amiga-library \
  --export-gate-acknowledged \
  --verify-only \
  --run-id '<existing-run-id>' \
  --json
```

Run tests:

```bash
TMPDIR="$PWD/.pytest-tmp" .venv/bin/python -m pytest -q
```

## Manual approvals (special-only releases)

Quarantined *special-only* release keys can be approved for publication with a
reviewed CLI workflow (URL allowlist, SHA-256 binding + safe-fail, revocation,
merge, and NFO source-link provenance). The operator commands and record handling
are documented in [`docs/COMMANDS.md`](./docs/COMMANDS.md).

```bash
# list quarantined groups
.venv/bin/amiga-adf-library-builder list-quarantine

# approve a special-only key (hashes computed read-only from original/)
.venv/bin/amiga-adf-library-builder approve \
  --release-key examplequestiii \
  --title "Example Quest III" \
  --folder "Example Quest III" \
  --source-url "https://www.lemonamiga.com/games/details.php?id=example" \
  --role metadata --allow-incomplete --reason "example operator approval"

# re-run the pipeline to apply approvals
.venv/bin/amiga-adf-library-builder build --library-root /path/to/my-amiga-library --export-gate-acknowledged
```

## Data layout

```text
<library_root>/
├── original/                 immutable source ADF/DSK files
├── catalog/
│   ├── metadata-cache/       provider results and provenance
│   └── metadata-curated/     operator overrides
├── assets/
│   ├── artwork-original/     preserved downloaded masters
│   ├── artwork-processed/    Gotek-sized JPG derivatives
│   └── nfo/                  generated rich NFO files
├── unknown/                  ambiguous/incomplete groups
├── work/staging/<run-id>/    reviewable Gotek output
├── output/                   generated output
├── config/manual-approvals/  manual approval records
├── reports/                  run reports
└── logs/                     logs
```

`cache_dir` defaults to `~/.cache/amiga-adf-library-builder` and is overridable.

## Safety model

- `original/` is never modified.
- Online access is opt-in with `--online`.
- `--require-artwork` prevents staging output when accepted releases lack JPG artwork.
- `export` writes only to a new run-owned staging tree.
- Existing SD-card folders are not overwritten by the application.
- Ambiguous groups are quarantined instead of guessed.

## Documentation

- [Quick start](docs/QUICKSTART.md)
- [Command reference](docs/COMMANDS.md)
- [Data and cache layout](docs/DATA-LAYOUT.md)
- [Migration guide](docs/MIGRATION.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)

Additional documents under `docs/` describe Gotek export behavior, NFO/provenance handling, local-media integration, test-corpus expectations, and other implementation details.

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
