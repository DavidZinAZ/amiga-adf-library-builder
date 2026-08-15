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

import logging
from typing import Optional

from PySide6.QtCore import QEvent, Qt
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
            "Set vault master password" if mode == "set" else "Unlock secret vault"
        )
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        warn = QLabel(
            "The master password is unrecoverable. If lost, the stored "
            "credentials cannot be recovered — store it in a password manager."
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
        self.resize(900, 680)

        self._paths = portable_paths or PortablePaths()
        self._paths.ensure_all()
        self._settings_store = settings_store or SettingsStore(self._paths.settings_file())
        try:
            self._settings = self._settings_store.load()
        except Exception:
            self._settings = Settings()
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
        self._mode_build = QCheckBox("Build (scan/parse/group/enrich/quarantine)")
        self._mode_build.setChecked(True)
        self._mode_export = QCheckBox("Export to staging (opens Gotek export gate)")
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
        self._open_log_button = QPushButton("Open log directory")
        self._open_log_button.clicked.connect(self._on_open_logs)
        btn_row.addWidget(self._run_button)
        btn_row.addWidget(self._cancel_button)
        btn_row.addWidget(self._open_log_button)
        btn_row.addStretch(1)
        run_layout.addLayout(btn_row)
        root.addWidget(run_box)

    def _dir_row(self, label: str, line_edit: QLineEdit) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addWidget(line_edit, 1)
        picker = QPushButton("Browse…")
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
        layout.addLayout(self._dir_row("Library root", self._le_library_root))
        layout.addLayout(self._dir_row("Original (read-only)", self._le_original_dir))
        layout.addLayout(self._dir_row("Staging dir", self._le_staging_dir))
        layout.addLayout(self._dir_row("Output dir", self._le_output_dir))
        layout.addStretch(1)
        return w

    def _build_options_tab(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)
        self._cb_online = QCheckBox("Online (allow metadata providers)")
        self._cb_refresh = QCheckBox("Refresh metadata (ignore cache)")
        self._cb_artwork = QCheckBox("Require artwork (export preflight)")
        self._cb_verify = QCheckBox("Verify only (no writes; export mode)")
        self._cb_gate = QCheckBox("Export gate acknowledged (I confirm safety gate)")
        self._cb_advanced = QCheckBox("Advanced mode")
        for cb in (
            self._cb_online,
            self._cb_refresh,
            self._cb_artwork,
            self._cb_verify,
            self._cb_gate,
            self._cb_advanced,
        ):
            layout.addWidget(cb)
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
                "Stores the token securely in the local AES vault. "
                "Pipeline/provider wiring is planned (coming soon); the token "
                "is not yet passed to the run engine."
            )
            secret_btn.clicked.connect(
                lambda _checked=False, p=provider: self._edit_credentials(p)
            )
            form.addRow("Credentials", secret_btn)
        status_btn = QPushButton("Test connection")
        status_btn.clicked.connect(
            lambda _checked=False, p=provider: self._test_provider(p)
        )
        form.addRow("", status_btn)
        return box

    def _build_diagnostics_tab(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("Run output (secrets are redacted automatically):"))
        self._diag = QPlainTextEdit(self)
        self._diag.setReadOnly(True)
        layout.addWidget(self._diag)
        return w

    # --- interaction ----------------------------------------------------------
    def _pick_dir(self, line_edit: QLineEdit) -> None:
        # F8: start the picker at the app config dir (portable layout), not at
        # the user/home directory. Falls back to the last-used value if present.
        start = line_edit.text() or str(self._paths.config_dir)
        chosen = QFileDialog.getExistingDirectory(self, "Select directory", start)
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
                self, "Vault error", f"Could not open the vault: {exc}"
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
        token_le.setPlaceholderText("secret token (stored in vault, never shown)")
        layout.addWidget(QLabel("Token (kept in the secret store, not in config):"))
        layout.addWidget(token_le)
        # F3: honest affordance. The token is stored securely in the vault; the
        # bridge from the vault to the core run engine is deferred (MVP).
        note = QLabel(
            "Stored securely in the local vault. Pipeline/provider wiring is "
            "planned (coming soon); the token is not yet passed to the run engine."
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
                "Vault is locked",
                "Vault is locked — set a master password first, then try again.",
            )
            logger.warning("credential save failed: %s", exc)
            return
        finally:
            redactor.remove_secret(token)
        self._status_label.setText(
            f"{provider.metadata.name} credentials saved to the vault."
        )

    def _test_provider(self, provider: Provider) -> None:
        status = provider.test_connection()
        QMessageBox.information(
            self,
            f"{provider.metadata.name} status",
            f"Configured: {status.configured}\n{status.message}",
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
            "Select a library root and input/output directories, choose Build or "
            "Export, then Run. The GUI calls the same core pipeline as the CLI, so "
            "results match. Online providers are optional and disabled by default.",
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            f"Amiga ADF Library Builder — GUI\nVersion {gui_version}\n"
            "PySide6 (LGPL) presentation layer over the shared core.",
        )

    def _on_open_logs(self) -> None:
        path = self._paths.logs_dir
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception:
            QMessageBox.information(self, "Log directory", str(path))

    # --- state <-> widgets ----------------------------------------------------
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
        if s.default_library_root:
            self._le_library_root.setText(s.default_library_root)
        if s.default_original_dir:
            self._le_original_dir.setText(s.default_original_dir)
        if s.default_staging_dir:
            self._le_staging_dir.setText(s.default_staging_dir)
        if s.default_output_dir:
            self._le_output_dir.setText(s.default_output_dir)
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
            try:
                self._settings_store.update(
                    default_library_root=state.library_root,
                    default_original_dir=state.original_dir,
                    default_staging_dir=state.staging_dir,
                    default_output_dir=state.output_dir,
                    online=state.online,
                    refresh_metadata=state.refresh_metadata,
                    require_artwork=state.require_artwork,
                    verify_only=state.verify_only,
                    export_gate_acknowledged=state.export_gate_acknowledged,
                    advanced_mode=self._cb_advanced.isChecked(),
                )
            except Exception:
                pass

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
            QMessageBox.critical(self, "Cannot start", str(exc))
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
        if self._cancel_event is not None:
            self._cancel_event.set()
        super().closeEvent(event)
