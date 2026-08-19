"""Ordered metadata roots (GH-23).

Acceptance (GH-23):
  1. List order of the configured roots IS the precedence order: when the
     same asset type (artwork) is present in more than one root, the FIRST
     root in the list wins. Reordering the list changes the winner.
  2. GUI: Move Up / Move Down reorder the artwork (table) and manual / RTFM
     (list) root widgets; the underlying ordered mapping changes accordingly;
     the actions are no-ops at the top / bottom boundaries.
  3. The reordered list persists through the settings layer and named
     profiles (GH-20 / GH-33 round-trip regression): save -> disk -> reload
     keeps the exact order.
  4. Group boxes are relabeled to state the precedence rule (Artwork roots,
     Manuals / RTFM roots).
  5. Missing roots are skipped at discovery (kept in config, never deleted);
     the next existing root still wins.
  6. The precedence path is offline (no network) and read-only.

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as
test_gui_issue20_profiles.py).
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from amiga_adf_library_builder import local_media as lm  # noqa: E402
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup  # noqa: E402

TITLE = "Example Space Tactics"

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


# --- helpers -----------------------------------------------------------------


def _make_group(title: str, *, source_filename=None) -> ReleaseGroup:
    fn = source_filename or f"{title}.adf"
    rec = ParsedRecord(
        source_filename=fn,
        ext="adf",
        title=title,
        chipset=None,
        language=None,
        alt_marker=None,
        trainer=False,
        edition=None,
        version=None,
    )
    return ReleaseGroup(
        release_key=(title or "").lower(),
        title=title,
        edition=None,
        group=None,
        chipset=None,
        language=None,
        version=None,
        alt_marker=None,
        ext="adf",
        records=[rec],
        disks=[rec],
    )


def _flat_media_root(base: Path, name: str, title: str, content: bytes) -> Path:
    """Create one typed media root with a single flat image named after the
    game title (the filename stem carries the identity)."""
    root = base / name
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{title}.png").write_bytes(content)
    return root


def _build_launchbox(root: Path, layout: dict[str, bytes]) -> None:
    """LaunchBox tree: <root>/Images/<Platform>/<Category>/<rest>."""
    for rel, content in layout.items():
        p = root / "Images" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _typed_provider(
    cache: Path, roots: list[Path], *, asset_type: str = "Box - Front"
) -> lm.LocalMediaProvider:
    cfg = lm.LocalMediaConfig(
        enabled=True,
        media_roots=tuple(
            lm.MediaRoot(path=str(p), asset_type=asset_type) for p in roots
        ),
    )
    prov = lm.LocalMediaProvider(cfg, cache)
    prov.discover()
    return prov


# --- 1. backend: first-configured root wins ----------------------------------


def test_first_configured_root_wins_same_type(tmp_path: Path):
    """Two typed roots, same asset type, same game: the FIRST list entry wins,
    and reversing the list reverses the winner."""
    root_a = _flat_media_root(tmp_path, "media_a", TITLE, b"ROOT_A")
    root_b = _flat_media_root(tmp_path, "media_b", TITLE, b"ROOT_B")

    prov = _typed_provider(tmp_path / "cache1", [root_a, root_b])
    res = prov.resolve(_make_group(TITLE))
    assert res.found is True
    assert res.cached_path.read_bytes() == b"ROOT_A"

    # Reversed list order -> the other root wins. This is the GH-23 contract:
    # reordering the user's list actually changes which image is selected.
    prov2 = _typed_provider(tmp_path / "cache2", [root_b, root_a])
    res2 = prov2.resolve(_make_group(TITLE))
    assert res2.found is True
    assert res2.cached_path.read_bytes() == b"ROOT_B"


def test_root_order_beats_path_sort(tmp_path: Path):
    """The first-configured root wins even when its path would sort LAST
    alphabetically. Sorting by path alone (pre-GH-23) would have picked the
    'aaa' root here."""
    root_first_but_z = _flat_media_root(
        tmp_path, "zzz_first", TITLE, b"CONFIG_FIRST"
    )
    root_second_but_a = _flat_media_root(
        tmp_path, "aaa_second", TITLE, b"PATH_FIRST"
    )
    assert str(root_second_but_a) < str(root_first_but_z)

    prov = _typed_provider(tmp_path / "cache", [root_first_but_z, root_second_but_a])
    res = prov.resolve(_make_group(TITLE))
    assert res.found is True
    assert res.cached_path.read_bytes() == b"CONFIG_FIRST"


def test_legacy_roots_precede_typed_media_roots(tmp_path: Path):
    """Documented precedence: legacy ``roots`` are scanned first, then typed
    ``media_roots``. A legacy root holding the same category wins over a typed
    one that sorts first by path."""
    lb = tmp_path / "lb_root"
    _build_launchbox(
        lb,
        {
            "Commodore Amiga/Box - Front/"
            f"{TITLE}/001.png": b"LEGACY",
        },
    )
    typed = _flat_media_root(tmp_path, "aaa_typed", TITLE, b"TYPED")
    # The typed path sorts before the legacy root's; precedence must not care.
    assert str(typed) < str(lb)

    cfg = lm.LocalMediaConfig(
        enabled=True,
        roots=(str(lb),),
        media_roots=(lm.MediaRoot(path=str(typed), asset_type="Box - Front"),),
    )
    prov = lm.LocalMediaProvider(cfg, tmp_path / "cache")
    prov.discover()
    res = prov.resolve(_make_group(TITLE))
    assert res.found is True
    assert res.cached_path.read_bytes() == b"LEGACY"


def test_missing_root_skipped_next_root_wins(tmp_path: Path):
    """A configured root that does not exist is skipped at discovery (kept in
    config, never deleted); the next existing root still wins."""
    missing = tmp_path / "does_not_exist"
    existing = _flat_media_root(tmp_path, "existing", TITLE, b"EXISTS")

    prov = _typed_provider(tmp_path / "cache", [missing, existing])
    assert prov.discover() == 1
    res = prov.resolve(_make_group(TITLE))
    assert res.found is True
    assert res.cached_path.read_bytes() == b"EXISTS"
    # The missing root stays in the config (surfaced by scan_launchbox_roots,
    # never deleted by the provider).
    assert missing in (Path(r.path) for r in prov.config.media_roots)


def test_within_root_path_tiebreak_deterministic(tmp_path: Path):
    """Within ONE root the path string remains the deterministic tiebreaker.
    Both files score confidently; the path-smaller one wins, every run."""
    root = tmp_path / "root"
    root.mkdir()
    (root / f"{TITLE}.png").write_bytes(b"PLAIN")
    (root / f"{TITLE}-01.png").write_bytes(b"ORDINAL")

    prov = _typed_provider(tmp_path / "cache", [root])
    first = prov.resolve(_make_group(TITLE))
    # "…-01.png" < "….png" (0x2D < 0x2E) -> the ordinal-stripped file wins.
    assert first.found is True
    assert first.cached_path.read_bytes() == b"ORDINAL"
    # Re-discover + re-resolve: the same winner, deterministically.
    prov2 = _typed_provider(tmp_path / "cache2", [root])
    assert prov2.resolve(_make_group(TITLE)).cached_path.read_bytes() == b"ORDINAL"


def test_precedence_is_offline(monkeypatch, tmp_path: Path):
    """The whole precedence path works with no network access at all."""

    def _blocked_socket(*args, **kwargs):
        raise OSError("network is blocked in this test")

    monkeypatch.setattr(socket, "socket", _blocked_socket)

    root_a = _flat_media_root(tmp_path, "a", TITLE, b"A")
    root_b = _flat_media_root(tmp_path, "b", TITLE, b"B")
    prov = _typed_provider(tmp_path / "cache", [root_a, root_b])
    res = prov.resolve(_make_group(TITLE))
    assert res.found is True
    assert res.cached_path.read_bytes() == b"A"


# --- GUI fixtures (offscreen) -------------------------------------------------


@pytest.fixture
def qt_offscreen(tmp_path: Path):
    """Provide a temp base dir for offscreen GUI construction."""
    return tmp_path / "gui-offscreen-base"


@pytest.fixture
def main_window(qt_offscreen: Path):
    """Construct a MainWindow backed by isolated portable paths (offscreen)."""
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui import MainWindow
    from amiga_adf_library_builder.gui.layout import PortablePaths
    from amiga_adf_library_builder.gui.secrets import SecretStore
    from amiga_adf_library_builder.gui.settings import SettingsStore

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


def _add_media_row(mw, folder: str, asset_type: str = "Box - Front") -> int:
    """Insert an artwork mapping row the way _lb_add_media_root does, without
    the (modal) directory dialog."""
    from PySide6.QtWidgets import QTableWidgetItem

    mw._lb_media_table.insertRow(mw._lb_media_table.rowCount())
    row = mw._lb_media_table.rowCount() - 1
    mw._lb_media_table.setItem(row, 0, QTableWidgetItem(folder))
    mw._lb_media_table.setCellWidget(row, 1, mw._lb_asset_type_combo(asset_type))
    return row


def _media_paths(mw) -> list[str]:
    return [m["path"] for m in mw._lb_media_mappings()]


def _manual_paths(mw) -> list[str]:
    return list(mw._lb_manual_mappings())


# --- 2. GUI: Move Up / Move Down ---------------------------------------------


def test_group_boxes_relabelled_with_precedence(main_window):
    """AC4: both group boxes carry the GH-23 precedence wording."""
    mw, _pp = main_window
    from PySide6.QtWidgets import QGroupBox

    titles = {gb.title() for gb in mw.findChildren(QGroupBox)}
    assert "Artwork roots (each root has one asset type)" in titles
    assert "Manuals / RTFM roots (PDF / TXT documents)" in titles


def test_move_up_down_reorders_artwork_rows(main_window):
    """AC2: Move Up / Down swap whole artwork rows (folder + asset type) and
    the ordered mapping follows the new list order."""
    mw, _pp = main_window
    a, b, c = "/roots/a", "/roots/b", "/roots/c"
    _add_media_row(mw, a, "Box - Front")
    _add_media_row(mw, b, "Screenshot - Game Title")
    _add_media_row(mw, c, "Box - Front")
    assert _media_paths(mw) == [a, b, c]

    # Select the bottom row and move it up: [a, c, b]; selection follows.
    mw._lb_media_table.selectRow(2)
    mw._lb_media_up_button.click()
    assert _media_paths(mw) == [a, c, b]
    assert mw._lb_media_table.currentRow() == 1
    # Asset types travelled with their folder (whole-row swap).
    combos = [
        mw._lb_media_table.cellWidget(r, 1).currentText()
        for r in range(mw._lb_media_table.rowCount())
    ]
    assert combos == ["Box - Front", "Box - Front", "Screenshot - Game Title"]

    # Move it back down: [a, b, c].
    mw._lb_media_down_button.click()
    assert _media_paths(mw) == [a, b, c]
    assert mw._lb_media_table.currentRow() == 2


def test_move_up_down_reorder_manual_roots(main_window):
    """AC2: manual / RTFM roots reorder the same way."""
    mw, _pp = main_window
    m1, m2, m3 = "/manuals/1", "/manuals/2", "/manuals/3"
    mw._lb_manual_list.addItem(m1)
    mw._lb_manual_list.addItem(m2)
    mw._lb_manual_list.addItem(m3)
    assert _manual_paths(mw) == [m1, m2, m3]

    mw._lb_manual_list.setCurrentRow(2)
    mw._lb_manual_up_button.click()
    assert _manual_paths(mw) == [m1, m3, m2]
    assert mw._lb_manual_list.currentRow() == 1

    mw._lb_manual_list.setCurrentRow(0)
    mw._lb_manual_down_button.click()
    assert _manual_paths(mw) == [m3, m1, m2]
    assert mw._lb_manual_list.currentRow() == 1


def test_reorder_noop_at_boundaries(main_window):
    """AC2: Move Up at the top and Move Down at the bottom are no-ops."""
    mw, _pp = main_window
    a, b = "/roots/a", "/roots/b"
    _add_media_row(mw, a)
    _add_media_row(mw, b)
    mw._lb_media_table.selectRow(0)
    mw._lb_media_up_button.click()
    assert _media_paths(mw) == [a, b]

    mw._lb_manual_list.addItem("/manuals/1")
    mw._lb_manual_list.setCurrentRow(0)
    mw._lb_manual_up_button.click()
    assert _manual_paths(mw) == ["/manuals/1"]

    mw._lb_media_table.selectRow(1)
    mw._lb_media_down_button.click()
    assert _media_paths(mw) == [a, b]
    mw._lb_manual_list.setCurrentRow(0)
    mw._lb_manual_down_button.click()
    assert _manual_paths(mw) == ["/manuals/1"]


def test_reorder_with_no_selection_is_safe(main_window):
    """AC2: with nothing selected the buttons do nothing and never crash."""
    mw, _pp = main_window
    _add_media_row(mw, "/roots/a")
    mw._lb_manual_list.addItem("/manuals/1")
    mw._lb_media_up_button.click()
    mw._lb_media_down_button.click()
    mw._lb_manual_up_button.click()
    mw._lb_manual_down_button.click()
    assert _media_paths(mw) == ["/roots/a"]
    assert _manual_paths(mw) == ["/manuals/1"]


# --- 3. persistence: order survives profiles (GH-20 / GH-33) ------------------


def test_reordered_order_persists_through_profile_round_trip(
    main_window, tmp_path: Path
):
    """AC3: a GUI reordering changes what a saved profile carries; a fresh
    store reads the exact order back; loading applies that order to the
    widgets (GH-20 round-trip + GH-33 mapping regression)."""
    mw, pp = main_window
    a, b = str(tmp_path / "media_a"), str(tmp_path / "media_b")
    for p in (a, b):
        Path(p).mkdir(parents=True, exist_ok=True)
    # The manual roots must EXIST on disk too: _load_profile warns modally
    # about missing profile paths (GH-20), and a modal loop cannot complete
    # offscreen. Creating them keeps this test focused on order round-trip.
    for name in ("manuals_2", "manuals_1"):
        Path(tmp_path / name).mkdir(parents=True, exist_ok=True)

    _add_media_row(mw, a, "Box - Front")
    _add_media_row(mw, b, "Box - Front")
    assert _media_paths(mw) == [a, b]

    # Reorder in the GUI: b becomes the first (winning) root.
    mw._lb_media_table.selectRow(1)
    mw._lb_media_up_button.click()
    assert _media_paths(mw) == [b, a]
    mw._lb_manual_list.addItem(str(tmp_path / "manuals_2"))
    mw._lb_manual_list.addItem(str(tmp_path / "manuals_1"))

    preset = mw._preset_from_widgets()
    preset.name = "Ordered"
    mw._settings_store.save_preset(preset)

    # Fresh store from the same file on disk: the order round-trips.
    from amiga_adf_library_builder.gui.settings import SettingsStore

    store2 = SettingsStore(pp.settings_file())
    s2 = store2.load()
    p2 = s2.presets["Ordered"]
    assert p2.launchbox_media_roots == [
        {"path": b, "asset_type": "Box - Front"},
        {"path": a, "asset_type": "Box - Front"},
    ]
    assert p2.launchbox_manual_roots == [
        str(tmp_path / "manuals_2"),
        str(tmp_path / "manuals_1"),
    ]

    # Loading the profile applies the persisted order to the widgets.
    assert mw._load_profile("Ordered") is True
    assert _media_paths(mw) == [b, a]
    assert _manual_paths(mw) == [
        str(tmp_path / "manuals_2"),
        str(tmp_path / "manuals_1"),
    ]


def test_profile_with_ordered_roots_contains_no_secret(main_window, tmp_path: Path):
    """Security: a profile that carries multiple ordered local root paths has
    zero secret material in the serialized file."""
    mw, pp = main_window
    from amiga_adf_library_builder.gui.settings import Preset

    roots = [str(tmp_path / f"media_{i}") for i in range(3)]
    for p in roots:
        Path(p).mkdir(parents=True, exist_ok=True)
    preset = Preset(
        name="Clean",
        library_root=str(tmp_path),
        online=True,
        launchbox_media_roots=[
            {"path": p, "asset_type": "Box - Front"} for p in roots
        ],
        launchbox_manual_roots=[str(tmp_path / f"manuals_{i}") for i in range(2)],
    )
    mw._settings_store.save_preset(preset)

    text = pp.settings_file().read_text(encoding="utf-8").lower()
    for forbidden in SECRET_KEY_NAMES:
        assert forbidden not in text, f"secret key leaked into profile: {forbidden}"
    # Order is intact in the on-disk file.
    store2 = mw._settings_store
    assert [m["path"] for m in store2.get().presets["Clean"].launchbox_media_roots] == roots
