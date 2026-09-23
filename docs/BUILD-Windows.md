# Building the Windows GUI

**Current baseline:** Amiga ADF Library Builder v0.2.26<br>
**Reviewed:** 2026-09-22

This is the current packaging reference for the Windows GUI.

## End users

Released Windows builds require:

- 64-bit Windows 10 or Windows 11;
- no separate Python installation;
- no administrator rights for normal portable execution.

Release artifacts:

- `amiga-adf-gui-portable.zip` — recommended onedir portable build;
- `amiga-adf-gui.exe` — single-file build.

The builds are unsigned, so Windows SmartScreen may display a warning.

## Developer prerequisites

The project supports Python 3.11+, while the authoritative Windows CI packaging
lane uses Python 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[gui]"
python -m pip install pyinstaller
```

## Build locally on Windows

Onedir:

```powershell
python tools\build_windows.py --target onedir --clean
```

Onefile:

```powershell
python tools\build_windows.py --target onefile
```

PyInstaller Windows binaries must be built on Windows; Linux is not an
authoritative cross-compilation path.

## GitHub Actions packaging

Workflow:

```text
.github/workflows/build-windows.yml
```

The Windows job:

1. checks out the requested commit;
2. installs Python 3.12;
3. installs `.[gui]` plus PyInstaller;
4. verifies the GUI entry point;
5. verifies version-source consistency;
6. verifies Pillow is available;
7. builds onedir;
8. builds onefile;
9. performs an offscreen GUI construction smoke test;
10. packages/uploads both artifacts.

For tag builds, release publication also checks that the normalized tag exactly
matches `pyproject.toml` `[project].version`.

## Entry point and spec

The packaged GUI ultimately launches:

```text
amiga_adf_library_builder.gui.app:run
```

`AmigaADFGui.spec` is generated/maintained by the Windows build tooling. Do not
treat it as an independent version source.

## Portable runtime layout

Frozen builds use the executable directory as their default application base:

```text
<app-base>/
├── config/
├── data/
├── logs/
├── cache/
└── themes/
```

`AMIGA_ADF_GUI_BASE` can override the base for advanced use.

This application base is separate from the Amiga Library Root.

## Secrets

No runtime vault, API key, password, or user credential belongs in the
PyInstaller bundle. Secret state is created at runtime.

## Release qualification

A release should be tested from the actual packaged Windows artifact, not only
from source. At minimum verify:

- executable launches;
- displayed/application version is correct;
- paths with spaces work;
- Library Root selection works;
- Preview & Curation populates;
- provider/settings state loads;
- artwork processing works;
- move/merge and lookup workflows work;
- Manual Lookup -> Preview -> Export state survives;
- check-only validation works;
- final export works;
- original ADF/DSK files remain unchanged.

See [DEVELOPER-GUIDE.md](DEVELOPER-GUIDE.md) for broader release engineering.
