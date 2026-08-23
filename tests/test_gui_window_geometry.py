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
the window height to ``minimumSizeHint()`` on the first ``show()``, and
``normalGeometry()`` is invalid while a maximized window is hidden. The
tests therefore exercise the hidden-window path (setGeometry/close/restore),
which is exactly what the persist/restore code path depends on.

GH-40 (this fix): the providers tab used to stack every provider panel
vertically, so its ``minimumSizeHint`` (~1393 px with 5 providers) pinned the
window's minimum height and the window could not be shrunk vertically. The
tab is now wrapped in a ``QScrollArea`` and the window declares an explicit
``MIN_WINDOW_SIZE``. The new ``# --- GH-40`` tests cover: the sensible
minimum, vertical resize (shrink + enlarge), a saved sub-minimum geometry
clamping to the minimum on show, and the providers tab scrolling instead of
pinning the window height.

GH-58 (regression lock-in): the issue reports the Providers page being
crushed and unscrollable in an old Windows package. Probing the current
main (BASE 6d09d6c) shows the GH-40 fix already resolves it -- the
viewport is healthy (>=187 px) at every probed size and the scrollbar
works. The ``# --- GH-58`` tests pin the issue's acceptance criteria
against regressions: the viewport is never reduced to a sliver at the
default or minimum window size, the scrollbar reaches the last provider
panel, and provider controls keep a readable height (>= their font
metrics height) when the window is at its minimum.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Optional

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from amiga_adf_library_builder.gui import main_window as mw_module
from amiga_adf_library_builder.gui.layout import PortablePaths
from amiga_adf_library_builder.gui.main_window import (
    MIN_WINDOW_SIZE,
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
    restored verbatim (the guard must not clobber valid geometry). The
    saved size is >= ``MIN_WINDOW_SIZE`` (GH-40) and the desktop is large
    enough for it, so the restore is exercised un-clamped."""
    fake = _FakeGuiApplication([_FakeScreen(0, 0, 800, 600)])
    monkeypatch.setattr(mw_module, "QGuiApplication", fake)
    base = tmp_path / "issue18-smallvalid"
    target = QRect(60, 40, 640, 480)
    _write_payload(base, "60,40,640,480")

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


# --- GH-40: vertical resizability ----------------------------------------------
def test_window_declares_sensible_minimum_size(qt_app, tmp_path: Path):
    """GH-40: the window has an explicit, sensible minimum size so the
    controls cannot collapse, and it is small enough to allow vertical
    shrinking far below the old 1739 px content-driven floor."""
    mw = _make_window(tmp_path / "gh40-min")
    minsize = mw.minimumSize()
    assert minsize == MIN_WINDOW_SIZE, (
        f"window minimumSize {minsize} != declared {MIN_WINDOW_SIZE}"
    )
    assert minsize.height() <= 500, (
        f"minimum height {minsize.height()} is too large for vertical resize"
    )
    # The minimum must never come from the content size hint: the providers
    # tab content alone used to demand ~1393 px of minimum height.
    assert mw.minimumSizeHint().height() < 1000, (
        f"content minimumSizeHint {mw.minimumSizeHint()} still pins the height"
    )
    mw.close()


def test_window_resizes_vertically(qt_app, tmp_path: Path):
    """GH-40: the reported defect -- the window could not be made shorter.
    After the fix a shown window accepts a smaller height (down to the
    declared minimum) and a larger one, and reports the new size."""
    mw = _make_window(tmp_path / "gh40-shrink")
    mw.setGeometry(QRect(20, 20, 900, 680))
    mw.show()
    qt_app.processEvents()

    # Shrink vertically well below the pre-fix 1739 px floor, to the minimum.
    mw.resize(900, 480)
    qt_app.processEvents()
    assert mw.height() == 480, (
        f"window refused vertical shrink: height={mw.height()} after "
        f"resize to 480 (minimum {mw.minimumSize()})"
    )

    # Enlarge vertically past the default.
    mw.resize(900, 760)
    qt_app.processEvents()
    assert mw.height() == 760, (
        f"window refused vertical enlarge: height={mw.height()} after "
        f"resize to 760"
    )

    # Shrink to the declared minimum and no further (the floor holds).
    mw.resize(900, 100)
    qt_app.processEvents()
    assert mw.height() == MIN_WINDOW_SIZE.height(), (
        f"window shrank below its declared minimum: {mw.height()} < "
        f"{MIN_WINDOW_SIZE.height()}"
    )
    mw.close()


def test_saved_subminimum_geometry_clamps_to_minimum_on_show(
    qt_app, tmp_path: Path
):
    """GH-40: a saved geometry smaller than MIN_WINDOW_SIZE must not
    collapse the controls on restore -- Qt clamps the shown window up to
    the declared minimum."""
    base = tmp_path / "gh40-submin"
    _write_payload(base, "10,10,200,150")  # far below the 640x480 minimum

    mw = _make_window(base)
    mw.show()
    qt_app.processEvents()
    assert mw.width() >= MIN_WINDOW_SIZE.width(), (
        f"restored width {mw.width()} below minimum {MIN_WINDOW_SIZE.width()}"
    )
    assert mw.height() >= MIN_WINDOW_SIZE.height(), (
        f"restored height {mw.height()} below minimum {MIN_WINDOW_SIZE.height()}"
    )
    mw.close()


def test_providers_tab_scrolls_instead_of_pinning_window(
    qt_app, tmp_path: Path
):
    """GH-40 root cause: the providers tab's content used to demand ~1393 px
    of minimum height, pinning the whole window. The tab is now a scroll
    viewport: its own minimumSizeHint stays tiny and the tall content
    scrolls inside the tab. (Regression guard: re-stacking the panels
    without the scroll area fails this test.)"""
    from PySide6.QtWidgets import QTabWidget

    mw = _make_window(tmp_path / "gh40-scroll")
    tabs = mw.findChild(QTabWidget)
    assert tabs is not None, "main window lost its tab widget"
    # Locate the Providers tab by text (tab order is stable: Library,
    # Options, Providers, ...).
    idx = None
    for i in range(tabs.count()):
        if tabs.tabText(i) == "Providers":
            idx = i
            break
    assert idx is not None, "Providers tab missing"
    provider_tab = tabs.widget(idx)

    # The tab's own minimum hint must be small (scroll viewport), not the
    # full stacked height of all provider panels.
    tab_hint = provider_tab.minimumSizeHint()
    assert tab_hint.height() < 400, (
        f"providers tab minimumSizeHint {tab_hint} still pins the window "
        "height (expected a small scroll viewport)"
    )
    # The window minimum must not be driven by that content either.
    assert mw.minimumSize().height() == MIN_WINDOW_SIZE.height()
    mw.close()


def _providers_scroll_area(providers_tab: QWidget) -> QScrollArea:
    """Return the QScrollArea that wraps the Providers tab content (GH-40).

    Fails loudly if the tab ever loses the scroll wrapper: that is exactly
    the regression GH-58's acceptance criteria guard against.
    """
    scroll = providers_tab.findChild(QScrollArea)
    assert scroll is not None, (
        "Providers tab no longer contains a QScrollArea -- the GH-40 "
        "scroll wrapper is gone and the providers stack will pin the "
        "window height again (GH-58 regression)"
    )
    assert scroll.widgetResizable(), (
        "Providers QScrollArea is not resizable; tall content will not "
        "be reachable by scrolling"
    )
    return scroll


def test_providers_viewport_keeps_real_height_at_min_window(
    qt_app, tmp_path: Path
):
    """GH-58: with the window at its minimum size and the Providers tab
    active, the scroll viewport must keep a real, usable height -- the
    reported defect was controls squashed into a compressed sliver."""
    from PySide6.QtWidgets import QTabWidget

    mw = _make_window(tmp_path / "gh58-viewport")
    mw.show()
    mw.resize(MIN_WINDOW_SIZE.width(), MIN_WINDOW_SIZE.height())
    qt_app.processEvents()

    tabs = mw.findChild(QTabWidget)
    assert tabs is not None
    idx = next(
        (i for i in range(tabs.count()) if tabs.tabText(i) == "Providers"),
        None,
    )
    assert idx is not None, "Providers tab missing"
    tabs.setCurrentIndex(idx)
    qt_app.processEvents()

    viewport = _providers_scroll_area(tabs.widget(idx)).viewport()
    assert viewport.height() >= 100, (
        "Providers controls collapsed to a "
        f"{viewport.height()} px viewport at the minimum window size "
        f"({MIN_WINDOW_SIZE.width()}x{MIN_WINDOW_SIZE.height()})"
    )
    mw.close()


def test_providers_scrollbar_reaches_overflowing_content(
    qt_app, tmp_path: Path
):
    """GH-58: the reported 'no working scrollbar' -- when the provider
    panels are taller than the viewport, the vertical scrollbar must be
    enabled and actually have something to scroll (maximum > 0)."""
    from PySide6.QtWidgets import QTabWidget

    mw = _make_window(tmp_path / "gh58-scrollbar")
    mw.show()
    mw.resize(MIN_WINDOW_SIZE.width(), MIN_WINDOW_SIZE.height())
    qt_app.processEvents()

    tabs = mw.findChild(QTabWidget)
    assert tabs is not None
    idx = next(
        (i for i in range(tabs.count()) if tabs.tabText(i) == "Providers"),
        None,
    )
    assert idx is not None, "Providers tab missing"
    tabs.setCurrentIndex(idx)
    qt_app.processEvents()

    scroll_area = _providers_scroll_area(tabs.widget(idx))
    bar = scroll_area.verticalScrollBar()
    if scroll_area.widget().sizeHint().height() > scroll_area.viewport().height():
        assert bar.maximum() > 0, (
            "content overflows the viewport but the scrollbar reports "
            "maximum=0 (nothing to scroll)"
        )
        assert bar.isEnabled(), "vertical scrollbar is disabled"
    else:
        # Content fits at this window size -- the scroll machinery must at
        # least not be hard-disabled.
        assert scroll_area.verticalScrollBarPolicy() != (
            Qt.ScrollBarAlwaysOff
        )
    mw.close()


def test_providers_controls_keep_readable_height_at_min_window(
    qt_app, tmp_path: Path
):
    """GH-58: at the minimum window size no provider control may be
    crushed below its natural (font metrics) height -- 'vertically
    compressed' is the exact symptom reported in the issue."""
    from PySide6.QtWidgets import QTabWidget

    mw = _make_window(tmp_path / "gh58-readable")
    mw.show()
    mw.resize(MIN_WINDOW_SIZE.width(), MIN_WINDOW_SIZE.height())
    qt_app.processEvents()

    tabs = mw.findChild(QTabWidget)
    assert tabs is not None
    idx = next(
        (i for i in range(tabs.count()) if tabs.tabText(i) == "Providers"),
        None,
    )
    assert idx is not None, "Providers tab missing"
    tabs.setCurrentIndex(idx)
    qt_app.processEvents()

    font_metrics = mw.fontMetrics()
    squashed = []
    for widget in tabs.widget(idx).findChildren(QWidget):
        if not widget.isVisible():
            continue
        natural = widget.sizeHint().height()
        if natural <= 0:
            # -1 / 0 means "no meaningful hint" (bare layout containers);
            # not a control.
            continue
        if widget.findChildren(QWidget):
            # Containers legitimately shrink to fit their children --
            # only leaf controls are the "vertically compressed" symptom.
            continue
        if widget.height() < natural - 2:
            squashed.append(
                f"{widget.metaObject().className()} "
                f"({widget.height()} px, natural {natural} px)"
            )
    assert not squashed, (
        "provider controls squashed below their natural height at the "
        f"minimum window size; font height {font_metrics.height()} px: "
        + "; ".join(squashed[:5])
    )
    mw.close()
