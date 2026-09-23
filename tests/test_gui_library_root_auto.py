"""GH-186 regression tests: auto-created Library Root beside the executable.

The Library Root is now automatically created at <portable-base>/Library-Root
on MainWindow construction. It is never derived from CWD, never placed under
AppData/Documents, and persists across relaunches.

Headless: relies on QT_QPA_PLATFORM=offscreen (same pattern as
tests/test_gui_import.py). Deterministic on pytest tmp dirs.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from amiga_adf_library_builder.gui.layout import PortablePaths
from amiga_adf_library_builder.gui.secrets import SecretStore
from amiga_adf_library_builder.gui.settings import SettingsStore
from amiga_adf_library_builder.gui import MainWindow


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "gh186-base"


def _make_window(base_dir: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    pp = PortablePaths(base_dir=base_dir)
    pp.ensure_all()
    return MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
    )


def test_construction_creates_library_root(qt_offscreen):
    """MainWindow construction must create <base>/Library-Root."""
    base = Path(qt_offscreen)
    mw = _make_window(base)
    assert (base / "Library-Root").is_dir()
    assert mw._library_root == base / "Library-Root"
    mw.close()


def test_relaunch_reuses_same_root(qt_offscreen):
    """Relaunching on the same base reuses the existing Library-Root."""
    base = Path(qt_offscreen)
    mw1 = _make_window(base)
    root1 = mw1._library_root
    mw1.close()

    mw2 = _make_window(base)
    root2 = mw2._library_root
    assert root1 == root2
    assert root1 == base / "Library-Root"
    mw2.close()


def test_cwd_independence(qt_offscreen):
    """Changing the process cwd must not change the Library Root."""
    import os

    base = Path(qt_offscreen)
    base.mkdir(parents=True, exist_ok=True)
    original_cwd = os.getcwd()
    try:
        os.chdir(base)
        mw = _make_window(base)
        assert mw._library_root == base / "Library-Root"
        assert str(mw._library_root) == str(base / "Library-Root")
        mw.close()
    finally:
        os.chdir(original_cwd)


def test_state_from_widgets_uses_internal_root(qt_offscreen):
    """_state_from_widgets() must resolve library_root to the internal path."""
    base = Path(qt_offscreen)
    mw = _make_window(base)
    state = mw._state_from_widgets()
    assert state.library_root == str(base / "Library-Root")
    mw.close()


def test_diagnostics_contains_library_root(qt_offscreen, capsys):
    """Diagnostics must contain the resolved Library Root path."""
    base = Path(qt_offscreen)
    mw = _make_window(base)
    # The Library Root is appended via _append_diag in __init__.
    diag_text = mw._diag.toPlainText()
    assert "Library Root:" in diag_text
    assert str(base / "Library-Root") in diag_text
    mw.close()


def test_library_root_under_base_not_cwd(qt_offscreen):
    """Library Root must never be derived from CWD."""
    base = Path(qt_offscreen)
    mw = _make_window(base)
    # The root must be under base, not under the process cwd.
    assert str(mw._library_root).startswith(str(base))
    assert "Library-Root" in mw._library_root.name
    mw.close()


def test_no_le_library_root_widget(qt_offscreen):
    """The _le_library_root widget must not exist after GH-186 changes."""
    base = Path(qt_offscreen)
    mw = _make_window(base)
    assert not hasattr(mw, "_le_library_root")
    mw.close()


def test_invalid_base_fails_cleanly(tmp_path: Path):
    """A base path that is a file (not a directory) must produce a clean
    fatal error with non-zero exit, not a traceback crash."""
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    file_base = tmp_path / "not-a-dir"
    file_base.touch()

    with pytest.raises(SystemExit) as excinfo:
        MainWindow(
            portable_paths=PortablePaths(base_dir=file_base),
            settings_store=SettingsStore(file_base / "gui-settings.toml"),
            secret_store=SecretStore.with_vault(file_base / "secrets.vault"),
        )
    assert excinfo.value.code != 0
    assert "not a directory" in str(excinfo.value)


def test_library_root_uses_library_root_property(qt_offscreen):
    """PortablePaths.library_root must return <base>/Library-Root."""
    base = Path(qt_offscreen)
    pp = PortablePaths(base_dir=base)
    assert pp.library_root == base / "Library-Root"


def test_default_library_root_loads_but_ignored(qt_offscreen):
    """Legacy default_library_root in settings loads without error but does
    not override the auto-managed runtime Library Root."""
    base = Path(qt_offscreen)
    pp = PortablePaths(base_dir=base)
    store = SettingsStore(pp.settings_file())
    store.load()
    store.update(default_library_root="/some/legacy/path")
    store2 = SettingsStore(pp.settings_file())
    s = store2.load()
    assert s.default_library_root == "/some/legacy/path"
    # But the actual Library Root is still auto-managed.
    mw = _make_window(base)
    assert mw._library_root == base / "Library-Root"
    assert mw._library_root != Path("/some/legacy/path")
    mw.close()
