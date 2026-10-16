# Installation and Upgrades

**Applies to v0.3.13. Reviewed 2026-10-16.**

## Portable Windows installation

Download from [GitHub Releases](https://github.com/DavidZinAZ/amiga-adf-library-builder/releases/latest). Windows 10/11, 64-bit, is the supported packaged target. Use a folder your account can write to; normal portable operation does not require administrator rights or a separate Python installation.

| Download | How to run |
|---|---|
| `amiga-adf-gui-portable.zip` | Extract all files and run `AmigaADFLibraryBuilder/AmigaADFLibraryBuilder.exe` |
| `amiga-adf-gui.exe` | Place in a writable folder and run directly |

Keep the ZIP's `_internal` directory with its executable. The single-file build unpacks bundled components temporarily and can start more slowly. Both include GUI, artwork processing, PDF extraction, and offline OCR dependencies. No separate Tesseract installation is needed for the bundled PDF OCR engine.

Releases are unsigned; Windows may request additional confirmation. Obtain files from the project's release page and check that the displayed version matches the selected release. Do not assume a checksum manifest exists unless one is actually attached to that release.

## Portable state

The frozen application's base is normally its executable directory. It creates portable configuration, cache/log/theme directories and `Library-Root`. The Library tab selects original disks and export output; the GUI Library-Root is managed automatically.

Advanced/source use can override the application base with `AMIGA_ADF_GUI_BASE`. This moves the app-relative base, including its Library-Root. CLI library-root/path overrides remain separate. See [Data Layout](DATA-LAYOUT.md).

## Upgrade without losing curation

1. Close the application and back up `Library-Root`, `config/gui-settings.toml`, and any credential vault and custom themes.
2. Extract the new portable ZIP into a separate writable folder. Keep the old installation until the update is checked.
3. Copy the backed-up application state and Library-Root into the new base, preserving their relative layout. If you used external original/export folders, verify the restored paths.
4. Launch the new executable and confirm its version, provider choices, DAT sources, and curation state.
5. Run **Build library** to refresh generated candidates. Inspect saved choices before exporting.

Do not copy old `_internal` libraries over a new build. Keep original ADF/DSK collections and source media outside generated build artifacts. A credential vault also needs its existing unlock password.

## Source / CLI installation

Python 3.11+ is required. From a checkout, Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[gui,dev]"
```

For Linux/macOS core development:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[gui,dev]'
```

Use `.[artwork,dev]` for core/artwork work without the GUI; add `rtfm-docs` for PDF/OCR processing. The core dependency list is empty, but GUI/PDF/artwork workflows require their extras.

See [Quick Start](QUICKSTART.md), [Developer Guide](DEVELOPER-GUIDE.md), and [Windows Build Reference](BUILD-Windows.md).
