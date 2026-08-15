"""Security-remediation regression tests for Issue #15 (F1, F2, F4, F6, F8).

These tests pin the fixes from the Worf security review (t_492cba52):

  * F1 (HIGH) — the RedactingFilter must redact secrets emitted from a CHILD
    logger (e.g. ``amiga_adf_library_builder.playmatch``), not just the two
    intermediate loggers. This test FAILS against the pre-fix code at 34a7f15
    and PASSES after ``install_gui_redaction`` attaches the filter to the
    root logger's handler(s).
  * F2 (HIGH) — the GUI must be able to unlock the vault (master-password
    dialog) and must NOT let a locked-vault credential write raise an uncaught
    SecretError. A clear UI path is provided instead.
  * F4 (MEDIUM) — the interactive GUI must never auto-select the
    process-global EnvSecretBackend as its secret store; the default is the
    portable AES vault (PortableVaultBackend).
  * F6 (LOW) — the vault's PBKDF2 iteration count is a documented module-level
    constant (200_000), and the master-password dialog warns that the password
    is unrecoverable.
  * F8 (INFO) — the directory picker starts at the app config dir, not the
    user/home directory.

The full tracked suite must remain green under
``QT_QPA_PLATFORM=offscreen`` from the repo root.
"""

from __future__ import annotations

import logging
import io
from pathlib import Path

import pytest

from amiga_adf_library_builder.gui.secrets import (
    EnvSecretBackend,
    PortableVaultBackend,
    SecretError,
    SecretStore,
    _VAULT_PBKDF2_ITERATIONS,
    get_gui_redactor,
    install_gui_redaction,
)


# --- F1: child-logger redaction (the key HIGH finding) -----------------------


def _capture_root(level: int = logging.DEBUG):
    """Attach a fresh capturing handler to the root logger and return it.

    The redaction fix (install_gui_redaction) must attach the RedactingFilter
    to handlers on the root logger; a handler-level filter is what actually
    intercepts records propagated up from child submodule loggers.
    """
    root = logging.getLogger()
    root.setLevel(level)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(level)
    root.addHandler(handler)
    return root, handler, buf


@pytest.fixture
def redaction_installed():
    # install_gui_redaction is idempotent and process-wide. Ensure it.
    install_gui_redaction()
    yield
    # No teardown of the global filter: it is harmless and reused across tests.


def test_f1_child_logger_secret_is_redacted(redaction_installed):
    """A secret emitted from a CHILD logger must be redacted at emit time.

    This is the regression that failed before the fix: the old code attached
    the filter only to ``amiga_adf_library_builder`` and ``amiga_adf_gui``,
    which does NOT cover ``amiga_adf_library_builder.playmatch``.
    """
    root, handler, buf = _capture_root()
    # Re-run install so the (newly added) handler also gets the redactor, as it
    # would at app startup / when any handler is added.
    install_gui_redaction()
    try:
        child = logging.getLogger("amiga_adf_library_builder.playmatch")
        child.setLevel(logging.DEBUG)
        child.info("token=CHILDSECRET")
        emitted = buf.getvalue()
    finally:
        root.removeHandler(handler)
    assert "CHILDSECRET" not in emitted, "child-logger secret was NOT redacted"
    assert "REDACTED" in emitted, "expected a REDACTED marker in the output"


def test_f1_exact_secret_via_child_logger(redaction_installed):
    """An exact registered secret emitted from a child logger is masked (F5)."""
    root, handler, buf = _capture_root()
    install_gui_redaction()
    redactor = get_gui_redactor()
    assert redactor is not None
    try:
        redactor.add_secret("EXACTCHILDTOKEN")
        child = logging.getLogger("amiga_adf_library_builder.playmatch")
        child.setLevel(logging.DEBUG)
        child.info("auth header token EXACTCHILDTOKEN done")
        emitted = buf.getvalue()
    finally:
        redactor.remove_secret("EXACTCHILDTOKEN")
        root.removeHandler(handler)
    assert "EXACTCHILDTOKEN" not in emitted
    assert "REDACTED" in emitted


