"""GUI package import + MainWindow construction tests (Issue #15).

Acceptance: the ``gui`` package imports on Linux under PySide6 offscreen
(``QT_QPA_PLATFORM=offscreen``) and constructs ``MainWindow`` without error.
Also covers ThemeManager and the portable layout module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amiga_adf_library_builder.gui.layout import PortablePaths
from amiga_adf_library_builder.gui.secrets import SecretStore
from amiga_adf_library_builder.gui.settings import SettingsStore
from amiga_adf_library_builder.gui.themes import (
    apply_theme,
    available_themes,
    load_theme,
)


def test_gui_package_imports():
    # Importing the package must not fail on Linux (offscreen platform).
    import amiga_adf_library_builder.gui as gui  # noqa: F401

    assert hasattr(gui, "MainWindow")
    assert hasattr(gui, "build_path_config_from_gui_state")
    assert hasattr(gui, "default_registry")


def test_main_window_constructs_offscreen(qt_offscreen):
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui import MainWindow, PortablePaths, SecretStore, SettingsStore

    app = QApplication.instance() or QApplication([])
    pp = PortablePaths(base_dir=Path(qt_offscreen))
    pp.ensure_all()
    mw = MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=None,
    )
    mw.show()
    assert mw.windowTitle() == "Amiga ADF Library Builder"
    mw.close()


def test_portable_paths_layout(tmp_path: Path):
    pp = PortablePaths(base_dir=tmp_path / "app")
    created = pp.ensure_all()
    assert pp.config_dir.is_dir()
    assert pp.data_dir.is_dir()
    assert pp.logs_dir.is_dir()
    assert pp.cache_dir.is_dir()
    assert pp.themes_dir.is_dir()
    assert len(created) == 5
    # App-relative: nothing under a user/home path.
    assert "home" not in str(pp.config_dir).split("/")


def test_themes_load_and_apply(qt_offscreen):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert "light" in available_themes()
    assert "dark" in available_themes()
    assert "system" in available_themes()
    qss = load_theme("dark")
    assert "background-color" in qss
    # Unknown theme falls back to default (system/light) without error.
    assert load_theme("not-a-theme").strip() != ""
    # Applying must not raise.
    apply_theme("dark")


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "gui-offscreen-base"
