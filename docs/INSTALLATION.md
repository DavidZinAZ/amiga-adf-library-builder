# Amiga ADF Library Builder — Installation Guide

**Applies to:** Amiga ADF Library Builder v0.2.26<br>
**Primary platform:** Windows 10/11, 64-bit<br>
**Guide date:** 2026-09-22

---

## 1. Recommended installation method

For most users, use the **portable Windows ZIP**.

You do **not** need:

- Python
- pip
- an installer
- administrator rights
- extra DLL downloads

The Windows release already contains the application, Python runtime, Qt runtime, and normal GUI dependencies.

The two distributed Windows packages are:

```text
amiga-adf-gui-portable.zip
amiga-adf-gui.exe
```

The portable ZIP is recommended for normal use because its directory structure is easier to inspect, back up, upgrade, and troubleshoot.

---

## 2. System requirements

### Windows

- Windows 10 or Windows 11
- 64-bit Windows
- enough disk space for:
  - the application;
  - your original ADF/DSK library;
  - metadata cache;
  - artwork;
  - manuals;
  - staging/export output.

The v0.2.26 Windows GUI is built as a 64-bit application.

### Internet access

Internet access is **not required** for the base application.

You need Internet access only when using online providers or downloading online metadata/artwork/manual content.

### Administrator rights

Administrator rights are not required for normal use.

For the smoothest portable experience, install the application in a directory where your Windows account has write access.

---

## 3. Download the release

Open the project's GitHub **Releases** page and select the latest release.

For v0.2.26, the release is:

```text
Amiga ADF Library Builder v0.2.26
```

Choose one of these files:

### Recommended

```text
amiga-adf-gui-portable.zip
```

### Alternative

```text
amiga-adf-gui.exe
```

The v0.2.26 release publishes both artifacts.

---

## 4. Verify the downloaded file

The GitHub release records SHA-256 digests for the release assets.

For v0.2.26:

```text
amiga-adf-gui-portable.zip
SHA-256:
6b51602814c3990a1af43add195fef76c70f4b1bf43f84cd28423c445fd60243
```

```text
amiga-adf-gui.exe
SHA-256:
93f3c089ffc70a3e7e4df66d109191eae1034f6c5474c9dbd2229e46c63416ee
```

### Verify with PowerShell

For the ZIP:

```powershell
Get-FileHash .\amiga-adf-gui-portable.zip -Algorithm SHA256
```

For the EXE:

```powershell
Get-FileHash .\amiga-adf-gui.exe -Algorithm SHA256
```

Compare the returned hash with the release digest.

If the values do not match, do not run the file. Download it again from the official release.

---

## 5. Install the portable ZIP

### Step 1 — Create a program directory

Choose a folder such as:

```text
C:\Tools\AmigaADFBuilder
```

or:

```text
D:\Apps\AmigaADFBuilder
```

A location outside `Program Files` is usually simplest because the application stores portable runtime state under its own base directory.

### Step 2 — Extract the ZIP

Extract:

```text
amiga-adf-gui-portable.zip
```

into the chosen folder.

The extracted application contains a directory similar to:

```text
AmigaADFLibraryBuilder\
    AmigaADFLibraryBuilder.exe
    _internal\
    ...
```

### Step 3 — Start the application

Run:

```text
AmigaADFLibraryBuilder.exe
```

No additional installation step is required.

---

## 6. Install the single-file version

If you prefer the one-file package, place:

```text
amiga-adf-gui.exe
```

in a writable application directory and run it directly.

Example:

```text
C:\Tools\AmigaADFBuilder\amiga-adf-gui.exe
```

The one-file version uses PyInstaller's single-file packaging and may start more slowly because its bundled files are unpacked to a temporary location when it launches.

Functionally, it is intended to provide the same application experience as the portable-directory build.

---

## 7. Windows SmartScreen warning

The current Windows release is not code-signed.

Windows SmartScreen may therefore display a warning the first time you run it.

This does not by itself mean the file is malicious; unsigned independently distributed applications commonly trigger this warning.

Before running the application:

1. make sure you downloaded it from the project's official GitHub release;
2. verify the SHA-256 digest;
3. confirm that the release version is the version you intended to install.

Do not bypass a security warning for a file obtained from an unknown mirror or third-party download site.

---

## 8. Portable application directories

ADF Builder uses a portable, application-relative runtime layout.

The application creates runtime directories underneath its application base directory, including:

```text
<application directory>\
├── config\
├── data\
├── logs\
├── cache\
└── themes\
```

Typical contents include:

```text
config\
    gui-settings.toml
    secrets.vault

data\
    metadata_sources.db

logs\
    ...

cache\
    ...

themes\
    ...
```

This portable runtime directory is **not the same thing** as your Amiga library root.

---

## 9. Application directory vs. Library Root

These two locations serve different purposes.

### Application directory

Contains the Windows program and portable application state.

Example:

```text
C:\Tools\AmigaADFBuilder
```

### Library Root

Contains your Amiga source library and generated library data.

