"""Export is one explicit action; legacy settings remain loadable."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from amiga_adf_library_builder.gui import MainWindow
from amiga_adf_library_builder.gui.layout import PortablePaths
from amiga_adf_library_builder.gui.secrets import SecretStore
from amiga_adf_library_builder.gui.settings import SettingsStore

PRIMARY_EXPORT_LABEL = "Export the library (writes the final files)"
ACK_LABEL = "I understand this run will write files"
LEGACY_GATE_LABEL = "Allow export"

WILL_EXPORT = "Files WILL be exported"
NOT_EXPORT = "will NOT"


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "gh43-base"


def _make_window(base_dir: Path) -> MainWindow:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    pp = PortablePaths(base_dir=base_dir)
    pp.ensure_all()
    return MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=None,
    )


def _state_text(mw: MainWindow) -> str:
    return mw._export_state_label.text()



def test_export_and_build_buttons_choose_run_mode(qt_offscreen, monkeypatch):
    mw = _make_window(qt_offscreen)
    calls = []
    monkeypatch.setattr(mw, "_on_run", lambda: calls.append(mw._state_from_widgets()))
    mw._export_button.click()
    assert calls[-1].run_mode == "export"
    assert calls[-1].export_gate_acknowledged
    mw._run_button.click()
    assert calls[-1].run_mode == "build"
    assert not calls[-1].export_gate_acknowledged
    assert mw._cb_gate.isHidden()
    assert mw._mode_export.isHidden()
    mw.close()


def test_check_only_removed_from_export_action(qt_offscreen, monkeypatch):
    mw = _make_window(qt_offscreen)
    calls = []
    monkeypatch.setattr(mw, "_on_run", lambda: calls.append(mw._state_from_widgets()))
    mw._cb_verify.setChecked(True)
    mw._export_button.click()
    assert not calls[-1].verify_only
    assert mw._cb_verify.isHidden()
    mw.close()


def test_no_provider_enabled_on_fresh_install(qt_offscreen):
    mw = _make_window(qt_offscreen)
    assert all(not provider.enabled() for provider in mw._registry.all())
    assert mw._lb_media_table.isColumnHidden(1)
    mw.close()


def test_build_export_controls_live_in_tab(qt_offscreen):
    from PySide6.QtWidgets import QTabWidget
    mw = _make_window(qt_offscreen)
    tabs = mw.findChild(QTabWidget)
    index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "Build & Export")
    panel = tabs.widget(index)
    assert panel.isAncestorOf(mw._export_button)
    assert panel.isAncestorOf(mw._run_button)
    assert mw._cb_verify.isHidden()
    mw.close()
