"""Issue #18 regression tests: persist + safely restore window geometry.

Root cause under test: ``MainWindow`` set a hard-coded ``resize(900, 680)``
and never saved the window geometry, so every session started at the same
default size/position even though ``Settings`` already declared a
``window_geometry`` key that was never written or read.

These tests cover the fix contract:
  * save on close -> reopen restores the SAME size and position
    (offscreen Qt, real closeEvent persist path);
  * a saved geometry ENTIRELY OFF-SCREEN (far positive AND far negative)
    is clamped back on-screen -- the window never restores fully off-screen;
  * a rect in the GAP between two side-by-side monitors (inside the union
    bounding box but on no screen) is clamped too (QRegion, not bbox);
  * a smaller virtual desktop (simulated screen list) clamps off-screen
    saves and keeps valid ones;
  * closing while maximized saves the NORMAL geometry + maximized flag and
    reopening re-applies the maximized state;
  * malformed / unknown-version payloads fall back to the default geometry
    without raising.

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as
tests/test_gui_folder_persistence.py). Deterministic on pytest tmp dirs.

Offscreen-platform note (probed 2026-08-15): the offscreen platform clamps
the window height to ``minimumSizeHint()`` (~732) on the first ``show()``,
and ``normalGeometry()`` is invalid while a maximized window is hidden. The
tests therefore exercise the hidden-window path (setGeometry/close/restore),
which is exactly what the persist/restore code path depends on.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Optional

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication

from amiga_adf_library_builder.gui import main_window as mw_module
from amiga_adf_library_builder.gui.layout import PortablePaths
from amiga_adf_library_builder.gui.main_window import (
    MainWindow,
    _decode_geometry,
)
from amiga_adf_library_builder.gui.secrets import SecretStore
from amiga_adf_library_builder.gui.settings import SettingsStore

# The offscreen platform reports one 800x800 screen (probed); keep the
# on-screen assertions anchored to the ACTUAL screen so the file stays
# correct if that ever changes.
SCREEN_AREA = QRect(0, 0, 800, 800)


@pytest.fixture
def qt_app():
    """Ensure a single QApplication exists for the test (offscreen)."""
    return QApplication.instance() or QApplication([])


def _make_window(base_dir: Path) -> MainWindow:
    QApplication.instance() or QApplication([])  # noqa: F841
    pp = PortablePaths(base_dir=base_dir)
    pp.ensure_all()
    return MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=None,
    )


def _read_payload(base_dir: Path) -> dict:
    """Read the ``window_geometry`` payload dict from the settings TOML."""
    with open(PortablePaths(base_dir=base_dir).settings_file(), "rb") as fh:
        table = tomllib.load(fh).get("gui", {})
    raw = table.get("window_geometry", "")
    assert raw, "window_geometry was not persisted"
    payload = json.loads(raw)
    assert payload["v"] == 1, f"unexpected payload version: {payload!r}"
    return payload


def _write_payload(base_dir: Path, geom: str, maximized: bool = False) -> None:
    """Write a versioned geometry payload through the public store API."""
    pp = PortablePaths(base_dir=base_dir)
    pp.ensure_all()
    store = SettingsStore(pp.settings_file())
    store.load()
    store.update(
        window_geometry=json.dumps({"v": 1, "geom": geom, "max": maximized})
    )


def _intersects(rect: QRect, area: QRect) -> bool:
    return not QRect(rect).intersected(QRect(area)).isNull()


# --- save -> restore ----------------------------------------------------------
def test_close_persists_geometry_reopen_restores(qt_app, tmp_path: Path):
    """The exact user repro: move/resize the window, close, reopen.

    The reopened window must have the SAME size and position (closeEvent
    persist path; no run is started). The window is never ``show()``-n:
    the offscreen platform clamps a shown window to its minimum height,
    which is unrelated to the persistence contract under test.
    """
    base = tmp_path / "issue18-base"
    target = QRect(100, 60, 720, 540)

    mw1 = _make_window(base)
    mw1.setGeometry(target)
    mw1.close()  # closeEvent must persist the geometry

    payload = _read_payload(base)
    assert payload["geom"] == "100,60,720,540"
    assert payload["max"] is False, "normal window must persist max=false"

    # Reopen a fresh window on the SAME settings file.
    mw2 = _make_window(base)
    assert mw2.geometry() == target, (
        f"restored geometry {mw2.geometry()} != saved {target}"
    )
    assert not mw2.isMaximized()
    mw2.close()


def test_close_while_maximized_saves_normal_geometry_and_restores_maximized(
    qt_app, tmp_path: Path
):
    """Closing a MAXIMIZED window must persist the normal (un-maximized)
    geometry -- not the maximized size -- plus the maximized flag, and
    reopening re-applies the maximized state on top of the restored normal
    geometry.

    The window is driven hidden: on the offscreen platform
    ``normalGeometry()`` is invalid for a hidden maximized window (the
    persist path falls back to ``geometry()``, which holds the exact
    pre-maximize rect) and ``showMaximized()`` would both show the window
    and clamp its height to the minimum size -- both unrelated to the
    persistence contract.
    """
    base = tmp_path / "issue18-max"
    normal = QRect(100, 60, 720, 540)

    mw1 = _make_window(base)
    mw1.setGeometry(normal)
    mw1.setWindowState(mw1.windowState() | Qt.WindowState.WindowMaximized)
    assert mw1.isMaximized()
    mw1.close()

    payload = _read_payload(base)
    assert payload["geom"] == "100,60,720,540", (
        "maximized size leaked into the persisted geometry"
    )
    assert payload["max"] is True, "maximized flag must be persisted"

    mw2 = _make_window(base)
    assert mw2.geometry() == normal, "normal geometry must be restored"
    assert mw2.isMaximized(), "maximized state must be re-applied on restore"
    mw2.close()


# --- safety: never restore fully off-screen ------------------------------------
def test_restore_fully_offscreen_positive_is_clamped_on_screen(
    qt_app, tmp_path: Path
):
    """KEY SAFETY REGRESSION: a saved position far off-screen (the monitor
    it lived on is gone) must be clamped back on-screen, never restored as
    is."""
    base = tmp_path / "issue18-offpos"
    _write_payload(base, "100000,100000,640,480")

    mw = _make_window(base)
    rect = mw.geometry()
    assert rect != QRect(100000, 100000, 640, 480), "off-screen rect restored verbatim"
    assert _intersects(rect, SCREEN_AREA), (
        f"restored geometry {rect} is entirely off-screen (screen {SCREEN_AREA})"
    )
    mw.close()


def test_restore_fully_offscreen_negative_is_clamped_on_screen(
    qt_app, tmp_path: Path
):
    """Same safety contract for far-negative positions (second monitor on
    the LEFT was disconnected)."""
    base = tmp_path / "issue18-offneg"
    _write_payload(base, "-5000,-5000,640,480")

    mw = _make_window(base)
    rect = mw.geometry()
    assert rect != QRect(-5000, -5000, 640, 480), "off-screen rect restored verbatim"
    assert _intersects(rect, SCREEN_AREA), (
        f"restored geometry {rect} is entirely off-screen (screen {SCREEN_AREA})"
    )
    mw.close()


class _FakeScreen:
    """Minimal stand-in for QScreen: only availableGeometry() is used."""

    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self._area = QRect(x, y, w, h)

    def availableGeometry(self) -> QRect:
        return self._area


class _FakeGuiApplication:
    """Stands in for QGuiApplication with a simulated screen list."""

    def __init__(self, screens: list) -> None:
        self._screens = screens

    def screens(self) -> list:
        return self._screens

    def primaryScreen(self):
        return self._screens[0]


def test_restore_gap_between_screens_is_clamped(
    qt_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two side-by-side monitors with a GAP between them: a saved rect in
    the gap is inside the union's BOUNDING BOX but on no screen. The guard
    must use the region union (not the bounding box) and clamp it back to
    one of the two monitors (the fallback default centered on the primary
    screen always lands on a screen)."""
    # Monitor A: 0..500, monitor B: 600..1100, gap 500..600 (both 400 tall).
    fake = _FakeGuiApplication(
        [_FakeScreen(0, 0, 500, 400), _FakeScreen(600, 0, 500, 400)]
    )
    monkeypatch.setattr(mw_module, "QGuiApplication", fake)
    base = tmp_path / "issue18-gap"
    _write_payload(base, "505,0,10,10")  # entirely inside the gap

    mw = _make_window(base)
    rect = mw.geometry()
    on_a = _intersects(rect, QRect(0, 0, 500, 400))
    on_b = _intersects(rect, QRect(600, 0, 500, 400))
    assert on_a or on_b, f"restored geometry {rect} is on neither monitor"
    mw.close()


