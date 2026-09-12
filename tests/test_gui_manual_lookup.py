"""GUI integration tests for the Slice 4 Manual Lookup panel.

Headless (QT_QPA_PLATFORM=offscreen, same pattern as the other GUI suites).
Exercises the real application integration path: MainWindow constructs the
"Manual Lookup" tab; the panel drives the real CanonicalLibrary and
MetadataSourceManager stores; overrides persist and survive provider
refresh; raw DAT/source media is never mutated.
"""
from __future__ import annotations

import pytest

from amiga_adf_library_builder.canonical import (
    CanonicalLibrary,
    Provenance,
    SourceAuthority,
)
from amiga_adf_library_builder.gui.manual_lookup_panel import ManualLookupPanel

pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def stores(tmp_path):
    canon_path = tmp_path / "curation" / "canonical.db"
    canon_path.parent.mkdir(parents=True, exist_ok=True)
    with CanonicalLibrary(canon_path) as canon:
        _seed(canon)
        yield canon


def _seed(canon: CanonicalLibrary) -> None:
    canon.claim_field(
        "game", "g-turrican", "title", "Turrican",
        Provenance(source="parser", authority=SourceAuthority.PARSER,
                   observed_at="2026-09-12T00:00:00+00:00"),
    )
    canon.claim_field(
        "release", "r-turrican", "title", "Turrican (DAT)",
        Provenance(source="tosec", record_key="Turrican-Key",
                   url="http://example/turrican",
                   authority=SourceAuthority.DAT,
                   observed_at="2026-09-12T00:00:00+00:00"),
    )
    canon.claim_field(
        "release", "r-turrican", "title", "Turrican (parsed)",
        Provenance(source="parser", authority=SourceAuthority.PARSER,
                   observed_at="2026-09-12T00:00:00+00:00"),
    )
    canon._conn.execute("INSERT OR IGNORE INTO game (game_id) VALUES ('g-turrican')")
    canon._conn.execute(
        "INSERT OR IGNORE INTO release (release_id, game_id) VALUES ('r-turrican','g-turrican')"
    )
    canon._conn.execute(
        "INSERT OR IGNORE INTO disk (disk_id, release_id, filename) "
        "VALUES ('d-turrican','r-turrican','Turrican_Disk1.adf')"
    )
    canon._conn.commit()


def _make_panel(tmp_path, canon):
    # The panel owns its own CanonicalLibrary connection to the same DB.
    return ManualLookupPanel(
        tmp_path / "curation" / "canonical.db",
        None,
    )


def test_panel_constructs_and_lists_entities(qapp, tmp_path, stores):
    panel = ManualLookupPanel(
        tmp_path / "curation" / "canonical.db", None
    )
    assert panel._game_combo.count() >= 1
    assert panel._game_combo.itemText(0) == "Turrican"


def test_conflict_display_and_winner(qapp, tmp_path, stores):
    panel = ManualLookupPanel(
        tmp_path / "curation" / "canonical.db", None
    )
    # Select the release with a conflicting title (DAT vs parser).
    # The panel targets the deepest active selection, so clear the
    # downstream release/disk combos to operate at the release level.
    panel._game_combo.setCurrentIndex(0)
    panel._on_game_selected()
    panel._release_combo.setCurrentIndex(-1)
    panel._disk_combo.setCurrentIndex(-1)
    panel._on_release_selected()
    idx = panel._release_combo.findData("r-turrican")
    panel._release_combo.setCurrentIndex(idx)
    panel._on_release_selected()
    # Repopulation of the disk combo re-deepens the scope; clear it and
    # refresh so the view targets the release-level conflict.
    panel._disk_combo.setCurrentIndex(-1)
    panel._refresh_claim_view()
    assert "conflict" in panel._status_label.text().lower()
    # Winner row carries the documented DAT explanation via Slice 3.
    winners = [
        r for r in range(panel._claims_table.rowCount())
        if panel._claims_table.item(r, 9) is not None
        and panel._claims_table.item(r, 9).text() == "YES"
    ]
    assert winners, "exactly the winning claim must be marked"
    winner_authority = panel._claims_table.item(winners[0], 3).text()
    assert winner_authority == "dat"


