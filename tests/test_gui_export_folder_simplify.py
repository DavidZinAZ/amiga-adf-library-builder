"""GH-187 regression tests: Export Destination removal and rename.

These tests verify the contract from GitHub issue #187:
  1. The Library tab shows "ADF Library Export Folder" and does NOT
     show "Export destination";
  2. MainWindow has no ``_le_output_dir`` attribute;
  3. The staging (ADF Library Export Folder) path persists across a
     settings round-trip (upgrade preservation);
  4. A legacy gui-settings.toml containing ``default_output_dir`` loads
     without error and does not affect the resolved staging/output;
  5. ``build_path_config_from_gui_state`` with empty ``output_dir``
     derives ``<root>/output`` (CLI-equivalence preserved).

Headless: relies on ``QT_QPA_PLATFORM=offscreen``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from amiga_adf_library_builder.gui import MainWindow
from amiga_adf_library_builder.gui.layout import PortablePaths
from amiga_adf_library_builder.gui.secrets import SecretStore
from amiga_adf_library_builder.gui.settings import SettingsStore
from amiga_adf_library_builder.gui.state import (
    GuiState,
    build_path_config_from_gui_state,
)
from amiga_adf_library_builder.paths import resolve_config


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "gh187-base"


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


class TestADFLibraryExportFolderLabel:
    """The Library tab label changes and old label is gone."""

    def test_shows_adf_library_export_folder(self, qt_offscreen: Path):
        """AC1a: The Library tab shows "ADF Library Export Folder"."""
        mw = _make_window(qt_offscreen)
        texts = _collect_display_texts(mw)
        assert "ADF Library Export Folder" in texts
        mw.close()

    def test_does_not_show_export_destination(self, qt_offscreen: Path):
        """AC1b: The Library tab does NOT show "Export destination"."""
        mw = _make_window(qt_offscreen)
        texts = _collect_display_texts(mw)
        assert "Export destination" not in texts
        mw.close()

    def test_no_le_output_dir_attribute(self, qt_offscreen: Path):
        """AC2: MainWindow has no _le_output_dir attribute."""
        mw = _make_window(qt_offscreen)
        assert not hasattr(mw, "_le_output_dir")
        mw.close()


class TestStagingPathPersistence:
    """The ADF Library Export Folder path persists across sessions."""

    def test_staging_path_round_trip(self, qt_offscreen: Path):
        """AC3: Staging (ADF Library Export Folder) path persists."""
        base = qt_offscreen
        mw1 = _make_window(base)
        custom_path = str(base / "my-export-folder")
        mw1._le_staging_dir.setText(custom_path)
        mw1.show()
        mw1.close()

        # Reopen on the same settings file.
        mw2 = _make_window(base)
        assert mw2._le_staging_dir.text() == custom_path
        mw2.close()


class TestLegacyOutputDirBackcompat:
    """Legacy settings files with default_output_dir still load."""

    def test_legacy_default_output_dir_loads(self, qt_offscreen: Path):
        """AC4: Legacy gui-settings.toml with default_output_dir loads."""
        base = qt_offscreen
        pp = PortablePaths(base_dir=base)
        pp.ensure_all()
        settings_path = pp.settings_file()

        # Write a legacy settings file containing default_output_dir.
        from amiga_adf_library_builder.gui.settings import Settings

        store = SettingsStore(settings_path)
        store.load()
        store.update(
            default_staging_dir=str(base / "staging"),
            default_output_dir=str(base / "legacy-output"),
        )

        # Reload must not error.
        store2 = SettingsStore(settings_path)
        s = store2.load()
        assert hasattr(s, "default_output_dir")
        assert s.default_output_dir == str(base / "legacy-output")

        # GUI construction must not fail.
        mw = _make_window(base)
        # The GUI no longer exposes output_dir; staging is the real path.
        assert hasattr(mw, "_le_staging_dir")
        assert not hasattr(mw, "_le_output_dir")
        mw.close()

    def test_legacy_output_dir_does_not_affect_staging(self, qt_offscreen: Path):
        """AC4b: Legacy default_output_dir does not override staging_dir."""
        base = qt_offscreen
        pp = PortablePaths(base_dir=base)
        pp.ensure_all()
        store = SettingsStore(pp.settings_file())
        store.load()
        store.update(
            default_staging_dir=str(base / "my-staging"),
            default_output_dir=str(base / "legacy-output"),
        )
        mw = _make_window(base)
        assert mw._le_staging_dir.text() == str(base / "my-staging")
        mw.close()


class TestCLIEquivalence:
    """GUI with empty output_dir resolves the same as CLI with no --output-dir."""

    def test_empty_output_dir_derives_root_output(self, tmp_path: Path):
        """AC5: build_path_config_from_gui_state with output_dir='' derives <root>/output."""
        root = tmp_path / "lib"
        root.mkdir()
        state = GuiState(
            library_root=str(root),
            original_dir=str(root / "original"),
            staging_dir=str(root / "work" / "staging"),
            output_dir="",
        )
        cfg = build_path_config_from_gui_state(state)
        # Should derive <root>/output when output_dir is empty.
        assert str(cfg.output_dir) == str((root / "output").resolve())

    def test_gui_empty_matches_cli_no_output_dir(self, tmp_path: Path):
        """AC5b: GUI with empty output_dir matches CLI with no --output-dir."""
        root = tmp_path / "lib"
        root.mkdir()
        # GUI-derived path config with empty output_dir.
        gui_state = GuiState(
            library_root=str(root),
            original_dir=str(root / "original"),
            staging_dir=str(root / "work" / "staging"),
            output_dir="",
        )
        gui_cfg = build_path_config_from_gui_state(gui_state)

        # CLI equivalent: resolve_config with no output_dir.
        ref, _ = resolve_config(
            library_root=str(root),
            original_dir=str(root / "original"),
            staging_dir=str(root / "work" / "staging"),
        )
        assert gui_cfg.output_dir == ref.output_dir
        assert gui_cfg.staging_dir == ref.staging_dir
        assert gui_cfg.original_dir == ref.original_dir


# --- helpers ----------------------------------------------------------------

def _collect_display_texts(widget) -> set[str]:
    """Yield every user-visible string under ``widget``."""
    seen: set[int] = set()
    results: set[str] = set()

    def _walk(w):
        if id(w) in seen:
            return
        seen.add(id(w))
        for getter in ("text", "title", "toolTip", "placeholderText"):
            value = getattr(w, getter, None)
            if callable(value):
                value = value()
            if isinstance(value, str) and value:
                results.add(value)
        if hasattr(w, "children"):
            for child in w.children():
                _walk(child)

    _walk(widget)
    return results
