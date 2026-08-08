# Migration Guide — Portable Path Configuration (portable path configuration)

This guide explains how to move an existing install from the old hard-coded
host paths (`DEFAULT_DATA_ROOT` / `DEFAULT_SD_ROOT`, the `--data-root` /
`--sd-root` CLI model, and the legacy `[data]`/`[output]` config schema) to the
new portable path-configuration model.

The core change: **no host path is hard-coded anywhere in the application.** A
single `library_root` anchors every working directory, and `cache_dir` defaults
to the XDG cache. You point the tool at your data; the tool no longer assumes
where your data lives.

## What changed

| Old (pre-#2) | New (portable path configuration) |
|--------------|---------------|
| old `DEFAULT_DATA_ROOT` host-specific default | `--library-root <path>` or `library_root` in config |
| old `DEFAULT_SD_ROOT` host-specific default | explicit publish step (not an app path) |
| `--data-root` / `--sd-root` CLI flags | `--library-root` + per-role `--*-dir` overrides |
| `original/` | `original_dir` (read-only) |
| `work/staging/<run-id>` | `staging_dir` (resolved from config) |
| generated output | `output_dir` |
| `unknown/` (quarantine) | `quarantine_dir` |
| `config/manual-approvals/` | `approvals_dir` (manual-approval feature records preserved) |
| reports | `reports_dir` |
| logs | `logs_dir` |
| embedded data-root config | XDG `~/.config/amiga-adf-library-builder/config.toml` |
| — | `cache_dir` (XDG cache by default) |

Both `config/manual-approvals/` (the manual-approval feature approval records) and `catalog/`,
`assets/`, `review/`, `rejected/` remain predictable children of `library_root`.
**No existing approval record is moved, rewritten, or re-hashed by migration.**

## The new config schema

```toml
library_root = "/path/chosen/by/you"

# Optional explicit overrides. Omit any of these and the tool derives it
# beneath library_root:
original_dir   = "/path/chosen/by/you/original"
staging_dir    = "/path/chosen/by/you/work/staging"
output_dir     = "/path/chosen/by/you/output"
quarantine_dir = "/path/chosen/by/you/unknown"
approvals_dir  = "/path/chosen/by/you/config/manual-approvals"
reports_dir    = "/path/chosen/by/you/reports"
logs_dir       = "/path/chosen/by/you/logs"
cache_dir      = "/path/to/cache/amiga-adf-library-builder"   # default: XDG cache
```

Derived (when only `library_root` is set):

- `original_dir`     → `<root>/original`
- `staging_dir`      → `<root>/work/staging`
- `output_dir`       → `<root>/output`
- `quarantine_dir`   → `<root>/unknown`
- `approvals_dir`    → `<root>/config/manual-approvals`
- `reports_dir`      → `<root>/reports`
- `logs_dir`         → `<root>/logs`
- `cache_dir`        → `~/.cache/amiga-adf-library-builder` (overrideable)

## Precedence (highest → lowest)

1. CLI flag (`--library-root`, `--original-dir`, …)
2. Environment variable (`AMIGA_ADF_LIBRARY_ROOT`, `AMIGA_ADF_ORIGINAL_DIR`, …)
3. Explicit `--config <file.toml>`
4. XDG per-user config `~/.config/amiga-adf-library-builder/config.toml`
5. Optional system-wide config `/etc/amiga-adf-library-builder/config.toml`
6. Safe built-in defaults (used only once a library root is supplied)

`AMIGA_ADF_CONFIG` selects an explicit config file (same as `--config`).

## Migration procedure (safe, non-destructive)

The migration tooling **never silently moves your data.** It shows you the
proposed resolved configuration and requires explicit confirmation before
creating or moving anything.

### Step 1 — Author the new config

Run `init` non-interactively to write a deterministic config, then review it:

```bash
amiga-adf-library-builder init \
  --no-input \
  --library-root /path/to/my-amiga-library \
  --config /path/to/config.toml
```

Or interactively (prompts for the read-only confirmation and per-role dirs):

```bash
amiga-adf-library-builder init
```

### Step 2 — Preview the resolved layout (dry run)

```bash
amiga-adf-library-builder config show --config /path/to/config.toml
amiga-adf-library-builder config validate --config /path/to/config.toml
```

`config show` prints each resolved path and whether it is derived or an explicit
override, plus an `original_writable` probe and any overlap/containment
warnings. `config validate` exits non-zero if `original_dir` is missing or a
writable role directory is not writable.

### Step 3 — Point existing data at the new layout (only with confirmation)

If your existing corpus, approvals, catalog, and output already live under one
directory that you now choose as `library_root`, nothing needs to move — the
derived roles match the historical layout (`original/`, `work/staging/`,
`output/`, `unknown/`, `config/manual-approvals/`, `reports/`, `logs/`).

If you want to relocate data, do it yourself with ordinary filesystem tools
(`cp -a` / `mv`) and update the config to point at the new location. The
application will create missing managed directories (under `library_root`) when
safe and authorized, but it will never move large data trees on your behalf.

### Step 4 — Verify before publishing

Build/export write only to the run-owned staging tree. Confirm originals are
untouched and the staging tree is correct before any SD-card publish step.

```bash
amiga-adf-library-builder build --config /path/to/config.toml --export-gate-acknowledged
amiga-adf-library-builder export --config /path/to/config.toml --export-gate-acknowledged --verify-only --run-id <id> --json
```

## Path-role safety guarantees (enforced at load time)

- `original_dir` is treated **read-only** by the application. Approval commands
  may read and hash originals but never alter them.
- `output_dir`, `staging_dir`, `cache_dir`, and `quarantine_dir` must not equal
  `original_dir` and must not be inside `original_dir`.
- Symlink resolution (`Path.resolve()`) is applied before containment checks, so
  a symlinked role directory cannot bypass the read-only restriction.
- Normal build/qualification does **not** write to mounted publication media;
  publishing is a separate explicit operator action.

## Keeping private paths local (maintainer)

The maintainer's private paths may stay in a **git-ignored** local config file
rather than a committed one. `config/local.toml` is already git-ignored. For
example:

```toml
# config/local.toml  (git-ignored; never committed)
library_root = "/your/private/library/path"
```

Then run with `--config config/local.toml`, or rely on the XDG/system config
discovery. Existing approval records and their byte-for-byte integrity are
preserved regardless of which config file selects the library root.

## Rollback

Because nothing is moved automatically, rollback is just: stop using the new
config and revert the working-tree changes (or `git checkout` the config file).
No data was relocated, so the previous layout is intact.
