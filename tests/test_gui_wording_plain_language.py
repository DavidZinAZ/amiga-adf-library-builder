"""Issue #22 regression tests: plain-language GUI wording.

The Options/Run/Export/safety terminology was rewritten in plain language
for normal users. These tests lock that in:

  * the key controls show the NEW plain-language labels;
  * none of the forbidden internal terms (``gate`` / ``preflight`` /
    ``staging``) appear in the user-facing text or tooltips of the
    Library/Options/Run areas of the main window;
  * buttons that open another screen say so (trailing ``…``);
  * the internal settings keys are UNCHANGED and a settings file written
    with the old key names still round-trips (persistence compatibility).

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as
tests/test_gui_folder_persistence.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QLabel,
    QPushButton,
)

from amiga_adf_library_builder.gui import MainWindow
from amiga_adf_library_builder.gui.layout import PortablePaths
from amiga_adf_library_builder.gui.secrets import SecretStore
from amiga_adf_library_builder.gui.settings import SETTINGS_KEYS, Settings, SettingsStore

#: Terms that must not appear in user-facing control text/tooltips of the
#: Library, Options and Run areas (issue #22).
FORBIDDEN_TERMS = ("gate", "preflight", "staging")

#: New plain-language labels that must be present (control text).
EXPECTED_LABELS = (
    "Build the library (scan, organize, prepare)",
    "Export the library (writes the final files)",
    "Use online metadata sources",
    "Refresh metadata even if cached",
    "Require artwork before export",
    "Check only — don't change files",
    "Allow export",
    "Remember these settings",
    "Library root",
    "Original disks (read-only)",
    "Export work folder",
    "Export destination",
)

#: Buttons that open a dialog/folder picker must end with an ellipsis.
EXPECTED_ELLIPSIS_BUTTONS = (
    "Choose…",
    "Open log files…",
    "Check connection…",
    "Set credentials…",
)

#: Expected success message for connection check (GH-42).
EXPECTED_CONNECTION_SUCCESS = "Connection successful"


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "issue22-base"


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


def _iter_display_strings(widget, seen=None):
    """Yield every user-visible string (text/tooltip/placeholder/title) under ``widget``."""
    seen = seen if seen is not None else set()
    if id(widget) in seen:
        return
    seen.add(id(widget))
    for getter in ("text", "title", "toolTip", "placeholderText"):
        value = getattr(widget, getter, None)
        if callable(value):
            value = value()
        if isinstance(value, str) and value:
            yield value
    title = getattr(widget, "windowTitle", None)
    if callable(title):
        value = title()
        if isinstance(value, str) and value:
            yield value
    for child in widget.findChildren(widget.__class__ if False else object):  # noqa
        yield from _iter_display_strings(child, seen)


def _area_widgets(mw: MainWindow):
    """The widgets of the Library tab, Options tab, and Run box."""
    from PySide6.QtWidgets import QTabWidget

    central = mw.centralWidget()
    tabs = central.findChildren(QTabWidget)
    widgets = []
    for tab in tabs:
        widgets.append(tab.widget(0))  # Library
        widgets.append(tab.widget(1))  # Options
    widgets.extend(central.findChildren(QGroupBox))
    return widgets


def test_plain_language_labels_present(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    texts = set()
    central = mw.centralWidget()
    for cls in (QCheckBox, QLabel, QPushButton):
        texts.update(w.text() for w in central.findChildren(cls))
    texts.update(w.title() for w in central.findChildren(QGroupBox))
    missing = [label for label in EXPECTED_LABELS if label not in texts]
    assert not missing, f"missing plain-language labels: {missing}"
    mw.close()


def test_forbidden_terms_absent_in_library_options_run(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    offenders = []
    for area in _area_widgets(mw):
        for value in _iter_display_strings(area):
            lowered = value.lower()
            for term in FORBIDDEN_TERMS:
                if term in lowered:
                    offenders.append(f"{term!r} in {value!r}")
    assert not offenders, f"forbidden internal terms in user-facing text: {offenders}"
    mw.close()


def test_dialog_buttons_indicate_opening_dialog(qt_offscreen: Path):
    mw = _make_window(qt_offscreen)
    buttons = {
        b.text()
        for b in mw.centralWidget().findChildren(QPushButton)
    }
    for expected in EXPECTED_ELLIPSIS_BUTTONS:
        assert expected in buttons, f"expected button {expected!r} (with trailing ellipsis)"
    # No legacy variants may linger.
    for legacy in ("Browse…", "Test connection", "Open log directory"):
        assert legacy not in buttons, f"legacy button text {legacy!r} still present"
    mw.close()


def test_settings_keys_unchanged():
    """The internal settings keys are the persistence contract — unchanged."""
    assert SETTINGS_KEYS == (
        "theme",
        "default_library_root",
        "default_original_dir",
        "default_staging_dir",
        "default_output_dir",
        "online",
        "refresh_metadata",
        "require_artwork",
        "verify_only",
        "export_gate_acknowledged",
        "advanced_mode",
        "window_geometry",
        # Issue #21: live Diagnostics log toggle (added after GH-22).
        "show_live_log",
        # GH-24: independent artwork / manuals-RTFM selection (both default ON).
        "include_artwork",
        "include_manuals_rtfm",
        # GH-33: LaunchBox local folder mappings (local paths, non-sensitive).
        "launchbox_media_roots",
        "launchbox_manual_roots",
    )
    # The dataclass still exposes the same attribute names (used by the
    # GUI and the real-Windows QA driver).
    for key in SETTINGS_KEYS:
        assert hasattr(Settings(), key), f"Settings lost attribute {key!r}"


def test_old_settings_file_still_round_trips(tmp_path: Path):
    """A settings file written with the OLD key names must still load."""
    path = tmp_path / "legacy-settings.toml"
    store = SettingsStore(path)
    store.load()
    store.update(
        default_library_root="/data/lib",
        default_original_dir="/data/lib/original",
        default_staging_dir="/data/lib/work/staging",
        default_output_dir="/data/lib/output",
        online=True,
        refresh_metadata=True,
        require_artwork=True,
        verify_only=False,
        export_gate_acknowledged=True,
        advanced_mode=False,
    )
    store.save()
    reloaded = SettingsStore(path).load()
    assert reloaded.default_staging_dir == "/data/lib/work/staging"
    assert reloaded.export_gate_acknowledged is True
    assert reloaded.default_library_root == "/data/lib"
    # And the serialized file uses the unchanged key names.
    content = path.read_text()
    for key in ("default_staging_dir", "export_gate_acknowledged", "advanced_mode"):
        assert f'{key} = ' in content or f'{key}=' in content, (
            f"key {key!r} missing from serialized settings"
        )


def test_connection_success_wording(qt_offscreen: Path):
    """GH-42: Check Connection success shows explicit 'Connection successful' message."""
    mw = _make_window(qt_offscreen)
    # Access the providers registry and test connection on a configured provider
    registry = mw._registry
    
    # Test Playmatch provider
    playmatch = registry.get("playmatch")
    assert playmatch is not None
    playmatch.set_field("base_url", "https://test.example.com")
    playmatch.set_enabled(True)
    status = playmatch.test_connection()
    assert status.ok is True
    assert status.message == EXPECTED_CONNECTION_SUCCESS, f"Expected '{EXPECTED_CONNECTION_SUCCESS}', got '{status.message}'"
    
    # Test Hasheous provider
    hasheous = registry.get("hasheous")
    assert hasheous is not None
    hasheous.set_field("base_url", "https://test.example.com")
    hasheous.set_enabled(True)
    status = hasheous.test_connection()
    assert status.ok is True
    assert status.message == EXPECTED_CONNECTION_SUCCESS, f"Expected '{EXPECTED_CONNECTION_SUCCESS}', got '{status.message}'"
    
    # Test IGDB provider
    igdb = registry.get("igdb")
    assert igdb is not None
    igdb.set_field("base_url", "https://test.example.com")
    igdb.set_enabled(True)
    status = igdb.test_connection()
    assert status.ok is True
    assert status.message == EXPECTED_CONNECTION_SUCCESS, f"Expected '{EXPECTED_CONNECTION_SUCCESS}', got '{status.message}'"
    
    mw.close()
