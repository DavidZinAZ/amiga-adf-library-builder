"""Issue #17 regression tests: GUI folder persistence across sessions.

Root cause under test: the GUI persisted folder defaults ONLY inside
``_on_run()``; ``closeEvent`` never persisted, so selecting the working
folders and closing WITHOUT starting a run lost the selections on reopen.

These tests cover the fix contract:
  * persist on close via the shared helper (``MainWindow.closeEvent``);
  * run and close call sites write IDENTICAL settings (parity);
  * missing persisted paths are restored visibly and gracefully (no crash,
    no silent clear, no modal);
  * a close cycle writes NO secret material into the settings TOML and
    leaves the vault file untouched;
  * the CLI is unaffected (import + ``--help`` dry path).

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as
tests/test_gui_import.py). Deterministic on pytest tmp dirs.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from amiga_adf_library_builder.gui.layout import PortablePaths
from amiga_adf_library_builder.gui.secrets import SecretStore
from amiga_adf_library_builder.gui.settings import SETTINGS_KEYS, SettingsStore
from amiga_adf_library_builder.gui import MainWindow


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "issue17-base"


def _make_window(base_dir: Path) -> MainWindow:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    pp = PortablePaths(base_dir=base_dir)
    pp.ensure_all()
    return MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=None,
    )


def _fill_folders(mw: MainWindow, base_dir: Path) -> dict:
    """Populate the 4 folder fields with fresh tmp dirs + option state."""
    dirs = {
        "library_root": base_dir / "lib",
        "original_dir": base_dir / "lib" / "original",
        "staging_dir": base_dir / "work" / "staging",
        "output_dir": base_dir / "out",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    mw._le_library_root.setText(str(dirs["library_root"]))
    mw._le_original_dir.setText(str(dirs["original_dir"]))
    mw._le_staging_dir.setText(str(dirs["staging_dir"]))
    mw._le_output_dir.setText(str(dirs["output_dir"]))
    mw._cb_online.setChecked(True)
    mw._cb_refresh.setChecked(True)
    return {k: str(v) for k, v in dirs.items()}


def _read_gui_table(settings_path: Path) -> dict:
    import tomllib

    with open(settings_path, "rb") as fh:
        return tomllib.load(fh).get("gui", {})


def test_close_persists_folder_defaults_reopen_restores(qt_offscreen):
    """The exact user repro: select folders, close WITHOUT running, reopen.

    All 4 folder fields (and option state) must be restored on the new
    window from the same settings file.
    """
    base = Path(qt_offscreen)
    mw1 = _make_window(base)
    expected = _fill_folders(mw1, base)
    mw1.show()
    mw1.close()  # closeEvent must persist; no run was ever started

    pp = PortablePaths(base_dir=base)
    table = _read_gui_table(pp.settings_file())
    assert table["default_library_root"] == expected["library_root"]
    assert table["default_original_dir"] == expected["original_dir"]
    assert table["default_staging_dir"] == expected["staging_dir"]
    assert table["default_output_dir"] == expected["output_dir"]
    assert table["online"] is True
    assert table["refresh_metadata"] is True

    # Reopen a fresh window on the SAME settings file.
    mw2 = _make_window(base)
    assert mw2._le_library_root.text() == expected["library_root"]
    assert mw2._le_original_dir.text() == expected["original_dir"]
    assert mw2._le_staging_dir.text() == expected["staging_dir"]
    assert mw2._le_output_dir.text() == expected["output_dir"]
    assert mw2._cb_online.isChecked() is True
    mw2.close()


def test_run_and_close_call_sites_write_identical_settings(qt_offscreen):
    """Parity: the run persist path and the close persist path share the
    helper, so the SAME widget state must produce byte-equivalent [gui]
    tables from both call sites."""
    base = Path(qt_offscreen)
    pp = PortablePaths(base_dir=base)
    pp.ensure_all()
    settings_path = pp.settings_file()

    mw = _make_window(base)
    _fill_folders(mw, base)

    # Call site 1: the run persist path (_on_run delegates here).
    assert mw._persist_defaults() is True
    table_a = _read_gui_table(settings_path)

    # Call site 2: the closeEvent persist path, same widget state.
    mw.show()
    mw.close()
    table_b = _read_gui_table(settings_path)

    # ``window_geometry`` tracks the WINDOW's presentation state, not the
    # widget state (the offscreen platform clamps a shown window to its
    # minimum height), so it is exempt from strict byte-parity; both call
    # sites must still have written a valid payload (Issue #18).
    for key in SETTINGS_KEYS:
        if key == "window_geometry":
            continue
        assert table_a[key] == table_b[key], f"parity broken on {key!r}"
    for table in (table_a, table_b):
        assert table["window_geometry"], (
            "window_geometry must be persisted by both call sites (Issue #18)"
        )


def test_missing_persisted_path_still_shown_and_flagged(qt_offscreen):
    """Persist two paths, delete them, reopen: fields stay populated and the
    missing paths are named in the status label -- no exception, no clear."""
    base = Path(qt_offscreen)
    mw1 = _make_window(base)
    expected = _fill_folders(mw1, base)
    mw1.show()
    mw1.close()

    # Delete two of the four persisted directories (simulate another machine).
    gone_staging = Path(expected["staging_dir"])
    gone_output = Path(expected["output_dir"])
    gone_staging.rmdir()
    gone_output.rmdir()

    mw2 = _make_window(base)
    # Fields are still populated (no silent clear).
    assert mw2._le_library_root.text() == expected["library_root"]
    assert mw2._le_original_dir.text() == expected["original_dir"]
    assert mw2._le_staging_dir.text() == expected["staging_dir"]
    assert mw2._le_output_dir.text() == expected["output_dir"]
    # The graceful/visible signal names exactly the missing paths.
    status = mw2._status_label.text()
    assert "Persisted path(s) not found" in status
    assert str(gone_staging) in status
    assert str(gone_output) in status
    # Paths that DO exist must not be flagged.
    assert expected["library_root"] not in status
    assert expected["original_dir"] not in status
    mw2.close()


def test_close_cycle_writes_no_secrets_and_vault_untouched(qt_offscreen):
    """After a full close cycle the settings TOML carries no secret-shaped
    material and the vault file is byte-identical to what it was."""
    base = Path(qt_offscreen)
    pp = PortablePaths(base_dir=base)
    pp.ensure_all()
    sentinel = b"sentinel-vault-bytes-0f593896"
    pp.vault_file().write_bytes(sentinel)

    mw = _make_window(base)
    _fill_folders(mw, base)
    mw.show()
    mw.close()

    text = pp.settings_file().read_text(encoding="utf-8")
    lowered = text.lower()
    for forbidden in ("token", "api_key", "secret", "password", "bearer", "master"):
        assert forbidden not in lowered, f"secret-shaped key leaked: {forbidden}"
    table = _read_gui_table(pp.settings_file())
    assert set(table.keys()) <= set(SETTINGS_KEYS)
    assert pp.vault_file().read_bytes() == sentinel


def test_cli_import_and_help_path_unaffected(capsys):
    """Guard: the CLI import and a dry ``--help`` invoke are unaffected by
    the GUI persistence fix (the fix must not touch cli.py behavior)."""
    from amiga_adf_library_builder.cli import build_parser, main

    parser = build_parser()
    assert parser.prog == "amiga-adf-library-builder"
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out.lower()
