# Building the Windows Standalone GUI (Issue #15)

This document describes how to produce a **portable, self-contained Windows
build** of the Amiga ADF Library Builder PySide6 GUI using PyInstaller. It
covers prerequisites, Linux-side validation (which you can do today), the
GitHub Actions `windows-latest` path that produces the real artifact, how to run
the result, the portable directory layout, known limitations, and the
operator push gate.

> Packaging/sysadmin scope only. The GUI logic and the core library are owned by
> other tickets (Case / Worf re-review PASS). This build freezes what already
> exists; it does not modify it.

---

## 1. What gets built

| Target    | Command switch                  | Output                                            | Uploaded artifact      |
|-----------|--------------------------------|---------------------------------------------------|------------------------|
| `onedir`  | `--target onedir`  (default)   | `dist/AmigaADFLibraryBuilder/` (extract-and-run)  | `amiga-adf-gui-portable` (zip) |
| `onefile` | `--target onefile`             | `dist/amiga-adf-gui.exe` (single file)            | `amiga-adf-gui-onefile` (exe)   |

* **Primary MVP:** the `onedir` portable folder. The user extracts it anywhere
  (USB stick, `C:\Program Files`, a path with spaces) and runs
  `AmigaADFLibraryBuilder\AmigaADFLibraryBuilder.exe`.
* **Secondary:** a single `amiga-adf-gui.exe` produced by the `onefile` target.
* Both are **64-bit Windows / Python 3.12**, built with `console=False`
  (windowed). A debug build with a console window is available via
  `python tools/build_windows.py --target onedir --console`.

### Hook target

The frozen entry point is:

```
amiga_adf_library_builder.gui.app:run
```

`GuiApp` constructs the `QApplication`, installs the secret-redaction filter,
builds the portable paths / settings / secret stores, shows `MainWindow`, and
calls `exec()`. No admin rights are required.

---

## 2. Prerequisites

### 2.1 To run the produced `.exe` (end users)

* A 64-bit Windows machine (Windows 10/11).
* **Nothing else.** No Python install, no pip, no DLL drop. PyInstaller bundles
  the interpreter, the package, and the Qt6 runtime.

### 2.2 To build locally (developers, on Windows)

* Windows 10/11, 64-bit.
* Python 3.12 (matching the CI target) from python.org.
* Build toolchain:

  ```bat
  python -m venv .venv
  .venv\Scripts\activate
  pip install -e ".[gui]"
  pip install pyinstaller
  python tools\build_windows.py --target onedir --clean
  python tools\build_windows.py --target onefile
  ```

> Cross-compiling a Windows binary from Linux is **not possible** with
> PyInstaller. The authoritative artifact is produced on GitHub Actions
> `windows-latest` (Section 4).

### 2.3 To validate on Linux (no Windows needed — you can do this now)

See Section 3.

---

## 3. Linux-side validation (do this before any push)

Everything below runs on Linux and proves the packaging inputs are sound. None
of it produces a Windows binary.

```bash
# 0. Set up an isolated venv (base Python here is externally managed).
python3 -m venv /tmp/venv-adfgui
/tmp/venv-adfgui/bin/python -m pip install -e ".[gui]" pyinstaller

# 1. The PyInstaller hook target imports cleanly.
/tmp/venv-adfgui/bin/python -c \
  "import amiga_adf_library_builder.gui.app as a; assert callable(a.run)"

# 2. Tooling is present.
/tmp/venv-adfgui/bin/python -m pyinstaller --version

# 3. The driver is valid and usable.
/tmp/venv-adfgui/bin/python -m py_compile tools/build_windows.py
/tmp/venv-adfgui/bin/python tools/build_windows.py --help

# 4. Dry run: emit the spec + show the PyInstaller command (no build).
/tmp/venv-adfgui/bin/python tools/build_windows.py --target onedir --print-cmd

# 5. The CI workflow YAML is well-formed.
/tmp/venv-adfgui/bin/python -c \
  "import yaml; yaml.safe_load(open('.github/workflows/build-windows.yml'))"

# 6. Review what was added before committing.
git diff --stat
```

**Confirm the following by inspection:**

* `AmigaADFGui.spec` / `tools/build_windows.py` reference
  `amiga_adf_library_builder.gui.app:run` and set `console=False` for the
  shipped build.
* **No secrets are bundled.** The `datas` list is empty and no `*.vault`,
  `*.env`, or password files are referenced. Runtime state (config/secrets/
  logs/cache) is created under the portable app base dir at runtime via
  `PortablePaths` — it is never shipped.
* `app.py` sets `AA_EnableHighDpiScaling` + `AA_UseHighDpiPixmaps` **before**
  `QApplication` is created (preserved; do not reorder).
