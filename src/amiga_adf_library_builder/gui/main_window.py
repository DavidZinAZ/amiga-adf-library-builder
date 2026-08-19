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
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import activity_log
from ..logging_utils import redact
from ..local_media import (
    DEFAULT_MEDIA_ROOT_ASSET_TYPE,
    LAUNCHBOX_IMAGE_CATEGORIES,
    LocalMediaConfig,
    MediaRoot,
    ManualRoot,
    scan_launchbox_roots,
)
from . import __version__ as gui_version
from .layout import PortablePaths
from .providers import Provider, ProviderRegistry, default_registry
from .secrets import SecretError, SecretStore, install_gui_redaction
from .settings import Preset, Settings, SettingsStore
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
        # (Issue #21) run state for the live Diagnostics log.
        self._run_in_progress = False
        self._run_mode = "build"
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
        # (GH-20) Named configuration profiles: save the current widget state
        # as a named preset, or load one back. Both operate on the existing
        # non-sensitive SettingsStore preset API — no secrets involved.
        save_profile_act = file_menu.addAction("Save Profile")
        save_profile_act.setToolTip(
            "Save the current settings as the last-used named profile."
        )
        save_profile_act.triggered.connect(self._on_save_profile)
        save_as_act = file_menu.addAction("Save Profile As…")
        save_as_act.setToolTip(
            "Save the current settings under a new profile name. Overwriting "
            "an existing profile asks for confirmation."
        )
        save_as_act.triggered.connect(self._on_save_profile_as)
        load_act = file_menu.addAction("Load Profile…")
        load_act.setToolTip(
            "Load a saved configuration profile into the current settings."
        )
        load_act.triggered.connect(self._on_load_profile)
        file_menu.addSeparator()
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
        # (GH-33) LaunchBox local folder mappings (image/media + manuals).
        tabs.addTab(self._build_launchbox_tab(), "LaunchBox media")
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
        # (GH-24) Independent artwork selection: whether to SEARCH for artwork
        # at all. Distinct from "Require artwork before export" (which gates
        # the export). Default ON.
        self._cb_include_artwork = QCheckBox("Include artwork")
        self._cb_include_artwork.setChecked(True)
        self._cb_include_artwork.setToolTip(
            "Look for each release's cover artwork (approved local copies, "
            "your configured local libraries, then online sources). Turn off "
            "to skip artwork entirely for this run. On by default."
        )
        # (GH-24) Independent manuals/RTFM selection: whether to build the
        # deterministic RTFM manuals at all. Default ON.
        self._cb_include_manuals = QCheckBox("Include manuals (RTFM)")
        self._cb_include_manuals.setChecked(True)
        self._cb_include_manuals.setToolTip(
            "Build the release's RTFM manual from its notes when an [rtfm] "
            "configuration is present. Turn off to skip manual building for "
            "this run. On by default."
        )
        for cb in (
            self._cb_online, self._cb_refresh, self._cb_artwork,
            self._cb_include_artwork, self._cb_include_manuals,
        ):
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

    # --- (GH-33) LaunchBox local media tab -------------------------------------
    def _build_launchbox_tab(self) -> QWidget:
        """Local LaunchBox folder mappings: image/media roots + manual roots.

        (GH-33) Everything here is LOCAL ONLY: native folder browsing, add /
        remove mappings, per-root asset-type selection, and a read-only
        "Check roots" diagnostic (scanned / missing, candidate counts). No
        network egress; nothing is ever deleted from the mappings list by a
        missing path — missing roots are surfaced as diagnostics only.
        """
        w = QWidget(self)
        layout = QVBoxLayout(w)

        # --- artwork (image / media) roots ----------------------------------
        # (GH-23) The list ORDER is the precedence order: when the same
        # asset type is found in more than one root, the FIRST root in this
        # list wins. Move Up / Move Down therefore change which artwork wins.
        media_box = QGroupBox("Artwork roots (each root has one asset type)")
        media_box.setToolTip(
            "Local LaunchBox artwork folders. Pick a folder and choose which "
            "LaunchBox media type it holds (for example \"Box - Front\"). "
            "List order is precedence order: when the same artwork type is "
            "present in several roots, the first root wins. Use Move Up / "
            "Move Down to change priority. Everything stays local; nothing "
            "is uploaded."
        )
        media_layout = QVBoxLayout(media_box)
        self._lb_media_table = QTableWidget(0, 2)
        self._lb_media_table.setHorizontalHeaderLabels(["Folder", "Asset type"])
        self._lb_media_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._lb_media_table.horizontalHeader().setStretchLastSection(True)
        self._lb_media_table.verticalHeader().setVisible(False)
        self._lb_media_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._lb_media_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._lb_media_table.setSelectionMode(QAbstractItemView.SingleSelection)
        media_layout.addWidget(self._lb_media_table)
        media_buttons = QHBoxLayout()
        self._lb_media_add_button = QPushButton("Add folder…")
        self._lb_media_add_button.setToolTip(
            "Choose a local folder of images (Browse). You can then select "
            "its asset type from the list."
        )
        self._lb_media_add_button.clicked.connect(self._lb_add_media_root)
        self._lb_media_remove_button = QPushButton("Remove")
        self._lb_media_remove_button.setToolTip(
            "Remove the selected mapping. The folder itself is never touched."
        )
        self._lb_media_remove_button.clicked.connect(self._lb_remove_media_root)
        self._lb_media_up_button = QPushButton("Move Up")
        self._lb_media_up_button.setToolTip(
            "Raise the selected artwork root's priority (closer to the top "
            "wins same-type artwork conflicts)."
        )
        self._lb_media_up_button.clicked.connect(self._lb_move_media_root_up)
        self._lb_media_down_button = QPushButton("Move Down")
        self._lb_media_down_button.setToolTip(
            "Lower the selected artwork root's priority (the first root in "
            "the list wins same-type artwork conflicts)."
        )
        self._lb_media_down_button.clicked.connect(self._lb_move_media_root_down)
        media_buttons.addWidget(self._lb_media_add_button)
        media_buttons.addWidget(self._lb_media_remove_button)
        media_buttons.addWidget(self._lb_media_up_button)
        media_buttons.addWidget(self._lb_media_down_button)
        media_buttons.addStretch(1)
        media_layout.addLayout(media_buttons)
        layout.addWidget(media_box)

        # --- manual / RTFM roots --------------------------------------------
        # (GH-23) Same rule as artwork: list order is precedence order; the
        # first manual root that contains a matching document wins.
        manual_box = QGroupBox("Manuals / RTFM roots (PDF / TXT documents)")
        manual_box.setToolTip(
            "Local folders holding manual / RTFM documents (.pdf, .txt). "
            "List order is precedence order: when the same manual is found "
            "in several roots, the first root wins. Use Move Up / Move Down "
            "to change priority. Local only; nothing is uploaded."
        )
        manual_layout = QVBoxLayout(manual_box)
        self._lb_manual_list = QListWidget()
        self._lb_manual_list.setToolTip(
            "Folders of manual / RTFM documents. Add as many as you need. "
            "Top of the list has the highest priority."
        )
        manual_layout.addWidget(self._lb_manual_list)
        manual_buttons = QHBoxLayout()
        self._lb_manual_add_button = QPushButton("Add folder…")
        self._lb_manual_add_button.setToolTip(
            "Choose a local folder of manual / RTFM documents (Browse)."
        )
        self._lb_manual_add_button.clicked.connect(self._lb_add_manual_root)
        self._lb_manual_remove_button = QPushButton("Remove")
        self._lb_manual_remove_button.setToolTip(
            "Remove the selected mapping. The folder itself is never touched."
        )
        self._lb_manual_remove_button.clicked.connect(self._lb_remove_manual_root)
        self._lb_manual_up_button = QPushButton("Move Up")
        self._lb_manual_up_button.setToolTip(
            "Raise the selected manual / RTFM root's priority (closer to the "
            "top wins same-manual conflicts)."
        )
        self._lb_manual_up_button.clicked.connect(self._lb_move_manual_root_up)
        self._lb_manual_down_button = QPushButton("Move Down")
        self._lb_manual_down_button.setToolTip(
            "Lower the selected manual / RTFM root's priority (the first root "
            "in the list wins same-manual conflicts)."
        )
        self._lb_manual_down_button.clicked.connect(self._lb_move_manual_root_down)
        manual_buttons.addWidget(self._lb_manual_add_button)
        manual_buttons.addWidget(self._lb_manual_remove_button)
        manual_buttons.addWidget(self._lb_manual_up_button)
        manual_buttons.addWidget(self._lb_manual_down_button)
        manual_buttons.addStretch(1)
        manual_layout.addLayout(manual_buttons)
        layout.addWidget(manual_box)

        # --- diagnostics ---------------------------------------------------------
        diag_row = QHBoxLayout()
        self._lb_check_button = QPushButton("Check roots…")
        self._lb_check_button.setToolTip(
            "Read-only check: shows which configured roots were scanned and "
            "which are missing, plus how many image / manual candidates each "
            "root holds. Missing folders are kept, just reported. No network."
        )
        self._lb_check_button.clicked.connect(self._lb_check_roots)
        self._lb_diag_label = QLabel("")
        self._lb_diag_label.setWordWrap(True)
        diag_row.addWidget(self._lb_check_button)
        diag_row.addWidget(self._lb_diag_label, 1)
        layout.addLayout(diag_row)
        layout.addStretch(1)
        return w

    # --- (GH-33) LaunchBox mapping helpers --------------------------------------
    def _lb_asset_type_combo(self, asset_type: str) -> QComboBox:
        combo = QComboBox(self)
        combo.addItem(DEFAULT_MEDIA_ROOT_ASSET_TYPE)
        for category in LAUNCHBOX_IMAGE_CATEGORIES:
            if category != DEFAULT_MEDIA_ROOT_ASSET_TYPE:
                combo.addItem(category)
        index = combo.findText(asset_type)
        combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    def _lb_add_media_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose an image / media folder"
        )
        if not folder:
            return
        self._lb_media_table.insertRow(self._lb_media_table.rowCount())
        path_item = QTableWidgetItem(folder)
        self._lb_media_table.setItem(self._lb_media_table.rowCount() - 1, 0, path_item)
        combo = self._lb_asset_type_combo(DEFAULT_MEDIA_ROOT_ASSET_TYPE)
        self._lb_media_table.setCellWidget(
            self._lb_media_table.rowCount() - 1, 1, combo
        )
        self._lb_diag_label.setText("")

    def _lb_remove_media_root(self) -> None:
        row = self._lb_media_table.currentRow()
        if row >= 0:
            self._lb_media_table.removeRow(row)
        self._lb_diag_label.setText("")

    def _lb_add_manual_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a manual documents folder"
        )
        if not folder:
            return
        self._lb_manual_list.addItem(folder)
        self._lb_diag_label.setText("")

    def _lb_remove_manual_root(self) -> None:
        item = self._lb_manual_list.currentItem()
        if item is not None:
            self._lb_manual_list.takeItem(self._lb_manual_list.row(item))
        self._lb_diag_label.setText("")

    # --- (GH-23) reorder handlers: list order is precedence order -----------
    def _lb_move_media_root_up(self) -> None:
        """Raise the selected artwork row one position (toward the top).

        Reorder swaps the whole row (folder + its asset-type combo), so the
        underlying ordered list produced by ``_lb_media_mappings()`` changes.
        The first root in the list wins same-category artwork conflicts, so
        this changes which image is selected. A no-op at the top.
        """
        row = self._lb_media_table.currentRow()
        if row <= 0:
            return
        self._lb_swap_media_rows(row - 1, row)
        self._lb_media_table.selectRow(row - 1)
        self._lb_diag_label.setText("")

    def _lb_move_media_root_down(self) -> None:
        """Lower the selected artwork row one position (toward the bottom).

        A no-op at the bottom. See :meth:`_lb_move_media_root_up`.
        """
        row = self._lb_media_table.currentRow()
        last = self._lb_media_table.rowCount() - 1
        if row < 0 or row >= last:
            return
        self._lb_swap_media_rows(row, row + 1)
        self._lb_media_table.selectRow(row + 1)
        self._lb_diag_label.setText("")

    def _lb_swap_media_rows(self, a: int, b: int) -> None:
        """Swap artwork rows ``a`` and ``b`` (path item + asset-type combo)."""
        pa = self._lb_media_table.takeItem(a, 0)
        pb = self._lb_media_table.takeItem(b, 0)
        wa = self._lb_media_table.takeWidget(a, 1)
        wb = self._lb_media_table.takeWidget(b, 1)
        self._lb_media_table.setItem(a, 0, pb)
        self._lb_media_table.setItem(b, 0, pa)
        self._lb_media_table.setCellWidget(a, 1, wb)
        self._lb_media_table.setCellWidget(b, 1, wa)

    def _lb_move_manual_root_up(self) -> None:
        """Raise the selected manual / RTFM row one position. No-op at top."""
        row = self._lb_manual_list.currentRow()
        if row <= 0:
            return
        self._lb_swap_manual_items(row - 1, row)
        self._lb_manual_list.setCurrentRow(row - 1)
        self._lb_diag_label.setText("")

    def _lb_move_manual_root_down(self) -> None:
        """Lower the selected manual / RTFM row one position. No-op at bottom."""
        row = self._lb_manual_list.currentRow()
        last = self._lb_manual_list.count() - 1
        if row < 0 or row >= last:
            return
        self._lb_swap_manual_items(row, row + 1)
        self._lb_manual_list.setCurrentRow(row + 1)
        self._lb_diag_label.setText("")

    def _lb_swap_manual_items(self, a: int, b: int) -> None:
        """Swap manual / RTFM list items ``a`` and ``b`` (text + user data)."""
        ia = self._lb_manual_list.takeItem(a)
        ib = self._lb_manual_list.takeItem(b)
        if ia is None or ib is None:
            # Defensive: put back whatever we took and abort the swap.
            if ia is not None:
                self._lb_manual_list.insertItem(a, ia)
            if ib is not None:
                self._lb_manual_list.insertItem(b, ib)
            return
        self._lb_manual_list.insertItem(a, ib)
        self._lb_manual_list.insertItem(b, ia)

    def _lb_media_mappings(self) -> list[dict]:
        out: list[dict] = []
        for row in range(self._lb_media_table.rowCount()):
            item = self._lb_media_table.item(row, 0)
            combo = self._lb_media_table.cellWidget(row, 1)
            if item is None:
                continue
            path = item.text().strip()
            if not path:
                continue
            asset_type = combo.currentText().strip() if combo is not None else ""
            out.append({"path": path, "asset_type": asset_type})
        return out

    def _lb_manual_mappings(self) -> list[str]:
        out: list[str] = []
        for row in range(self._lb_manual_list.count()):
            item = self._lb_manual_list.item(row)
            if item is not None:
                text = item.text().strip()
                if text:
                    out.append(text)
        return out

    def _lb_check_roots(self) -> None:
        """Read-only diagnostics: scan the configured LaunchBox roots (GH-33)."""
        cfg = LocalMediaConfig(
            enabled=True,
            media_roots=tuple(
                MediaRoot(path=m["path"], asset_type=m["asset_type"])
                for m in self._lb_media_mappings()
            ),
            manual_roots=tuple(
                ManualRoot(path=p) for p in self._lb_manual_mappings()
            ),
        )
        try:
            report = scan_launchbox_roots(cfg)
        except Exception as exc:  # a check failure is reported, never fatal
            self._lb_diag_label.setText(f"Check failed: {exc}")
            return
        if not report.roots:
            self._lb_diag_label.setText("No LaunchBox roots configured yet.")
            return
        self._lb_diag_label.setText("\n".join(report.to_lines()))
        for line in report.to_lines():
            self._append_diag(line)

    def _build_diagnostics_tab(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.addWidget(
            QLabel("Live activity log (sensitive values are hidden):")
        )

        # (Issue #21) Toolbar: show-live toggle + log controls.
        # Order: [Show live processing log]  [Clear]  [Jump to top]
        #         [Bottom / Follow Live: On]  [stretch]
        # Copy Log / Open Log File stay in the Run area below (unchanged).
        bar = QHBoxLayout()
        self._cb_show_live_log = QCheckBox("Show live processing log")
        self._cb_show_live_log.setToolTip(
            "Show what the app is doing while a run is in progress. "
            "Your choice is remembered."
        )
        self._cb_show_live_log.setChecked(self._settings.show_live_log)
        self._diag_clear_button = QPushButton("Clear")
        self._diag_clear_button.setToolTip("Clear the log shown here.")
        self._diag_top_button = QPushButton("Jump to top")
        self._diag_top_button.setToolTip("Go to the first line of the log.")
        self._diag_bottom_button = QPushButton("Follow Live: On")
        self._diag_bottom_button.setToolTip(
            "Keep the view pinned to the newest lines while the run is "
            "progressing. Click to turn it off; click again to turn it "
            "back on."
        )
        self._diag_bottom_button.clicked.connect(self._on_diag_bottom)
        bar.addWidget(self._cb_show_live_log)
        bar.addSpacing(12)
        bar.addWidget(self._diag_clear_button)
        bar.addWidget(self._diag_top_button)
        bar.addWidget(self._diag_bottom_button)
        bar.addStretch(1)
        layout.addLayout(bar)

        self._diag = QPlainTextEdit(self)
        self._diag.setReadOnly(True)
        # Keep the widget's memory bounded for very long runs: drop the
        # oldest lines once the view grows past this many.
        self._diag.setMaximumBlockCount(5000)
        layout.addWidget(self._diag)

        self._diag_clear_button.clicked.connect(self._on_diag_clear)
        self._diag_top_button.clicked.connect(self._on_diag_top)
        # (Issue #21) Follow Live: pinned to the newest line by default.
        self._follow_live = True
        return w

    # --- (Issue #21) Diagnostics log controls ---------------------------------
    def _append_diag(self, line: str) -> None:
        """Append one line to the Diagnostics log with a timestamp.

        Every entry is timestamped (``HH:MM:SS``) and redacted so a folder
        path or provider detail can never carry a secret into the view.
        While Follow Live is on the view stays pinned to the newest line.
        """
        if not self._cb_show_live_log.isChecked() and getattr(
            self, "_run_in_progress", False
        ):
            # Live log switched off mid-run: stop appending live lines.
            # Run-boundary markers are still added (see _run_marker).
            return
        text = activity_log.run_activity_line(line)
        self._diag.appendPlainText(text)
        if self._follow_live:
            self._diag.verticalScrollBar().setValue(
                self._diag.verticalScrollBar().maximum()
            )

    def _run_marker(self, text: str) -> None:
        """Append a run boundary line (start/end) even when the live log is off."""
        self._diag.appendPlainText(activity_log.run_activity_line(text))
        self._diag.verticalScrollBar().setValue(
            self._diag.verticalScrollBar().maximum()
        )

    def _on_diag_clear(self) -> None:
        self._diag.clear()

    def _on_diag_top(self) -> None:
        self._diag.verticalScrollBar().setValue(0)

    def _on_diag_bottom(self) -> None:
        """Jump to the newest line and toggle the Follow Live pin."""
        self._diag.verticalScrollBar().setValue(
            self._diag.verticalScrollBar().maximum()
        )
        self._follow_live = not self._follow_live
        self._diag_bottom_button.setText(
            "Follow Live: On" if self._follow_live else "Follow Live: Off"
        )

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
                # (Issue #21) live Diagnostics log toggle (non-sensitive).
                "show_live_log": self._cb_show_live_log.isChecked(),
                # (GH-24) independent metadata selection (non-sensitive).
                "include_artwork": state.include_artwork,
                "include_manuals_rtfm": state.include_manuals_rtfm,
                # (GH-33) LaunchBox local folder mappings (non-sensitive local
                # paths; missing folders are kept, never deleted).
                "launchbox_media_roots": self._lb_media_mappings(),
                "launchbox_manual_roots": self._lb_manual_mappings(),
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
            # (GH-24) independent metadata selection.
            include_artwork=self._cb_include_artwork.isChecked(),
            include_manuals_rtfm=self._cb_include_manuals.isChecked(),
            # (GH-33) LaunchBox local folder mappings (LOCAL ONLY; empty lists
            # keep the pipeline identical to the CLI's provider-config path).
            launchbox_media_roots=self._lb_media_mappings(),
            launchbox_manual_roots=self._lb_manual_mappings(),
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
        self._cb_show_live_log.setChecked(s.show_live_log)
        # (GH-24) independent metadata selection.
        self._cb_include_artwork.setChecked(s.include_artwork)
        self._cb_include_manuals.setChecked(s.include_manuals_rtfm)
        self._lb_restore_mappings(s)
        apply_theme(s.theme or "system", themes_dir=self._paths.themes_dir)

    # --- (GH-33) LaunchBox mappings: restore from persisted settings ----------
    def _lb_restore_mappings(self, s: "Settings") -> None:
        """Restore persisted LaunchBox mappings into the tab widgets (GH-33).

        Missing paths are KEPT (never deleted on absence) and surfaced as a
        status diagnostic — the same behavior as the Issue #17 folder fields.
        """
        self._lb_media_table.setRowCount(0)
        for mapping in s.launchbox_media_roots or []:
            path = str(mapping.get("path") or "").strip()
            if not path:
                continue
            row = self._lb_media_table.rowCount()
            self._lb_media_table.insertRow(row)
            self._lb_media_table.setItem(row, 0, QTableWidgetItem(path))
            self._lb_media_table.setCellWidget(
                row, 1, self._lb_asset_type_combo(mapping.get("asset_type", ""))
            )
        self._lb_manual_list.clear()
        for path in s.launchbox_manual_roots or []:
            text = str(path).strip()
            if text:
                self._lb_manual_list.addItem(text)
        # Diagnostic for mappings that cannot be found on THIS machine.
        missing: list[str] = []
        for mapping in s.launchbox_media_roots or []:
            path = str(mapping.get("path") or "").strip()
            if path and not Path(path).is_dir():
                missing.append(path)
        for path in s.launchbox_manual_roots or []:
            text = str(path).strip()
            if text and not Path(text).is_dir():
                missing.append(text)
        if missing:
            message = (
                "LaunchBox root(s) not found on this machine (kept): "
                + "; ".join(missing)
            )
            logger.debug("%s", message)
            self._status_label.setText(message)

    # --- (GH-20) named configuration profiles ----------------------------------
    #
    # A profile is a named snapshot of the non-sensitive settings currently in
    # the widgets: the four folder paths, the run-mode toggles, the GH-24
    # independent metadata selection, and the GH-33 LaunchBox local mappings.
    # It is stored through the EXISTING SettingsStore preset API
    # (``save_preset`` / ``apply_preset`` / ``get().presets``), which already
    # serializes only non-sensitive data. There is deliberately no code path
    # here that reads SecretStore or any secret field — the ``Preset``
    # dataclass has no such field, so a saved profile file cannot carry a
    # secret.
    #
    # Loading a profile is NON-DESTRUCTIVE in the same sense as the startup
    # restore: folder paths are applied to the fields even when the folder
    # does not exist on THIS machine (a profile may travel between machines),
    # but the missing paths are reported cleanly — status label plus a
    # QMessageBox warning — and nothing is deleted or overwritten on disk.

    _DEFAULT_PROFILE_NAME = "Default"

    def _preset_from_widgets(self) -> "Preset":
        """Collect the current widget state into a :class:`Preset`.

        Never touches SecretStore or any secret field: every value comes from
        the non-sensitive widgets (folder line edits, option checkboxes,
        LaunchBox mapping tables) via the same accessors ``_persist_defaults``
        uses for the last-used settings.
        """
        state = self._state_from_widgets()
        return Preset(
            name=self._last_profile_name(),
            library_root=state.library_root,
            original_dir=state.original_dir,
            staging_dir=state.staging_dir,
            output_dir=state.output_dir,
            online=state.online,
            refresh_metadata=state.refresh_metadata,
            require_artwork=state.require_artwork,
            verify_only=state.verify_only,
            export_gate_acknowledged=state.export_gate_acknowledged,
            advanced_mode=self._cb_advanced.isChecked(),
            include_artwork=state.include_artwork,
            include_manuals_rtfm=state.include_manuals_rtfm,
            launchbox_media_roots=self._lb_media_mappings(),
            launchbox_manual_roots=self._lb_manual_mappings(),
        )

    def _last_profile_name(self) -> str:
        """Name the current settings snapshot should be saved under.

        Tracks the last profile loaded by name (``_profile_name``), so a
        session that loads "Work" and tweaks the widgets can re-save "Work"
        from File -> Save Profile without retyping the name. Fresh or
        default-startup sessions fall back to the "Default" profile.
        """
        name = (getattr(self, "_profile_name", "") or "").strip()
        return name or self._DEFAULT_PROFILE_NAME

    def _on_save_profile(self) -> None:
        """File -> Save Profile: store the widgets under the last name."""
        preset = self._preset_from_widgets()
        preset.name = self._last_profile_name()
        try:
            self._settings_store.save_preset(preset)
            self._profile_name = preset.name
            self._status_label.setText(f"Saved profile '{preset.name}'.")
            self._append_diag(f"Saved profile '{preset.name}'.")
        except Exception as exc:
            QMessageBox.critical(self, "Save profile", f"Could not save profile: {exc}")
            self._status_label.setText("Saving the profile failed.")

    def _on_save_profile_as(self) -> None:
        """File -> Save Profile As…: prompt for a name, confirm overwrites."""
        presets = self._settings_store.get().presets
        name, ok = QInputDialog.getText(
            self,
            "Save Profile As",
            "Profile name:",
            text=self._last_profile_name(),
        )
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in presets:
            answer = QMessageBox.question(
                self,
                "Save Profile",
                f"A profile named '{name}' already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        preset = self._preset_from_widgets()
        preset.name = name
        try:
            self._settings_store.save_preset(preset)
            self._profile_name = name
            self._status_label.setText(f"Saved profile '{name}'.")
            self._append_diag(f"Saved profile '{name}'.")
        except Exception as exc:
            QMessageBox.critical(self, "Save profile", f"Could not save profile: {exc}")
            self._status_label.setText("Saving the profile failed.")

    def _on_load_profile(self) -> None:
        """File -> Load Profile…: pick a saved preset and apply it."""
        names = sorted(self._settings_store.get().presets)
        if not names:
            QMessageBox.information(
                self, "Load Profile", "No saved profiles yet."
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Load Profile")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Choose a configuration profile to load:"))
        self._profile_choice = QListWidget(dialog)
        for name in names:
            self._profile_choice.addItem(QListWidgetItem(name, self._profile_choice))
        if len(names) == 1:
            self._profile_choice.setCurrentRow(0)
        layout.addWidget(self._profile_choice)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        row = self._profile_choice.currentRow()
        if row < 0:
            return
        self._load_profile(names[row])

    def _load_profile(self, name: str) -> bool:
        """Load preset ``name`` into the widgets and persist as last-used.

        Non-destructive: the preset's folder paths are applied to the fields
        even when a folder is missing on this machine (profiles travel
        between machines); the missing paths are then reported via a clean
        QMessageBox warning plus the status label. Returns True when the
        preset was applied (or reported-with-missing-paths), False when the
        name is unknown or the store rejected it — in which case the current
        widgets are left untouched.
        """
        try:
            preset = self._settings_store.apply_preset(name)
        except Exception as exc:
            QMessageBox.critical(
                self, "Load Profile", f"Could not load profile '{name}': {exc}"
            )
            self._status_label.setText("Loading the profile failed.")
            return False

        # Map Preset -> Settings onto the same widget targets the startup
        # restore uses (``_apply_settings_to_widgets`` reads ``self._settings``).
        # window_geometry is deliberately left as-is: loading a profile must
        # not move or resize the window.
        self._settings.default_library_root = preset.library_root
        self._settings.default_original_dir = preset.original_dir
        self._settings.default_staging_dir = preset.staging_dir
        self._settings.default_output_dir = preset.output_dir
        self._settings.online = preset.online
        self._settings.refresh_metadata = preset.refresh_metadata
        self._settings.require_artwork = preset.require_artwork
        self._settings.verify_only = preset.verify_only
        self._settings.export_gate_acknowledged = preset.export_gate_acknowledged
        self._settings.advanced_mode = preset.advanced_mode
        self._settings.include_artwork = preset.include_artwork
        self._settings.include_manuals_rtfm = preset.include_manuals_rtfm
        self._settings.launchbox_media_roots = preset.launchbox_media_roots
        self._settings.launchbox_manual_roots = preset.launchbox_manual_roots
        self._profile_name = name
        self._apply_settings_to_widgets()

        # Report paths the preset carries that do not exist on THIS machine.
        # They are KEPT in the fields (never cleared, nothing deleted) — the
        # operator reselects them if needed.
        missing = [
            path
            for path in (
                preset.library_root,
                preset.original_dir,
                preset.staging_dir,
                preset.output_dir,
            )
            if path and not Path(path).is_dir()
        ]
        for mapping in preset.launchbox_media_roots or []:
            path = str(mapping.get("path") or "").strip()
            if path and not Path(path).is_dir():
                missing.append(path)
        for path in preset.launchbox_manual_roots or []:
            text = str(path).strip()
            if text and not Path(text).is_dir():
                missing.append(text)
        if missing:
            message = "Profile path(s) not found on this machine (kept): " + "; ".join(
                missing
            )
            logger.debug("%s", message)
            QMessageBox.warning(self, "Load Profile", message)
            self._status_label.setText(message)

        # Persist the loaded profile as the new last-used settings so the
        # next launch starts exactly where this profile left off. The
        # automatic last-used behavior (run/close persistence) coexists: it
        # simply re-persists whatever is in the widgets at that moment.
        self._persist_defaults()
        self._append_diag(f"Loaded profile '{name}'.")
        return True

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
            # (Issue #21) live activity lines from the worker thread.
            self._worker.activity.connect(self._on_activity)
            self._run_in_progress = True
            self._run_mode = state.run_mode
            # (Issue #21) run boundary: the log shows every run start/end.
            self._run_marker("=== RUN START ===")
            self._run_button.setEnabled(False)
            self._cancel_button.setEnabled(True)
            self._status_label.setText("Running…")
            self._worker.start()
        except Exception as exc:  # configuration errors surface as clear UI text
            self._run_in_progress = False
            self._run_marker(f"Run could not be started: {exc}")
            QMessageBox.critical(self, "Cannot start", f"Could not start: {exc}")
            self._status_label.setText(f"Error: {exc}")

    def _on_activity(self, line: str) -> None:
        """Worker-thread activity line -> live Diagnostics log (issue #21)."""
        self._append_diag(line)

    def _on_cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self._status_label.setText("Cancelling…")
            self._append_diag("Cancelling the run…")

    def _on_progress(self, phase: str, percent: int, detail: str) -> None:
        # (Issue #21) The Diagnostics log no longer repeats stage names here;
        # the worker's activity lines carry the real progress. The progress
        # bar + status label keep the phase for at-a-glance feedback.
        self._progress.setValue(percent)
        self._status_label.setText(phase + (f" — {redact(detail)}" if detail else ""))

    def _on_finished(self, result, error: str, cancelled: bool) -> None:
        self._run_in_progress = False
        self._run_button.setEnabled(True)
        self._cancel_button.setEnabled(False)
        if cancelled:
            self._status_label.setText("Cancelled.")
            self._run_marker("Run cancelled by the operator.")
            self._run_marker("=== RUN END (cancelled) ===")
            return
        if error:
            # Errors never contain secret values; they are core/CLI messages.
            self._status_label.setText("Failed.")
            self._append_diag(f"ERROR: {error}")
            self._run_marker("=== RUN END (failed) ===")
            QMessageBox.critical(self, "Run failed", error)
            return
        self._progress.setValue(100)
        groups = result.get("groups", 0) if result else 0
        self._status_label.setText(f"Done. {groups} group(s) processed.")
        # (Issue #21) end-of-run result summary: outcome, counts, destinations.
        for line in activity_log.render_run_summary(
            result, run_mode=getattr(self, "_run_mode", "build")
        ):
            self._append_diag(line)
        self._run_marker("=== RUN END (done) ===")

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
