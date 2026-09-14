"""GH-134 regression tests: DAT source removal via GUI.

Acceptance (GH-134):
  1. Selected DAT source disappears immediately after Remove.
  2. Persisted settings update immediately (SQLite commit).
  3. DAT view/index refreshes deterministically to reflect remaining sources.
  4. Records contributed only by the removed source disappear/rebuild correctly.
  5. Remaining DAT source data remains available.
  6. Removing the last DAT source leaves an intentional, clear empty state.
  7. No-selection/invalid Remove yields an actionable message.
  8. Both DAT file and DAT folder sources behave correctly.

Headless: relies on QT_QPA_PLATFORM=offscreen (same pattern as
test_gui_issue20_profiles.py / test_gui_folder_persistence.py).
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from amiga_adf_library_builder.gui.layout import PortablePaths  # noqa: E402
from amiga_adf_library_builder.gui.secrets import SecretStore  # noqa: E402
from amiga_adf_library_builder.gui.settings import SettingsStore  # noqa: E402
from amiga_adf_library_builder.gui import MainWindow  # noqa: E402
from amiga_adf_library_builder.metadata_source import (
    MetadataSourceManager,
)


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "gh134-base"


@pytest.fixture
def main_window(qt_offscreen: Path):
    """Construct a MainWindow backed by isolated portable paths (offscreen)."""
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


def _add_source(mw: MainWindow, dat_path: Path, *, name: str = "Test DAT", source_type: str = "dat") -> str:
    """Add a source via the manager and refresh the table. Returns source_id."""
    mgr = mw._metadata_manager
    sid = mgr.add_source(dat_path, name=name, source_type=source_type)
    assert sid is not None, f"add_source returned None for {dat_path} (type={source_type})"
    mw._refresh_ms_table()
    return sid


def _unique_dat(tmp_path: Path, name: str) -> Path:
    """Create a unique DAT file with distinct content to avoid hash dedup."""
    dat = tmp_path / f"{name}.dat"
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<!DOCTYPE datafile PUBLIC "-//Logiqx//DTD ROM Datafile//EN" "datafile.dtd">\n'
        f'<datafile><header><name>{name}</name></header></datafile>\n'
    )
    dat.write_text(content)
    return dat


class TestMetadataSourceRemove:
    """GH-134: GUI-level Remove button regression tests."""

    def test_remove_one_source_updates_table(self, main_window, sample_dat):
        """Remove a single source: it disappears from the table immediately."""
        mw, _ = main_window
        sid = _add_source(mw, sample_dat)
        mw._ms_table.setCurrentCell(0, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            mw._on_ms_remove()
        assert mw._ms_table.rowCount() == 0, "Source should disappear from table"
        assert mw._metadata_manager.get_source(sid) is None, "Source removed from DB"

    def test_remove_persists_to_database(self, main_window, sample_dat):
        """Removed source is gone from the SQLite DB after removal."""
        mw, _ = main_window
        sid = _add_source(mw, sample_dat)
        mw._ms_table.setCurrentCell(0, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            mw._on_ms_remove()
        # Re-create manager from same DB to confirm persistence
        mgr2 = MetadataSourceManager(mw._metadata_manager.db_path)
        assert mgr2.get_source(sid) is None, "Source must be gone after re-read"
        mgr2.close()

    def test_remove_last_source_leaves_empty_state(self, main_window, sample_dat):
        """Removing the last source leaves zero rows, not a crash."""
        mw, _ = main_window
        sid = _add_source(mw, sample_dat)
        mw._ms_table.setCurrentCell(0, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            mw._on_ms_remove()
        assert mw._ms_table.rowCount() == 0
        assert mw._ms_table.columnCount() > 0, "Table structure intact"

    def test_remove_no_selection_shows_message(self, main_window, sample_dat):
        """Remove with no row selected shows 'Select a source first.' dialog."""
        mw, _ = main_window
        sid = _add_source(mw, sample_dat)
        mw._ms_table.setCurrentCell(-1, -1)  # No selection
        info_calls = []

        def _fake_info(*args, **kwargs):
            info_calls.append(args)

        with patch.object(QMessageBox, "information", side_effect=_fake_info):
            mw._on_ms_remove()
        assert len(info_calls) == 1, "Should show info dialog"
        assert any("Select a source first." in str(c) for c in info_calls), \
            "Dialog message should be actionable"

    def test_remove_remaining_source_still_available(self, main_window, sample_dat, tmp_path):
        """After removing one of two sources, the other remains functional."""
        mw, _ = main_window
        # Create two unique DAT files to avoid content-hash dedup
        dat1 = _unique_dat(tmp_path, "SourceA")
        dat2 = _unique_dat(tmp_path, "SourceB")
        sid1 = _add_source(mw, dat1)
        sid2 = _add_source(mw, dat2)
        mw._refresh_ms_table()
        assert mw._ms_table.rowCount() == 2

        # Remove first source
        mw._ms_table.setCurrentCell(0, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            mw._on_ms_remove()
        assert mw._ms_table.rowCount() == 1
        assert mw._metadata_manager.get_source(sid1) is None
        assert mw._metadata_manager.get_source(sid2) is not None, \
            "Remaining source must still exist"

    def test_remove_folder_source_behaves_same(self, main_window, tmp_path):
        """Folder-type sources also remove correctly after the fix."""
        folder = tmp_path / "test_folder"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "game.dat").write_text("<?xml version='1.0'?><root></root>")

        mw, _ = main_window
        sid = _add_source(mw, folder, name="Test Folder", source_type="folder")
        mw._ms_table.setCurrentCell(0, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            mw._on_ms_remove()
        assert mw._ms_table.rowCount() == 0
        assert mw._metadata_manager.get_source(sid) is None

    def test_remove_confirmed_only_on_yes(self, main_window, sample_dat):
        """Selecting No in the confirmation dialog cancels the removal."""
        mw, _ = main_window
        sid = _add_source(mw, sample_dat)
        mw._ms_table.setCurrentCell(0, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            mw._on_ms_remove()
        assert mw._ms_table.rowCount() == 1, "Source must remain after No"
        assert mw._metadata_manager.get_source(sid) is not None

    def test_remove_two_sources_preserves_after_first(self, main_window, sample_dat, tmp_path):
        """Remove source A, then verify source B data still accessible."""
        mw, _ = main_window
        # Use unique DAT files to avoid content-hash dedup
        dat1 = _unique_dat(tmp_path, "SourceA")
        dat2 = _unique_dat(tmp_path, "SourceB")
        sid1 = _add_source(mw, dat1)
        sid2 = _add_source(mw, dat2)
        mw._refresh_ms_table()

        # Remove first source
        mw._ms_table.setCurrentCell(0, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            mw._on_ms_remove()
        assert mw._ms_table.rowCount() == 1

        # Verify remaining source still in DB
        remaining = mw._metadata_manager.list_sources()
        assert len(remaining) == 1
        assert remaining[0].source_id == sid2

        # Remove second source
        mw._ms_table.setCurrentCell(0, 0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            mw._on_ms_remove()
        assert mw._ms_table.rowCount() == 0
        assert len(mw._metadata_manager.list_sources()) == 0


@pytest.fixture(autouse=True)
def sample_dat():
    """Return the synthetic TOSEC-style DAT fixture path."""
    return Path(__file__).parent / "fixtures" / "sample.dat"