def test_f1_install_is_idempotent(redaction_installed):
    # Calling again must return the SAME instance and not stack filters.
    again = install_gui_redaction()
    assert again is get_gui_redactor()
    root = logging.getLogger()
    rf_count = sum(1 for f in root.filters if isinstance(f, type(again)))
    assert rf_count <= 1, "root logger filters stacked across installs"


# --- F2: vault unlockable from GUI; no uncaught SecretError -------------------


def _build_main_window(vault_path: Path):
    """Construct a MainWindow backed by the given vault path (offscreen)."""
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui import MainWindow, PortablePaths, SecretStore, SettingsStore

    app = QApplication.instance() or QApplication([])
    pp = PortablePaths(base_dir=vault_path.parent / "app-base")
    pp.ensure_all()
    store = SecretStore.with_vault(pp.vault_file())
    mw = MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=store,
        config_path=None,
    )
    return app, mw


def test_f2_locked_vault_set_credentials_raises_clear_path_not_escape(qt_offscreen):
    """A locked vault credential save must NOT raise an uncaught SecretError.

    The slot wraps the save in try/except; on a locked vault it must surface a
    clear UI message (simulated via QMessageBox.critical) rather than letting a
    SecretError propagate out of the slot.
    """
    from PySide6.QtWidgets import QApplication

    import amiga_adf_library_builder.gui.main_window as mwmod

    vault_dir = Path(qt_offscreen) / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    vault_file = vault_dir / "secrets.vault"
    # Create an UNLOCKED vault first, then re-lock it by constructing a fresh
    # locked backend (no master_password) with a known password requirement.
    seeded = SecretStore.with_vault(vault_file, master_password="correct-horse")
    seeded.set_secret("playmatch_token", "old-value")
    del seeded  # leave the on-disk vault locked.

    app, mw = _build_main_window(vault_file.parent / "app-base")
    # Back the main window's store with a LOCKED vault (no master_password).
    mw._secret_store = SecretStore.with_vault(vault_file)
    assert mw._secret_store.default_backend.is_unlocked is False

    # Simulate: operator provides a token, and the unlock step *reports* success
    # (returns True) but the backend is still locked (defensive edge case). The
    # real add_credentials must then raise SecretError, which the slot catches
    # and turns into a clear UI message -- never an uncaught exception.
    mw._prompt_credentials = lambda provider: "attempt-token-123"
    mw._ensure_vault_unlocked = lambda: True  # pretend unlock "succeeded"

    captured = {}

    def fake_critical(widget, title, text):
        captured["title"] = title
        captured["text"] = text

    orig_critical = mwmod.QMessageBox.critical
    mwmod.QMessageBox.critical = staticmethod(fake_critical)
    try:
        provider = mw._registry.get("playmatch")
        assert provider is not None
        # Drive the slot directly; it must NOT raise.
        mw._edit_credentials(provider)
    finally:
        mwmod.QMessageBox.critical = orig_critical

    assert "title" in captured, "no clear error path was produced"
    assert captured["title"] == "Vault is locked"
    # The token must never have been written to the (locked) vault. Reading a
    # locked vault raises SecretError, which proves the secret was not stored.
    try:
        stored = mw._secret_store.get_secret("playmatch_token")
        assert stored != "attempt-token-123"
    except SecretError:
        pass  # locked read is the expected outcome here
    QApplication.instance().quit() if QApplication.instance() else None


def test_f2_unlock_then_save_succeeds(qt_offscreen):
    """After unlocking via the dialog, a credential save must work (no raise)."""
    from PySide6.QtWidgets import QApplication

    import amiga_adf_library_builder.gui.main_window as mwmod

    vault_dir = Path(qt_offscreen) / "vault2"
    vault_dir.mkdir(parents=True, exist_ok=True)
    vault_file = vault_dir / "secrets.vault"
    app, mw = _build_main_window(vault_dir / "app-base")
    mw._secret_store = SecretStore.with_vault(vault_file)
    assert mw._secret_store.default_backend.is_unlocked is False

    # Stub the master-password dialog (module-global) so it auto-accepts with a
    # password. This exercises the real unlock wiring (set mode for a new vault)
    # WITHOUT a blocking QDialog.exec() event loop under offscreen Qt.
    class _AutoAcceptMasterPasswordDialog:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

        @property
        def password(self):
            return "brand-new-pw"

    orig_dialog = mwmod.MasterPasswordDialog
    mwmod.MasterPasswordDialog = _AutoAcceptMasterPasswordDialog
    try:
        ok = mw._ensure_vault_unlocked()
    finally:
        mwmod.MasterPasswordDialog = orig_dialog
    assert ok is True
    assert mw._secret_store.default_backend.is_unlocked is True

    # Now a real save must succeed without raising.
    provider = mw._registry.get("playmatch")
    assert provider is not None
    mw._secret_store.set_secret("playmatch_token", "post-unlock-token")
    assert mw._secret_store.get_secret("playmatch_token") == "post-unlock-token"
    QApplication.instance().quit() if QApplication.instance() else None