def test_override_via_ui_persists_and_survives_refresh(qapp, tmp_path, stores):
    panel = ManualLookupPanel(
        tmp_path / "curation" / "canonical.db", None
    )
    # Game-level override: clear downstream combos so the panel's deepest
    # active selection is the game itself.
    panel._game_combo.setCurrentIndex(0)
    panel._on_game_selected()
    panel._release_combo.setCurrentIndex(-1)
    panel._disk_combo.setCurrentIndex(-1)
    panel._on_release_selected()
    panel._field_combo.setCurrentText("title")
    panel._value_edit.setText("Operator Renamed")
    panel._on_apply_override()

    # Persisted at curation authority in the same store the panel reads.
    value, prov = stores.resolve_field("game", "g-turrican", "title")
    assert value == "Operator Renamed"
    assert prov.authority == SourceAuthority.CURATION

    # Simulated provider refresh (automated claim appended) does NOT win.
    stores.claim_field(
        "game", "g-turrican", "title", "Provider Renamed",
        Provenance(source="tosec", authority=SourceAuthority.DAT,
                   observed_at="2099-01-01T00:00:00+00:00"),
    )
    value, prov = stores.resolve_field("game", "g-turrican", "title")
    assert value == "Operator Renamed"


def test_revert_via_ui_restores_precedence(qapp, tmp_path, stores):
    panel = ManualLookupPanel(
        tmp_path / "curation" / "canonical.db", None
    )
    panel._game_combo.setCurrentIndex(0)
    panel._on_game_selected()
    panel._field_combo.setCurrentText("title")
    panel._value_edit.setText("Operator Renamed")
    panel._on_apply_override()
    panel._on_revert_override()
    value, prov = stores.resolve_field("game", "g-turrican", "title")
    # Falls back to documented automated precedence (parser claim only here).
    assert prov.authority in (SourceAuthority.PARSER, SourceAuthority.DAT)
    assert value != "Operator Renamed"


def test_source_browser_search_and_no_dat_mutation(qapp, tmp_path, stores):
    dat = tmp_path / "sample.dat"
    dat.write_text(
        '<datafile><game name="Turrican (1990)"><rom name="T.adf" '
        'size="901120" crc="1234ABCD" md5="' + "a" * 32 + '" sha1="'
        + "b" * 40 + '"/></game></datafile>',
        encoding="utf-8",
    )
    original = dat.read_bytes()
    from amiga_adf_library_builder.metadata_source import MetadataSourceManager

    with MetadataSourceManager(tmp_path / "sources.db") as mgr:
        mgr.add_source(dat)
        panel = ManualLookupPanel(
            tmp_path / "curation" / "canonical.db", mgr
        )
        panel._query_edit.setText("turrican")
        panel._on_search_sources()
        assert panel._candidates_table.rowCount() >= 1
        assert "Turrican" in panel._candidates_table.item(0, 1).text()
        # Selecting a candidate copies the title into the override box.
        panel._candidates_table.selectRow(0)
        panel._on_use_query_value()
        assert panel._value_edit.text().startswith("Turrican")
        # Hash search path too.
        panel._query_edit.setText("b" * 40)
        panel._on_search_sources()
        assert panel._candidates_table.rowCount() >= 1
    assert dat.read_bytes() == original


def test_main_window_integration_manual_lookup_tab(tmp_path, qapp):
    """The real MainWindow exposes the Manual Lookup tab (offscreen)."""
    from amiga_adf_library_builder.gui.main_window import MainWindow
    from amiga_adf_library_builder.gui.layout import PortablePaths
    from amiga_adf_library_builder.gui.secrets import SecretStore
    from amiga_adf_library_builder.gui.settings import SettingsStore

    pp = PortablePaths(base_dir=tmp_path)
    pp.ensure_all()
    win = MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=None,
    )
    tabs = win._tabs if hasattr(win, "_tabs") else None
    # Locate the QTabWidget via the window's children.
    from PySide6.QtWidgets import QTabWidget

    tabw = win.findChild(QTabWidget)
    assert tabw is not None, "MainWindow must contain a tab widget"
    titles = [tabw.tabText(i) for i in range(tabw.count())]
    assert "Manual Lookup" in titles
    assert "Metadata Sources" in titles
    idx = titles.index("Manual Lookup")
    assert tabw.widget(idx) is not None
    win.close()
