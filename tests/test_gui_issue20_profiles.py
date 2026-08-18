"""Named GUI configuration profiles (GH-20).

Acceptance (GH-20):
  1. A named profile can be saved from the current widget state.
  2. A saved profile can be loaded back into the widgets.
  3. Folder paths and non-secret GUI settings round-trip correctly.
  4. Invalid/missing paths on Load are reported cleanly (no crash; warning
     surfaced; broken paths are kept, never applied destructively).
  5. Profile files contain no secret material (assertion pattern reused from
     test_gui_settings.py against the secret key names used by gui/secrets.py).
  6. The automatic last-used settings persistence (SettingsStore.update, the
     path behind _persist_defaults) coexists with named profiles.

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as
test_gui_import.py / test_gui_window_geometry.py).
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from amiga_adf_library_builder.gui.layout import PortablePaths  # noqa: E402
from amiga_adf_library_builder.gui.secrets import SecretStore  # noqa: E402
from amiga_adf_library_builder.gui.settings import (  # noqa: E402
    Preset,
    SettingsStore,
)

# Secret key names used by gui/secrets.py (vault keys + redaction fragments).
# A serialized profile file must contain NONE of these.
SECRET_KEY_NAMES = (
    "token",
    "api_key",
    "apikey",
    "access_token",
    "secret",
    "password",
    "bearer",
    "authorization",
)


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "gui-offscreen-base"


@pytest.fixture
def main_window(qt_offscreen: Path):
    """Construct a MainWindow backed by isolated portable paths (offscreen)."""
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui import MainWindow

    app = QApplication.instance() or QApplication([])  # noqa: F841
    pp = PortablePaths(base_dir=Path(qt_offscreen))
    pp.ensure_all()
    mw = MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=None,
    )
    yield mw, pp
    mw.close()


def _set_folder_fields(mw, lib, orig, staging, output) -> None:
    mw._le_library_root.setText(lib)
    mw._le_original_dir.setText(orig)
    mw._le_staging_dir.setText(staging)
    mw._le_output_dir.setText(output)


def _make_preset(name: str, lib: str) -> Preset:
    return Preset(
        name=name,
        library_root=lib,
        original_dir=lib + "/original",
        staging_dir=lib + "/work/staging",
        output_dir=lib + "/output",
        online=True,
        refresh_metadata=False,
        require_artwork=True,
        verify_only=False,
        export_gate_acknowledged=False,
        advanced_mode=True,
        include_artwork=False,
        include_manuals_rtfm=True,
        launchbox_media_roots=[{"path": lib + "/media/Box - Front", "asset_type": "Box - Front"}],
        launchbox_manual_roots=[lib + "/manuals"],
    )


def test_save_named_profile_round_trips_to_fresh_store(main_window, tmp_path: Path):
    """AC1: saving a named profile stores it and it survives a reload."""
    mw, pp = main_window
    lib = str(tmp_path / "lib")
    (tmp_path / "lib" / "original").mkdir(parents=True)
    _set_folder_fields(mw, lib, lib + "/original", lib + "/work", lib + "/output")
    mw._cb_online.setChecked(True)
    mw._cb_artwork.setChecked(True)
    mw._cb_include_artwork.setChecked(False)
    mw._cb_advanced.setChecked(True)

    preset = mw._preset_from_widgets()
    preset.name = "Work"
    mw._settings_store.save_preset(preset)

    assert "Work" in mw._settings_store.get().presets

    # Fresh store from the same file on disk: the profile round-trips.
    store2 = SettingsStore(pp.settings_file())
    s2 = store2.load()
    assert "Work" in s2.presets
    p = s2.presets["Work"]
    assert p.library_root == lib
    assert p.original_dir == lib + "/original"
    assert p.staging_dir == lib + "/work"
    assert p.output_dir == lib + "/output"
    assert p.online is True
    assert p.require_artwork is True
    assert p.include_artwork is False
    assert p.advanced_mode is True


def test_load_named_profile_applies_paths_and_toggles(main_window, tmp_path: Path):
    """AC2 + AC3: loading a preset applies paths and non-secret toggles."""
    mw, pp = main_window
    lib = str(tmp_path / "lib2")
    for sub in ("original", "work/staging", "output", "media/Box - Front", "manuals"):
        (tmp_path / "lib2" / sub).mkdir(parents=True, exist_ok=True)
    mw._settings_store.save_preset(_make_preset("Home", lib))

    # Disturb the widgets first, so a successful load must visibly change them.
    _set_folder_fields(mw, "/somewhere/else", "", "", "")
    mw._cb_online.setChecked(False)
    mw._cb_artwork.setChecked(False)
    mw._cb_include_artwork.setChecked(True)

    assert mw._load_profile("Home") is True

    assert mw._le_library_root.text() == lib
    assert mw._le_original_dir.text() == lib + "/original"
    assert mw._le_staging_dir.text() == lib + "/work/staging"
    assert mw._le_output_dir.text() == lib + "/output"
    assert mw._cb_online.isChecked() is True
    assert mw._cb_artwork.isChecked() is True
    assert mw._cb_include_artwork.isChecked() is False
    assert mw._cb_advanced.isChecked() is True
    # GH-33 LaunchBox mappings land in the mapping widgets.
    assert mw._lb_media_mappings() == [
        {"path": lib + "/media/Box - Front", "asset_type": "Box - Front"}
    ]
    assert mw._lb_manual_mappings() == [lib + "/manuals"]


def test_load_named_profile_round_trip_values_match(main_window, tmp_path: Path):
    """AC3: save -> reload from disk -> load -> values match the preset."""
    mw, pp = main_window
    lib = str(tmp_path / "lib3")
    for sub in ("original", "work/staging", "output", "media/Box - Front", "manuals"):
        (tmp_path / "lib3" / sub).mkdir(parents=True, exist_ok=True)
    preset = _make_preset("Round", lib)
    mw._settings_store.save_preset(preset)

    # Simulate a restart: fresh store from disk, fresh window.
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui import MainWindow

    app = QApplication.instance()  # noqa: F841
    pp2 = PortablePaths(base_dir=tmp_path / "app2")
    pp2.ensure_all()
    import shutil

    shutil.copyfile(pp.settings_file(), pp2.settings_file())
    mw2 = MainWindow(
        portable_paths=pp2,
        settings_store=SettingsStore(pp2.settings_file()),
        secret_store=SecretStore.with_vault(pp2.vault_file()),
        config_path=None,
    )
    assert mw2._load_profile("Round") is True

    reloaded = mw2._settings_store.get().presets["Round"]
    assert reloaded.as_dict() == preset.as_dict()
    assert mw2._le_library_root.text() == preset.library_root
    assert mw2._cb_online.isChecked() is preset.online
    mw2.close()


def test_load_missing_paths_reported_cleanly_not_destructive(
    main_window, tmp_path: Path, monkeypatch
):
    """AC4: missing folders on Load are warned, kept in fields, no crash."""
    from amiga_adf_library_builder.gui import main_window as mw_module

    mw, _pp = main_window
    ghost = str(tmp_path / "does-not-exist-anywhere")
    preset = Preset(
        name="Ghost",
        library_root=ghost,
        original_dir=ghost + "/original",
        staging_dir=ghost + "/staging",
        output_dir=ghost + "/output",
        online=True,
    )
    mw._settings_store.save_preset(preset)

    # The apply/validation path: _load_profile must not raise, must apply the
    # (missing) paths non-destructively, and must surface them as a warning.
    # The static QMessageBox.warning() enters a modal event loop that cannot
    # be dismissed offscreen, so stub it and assert the warning IS surfaced.
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mw_module.QMessageBox,
        "warning",
        lambda parent, title, text: warnings.append((title, text)),
    )
    assert mw._load_profile("Ghost") is True
    assert mw._le_library_root.text() == ghost
    assert mw._le_output_dir.text() == ghost + "/output"
    # Warning surfaced (modal box + status label carry the same report).
    assert warnings, "expected a QMessageBox.warning for the missing paths"
    assert warnings[0][0] == "Load Profile"
    assert ghost in warnings[0][1]
    assert "not found on this machine" in warnings[0][1]
    assert "not found on this machine" in mw._status_label.text()
    assert ghost in mw._status_label.text()
    # Toggles still applied alongside the missing-path report.
    assert mw._cb_online.isChecked() is True


def test_load_unknown_profile_leaves_widgets_untouched(
    main_window, monkeypatch
):
    """Load of an unknown name is a clean failure; widgets unchanged."""
    from amiga_adf_library_builder.gui import main_window as mw_module

    mw, _pp = main_window
    _set_folder_fields(mw, "/keep/me", "", "", "")
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mw_module.QMessageBox,
        "critical",
        lambda parent, title, text: errors.append((title, text)),
    )
    assert mw._load_profile("no-such-profile") is False
    assert mw._le_library_root.text() == "/keep/me"
    assert errors and "no-such-profile" in errors[0][1]


def test_saved_profile_file_contains_no_secret(main_window, tmp_path: Path):
    """AC5: serialized profile carries zero secret material."""
    mw, pp = main_window
    lib = str(tmp_path / "library4")
    mw._settings_store.save_preset(_make_preset("Clean", lib))
    # Also exercise the Save Profile As collection path end to end.
    _set_folder_fields(mw, lib, lib + "/original", lib + "/work", lib + "/out")
    preset = mw._preset_from_widgets()
    preset.name = "Clean2"
    mw._settings_store.save_preset(preset)

    text = pp.settings_file().read_text(encoding="utf-8")
    lowered = text.lower()
    for forbidden in SECRET_KEY_NAMES:
        assert forbidden not in lowered, f"secret key leaked into profile: {forbidden}"
    # The profile itself is present and readable back.
    store2 = SettingsStore(pp.settings_file())
    s2 = store2.load()
    assert "Clean" in s2.presets
    assert "Clean2" in s2.presets


def test_last_used_settings_coexist_with_presets(main_window, tmp_path: Path):
    """AC6: SettingsStore.update (last-used) keeps working alongside presets."""
    mw, pp = main_window
    lib = str(tmp_path / "lib5")
    # All preset paths exist, so the load path takes the warning-free branch.
    for sub in ("original", "work/staging", "output", "media/Box - Front", "manuals"):
        (tmp_path / "lib5" / sub).mkdir(parents=True, exist_ok=True)

    # Automatic last-used persistence still runs and writes the [gui] table.
    _set_folder_fields(mw, lib, lib + "/original", lib + "/work", lib + "/output")
    mw._cb_online.setChecked(True)
    assert mw._persist_defaults() is True

    # A preset saved after the persist is independent of it...
    mw._settings_store.save_preset(_make_preset("Lone", lib))
    store1 = SettingsStore(pp.settings_file())
    s1 = store1.load()
    assert s1.default_library_root == lib
    assert s1.online is True
    assert "Lone" in s1.presets

    # ...and a last-used update after a profile load reflects the loaded
    # profile (the coexistence direction the ticket calls out).
    assert mw._load_profile("Lone") is True
    store2 = SettingsStore(pp.settings_file())
    s2 = store2.load()
    assert s2.default_library_root == lib
    assert s2.online is True  # _make_preset sets online=True
    assert s2.require_artwork is True
    assert s2.include_artwork is False
    assert "Lone" in s2.presets
    # The last-used table and the preset table coexist in one file.
    text = pp.settings_file().read_text(encoding="utf-8")
    assert "[gui]" in text
    assert "[gui.presets.Lone]" in text


def test_save_profile_menu_actions_exist(main_window):
    """Menu wiring: the File menu carries Save/Save As/Load profile actions."""
    mw, _pp = main_window
    menubar = mw.menuBar()
    texts = [a.text() for a in menubar.actions()]
    assert any("File" in t.replace("&", "") for t in texts)
    # Locate the File menu object by its title.
    file_menu_obj = None
    for action in menubar.actions():
        menu = action.menu()
        if menu is not None and menu.title().replace("&", "") == "File":
            file_menu_obj = menu
            break
    assert file_menu_obj is not None
    labels = [a.text() for a in file_menu_obj.actions()]
    assert "Save Profile" in labels
    assert any("Save Profile As" in t for t in labels)
    assert any("Load Profile" in t for t in labels)


def test_preset_from_widgets_never_reads_secret_store(main_window):
    """Security: the collect path builds a Preset without SecretStore access."""
    mw, _pp = main_window
    lib = str(Path("/tmp") / "profile-test-lib")
    _set_folder_fields(mw, lib, "", "", "")
    # Spy on the secret store: any read during collection is a failure.
    original = mw._secret_store
    calls = []

    class _SpySecretStore:
        def __getattr__(self, item):
            calls.append(item)
            return getattr(original, item)

    mw._secret_store = _SpySecretStore()
    try:
        preset = mw._preset_from_widgets()
    finally:
        mw._secret_store = original
    assert calls == []
    assert isinstance(preset, Preset)
    assert preset.library_root == lib
    # And the Preset type itself has no secret-bearing field.
    field_names = {f for f in preset.__dict__}
    assert not (field_names & set(SECRET_KEY_NAMES))