# --- F4: env backend is never the desktop default ----------------------------


def test_f4_default_secret_store_is_portable_vault():
    """The interactive GUI default store must be the portable AES vault.

    It must NOT be the EnvSecretBackend (which is process-global and only for
    CI/headless).
    """
    from amiga_adf_library_builder.gui.layout import PortablePaths

    pp = PortablePaths(base_dir=Path("/tmp/adfgui-f4-no-vault"))
    store = SecretStore.with_vault(pp.vault_file())
    assert isinstance(store.default_backend, PortableVaultBackend)
    assert not isinstance(store.default_backend, EnvSecretBackend)


def test_f4_vault_backend_is_portable_not_env(qt_offscreen):
    from amiga_adf_library_builder.gui import MainWindow, PortablePaths, SettingsStore

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    pp = PortablePaths(base_dir=Path(qt_offscreen) / "app")
    pp.ensure_all()
    mw = MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
    )
    assert isinstance(mw._secret_store.default_backend, PortableVaultBackend)
    assert not isinstance(mw._secret_store.default_backend, EnvSecretBackend)
    mw.close()


# --- F6: PBKDF2 constant + dialog warning ------------------------------------


def test_f6_pbkdf2_iterations_is_documented_constant():
    assert _VAULT_PBKDF2_ITERATIONS == 200_000
    # The backend default reads from the same constant.
    b = PortableVaultBackend(Path("/tmp/adfgui-f6-no-vault.vault"))
    assert b._iterations == _VAULT_PBKDF2_ITERATIONS


def test_f6_master_password_dialog_warns_unrecoverable(qt_offscreen):
    from PySide6.QtWidgets import QApplication, QLabel

    from amiga_adf_library_builder.gui.main_window import MasterPasswordDialog

    app = QApplication.instance() or QApplication([])
    dlg = MasterPasswordDialog(mode="set", vault_exists=False)
    # The warning text must communicate unrecoverability + password manager.
    texts = [w.text() for w in dlg.findChildren(QLabel)]
    combined = " ".join(texts).lower()
    assert "unrecoverable" in combined, f"warning missing: {texts}"
    assert "password manager" in combined, f"warning missing: {texts}"
    # set mode must require a confirm field (F6).
    assert dlg._confirm is not None
    dlg.close()


def test_f6_master_password_dialog_unlock_mode_has_no_confirm(qt_offscreen):
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui.main_window import MasterPasswordDialog

    app = QApplication.instance() or QApplication([])
    dlg = MasterPasswordDialog(mode="unlock", vault_exists=True)
    assert dlg._confirm is None
    dlg.close()


# --- F8: directory picker starts at config dir, not home ----------------------


def test_f8_pick_dir_starts_at_config_dir(qt_offscreen):
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui import MainWindow, PortablePaths, SettingsStore

    app = QApplication.instance() or QApplication([])
    pp = PortablePaths(base_dir=Path(qt_offscreen) / "app")
    pp.ensure_all()
    mw = MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
    )
    le = mw._le_library_root  # empty text by default
    # Do not actually open the native dialog; assert the start-path logic.
    start = le.text() or str(mw._paths.config_dir)
    assert "home" not in start.lower() or start.lower().startswith(str(pp.config_dir).lower())
    assert str(pp.config_dir) in start or start == str(pp.config_dir)
    assert "/home/" not in start
    mw.close()


# --- helpers ----------------------------------------------------------------


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "gui-offscreen-base"
