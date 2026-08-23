"""GH-43 regression tests: duplicate export controls.

GH-43 ([P1][Bug]): the Run / Export Settings area presented BOTH an
'Export the library' control and a separate 'Allow export' gate control, so a
user could not tell whether one or both must be enabled for a real export.

The fix locks in:

  * exactly ONE obvious primary choice decides whether the run exports files
    ('Export the library (writes the final files)');
  * the second control is the unmistakable safety acknowledgement
    'I understand this run will write files' — the old ambiguous
    'Allow export' label must be gone from the UI;
  * contradictory combinations are prevented: the acknowledgement is disabled
    (and cannot hold a checked value) while 'Export the library' is off, and
    it is enabled while export mode is selected;
  * the pre-Run state label explains exactly why export will or will not
    occur for every supported combination: build-only / export pending
    acknowledgement / check-only / export will run;
  * the internal settings key (``export_gate_acknowledged``) is unchanged and
    the persisted value round-trips (persistence compatibility).

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as
tests/test_gui_wording_plain_language.py).
"""

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


# --- 1. single primary choice + unmistakable acknowledgement -----------------

def test_single_primary_choice_and_ack_wording(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    checks = {cb.text() for cb in mw._mode_build.window().findChildren(type(mw._mode_export))}
    assert PRIMARY_EXPORT_LABEL in checks
    assert ACK_LABEL in checks
    # The ambiguous duplicate gate label must be gone from user-facing text.
    assert LEGACY_GATE_LABEL not in checks
    assert LEGACY_GATE_LABEL not in mw._mode_export.toolTip()
    assert LEGACY_GATE_LABEL not in mw._cb_gate.toolTip()
    assert LEGACY_GATE_LABEL not in _state_text(mw)
    # 'Check only' remains a distinct mode, not a second export switch.
    assert "Check only — don't change files" in checks
    mw.close()


# --- 2. contradictory combinations are prevented ------------------------------

def test_default_state_is_build_only_with_ack_disabled(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    assert mw._mode_build.isChecked() is True
    assert mw._mode_export.isChecked() is False
    # The acknowledgement must not be usable in build-only mode.
    assert mw._cb_gate.isEnabled() is False
    assert mw._cb_gate.isChecked() is False
    mw.close()


def test_ack_follows_export_mode_toggling(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    mw._mode_export.setChecked(True)
    assert mw._cb_gate.isEnabled() is True
    # Confirm, then switch back to build-only: the check must not linger.
    mw._cb_gate.setChecked(True)
    assert mw._cb_gate.isChecked() is True
    mw._mode_export.setChecked(False)
    assert mw._cb_gate.isEnabled() is False
    assert mw._cb_gate.isChecked() is False
    # And re-entering export mode starts unconfirmed (no stale value).
    mw._mode_export.setChecked(True)
    assert mw._cb_gate.isChecked() is False
    mw.close()


# --- 3. pre-run explanation for every supported combination -------------------

def test_state_explains_build_only(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    text = _state_text(mw)
    assert NOT_EXPORT in text
    assert "Build-only run" in text
    mw.close()


def test_state_explains_export_pending_acknowledgement(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    mw._mode_export.setChecked(True)
    text = _state_text(mw)
    assert NOT_EXPORT in text
    # Must point at the exact acknowledgement by name.
    assert ACK_LABEL in text
    mw.close()


def test_state_explains_check_only(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    mw._mode_export.setChecked(True)
    mw._cb_verify.setChecked(True)
    text = _state_text(mw)
    assert NOT_EXPORT in text
    assert "Check-only run" in text
    # Check-only takes precedence: no demand to confirm writing.
    assert ACK_LABEL not in text
    mw.close()


def test_state_explains_export_will_run(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    mw._mode_export.setChecked(True)
    mw._cb_gate.setChecked(True)
    text = _state_text(mw)
    assert WILL_EXPORT in text
    assert NOT_EXPORT not in text
    mw.close()


def test_state_notes_default_output_when_unset(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    mw._mode_export.setChecked(True)
    mw._cb_gate.setChecked(True)
    assert "default" in _state_text(mw).lower()
    mw.close()


# --- 4. state mapping and persistence are unchanged ---------------------------

def test_state_from_widgets_maps_ack_to_gate_key(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    state = mw._state_from_widgets()
    assert state.run_mode == "build"
    assert state.export_gate_acknowledged is False
    mw._mode_export.setChecked(True)
    mw._cb_gate.setChecked(True)
    state = mw._state_from_widgets()
    assert state.run_mode == "export"
    assert state.export_gate_acknowledged is True
    assert state.verify_only is False
    mw.close()


def test_persisted_ack_round_trips(qt_offscreen: Path):
    base = qt_offscreen
    mw = _make_window(base)
    mw._mode_export.setChecked(True)
    mw._cb_gate.setChecked(True)
    assert mw._persist_defaults() is True
    mw.close()

    mw2 = _make_window(base)
    # Startup restore: default mode is build-only, so the ack is not applied
    # as a checked box (contradictory state prevented) …
    assert mw2._cb_gate.isChecked() is False
    assert mw2._cb_gate.isEnabled() is False
    # … but the persisted value is intact, and the box is usable once export
    # mode is selected. It starts UNCHECKED: a run that writes files always
    # gets a fresh, explicit acknowledgement (no stale value carried over).
    assert mw2._settings.export_gate_acknowledged is True
    mw2._mode_export.setChecked(True)
    assert mw2._cb_gate.isEnabled() is True
    assert mw2._cb_gate.isChecked() is False
    assert NOT_EXPORT in _state_text(mw2)
    mw2._cb_gate.setChecked(True)
    assert WILL_EXPORT in _state_text(mw2)
    mw2.close()