def test_restore_smaller_virtual_desktop_clamps(qt_app, tmp_path: Path, monkeypatch):
    """Simulated smaller virtual desktop (600x400, e.g. a laptop lid screen
    after an external monitor is unplugged): a saved geometry that fit the
    old layout is clamped back on-screen."""
    fake = _FakeGuiApplication([_FakeScreen(0, 0, 600, 400)])
    monkeypatch.setattr(mw_module, "QGuiApplication", fake)
    base = tmp_path / "issue18-small"
    _write_payload(base, "100000,100000,640,480")

    mw = _make_window(base)
    rect = mw.geometry()
    assert _intersects(rect, QRect(0, 0, 600, 400)), (
        f"restored geometry {rect} is entirely off the 600x400 desktop"
    )
    mw.close()


def test_restore_smaller_virtual_desktop_keeps_valid_position(
    qt_app, tmp_path: Path, monkeypatch
):
    """Same smaller desktop, but a saved position that STILL fits must be
    restored verbatim (the guard must not clobber valid geometry)."""
    fake = _FakeGuiApplication([_FakeScreen(0, 0, 600, 400)])
    monkeypatch.setattr(mw_module, "QGuiApplication", fake)
    base = tmp_path / "issue18-smallvalid"
    target = QRect(100, 50, 300, 250)
    _write_payload(base, "100,50,300,250")

    mw = _make_window(base)
    assert mw.geometry() == target, (
        f"valid geometry {target} was clobbered: {mw.geometry()}"
    )
    mw.close()


