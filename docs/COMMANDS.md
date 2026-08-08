# Command Reference

All normal commands (`scan`, `build`, `export`, `verify-export-gate`,
`list-quarantine`, `approve`, `list-approvals`, `inspect-approval`, `revoke`)
accept the same portable path-resolution flags and **never prompt for paths**:

- `--config <path>` — explicit TOML config file.
- `--library-root <path>` — library root directory.
- `--original-dir`, `--staging-dir`, `--output-dir`, `--quarantine-dir`,
  `--approvals-dir`, `--reports-dir`, `--logs-dir`, `--cache-dir` — per-role
  overrides (each optional; unspecified roles are derived beneath the library root).

Environment overrides (`AMIGA_ADF_LIBRARY_ROOT`, `AMIGA_ADF_ORIGINAL_DIR`,
`AMIGA_ADF_STAGING_DIR`, `AMIGA_ADF_OUTPUT_DIR`, `AMIGA_ADF_QUARANTINE_DIR`,
`AMIGA_ADF_APPROVALS_DIR`, `AMIGA_ADF_REPORTS_DIR`, `AMIGA_ADF_LOGS_DIR`,
`AMIGA_ADF_CACHE_DIR`, and the global `AMIGA_ADF_CONFIG`) take precedence below
an explicit flag but above the discovered config file.

## `init`

Creates a portable library configuration. Interactive by default; add
`--no-input` for non-interactive use.

```bash
amiga-adf-library-builder init \
  --library-root /path/to/my-amiga-library \
  [--original-dir ...] [--output-dir ...] [--staging-dir ...] \
  [--config /path/to/config.toml] [--system] [--original-readonly]
```

Writes a deterministic TOML config and prints the resolved layout, marking each
role as derived or explicit.

## `config show`

Prints the resolved configuration: config source, every resolved path, which
roles are derived vs explicit, whether `original_dir` appears writable, and any
overlap/containment warnings. Deterministic output.

```bash
amiga-adf-library-builder config show [--config ...] [--library-root ...]
```

## `config validate`

Validates path relationships and permissions. Returns a non-zero exit code when
the configuration is unsafe (missing `original_dir`, unwritable role dirs, or
containment violations).

```bash
amiga-adf-library-builder config validate [--config ...] [--library-root ...]
```

## `scan`

Read-only dry-run entry point.

```bash
amiga-adf-library-builder scan [--config ...] [--library-root ...] [--online]
```

## `build`

Scans, parses, groups, enriches, caches metadata, generates NFO/artwork assets,
and quarantines unresolved groups. It does not create the Gotek staging tree.

```bash
amiga-adf-library-builder build \
  [--config ...] [--library-root ...] \
  [--online] [--refresh-metadata] \
  [--export-gate-acknowledged] [--json]
```

## `export`

Runs the pipeline and writes a run-owned Gotek staging tree.

```bash
amiga-adf-library-builder export \
  [--config ...] [--library-root ...] \
  [--online] [--refresh-metadata] [--require-artwork] \
  --export-gate-acknowledged \
  [--verify-only] [--run-id ID] [--json]
```

Important options:

- `--online`: allow network metadata/artwork retrieval.
- `--refresh-metadata`: ignore cached provider records and query again.
- `--require-artwork`: preflight accepted releases and refuse staging writes when any JPG is missing.
- `--verify-only`: inspect conflicts and report intended changes without writing.
- `--run-id`: name the staging directory; use only letters, digits, dots, underscores, and hyphens.
- `--json`: emit machine-readable results.

## `verify-export-gate`

Reports whether export is permitted without writing anything.

```bash
amiga-adf-library-builder verify-export-gate --export-gate-acknowledged
```

## manual-approval feature manual-approval commands

These take the same config flags as above. They never write to `original/` and
never write to the SD card.

- `list-quarantine` — list groups currently quarantined (special-only).
- `approve` — create a manual approval record (SHA-256 bound read-only from `original/`).
- `list-approvals` — list all loaded approval records.
- `inspect-approval --approval-id ID` — print one record in full.
- `revoke --approval-id ID --reason TEXT` — revoke (history retained, never deleted).

See the approval command sections above for the operator workflow.
