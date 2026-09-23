# Quick Start

**Applies to:** Amiga ADF Library Builder v0.2.26

## Windows — recommended path

Normal Windows users do **not** need Python.

1. Download the latest release.
2. Choose either:
   - `amiga-adf-gui-portable.zip` — recommended; extract it and run `AmigaADFLibraryBuilder\AmigaADFLibraryBuilder.exe`.
   - `amiga-adf-gui.exe` — single-file portable executable.
3. Start the application.
4. On the **Library** tab, choose a Library Root.
5. Put or point your preservation `.adf`/`.dsk` collection at the Original Disks location.
6. On **Options**, decide whether online metadata is allowed. Leave **Include artwork** enabled if desired and enable **Include manuals (RTFM)** if you have manual sources.
7. In **Run / Export Settings**, choose **Build the library (scan, organize, prepare)** for the first run.
8. Click **Run**.
9. Review the results under **Preview & Curation** before doing a final export.

For the first pass, do **not** start with a final export.

## Review before export

Use these Preview filters to find work quickly:

- Needs Review
- Missing Artwork
- Missing RTFM
- Unmatched
- Ghost

Resolve incorrect matches, move/merge errors, artwork, manuals, and preferred releases before publishing a final library.

## Safe export

When the library is ready:

1. Choose **Export the library**.
2. Enable **Check only — don't change files**.
3. Run verification.
4. Confirm the destination and diagnostics.
5. Disable Check only.
6. Enable **I understand this run will write files**.
7. Run the final export.

## Developer / CLI quick start

The Python package requires Python 3.11+.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[artwork,dev]'
```

Initialize a library:

```bash
amiga-adf-library-builder init --library-root /path/to/my-amiga-library
```

Build offline:

```bash
amiga-adf-library-builder build \
  --library-root /path/to/my-amiga-library \
  --json
```

See the [Full User Guide](USER-GUIDE.md), [Installation Guide](INSTALLATION.md), and [Command Reference](COMMANDS.md) for more detail.