* `PortablePaths._default_base()` resolves relative to `sys.executable`
  (frozen → app dir) and does **not** fall back to `Path.home()` at runtime
  (Worf finding F8).

---

## 4. The CI path (produces the real artifact)

`.github/workflows/build-windows.yml` runs on `windows-latest` and:

1. Checks out the branch.
2. Sets up Python 3.12.
3. Installs the package (`pip install -e ".[gui]"`) and `pyinstaller`.
4. Verifies the hook target imports.
5. Runs `tools/build_windows.py --target onedir --clean` and
   `--target onefile`.
6. Runs a **headless GUI smoke test** under `QT_QPA_PLATFORM=offscreen`
   (constructs `GuiApp` without showing a window) as a build-time sanity gate.
   > Full windowed, real-execution qualification (launch, offline export under a
   > path with spaces, settings persistence, theme switch, actionable failure
   > path) is owned by the independent QA ticket; it is **not** performed in
   > this build workflow.
7. Packages the onedir folder as `amiga-adf-gui-portable.zip` and uploads both
   `amiga-adf-gui-portable` and `amiga-adf-gui-onefile` as workflow artifacts.

This workflow **only** builds + uploads artifacts. It does **not** auto-release
or publish.

---

## 5. Running the produced `.exe`

### 5.1 onedir (MVP)

```
AmigaADFLibraryBuilder/
└── AmigaADFLibraryBuilder.exe
└── (Qt DLLs, python3X.dll, _internal/, etc.)
```

Extract the zip and run `AmigaADFLibraryBuilder\AmigaADFLibraryBuilder.exe`.
No install step.

### 5.2 onefile

Run `amiga-adf-gui.exe`. PyInstaller extracts to a temp dir on first launch,
so startup is slower than onedir; otherwise behavior is identical.

### 5.3 Portable directory layout (created at runtime)

The app writes nothing to the user profile. All runtime state is created under
the app base dir (parent of the `.exe`) via `PortablePaths`:

```
<app base dir>/
├── config/      gui-settings.toml, secrets.vault (if a master password is set)
├── data/        runtime working data
├── logs/        diagnostic logs (secret-redacted)
├── cache/       transient cache
└── themes/      theme .qss files (loaded at runtime; none shipped today)
```

Override the base at launch with `AMIGA_ADF_GUI_BASE=<dir>` (used by tests and
advanced users).

---

## 6. Known limitations

* **Windows-only.** There is no macOS/Linux standalone build from this spec.
  (The package remains a normal `pip install .[gui]` Python app on those OSes.)
* **Optional features degrade gracefully.** Artwork (Pillow) and RTFM/PDF/Tesseract
  extraction (`pypdf`/`fitz`/`pytesseract`) are *lazy* imports inside the core.
  The Windows build bundles **only** the `gui` extra (PySide6, cryptography,
  tomli-w). If a user invokes artwork/RTFM features, the app raises a clear
  "dependency unavailable" error rather than crashing. To bundle those, add
  `artwork`/`rtfm-docs` to the install step and the spec's hidden imports.
* **No code signing.** The artifact is unsigned. Windows SmartScreen may warn on
  first launch. Signing is a separate operator concern (certificate + `codesign`
 /`signtool`).
* **onefile is slower to start** (self-extracts to temp each run).
* **No auto-update.** Distribution is manual (download the artifact).

---

## 7. Operator push gate (read before building on CI)

Producing the **real** Windows artifact requires the workflow to run on GitHub
Actions `windows-latest`, which requires pushing `feat/issue15-windows-gui` to
`origin`. Under the PLAN → EXECUTE → COMMIT → PUSH → PRODUCTION governance, that
is an explicit **PUSH** action and is **not** performed automatically.

* If you have explicit operator authorization (e.g. a ticket comment
  `AUTHORIZED: push feat/issue15-windows-gui to origin for CI`), run:

  ```bash
  git push -u origin feat/issue15-windows-gui
  ```

  then trigger the workflow (push / `workflow_dispatch`) and capture the run URL
  + artifact URLs.

* **If no such authorization is present, do not push.** Complete the packaging
  work (spec, build script, CI file, docs, Linux-side validation), commit
  locally, and raise the gate with the exact command above. Making the repo
  public or cutting a release is a **separate** gate and must never happen here.

---

## 8. Files owned by this packaging work

| File                                | Purpose                                          |
|-------------------------------------|--------------------------------------------------|
| `tools/build_windows.py`            | Reproducible PyInstaller driver (`--target`, `--print-cmd`). |
| `AmigaADFGui.spec`                  | Committed, deterministic spec (generated by the driver). |
| `.github/workflows/build-windows.yml` | `windows-latest` build + artifact upload.      |
| `docs/BUILD-Windows.md`             | This document.                                   |
| `pyproject.toml`                    | Adds `amiga-adf-gui` dev console script.         |