Example:

```text
D:\AmigaLibrary
```

A recommended layout is:

```text
C:\
└── Tools\
    └── AmigaADFBuilder\
        └── application files

D:\
└── AmigaLibrary\
    ├── original\
    ├── catalog\
    ├── assets\
    ├── work\
    ├── output\
    ├── reports\
    └── logs\
```

Keeping the application and collection separate makes upgrades much easier.

---

## 10. Recommended Library Root

Create a dedicated directory for the collection.

For example:

```text
D:\AmigaLibrary
```

Place your preservation source disk images under:

```text
D:\AmigaLibrary\original
```

Example:

```text
D:\AmigaLibrary\original\Lemmings_Disk1.adf
D:\AmigaLibrary\original\Lemmings_Disk2.adf
D:\AmigaLibrary\original\Xenon2.adf
```

Do not place your only copy of important ADFs in a temporary application or export folder.

---

## 11. First launch configuration

After starting ADF Builder, open the **Library** tab.

Set the **Library Root** to the directory you created.

Example:

```text
D:\AmigaLibrary
```

Confirm the resolved source/original directory points to the location containing your ADF/DSK files.

Before running an export, also verify:

- Export work/staging path
- Export destination

For your first run, use **Build the library**, not a final export.

---

## 12. First-run recommended settings

For a first test:

### Run mode

Enable:

```text
Build the library (scan, organize, prepare)
```

Do not enable final export yet.

### Metadata

If you want Internet metadata lookup:

```text
Use online metadata sources
```

Otherwise leave it off.

### Artwork

Leave:

```text
Include artwork
```

enabled if you want artwork processing.

### Manuals

Leave:

```text
Include manuals (RTFM)
```

enabled if you intend to use manual sources.

### Export

Keep:

```text
Export the library (writes the final files)
```

off until you have reviewed Preview & Curation.

Then click **Run**.

---

## 13. Confirm the installation works

A successful basic installation should let you:

1. launch the GUI;
2. open all major tabs;
3. select a Library Root;
4. scan a folder containing ADF/DSK files;
5. see activity in Diagnostics;
6. populate Preview & Curation after processing.

The major tabs in v0.2.26 are:

```text
Library
Options
Providers
LaunchBox media
Preview & Curation
Diagnostics
Metadata Sources
Manual Lookup
```

If the application launches but these tabs are missing, check the version you are actually running.

---

## 14. Upgrading to a newer portable release

Because the Windows GUI is portable, upgrades should be treated as **application replacement**, not as a traditional installed-program upgrade.

### Recommended upgrade method

1. Close ADF Builder.
2. Back up the existing application directory.
3. Download the new release.
4. Verify its checksum.
5. Extract it into a **new** application directory.
6. Copy or migrate only the portable state you intentionally want to keep.
7. Launch the new version.
8. Confirm your Library Root and settings.
9. Run a Build or check-only operation before a final export.

Example:

```text
C:\Tools\
├── AmigaADFBuilder-0.2.26\
└── AmigaADFBuilder-0.2.27\
```

Once the new version is proven, the old application directory can be archived.

---

## 15. What to preserve during an upgrade

The application directory may contain:

```text
config\
data\
logs\
cache\
themes\
```

Important items can include:

```text
config\gui-settings.toml
config\secrets.vault
data\metadata_sources.db
```

Do not blindly overwrite a new release's program files with an old application's entire directory tree.

Keep the **Library Root** separate so the actual Amiga collection and curation data survive application replacement.

---

## 16. Credentials during upgrades

Provider secrets may be stored in:

```text
config\secrets.vault
```

If you move to a new portable directory and expect existing provider credentials to continue working, preserve the relevant portable secret store intentionally.

Do not publish, email, commit, or casually copy this file to shared locations.

If credential migration is uncertain, it is safer to re-enter provider credentials through the GUI.

---

## 17. Downgrading

If a new release has a problem:

1. close it;
2. keep your Library Root untouched;
3. reopen your previous portable application directory;
4. verify its settings before running;
5. avoid using two versions against the same active library simultaneously.

If a newer release changed persistent data formats, review release notes before using an older version against data already modified by the newer release.

---

## 18. Multiple versions side-by-side

Portable builds make side-by-side testing straightforward.

Example:

```text
C:\Tools\
├── ADFBuilder-0.2.25\
├── ADFBuilder-0.2.26\
└── ADFBuilder-Test\
```

This is useful for regression testing.

Do not run multiple instances simultaneously against the same library if both could write state.

---

## 19. Running from a USB drive

The portable design allows the application to run from removable storage.

Example:

```text
E:\AmigaTools\ADFBuilder\
```

However:

- removable storage can be slower;
- drive letters can change;
- sudden removal can corrupt application state;
- a large library is usually better on fixed storage or a reliable network share.

The application directory and the Library Root do not have to be on the same drive.

---

## 20. Running from a network share

The portable path system can work with network-backed directories, but use care.

Possible concerns include:

