# Quick Start

## Prerequisites

- Python 3.11 or newer
- Pillow for artwork processing
- read/write access to the configured library root
- internet access only when using `--online`

## Install

From the repository root (the directory containing `pyproject.toml`):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[artwork,dev]'
```

## Initialize

Choose a directory to own all library data:

```bash
.venv/bin/amiga-adf-library-builder init \
  --library-root /path/to/my-amiga-library
```

This writes a config (per-user by default) and prints the resolved layout. To write to an explicit file, add `--config /path/to/config.toml`. All role directories are derived beneath `<library_root>` unless you override them.

## Add source files

Copy source `.adf` and `.dsk` files into:

```text
<library_root>/original
```

Do not rename or modify the originals after importing them.

## Create an enriched staging run

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

Expected results:

- metadata JSON under `catalog/metadata-cache`;
- artwork masters under `assets/artwork-original`;
- processed JPGs under `assets/artwork-processed`;
- rich NFOs under `assets/nfo`;
- Gotek output under `work/staging/$RUN_ID`.

## Review before publishing

```bash
find "<library_root>/work/staging/$RUN_ID" \
  -type f -printf '%P\n' | sort
```

Inspect NFOs:

```bash
find "<library_root>/work/staging/$RUN_ID" \
  -type f -name '*.nfo' -print -exec sed -n '1,220p' {} \;
```

Inspect artwork:

```bash
find "<library_root>/work/staging/$RUN_ID" \
  -type f -iname '*.jpg' -printf '%p\n'
```

The application does not publish directly to the SD card; copy the verified staging output to the SD card using your normal deployment process.
