"""Main window for the Windows GUI (PySide6).

This is the presentation layer over the shared core. It builds a
:class:`~amiga_adf_library_builder.gui.state.GuiState` from the widgets, then
runs the core pipeline through :class:`~amiga_adf_library_builder.gui.worker.PipelineWorker`.
It never reimplements scanning/parsing/grouping/enrichment/export/validation.

The provider panel is rendered GENERICALLY from
:class:`~amiga_adf_library_builder.gui.providers.ProviderMetadata` -- there is
no per-provider UI. Secrets for providers come from the
:class:`~amiga_adf_library_builder.gui.secrets.SecretStore` and are never shown
in the UI or written to logs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QGuiApplication, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__ as gui_version
from .layout import PortablePaths
from .providers import Provider, ProviderRegistry, default_registry
from .secrets import SecretError, SecretStore, install_gui_redaction
from .settings import Settings, SettingsStore
from .state import GuiState
from .themes import apply_theme, available_themes

logger = logging.getLogger("amiga_adf_gui")


# --- window geometry persistence (Issue #18) ----------------------------------
#
# The main window's geometry is persisted in the same non-sensitive settings
# store used for the folder defaults (Issue #17), under the ``window_geometry``
# key of the ``[gui]`` table.
#
# Serialized payload (JSON string, stable + versioned so future schema changes
# cannot break restore):
#   {"v": 1, "geom": "<x,y,w,h>", "max": true|false}
#
# ``geom`` is the window's *normal* geometry (the size/position it would have
# if not maximized) -- the correct value to restore even when the window was
# closed while maximized. ``max`` is an explicit flag: the offscreen platform
# and some WMs leave ``normalGeometry()`` invalid while maximized, and
# ``QMainWindow.restoreState()`` does not reliably round-trip the maximized
# flag, so the flag is stored as data, not as an opaque byte blob.
#
# SAFETY: on restore the saved rect is tested against the union of every
# screen's ``availableGeometry`` (QGuiApplication.screens()). If the rect does
# not intersect that union -- e.g. the monitor it lived on was disconnected or
# the display layout changed -- it is clamped back on-screen: the default
# size repositioned to the center of the primary available area. The window
# must never be restored entirely off-screen.


def _default_window_geometry() -> QRect:
    """Default geometry: 900x680 centered on the primary available area.

    ``QGuiApplication.primaryScreen()`` is guaranteed non-None once a
    QApplication exists (a virtual screen is always present, offscreen
    included). If it ever is not, the 0,0 800x600 rect is returned rather
    than raising from the window constructor.
    """
    screen = QGuiApplication.primaryScreen()
    if screen is None:  # pragma: no cover - requires a live QApplication
        return QRect(0, 0, 800, 600)
    return _centered_rect(QRect(0, 0, 900, 680), screen.availableGeometry())


def _centered_rect(size: QRect, area: QRect) -> QRect:
    """Place ``size`` (its width/height) centered inside ``area``."""
    x = area.x() + max(0, (area.width() - size.width()) // 2)
    y = area.y() + max(0, (area.height() - size.height()) // 2)
    return QRect(x, y, size.width(), size.height())


def _geometry_is_on_screen(rect: QRect) -> bool:
    """True if ``rect`` intersects ANY screen's available area.

    Uses the screen-geometry union (QRegion), not the bounding box, so a
    rect sitting in the GAP between two side-by-side monitors (inside the
    union's bounding box but off every screen) is correctly rejected.
    """
    region = QRegion()
    for screen in QGuiApplication.screens():
        region = region.united(QRegion(QRect(screen.availableGeometry())))
    return not region.intersected(QRegion(QRect(rect))).isEmpty()


def _sanitize_geometry(rect: QRect) -> QRect:
    """Clamp a saved geometry back on-screen if it is no longer visible.

    Returns ``rect`` unchanged when it intersects any available screen area;
    otherwise returns the default size centered on the primary screen's
    available area. The result always intersects the virtual desktop.
    """
    if rect.isValid() and _geometry_is_on_screen(rect):
        return rect
    return _default_window_geometry()


def _encode_geometry(rect: QRect, maximized: bool) -> str:
    """Serialize geometry + maximized flag to the versioned JSON payload."""
    payload = {
        "v": 1,
        "geom": f"{rect.x()},{rect.y()},{rect.width()},{rect.height()}",
        "max": bool(maximized),
    }
    return json.dumps(payload)


def _decode_geometry(text: str) -> tuple[Optional[QRect], bool]:
    """Parse a versioned geometry payload; tolerant of garbage.

    Returns ``(None, False)`` for empty/malformed payloads, unknown
    versions, or invalid rects -- the caller falls back to the default
    geometry. A missing ``max`` field (or any non-bool value) decodes to
    ``False`` so a partial payload still restores a usable window.
    """
    if not text:
        return None, False
    try:
        payload = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None, False
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return None, False
    parts = payload.get("geom")
    if not isinstance(parts, str):
        return None, False
    try:
        x, y, w, h = (int(p) for p in parts.split(","))
    except ValueError:
        return None, False
    rect = QRect(x, y, w, h)
    if not rect.isValid():
        return None, False
    return rect, payload.get("max") is True


class MasterPasswordDialog(QDialog):
    """Master-password dialog for the AES-GCM vault (F2 / F6).

    Modes:
      * ``unlock``  -- single password field; confirms the vault can be opened.
      * ``set``     -- set + confirm fields; used when no vault file exists yet.

    The dialog NEVER reveals the master password and NEVER persists it. The
    calling code keeps the unlocked state in memory for the session only
    (F2). A prominent warning communicates that the master password is
    unrecoverable once set (F6).
    """

    def __init__(
        self,
        mode: str = "unlock",
        vault_exists: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        if mode not in ("unlock", "set"):
            raise ValueError(f"unknown master-password dialog mode: {mode!r}")
        self._mode = mode
        self._vault_exists = vault_exists
        self.setWindowTitle(
            "Set your master password" if mode == "set" else "Open the credential store"
        )
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        warn = QLabel(
            "This password unlocks the stored credentials and cannot be "
            "recovered if lost — if you forget it, the stored credentials are "
            "gone. Save it in a password manager."
        )
        warn.setWordWrap(True)
        layout.addWidget(warn)

        form = QFormLayout()
        self._pw = QLineEdit(self)
        self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw.setPlaceholderText("master password")
        form.addRow("Master password", self._pw)

        if self._mode == "set":
            self._confirm = QLineEdit(self)
            self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
            self._confirm.setPlaceholderText("re-enter master password")
            form.addRow("Confirm", self._confirm)
        else:
            self._confirm = None
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        # Keep the password out of tooltips/status; just the generic label.
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if self._mode == "set":
            if not self._pw.text():
                QMessageBox.warning(self, "Master password", "Enter a master password.")
                return
            if self._pw.text() != self._confirm.text():
                QMessageBox.warning(self, "Master password", "Passwords do not match.")
                return
        self.accept()

    @property
    def password(self) -> str:
        return self._pw.text()


class MainWindow(QMainWindow):
    """Primary application window (CLI-equivalent controls over the core)."""

    def __init__(
        self,
        *,
        portable_paths: Optional[PortablePaths] = None,
        settings_store: Optional[SettingsStore] = None,
        secret_store: Optional[SecretStore] = None,
        provider_registry: Optional[ProviderRegistry] = None,
        config_path: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Amiga ADF Library Builder")
        # (Issue #18) Default size; replaced below by the persisted geometry
        # restore when a valid saved geometry exists.
        self.resize(900, 680)

        self._paths = portable_paths or PortablePaths()
        self._paths.ensure_all()
        self._settings_store = settings_store or SettingsStore(self._paths.settings_file())
        try:
            self._settings = self._settings_store.load()
        except Exception:
            self._settings = Settings()
        self._saved_maximized = False
        self._restore_geometry()
        self._secret_store = secret_store or SecretStore.with_vault(self._paths.vault_file())
        self._registry = provider_registry or default_registry()
        self._config_path = config_path

        # Install the redacting log filter process-wide (F1 / F7). This attaches
        # the SAME RedactingFilter to the root logger's handlers so records from
        # ANY submodule logger (e.g. amiga_adf_library_builder.playmatch) are
        # redacted during propagation. Idempotent: safe to call again.
        self._redactor = install_gui_redaction()

        self._build_widgets()
        self._apply_settings_to_widgets()
        self._build_menu()
        self._worker = None
        self._cancel_event = None
        # (Issue #18) Re-apply the maximized flag now that the window is fully
        # built, so widget construction cannot clobber it. ``setWindowState``
        # (not ``showMaximized``) keeps a hidden window hidden -- the flag
        # takes effect on the first ``show()``.
        if self._saved_maximized:
            self.setWindowState(Qt.WindowState.WindowMaximized)

    # --- menu -----------------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = QMenuBar(self)
        self.setMenuBar(menubar)

        file_menu = QMenu("&File", self)
        menubar.addMenu(file_menu)
        quit_act = file_menu.addAction("E&xit")
        quit_act.triggered.connect(self.close)

        theme_menu = QMenu("&Theme", self)
        menubar.addMenu(theme_menu)
        for name in available_themes(themes_dir=self._paths.themes_dir):
            act = theme_menu.addAction(name.title())
            act.triggered.connect(lambda _checked=False, n=name: self._set_theme(n))

        help_menu = QMenu("&Help", self)
        menubar.addMenu(help_menu)
        help_menu.addAction("&Help").triggered.connect(self._show_help)
        help_menu.addAction("&About").triggered.connect(self._show_about)

    # --- widgets --------------------------------------------------------------
    def _build_widgets(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        tabs = QTabWidget(self)
        root.addWidget(tabs)
        tabs.addTab(self._build_paths_tab(), "Library")
        tabs.addTab(self._build_options_tab(), "Options")
        tabs.addTab(self._build_providers_tab(), "Providers")
        tabs.addTab(self._build_diagnostics_tab(), "Diagnostics")

        # --- run controls ---
        run_box = QGroupBox("Run")
        run_layout = QVBoxLayout(run_box)
        mode_row = QHBoxLayout()
        self._mode_build = QCheckBox("Build the library (scan, organize, prepare)")
        self._mode_build.setChecked(True)
        self._mode_build.setToolTip(
            "Scans the library, groups the disks into releases, and prepares the "
            "metadata. Nothing is written to the export destination."
        )
        self._mode_export = QCheckBox("Export the library (writes the final files)")
        self._mode_export.setToolTip(
            "Builds the library and then writes the final export files to the "
            "export destination. A confirmation is requested before files are "
            "written."
        )
        mode_row.addWidget(self._mode_build)
        mode_row.addWidget(self._mode_export)
        run_layout.addLayout(mode_row)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        run_layout.addWidget(self._progress)

        self._status_label = QLabel("")
        run_layout.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        self._run_button = QPushButton("Run")
        self._run_button.clicked.connect(self._on_run)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._on_cancel)
        self._open_log_button = QPushButton("Open log files…")
        self._open_log_button.clicked.connect(self._on_open_logs)
        btn_row.addWidget(self._run_button)
        btn_row.addWidget(self._cancel_button)
        btn_row.addWidget(self._open_log_button)
        btn_row.addStretch(1)
        run_layout.addLayout(btn_row)
        root.addWidget(run_box)

    def _dir_row(self, label: str, line_edit: QLineEdit, tooltip: str = "") -> QHBoxLayout:
        row = QHBoxLayout()
        label_widget = QLabel(label)
        if tooltip:
            label_widget.setToolTip(tooltip)
        row.addWidget(label_widget)
        row.addWidget(line_edit, 1)
        picker = QPushButton("Choose…")
        picker.clicked.connect(lambda _checked=False, le=line_edit: self._pick_dir(le))
        row.addWidget(picker)
        return row

    def _build_paths_tab(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)
        self._le_library_root = QLineEdit(self)
        self._le_original_dir = QLineEdit(self)
        self._le_staging_dir = QLineEdit(self)
        self._le_output_dir = QLineEdit(self)
        layout.addLayout(
            self._dir_row(
                "Library root",
                self._le_library_root,
                "The top-level folder containing your ADF collection.",
            )
        )
        layout.addLayout(
            self._dir_row(
                "Original disks (read-only)",
                self._le_original_dir,
                "Where the original .adf files live. This folder is never "
                "modified — leave it blank to use the default location.",
            )
        )
        layout.addLayout(
            self._dir_row(
                "Export work folder",
                self._le_staging_dir,
                "A scratch area where export files are prepared before the "
                "final export. Safe to delete — it is rebuilt on every export. "
                "Leave blank to use the default location.",
            )
        )
        layout.addLayout(
            self._dir_row(
                "Export destination",
                self._le_output_dir,
                "Where the finished export files are written. Leave blank to "
                "use the default location.",
            )
        )
        layout.addStretch(1)
        return w

    def _build_options_tab(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)

        # --- routine options --------------------------------------------------
        routine_box = QGroupBox("Metadata")
        routine_layout = QVBoxLayout(routine_box)
        self._cb_online = QCheckBox("Use online metadata sources")
        self._cb_online.setToolTip(
            "Allow the app to look up game titles and artwork from online "
            "sources. Off by default; everything works fully offline."
        )
        self._cb_refresh = QCheckBox("Refresh metadata even if cached")
        self._cb_refresh.setToolTip(
            "Re-fetch metadata from the sources instead of using the copies "
            "from the last run. Slower; only needed if a lookup came back "
            "wrong."
        )
        self._cb_artwork = QCheckBox("Require artwork before export")
        self._cb_artwork.setToolTip(
            "Stop the export if any release is missing its cover artwork, "
            "instead of exporting with missing covers."
        )
        for cb in (self._cb_online, self._cb_refresh, self._cb_artwork):
            routine_layout.addWidget(cb)
        layout.addWidget(routine_box)

        # --- advanced options (visually separated from routine controls) ------
        advanced_box = QGroupBox("Advanced")
        advanced_box.setToolTip(
            "Power-user controls. The defaults are safe for normal use — "
            "leave them alone unless you know what you are changing."
        )
        advanced_layout = QVBoxLayout(advanced_box)
        self._cb_verify = QCheckBox("Check only — don't change files")
        self._cb_verify.setToolTip(
            "Run the full build/export check without writing any files. "
            "Useful to verify your library and settings before a real export."
        )
        self._cb_gate = QCheckBox("Allow export")
        self._cb_gate.setToolTip(
            "Confirm that you want to write the export files. The export is "
            "refused until this box is checked."
        )
        self._cb_advanced = QCheckBox("Remember these settings")
        self._cb_advanced.setToolTip(
            "Keep the choices in this group so they are restored the next "
            "time you start the app. Off by default."
        )
        for cb in (self._cb_verify, self._cb_gate, self._cb_advanced):
            advanced_layout.addWidget(cb)
        layout.addWidget(advanced_box)
        layout.addStretch(1)
        return w

    def _build_providers_tab(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)
        for provider in self._registry.all():
            layout.addWidget(self._build_provider_panel(provider))
        layout.addStretch(1)
        return w

    def _build_provider_panel(self, provider: Provider) -> QWidget:
        box = QGroupBox(provider.metadata.name)
        box.setToolTip(provider.metadata.description)
        form = QFormLayout(box)
        enabled = QCheckBox("Enabled")
        enabled.setChecked(provider.enabled())
        enabled.setToolTip(
            "Turn this metadata source on. A source only activates once it "
            "has been set up below."
        )
        enabled.stateChanged.connect(
            lambda state, p=provider: p.set_enabled(state == Qt.CheckState.Checked.value)
        )
        form.addRow("", enabled)
        cfg = provider.to_config_dict()
        for field in provider.metadata.fields:
            le = QLineEdit(self)
            le.setText(str(cfg.get(field.key, field.default)))
            le.setPlaceholderText(field.placeholder)
            le.setToolTip(field.help_text)
            le.textChanged.connect(
                lambda text, p=provider, k=field.key: p.set_field(k, text)
            )
            form.addRow(field.label, le)
        if provider.metadata.requires_secret or provider.metadata.auth_required != "none":
            secret_btn = QPushButton("Set credentials…")
            secret_btn.setToolTip(
                "Store this source's token securely in the local encrypted "
                "credential store. Live lookups are not connected to runs yet — "
                "the token is saved but not used by the app until that lands."
            )
            secret_btn.clicked.connect(
                lambda _checked=False, p=provider: self._edit_credentials(p)
            )
            form.addRow("Credentials", secret_btn)
        status_btn = QPushButton("Check connection…")
        status_btn.setToolTip(
            "Shows whether this source is set up and reachable. No live "
            "request is made."
        )
        status_btn.clicked.connect(
            lambda _checked=False, p=provider: self._test_provider(p)
        )
        form.addRow("", status_btn)
        return box

    def _build_diagnostics_tab(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.addWidget(
            QLabel("Activity from the last run (sensitive values are hidden):")
        )
        self._diag = QPlainTextEdit(self)
        self._diag.setReadOnly(True)
        layout.addWidget(self._diag)
        return w

    # --- interaction ----------------------------------------------------------
    def _pick_dir(self, line_edit: QLineEdit) -> None:
        # F8: start the picker at the app config dir (portable layout), not at
        # the user/home directory. Falls back to the last-used value if present.
        start = line_edit.text() or str(self._paths.config_dir)
        chosen = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        if chosen:
            line_edit.setText(chosen)

    def _ensure_vault_unlocked(self) -> bool:
        """Ensure the secret vault is unlocked; prompt for the master password.

        Returns True if the vault is (now) unlocked, False if the operator
        cancelled. On a brand-new vault (no file) this offers a SET flow; on an
        existing vault it offers an UNLOCK flow (F2).
        """
        backend = self._secret_store.default_backend
        if getattr(backend, "is_unlocked", False):
            return True
        from .secrets import PortableVaultBackend

        if not isinstance(backend, PortableVaultBackend):
            return False
        vault_exists = backend.vault_path.is_file()
        mode = "set" if not vault_exists else "unlock"
        dlg = MasterPasswordDialog(mode=mode, vault_exists=vault_exists, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            if mode == "set":
                # Initialize the new vault under the chosen master password.
                backend.unlock(dlg.password)
            else:
                self._secret_store.unlock(dlg.password)
        except SecretError as exc:
            QMessageBox.critical(
                self,
                "Credential store error",
                f"Could not open the credential store: {exc}",
            )
            return False
        return bool(getattr(backend, "is_unlocked", False))

    def _prompt_credentials(self, provider: Provider) -> "Optional[str]":
        """Show the credentials dialog and return the entered token, or None.

        Kept as a separate method so the token-prompt concern is isolated and
        the save/unlock flow in :meth:`_edit_credentials` stays testable.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{provider.metadata.name} credentials")
        layout = QVBoxLayout(dlg)
        token_le = QLineEdit(dlg)
        token_le.setEchoMode(QLineEdit.EchoMode.Password)
        token_le.setPlaceholderText("secret token (stored securely, never shown)")
        layout.addWidget(QLabel("Token (stored in the encrypted credential store):"))
        layout.addWidget(token_le)
        # F3: honest affordance. The token is stored securely in the vault; the
        # bridge from the vault to the core run engine is deferred (MVP).
        note = QLabel(
            "Stored securely in the local credential store. Live lookups are "
            "not connected to runs yet — the token is saved but not used by "
            "the app until that lands."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return token_le.text() or None

    def _edit_credentials(self, provider: Provider) -> None:
        token = self._prompt_credentials(provider)
        if not token:
            return
        # F2: the vault must be unlocked before any write; prompt if needed.
        if not self._ensure_vault_unlocked():
            return
        # F5: register the exact resolved value so it is redacted at save time,
        # then drop it after the write (the root-handler filter does the masking).
        redactor = self._redactor
        try:
            redactor.add_secret(token)
            provider.add_credentials(self._secret_store, token=token)
        except SecretError as exc:
            # Fail-closed and clear: never let an uncaught SecretError escape the
            # slot. Surface an actionable message instead.
            QMessageBox.critical(
                self,
                "Credential store is locked",
                "The credential store is locked — open it with your master "
                "password first, then try again.",
            )
            logger.warning("credential save failed: %s", exc)
            return
        finally:
            redactor.remove_secret(token)
        self._status_label.setText(
            f"{provider.metadata.name} token saved to the credential store."
        )

    def _test_provider(self, provider: Provider) -> None:
        status = provider.test_connection()
        QMessageBox.information(
            self,
            f"{provider.metadata.name} status",
            f"Status: {status.message}",
        )

    def _set_theme(self, name: str) -> None:
        apply_theme(name, themes_dir=self._paths.themes_dir)
        if self._settings_store is not None:
            try:
                self._settings_store.update(theme=name)
            except Exception:
                pass

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "Help",
            "1. On the Library tab, point the Library root at your ADF "
            "collection (the other folders can stay at their defaults).\n"
            "2. On the Options tab, turn on any options you need (all "
            "optional; offline is the default).\n"
            "3. Choose Build or Export in the Run area and press Run.\n"
            "Exporting asks for a final confirmation before writing files. "
            "Online metadata sources are optional and off by default.",
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            f"Amiga ADF Library Builder — GUI\nVersion {gui_version}\n"
            "A graphical front end for building Amiga ADF libraries for the "
            "Gotek. It runs the same processing as the command-line tool, so "
            "results match.",
        )

    def _on_open_logs(self) -> None:
        path = self._paths.logs_dir
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception:
            QMessageBox.information(self, "Log files", str(path))

    # --- state <-> widgets ----------------------------------------------------
    def _restore_geometry(self) -> None:
        """Restore persisted window geometry (Issue #18), safely.

        The saved payload is decoded and the rect is sanitized against the
        CURRENT display layout (see :func:`_sanitize_geometry`): a geometry
        that no longer intersects any screen (monitor disconnected, display
        layout changed) is replaced by the default size centered on the
        primary screen. The window is therefore never restored fully
        off-screen.

        The maximized flag is stored as explicit data in the payload (the
        offscreen platform and some WMs do not round-trip it through
        ``saveState``/``restoreState``). It is re-applied at the END of
        ``__init__`` (see the ``_saved_maximized`` flag) so widget
        construction cannot clobber it, and only when the saved rect is
        on-screen -- a clamped restore comes up in the normal state.

        Malformed payloads, unknown versions, and empty settings all fall
        through to the default geometry; this method never raises.
        """
        try:
            rect, maximized = _decode_geometry(self._settings.window_geometry)
        except Exception:
            rect, maximized = None, False
        if rect is None:
            # No saved (or unparseable) geometry: default position + size.
            self.setGeometry(_default_window_geometry())
            return
        on_screen = _geometry_is_on_screen(rect)
        self.setGeometry(_sanitize_geometry(rect))
        self._saved_maximized = bool(maximized) and on_screen

    def _current_persist_geometry(self) -> Optional[str]:
        """The geometry payload to persist right now, or None to skip.

        Persists the *normal* geometry (a maximized size must not be baked
        into the settings) plus the maximized flag. While maximized,
        ``normalGeometry()`` is the right value, but on some platforms --
        including the offscreen test platform -- it is invalid in that
        state; ``geometry()`` is then used instead (the saved rect is
        sanitized on restore anyway). While fullscreen the WM owns the
        surface, so nothing is persisted.
        """
        if self.isFullScreen():
            return None
        maximized = self.isMaximized()
        rect = self.normalGeometry() if maximized else self.geometry()
        if not rect.isValid():
            rect = self.geometry()
        if not rect.isValid():
            return None
        return _encode_geometry(rect, maximized)

    def _persist_defaults(self) -> bool:
        """Persist current widget values as non-sensitive defaults (Issue #17)
        plus the window geometry (Issue #18).

        Single persist path shared by the run and close call sites -- no
        duplicated key lists. Reads the widgets (not a prior run's GuiState),
        so closing WITHOUT having started a run still saves the folder
        selections and the window geometry. A settings-write failure is
        logged, never raised: it must not block or crash window close.
        Returns True on success.
        """
        try:
            state = self._state_from_widgets()
            changes: dict = {
                "default_library_root": state.library_root,
                "default_original_dir": state.original_dir,
                "default_staging_dir": state.staging_dir,
                "default_output_dir": state.output_dir,
                "online": state.online,
                "refresh_metadata": state.refresh_metadata,
                "require_artwork": state.require_artwork,
                "verify_only": state.verify_only,
                "export_gate_acknowledged": state.export_gate_acknowledged,
                "advanced_mode": self._cb_advanced.isChecked(),
            }
            geometry = self._current_persist_geometry()
            if geometry is not None:
                changes["window_geometry"] = geometry
            self._settings_store.update(**changes)
            return True
        except Exception as exc:  # pragma: no cover - filesystem failure path
            logger.debug("settings persist failed (non-fatal): %s", exc)
            return False

    def _state_from_widgets(self) -> GuiState:
        state = GuiState(
            library_root=self._le_library_root.text().strip(),
            original_dir=self._le_original_dir.text().strip(),
            staging_dir=self._le_staging_dir.text().strip(),
            output_dir=self._le_output_dir.text().strip(),
            online=self._cb_online.isChecked(),
            refresh_metadata=self._cb_refresh.isChecked(),
            require_artwork=self._cb_artwork.isChecked(),
            verify_only=self._cb_verify.isChecked(),
            export_gate_acknowledged=self._cb_gate.isChecked(),
            run_mode="export" if self._mode_export.isChecked() else "build",
            provider_config_path=self._config_path or "",
        )
        return state

    def _apply_settings_to_widgets(self) -> None:
        s = self._settings
        # (Issue #17) Persisted folder paths that do not exist on THIS machine
        # (e.g. a USB stick mounted at a different drive letter) are still
        # restored into the fields, but the user is told so visibly instead of
        # silently. No modal, no clearing, no crash.
        folder_fields = (
            (self._le_library_root, s.default_library_root),
            (self._le_original_dir, s.default_original_dir),
            (self._le_staging_dir, s.default_staging_dir),
            (self._le_output_dir, s.default_output_dir),
        )
        missing: list[str] = []
        for line_edit, value in folder_fields:
            if not value:
                continue
            line_edit.setText(value)
            if not Path(value).exists():
                missing.append(value)
        if missing:
            message = (
                "Persisted path(s) not found on this machine: "
                + "; ".join(missing)
            )
            logger.debug("%s (fields kept; reselect if needed)", message)
            self._status_label.setText(message)
        self._cb_online.setChecked(s.online)
        self._cb_refresh.setChecked(s.refresh_metadata)
        self._cb_artwork.setChecked(s.require_artwork)
        self._cb_verify.setChecked(s.verify_only)
        self._cb_gate.setChecked(s.export_gate_acknowledged)
        self._cb_advanced.setChecked(s.advanced_mode)
        apply_theme(s.theme or "system", themes_dir=self._paths.themes_dir)

    # --- run ------------------------------------------------------------------
    def _on_run(self) -> None:
        try:
            state = self._state_from_widgets()
            # Persist non-sensitive defaults for next launch (no secrets here).
            self._persist_defaults()

            from .worker import PipelineWorker

            self._cancel_event = __import__("threading").Event()
            self._worker = PipelineWorker(
                state, config_path=self._config_path, cancel_event=self._cancel_event
            )
            self._worker.progress.connect(self._on_progress)
            self._worker.finished.connect(self._on_finished)
            self._run_button.setEnabled(False)
            self._cancel_button.setEnabled(True)
            self._status_label.setText("Running…")
            self._worker.start()
        except Exception as exc:  # configuration errors surface as clear UI text
            QMessageBox.critical(self, "Cannot start", f"Could not start: {exc}")
            self._status_label.setText(f"Error: {exc}")

    def _on_cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self._status_label.setText("Cancelling…")

    def _on_progress(self, phase: str, percent: int, detail: str) -> None:
        self._progress.setValue(percent)
        self._status_label.setText(phase + (f" — {detail}" if detail else ""))
        self._diag.appendPlainText(phase)

    def _on_finished(self, result, error: str, cancelled: bool) -> None:
        self._run_button.setEnabled(True)
        self._cancel_button.setEnabled(False)
        if cancelled:
            self._status_label.setText("Cancelled.")
            self._diag.appendPlainText("Run cancelled.")
            return
        if error:
            # Errors never contain secret values; they are core/CLI messages.
            self._status_label.setText("Failed.")
            self._diag.appendPlainText(f"ERROR: {error}")
            QMessageBox.critical(self, "Run failed", error)
            return
        self._progress.setValue(100)
        groups = result.get("groups", 0) if result else 0
        self._status_label.setText(f"Done. {groups} group(s) processed.")
        self._diag.appendPlainText(
            f"files scanned: {result.get('files_scanned') if result else '?'}"
        )
        if result and result.get("export"):
            exp = result["export"]
            self._diag.appendPlainText(
                f"export: {exp.get('releases_exported')} releases, "
                f"{exp.get('folders_written')} folders"
            )

    # --- close ----------------------------------------------------------------
    def closeEvent(self, event: QEvent) -> None:
        # (Issue #17) Persist current folder/option defaults on normal exit so a
        # session that never started a run still survives the close/reopen cycle;
        # (Issue #18) the same call also persists the window geometry. Never
        # blocks or crashes the close (see _persist_defaults).
        self._persist_defaults()
        if self._cancel_event is not None:
            self._cancel_event.set()
        super().closeEvent(event)