- write permissions;
- latency;
- intermittent connectivity;
- file locking;
- Windows security zones;
- provider cache performance.

For important preservation data, make sure the storage itself is reliably backed up.

---

## 21. Advanced portable base override

The GUI supports an advanced environment variable:

```text
AMIGA_ADF_GUI_BASE
```

This overrides the application's portable base directory.

Example in PowerShell:

```powershell
$env:AMIGA_ADF_GUI_BASE = "D:\ADFBuilderState"
.\AmigaADFLibraryBuilder.exe
```

Then the application's portable state directories are created beneath:

```text
D:\ADFBuilderState
```

This is an advanced deployment option. Most users should use the default app-relative layout.

---

## 22. Uninstalling the portable GUI

There is no traditional Windows uninstaller.

To remove the application:

1. close ADF Builder;
2. preserve anything needed from `config`, `data`, or other portable state;
3. delete the application directory.

Do **not** delete your Library Root unless you intentionally want to remove the Amiga collection and its managed data.

---

## 23. Developer / Python installation

This section is for developers and advanced users.

It is not required for the normal Windows portable GUI.

The project requires:

```text
Python 3.11+
```

The Windows build pipeline currently uses Python 3.12.

### Clone the repository

```bash
git clone <repository-url>
cd amiga-adf-library-builder
```

### Create a virtual environment

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Install core development dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev,artwork]"
```

### Install the GUI dependencies

```bash
python -m pip install -e ".[gui]"
```

This installs the GUI dependencies declared by the project, including:

- PySide6
- cryptography
- Pillow
- tomli-w

---

## 24. Optional RTFM document dependencies

The project provides an optional extra for document/manual extraction:

```bash
python -m pip install -e ".[rtfm-docs]"
```

This adds Python packages such as:

- pypdf
- PyMuPDF
- Pillow
- pytesseract

Some OCR workflows may additionally require a system installation of Tesseract.

The packaged Windows GUI does not automatically imply that every optional RTFM/OCR dependency is included.

---

## 25. Run the developer GUI

After installing the GUI extra:

```bash
amiga-adf-gui
```

or invoke the module entry point according to your development environment.

The installed command-line application is:

```bash
amiga-adf-library-builder
```

---

## 26. Test the developer installation

Run:

```bash
python -m pytest
```

The repository's test suite is designed to run without live external network dependencies for its normal automated tests.

---

## 27. Build a Windows executable locally

A local Windows developer build requires Windows and Python 3.12 for parity with CI.

Example:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -e ".[gui]"
python -m pip install pyinstaller

python tools\build_windows.py --target onedir --clean
python tools\build_windows.py --target onefile
```

Outputs are created under the build/dist directories according to the packaging target.

PyInstaller does not provide the project's authoritative Windows build by cross-compiling from Linux. The project's release workflow uses a Windows GitHub Actions runner.

---

## 28. Troubleshooting installation problems

### The EXE does not start

Check:

- 64-bit Windows 10/11
- file downloaded completely
- SHA-256 digest
- antivirus/SmartScreen messages
- application folder write permissions
- `logs` directory after launch

Try the portable ZIP build if the one-file EXE behaves differently.

### The application starts but cannot save settings

Move it to a writable directory such as:

```text
C:\Tools\AmigaADFBuilder
```

rather than a locked-down system location.

### Settings disappear after replacing the app

The GUI is portable. Its settings may live under the old application directory.

Inspect and intentionally migrate:

```text
config\
data\
```

Do not assume Windows stores everything globally in the user profile.

### Provider credentials disappeared

Check whether the previous portable:

```text
config\secrets.vault
```

was preserved.

If unsure, re-enter the credentials.

### My games disappeared after upgrading

The application binary should not be your only library location.

Confirm the new version is pointed to the same **Library Root** as before.

### SmartScreen blocks the app

Verify the official release source and SHA-256 before deciding whether to run an unsigned build.

---

## 29. Recommended production layout

A clean Windows deployment might look like:

```text
C:\Tools\
└── AmigaADFBuilder-0.2.26\
    └── AmigaADFLibraryBuilder\
        ├── AmigaADFLibraryBuilder.exe
        ├── _internal\
        ├── config\
        ├── data\
        ├── logs\
        └── cache\

D:\AmigaLibrary\
├── original\
├── catalog\
├── assets\
├── unknown\
├── work\
├── output\
├── config\
├── reports\
└── logs\
```

This gives you:

- replaceable application files on `C:`;
- long-lived library data on `D:`;
- straightforward upgrades;
- straightforward backups.

---

## 30. Installation checklist

Before your first real run, confirm:

- You downloaded the official release.
- The checksum matches.
- You extracted the portable ZIP to a writable directory.
- The GUI launches.
- You know which application version is running.
- You created a dedicated Library Root.
- Your source ADF/DSK files are under the correct originals path.
- Your Library Root is backed up appropriately.
- You have not configured your export destination to overwrite originals.
- You will perform a Build before your first final Export.

Once these are true, installation is complete.

---

**End of Installation Guide — v0.2.26**
