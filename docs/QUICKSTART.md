# Quick Start

**Applies to v0.3.13. Reviewed 2026-10-16.**

## Windows

1. Download the [latest Windows release](https://github.com/DavidZinAZ/amiga-adf-library-builder/releases/latest). Python is not needed.
2. Extract `amiga-adf-gui-portable.zip` and run `AmigaADFLibraryBuilder/AmigaADFLibraryBuilder.exe`, or run the standalone `amiga-adf-gui.exe` from a writable folder.
3. The app creates `Library-Root` beside the application. On **Library**, choose **Original disks (read-only)** and the **ADF Library Export Folder**.
4. Add artwork and manual folders on **Local Media**. Artwork category folders are detected automatically.
5. Add DAT files or a folder of DATs on **Metadata Sources** if available.
6. Leave online lookup off for an offline build. Online providers start unchecked; enable and configure only the providers you want.
7. Open **Build & Export** and click **Build library**.
8. Review results in **Preview & Curation**. Compare ambiguous media, choose the preferred release, and correct disk grouping before export.
9. Return to **Build & Export**, check the displayed destination and requirements, then click **Export library**.

Build prepares working data; Export writes a run-owned output folder. The GUI has one Export action and no Check only or acknowledgement checkbox. Original disks and source media stay unchanged.

## Review media

**Use this** saves a choice immediately. **Next** and **Previous** navigate the review. Shared artwork/RTFM is the default; select **Different media per disk** for custom disk choices. Disks without an override inherit shared media.

Review also offers **Move disks to another release…** and **Assign media to another release…**, with undo. See [Media Choices](MEDIA-CHOICES.md).

## CLI / source use

Python 3.11+ is required only for source use. Install `.[gui,dev]` for GUI development or `.[artwork,dev]` for core/artwork work.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[artwork,dev]'
amiga-adf-library-builder init --library-root /path/to/library
amiga-adf-library-builder build --library-root /path/to/library --json
```

The CLI retains `--verify-only` and `--export-gate-acknowledged`; these are separate from the simplified GUI. See [Commands](COMMANDS.md), [Installation](INSTALLATION.md), and [User Guide](USER-GUIDE.md).