# --- payload tolerance ----------------------------------------------------------
_MALFORMED_PAYLOADS = (
    ("empty", ""),
    ("not-json", "garbage-not-json"),
    ("unknown-version", json.dumps({"v": 2, "geom": "10,10,20,20"})),
    ("bad-geom", json.dumps({"v": 1, "geom": "nope"})),
    ("zero-size", json.dumps({"v": 1, "geom": "10,10,0,0"})),
    ("bad-max", json.dumps({"v": 1, "geom": "10,10,20,20", "max": "yes"})),
)


@pytest.mark.parametrize("name,raw", _MALFORMED_PAYLOADS)
def test_malformed_payload_falls_back_to_default(
    qt_app, tmp_path: Path, name: str, raw: str
):
    """Any malformed, unknown-version, or invalid payload must fall back to
    an on-screen geometry instead of raising or restoring garbage. Fully
    invalid payloads land on the default; a payload with a valid rect but a
    bad ``max`` field still restores a usable, non-maximized window."""
    base = tmp_path / f"issue18-bad-{name}"
    pp = PortablePaths(base_dir=base)
    pp.ensure_all()
    store = SettingsStore(pp.settings_file())
    store.load()
    store.update(window_geometry=raw)

    mw = _make_window(base)  # must not raise
    rect = mw.geometry()
    assert _intersects(rect, SCREEN_AREA), (
        f"fallback geometry {rect} is off-screen (screen {SCREEN_AREA})"
    )
    assert rect.isValid() and rect.width() > 0 and rect.height() > 0
    mw.close()


def test_decode_geometry_round_trip(qt_app):
    """Unit check on the versioned codec itself (no window involved)."""
    rect, maximized = _decode_geometry(
        json.dumps({"v": 1, "geom": "5,6,700,500", "max": True})
    )
    assert rect == QRect(5, 6, 700, 500)
    assert maximized is True
    assert _decode_geometry("") == (None, False)
    assert _decode_geometry("{") == (None, False)
    assert _decode_geometry(json.dumps({"v": 99, "geom": "5,6,700,500"})) == (None, False)
    # Missing max -> non-maximized; still a valid rect.
    rect, maximized = _decode_geometry(json.dumps({"v": 1, "geom": "5,6,700,500"}))
    assert rect == QRect(5, 6, 700, 500)
    assert maximized is False
