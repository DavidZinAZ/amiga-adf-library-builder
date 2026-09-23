# Windows GUI Architecture (Issue #15)

Portable, 64-bit Windows GUI for `amiga-adf-library-builder`, built as a
**presentation layer over the shared core**. This document describes the
core-vs-GUI boundary, the framework rationale, the provider / settings / secrets
architecture, the portable on-disk layout, and the PyInstaller build entry hook
owned by Dixie.

The implementation lives under `src/amiga_adf_library_builder/gui/` and is an
**optional extra** (`pip install amiga-adf-library-builder[gui]`). The CLI and
core remain dependency-free; PySide6 is LGPL and license-compatible (PyQt6 was
rejected: its GPLv3 would force GPL on an MIT-licensed project).

---

## 1. Core boundary (HARD)

The GUI never reimplements scanning, parsing, grouping, enrichment, export, or
validation. It builds a `PathConfig` and calls `pipeline.run_pipeline(...)` —
the same core the CLI uses. CLI and GUI are therefore behaviorally equivalent by
construction:

```
CLI:   argparse flags  -> resolve_config() -> PathConfig -> run_pipeline()
GUI:   widget state    -> build_path_config_from_gui_state() -> PathConfig -> run_pipeline()
```

`gui/state.py::build_path_config_from_gui_state` and
`gui/state.py::build_pipeline_kwargs` are the *single* bridge. They mirror the
flag→keyword mapping in `cli.py` exactly (verified by
`tests/test_gui_equivalence.py`, including a direct comparison against the
`build` command's kwargs). The accepted mapping:

| GUI control            | Core effect                                  |
|------------------------|----------------------------------------------|
| Library root           | `library_root` (required)                    |
| Original dir           | `original_dir` (else derived under root)     |
| Staging dir            | `staging_dir` (else derived under root)      |
| Output dir             | `output_dir` (else derived under root)       |
| Online checkbox        | `online`                                      |
| Refresh metadata       | `refresh_metadata`                            |
| Require artwork        | `require_artwork`                             |
| Verify only            | `verify_only`                                |
| Export-gate ack.       | `upstream_task_closed`                        |
| Run mode (build/export)| `export=` flag                               |

The GUI never imports provider internals to "do" enrichment; it only passes the
resolved provider-config file to `run_pipeline` (same as `--config` in the CLI).

### Equivalence enabler

`build_path_config_from_gui_state(state) -> PathConfig` is exported from the
`gui` package so independent QA can assert that identical inputs produce
identical staging output as the CLI. Same inputs → same `PathConfig` → same
pipeline behavior.

---

## 2. Framework rationale

* **PySide6 (LGPL)** — chosen over PyQt6 (GPLv3, incompatible with MIT) and
  tkinter (weak theming / HiDPI / control set). Provides QSS theming, native
  High-DPI scaling, and accessible widgets.
* Build / packaging Python is **3.12** (see PyInstaller section).
* The GUI sets `Qt.AA_EnableHighDpiScaling` and `Qt.AA_UseHighDpiPixmaps`
  **before** `QApplication` is created (`gui/app.py`), satisfying HiDPI +
  multi-monitor requirements.

---

## 3. Provider abstraction (generic panel)

`gui/providers.py` defines a `Provider` protocol/ABC with the locked contract:
`id`, `name`, `enabled`, `auth_required` (`required|optional|none`),
`configured`, `capabilities`, `test_connection()`, `add_credentials()`,
`remove_credentials()`, `status()`, plus declarative metadata.

The GUI renders **one generic provider panel** from `ProviderMetadata`
(fields/capabilities) — there is no per-provider UI. Playmatch and Hasheous (the
two optional, DISABLED-by-default online resolvers in the core) are surfaced
generically via `PlaymatchProvider` / `HasheousProvider` adapters. Credentials
come from the `SecretStore` (never embedded in config or code). Each adapter
builds its own typed `[playmatch]` / `[hasheous]` TOML table, mirroring the
core `PlaymatchConfig` / `HasheousConfig` shapes.

`ProviderRegistry` holds the known providers; `default_registry()` pre-loads
Playmatch + Hasheous. The registry's `config_dict()` assembles the combined
provider TOML the GUI writes and passes to `run_pipeline`.

---

## 4. Settings vs Secrets (HARD separation)

### SettingsStore (`gui/settings.py`) — non-sensitive only
Portable TOML under the app `config/` dir. Holds theme, default paths, run
modes, window geometry, and named presets. **No code path accepts a secret**, so
config import/export can never leak credentials. Round-trips via `tomli_w` /
`tomllib` and is defensive against malformed files.

### SecretStore (`gui/secrets.py`) — secrets only
* `SecretBackend` ABC. MVP backends:
  * **`PortableVaultBackend`** (default) — AES-GCM vault file in `config/`,
    master-password unlocked, portable. Salt is persisted so re-opens are
    consistent; wrong password fails closed (never silently succeeds).
  * **`EnvSecretBackend`** — runtime `AMIGA_ADF_SECRET_*` environment variables
    (no persistence; useful for CI/headless).
  * **`WinDpapiSecretBackend`** — **reserved**, constructed only behind
    `win_dpapi_available()` (Windows + DPAPI extension). It is never the only
    path; the portable vault and env backends remain available everywhere.
* `SecretStore` is the GUI frontend; it exposes key-level ops and never returns
  a value suitable for logging. `RedactingFilter` (a `logging.Filter`) masks
  known secret values that reach the logging system: `key=value` sensitive pairs,
  `Bearer`/`Authorization` headers, and exact secret strings registered via
  `RedactingFilter.add_secret` (the GUI registers a resolved token at save time
  and removes it afterwards). **No secret is written to logs, diagnostics,
  exports, or UI error strings.** See §4.4 for the redaction mechanism.

The `cli.py` error path already redacts via `logging_utils.redact`; the GUI adds
the `RedactingFilter` at the application level as defense-in-depth.

### 4.1 Vault master-password policy (F2 / F6)

The portable vault is AES-GCM with a master password derived via
PBKDF2-HMAC-SHA256 at a **documented module-level constant**
`_VAULT_PBKDF2_ITERATIONS = 200_000` (OWASP-aligned floor). The master password
is **never persisted**; only the unlocked in-memory state is kept for the
session.

The GUI unlocks the vault through a `MasterPasswordDialog`:

* **New vault (no vault file):** *Set* mode — `set` + `confirm` password fields.
* **Existing vault:** *Unlock* mode — single password field.

Both modes display an explicit, non-dismissable warning: **"The master password
is unrecoverable. If lost, the stored credentials cannot be recovered — store it
in a password manager."** There is no recovery path; a mistyped master password
on first save silently creates a vault encrypted under the wrong key, so the
confirm field is mandatory in Set mode.

`SecretStore.unlock(password)` / `change_password(...)` are wired into the GUI.
On a first credential write while the vault is locked, the GUI prompts for the
master password, unlocks, then saves. A `try/except SecretError` around the
credential write surfaces a clear `QMessageBox` ("Vault is locked — set a master
password first") instead of an uncaught exception.

### 4.2 Credential affordance honesty — MVP deferral (F3)

The "Set credentials" flow is **fully functional**: the entered token is stored
securely in the AES vault and is never shown in the UI, config, or logs.

**However, the bridge from the GUI vault to the core run engine is DEFERRED for
MVP.** The GUI does **not** yet export `AMIGA_ADF_SECRET_*` environment variables
or write a provider-config TOML that the pipeline actually consumes. Provider
enable/base-URL toggles are **in-memory session UI state only** — they are NOT
persisted and are NOT consumed by the run engine.

This is a deliberate fail-closed posture: it keeps the vault subsystem complete
and honest (the token is stored, not leaked) while avoiding a misleading
external-transmission affordance. The UI tooltip/help states this explicitly
("stored securely in the local vault; pipeline/provider wiring is planned
(coming soon)"). Future work will wire the vault to the run engine.

### 4.3 Secret backend posture — vault is the desktop default (F4)

The interactive GUI **always** defaults to `PortableVaultBackend` (architecture
decision #5). The `EnvSecretBackend` is **never auto-selected** for the desktop
GUI: it writes/reads process-global `AMIGA_ADF_SECRET_*` environment variables,
which can surface in `os.environ` dumps, crash reporters, or `/proc/<pid>/environ`.
It is reserved for **CI/headless** runs only, where the process boundary is
trusted and ephemeral. A guard/test asserts the GUI's default `SecretStore`
backend is `PortableVaultBackend`, never `EnvSecretBackend`.

### 4.4 Redaction coverage — handler-level, covers all submodule loggers (F1 / F5 / F7)

`install_gui_redaction()` (called once from `GuiApp.__init__`, and idempotently
guard-called from `MainWindow.__init__`) installs a single process-wide
`RedactingFilter`. **Python logging semantics note:** a `logging.Filter`
attached to a *logger* is evaluated only for records emitted by that logger,
not for records propagated up from a child logger. To redact secrets emitted by
the core submodule loggers (e.g. `amiga_adf_library_builder.playmatch`), the
filter is therefore attached to the **root logger's handler(s)** — the effective
control. Idempotency (F7) prevents stacking across multiple windows.

Exact-secret redaction (F5) is activated at save time: the resolved value is
registered via `RedactingFilter.add_secret(value)` and removed via
`remove_secret(value)` after the save, so positionally-formatted secrets are
masked by the root-handler filter. The `RedactingFilter` docstring is precise
about what it masks (key=value pairs, Bearer headers, and explicitly registered
exact secrets) and does **not** claim to mask arbitrary "secret-shaped" values.

---

## 5. Portable layout (no hard-coded user/home)

`gui/layout.py::PortablePaths` resolves five app-relative directories from a
single base:

```
<base>/config   (settings TOML + secrets vault)
<base>/data
<base>/logs
<base>/cache
<base>/themes   (optional *.qss overrides)
```

The base defaults to the PyInstaller app directory (parent of the executable);
it can be overridden via `AMIGA_ADF_GUI_BASE` or an explicit `base_dir` (tests
always pass one). No path is derived from `/home/<user>` or the Windows user
profile, so the build works from a USB stick, a network share, or
`C:\Program Files`. **Spaces in paths work** because everything is `Path`-based.

---

## 6. Themes

`gui/themes.py::ThemeManager` loads QSS by name: **Light / Dark / System**. System
follows the OS at apply time. Additional themes can be dropped in as
`<name>.qss` under `<base>/themes/` and are auto-discovered. The worker imports
the core pipeline lazily so the GUI remains importable headless (for the
equivalence tests) without running anything.

---

## 7. Runtime / worker

`gui/worker.py::PipelineWorker` runs `run_pipeline` on a `QThread`, emits
progress + a final result, and supports cooperative cancellation via a
`threading.Event` checked between phases. Cancellation is safe: the pipeline
writes only managed directories and the original corpus is read-only.

`gui/main_window.py::MainWindow` is the primary window: directory selectors,
build/export run mode, online/refresh/artwork/verify/gate toggles, the generic
provider panel, theme menu, Help/About, progress + cancel, and an "open log
directory" button. It builds a `GuiState` from widgets, persists non-sensitive
defaults, and runs the worker.

`gui/app.py::run` is the application entry: sets HiDPI attributes, builds the
portable paths / settings / secrets, constructs `MainWindow`, and `exec()`s.

---

## 8. PyInstaller build entry hook (Dixie owns spec/CI)

The GUI owns only the app entry and runtime hooks; packaging is Dixie's. Contract
for the spec:

* **Framework:** PySide6, Python 3.12.
* **MVP artifacts:**
  * onedir portable directory — extract-and-run with
    `amiga_adf_library_builder.exe` + Qt DLLs + `_internal/`.
  * onefile build (single executable).
* **Entry point:** `amiga_adf_library_builder.gui.app:run` (or a thin
  `amiga-adf-gui` console script).
* **Runtime hooks:** ensure `PySide6` Qt plugins are collected; the GUI sets
  HiDPI attributes before `QApplication` creation (already in `app.py`).
* **Portable base:** when frozen, `sys.executable`'s parent is the app root, so
  `config/ data/ logs/ cache/ themes/` are created next to the exe. No
  user-profile paths are used.
* **Hidden imports (if needed):** `amiga_adf_library_builder.gui`,
  `amiga_adf_library_builder.pipeline`, `PySide6.QtWidgets`, etc.
* **No secrets in the bundle:** the vault is created at runtime; nothing
  sensitive is embedded in the spec or the executable.

---

## 9. Testing / acceptance

* `tests/test_gui_providers.py` — generic provider abstraction + config shape.
* `tests/test_gui_settings.py` — non-sensitive settings round-trip + presets.
* `tests/test_gui_secrets.py` — vault encrypt/decrypt round-trip, lock state,
  wrong-password failure, env backend, DPAPI gating, and **`RedactingFilter`
  masking guarantees** (key=value, Bearer, exact secret).
* `tests/test_gui_equivalence.py` — `build_path_config_from_gui_state` matches
  `resolve_config`, and `build_pipeline_kwargs` matches the CLI `build` command,
  for every accepted flag.
* `tests/test_gui_import.py` — package imports under offscreen Qt and
  `MainWindow` constructs without error; themes + portable layout.

The full suite (`pytest -q`) passes with the GUI added; the existing CLI/core
behavior is unchanged.
