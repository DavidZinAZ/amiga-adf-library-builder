"""GH-33 / issue #33: configurable LOCAL LaunchBox media/manual folder mappings.

Everything here is LOCAL ONLY: no LaunchBox online API, no website scraping, no
download/redistribution, no network egress. The tests exercise the existing
read-only ``local_media`` backend (``LocalMediaConfig`` / ``LocalMediaProvider``),
the issue-#33 typed ``media_roots`` / ``manual_roots`` extensions, the
``scan_launchbox_roots`` diagnostics, the ``discover_manuals`` PDF/TXT walker,
the GUI "LaunchBox media" tab (native Browse + Add/Remove + per-root asset-type
selector + "Check roots" diagnostic), SettingsStore persistence + preset
participation, and the ``state.py`` provider-config merge.

Acceptance coverage (issue #33):
  #1 GUI has a clearly-labeled LaunchBox local-media configuration area.
  #2 Native Browse folder picker used for adding roots.
  #3 Multiple image/media roots; Add/Remove works; removing one keeps others.
  #4 Each image root has an explicit asset/media-type selector.
  #5 Multiple manual roots (Add/Remove); PDF + TXT discoverable.
  #6 Mappings persist across reload and participate in named presets.
  #7 Temporarily-unavailable paths are retained (not deleted) + diagnostic.
  #8 No network egress (assert no sockets opened during a LaunchBox scan).
  #9 Existing non-LaunchBox local media/manual roots still work (no regression).
  #11 New automated tests cover persistence, multiple roots, asset-type mapping,
      unavailable paths, and representative LaunchBox layouts.

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as the other
GUI tests). Deterministic on pytest tmp dirs. No host paths, no real corpus.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from amiga_adf_library_builder import local_media as lm
from amiga_adf_library_builder.gui.settings import (
    Preset,
    Settings,
    SettingsStore,
)
from amiga_adf_library_builder.gui.state import (
    GuiState,
    build_pipeline_kwargs,
    resolve_local_media_config_path,
)
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.paths import PathConfig


# --- helpers -----------------------------------------------------------------


def _make_group(title: str) -> ReleaseGroup:
    rec = ParsedRecord(source_filename=f"{title}.adf", ext="adf", title=title)
    return ReleaseGroup(
        release_key=title.lower(),
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


def _typed_cfg(
    cache: Path,
    *,
    media_roots=(),
    manual_roots=(),
    roots=(),
    preferred=None,
) -> lm.LocalMediaConfig:
    return lm.LocalMediaConfig(
        enabled=True,
        roots=tuple(roots),
        media_roots=tuple(media_roots),
        manual_roots=tuple(manual_roots),
        platform_names=("Commodore Amiga", "Amiga"),
        preferred_image_types=tuple(preferred or lm.DEFAULT_PREFERRED_TYPES),
        recursive=True,
    )


def _img(path: Path, name: str) -> None:
    p = path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    # A tiny valid PNG (1x1) so the provider's optional Pillow/size checks
    # have real bytes to read if it ever inspects the file.
    p.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\r\xe1\xa5\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# --- 1) backend: typed media roots + asset-type mapping ----------------------


def test_typed_media_root_resolves_to_explicit_asset_type(tmp_path: Path):
    """A flat typed media root attributes its whole image set to the configured
    asset type (acceptance #4: explicit per-root asset/media-type mapping)."""
    root = tmp_path / "boxback"
    (root).mkdir()
    _img(root, "Example Space Tactics.png")
    cache = tmp_path / "cache"

    cfg = _typed_cfg(
        cache,
        media_roots=(lm.MediaRoot(path=str(root), asset_type="Box - Back"),),
        # Force "Box - Back" into the priority so resolution can succeed and we
        # can assert the category carried by the result.
        preferred=("Box - Back",),
    )
    prov = lm.LocalMediaProvider(cfg, cache)
    prov.discover()
    res = prov.resolve(_make_group("Example Space Tactics"))
    assert res.found is True
    assert res.category == "Box - Back"


def test_multiple_typed_media_roots_all_scanned(tmp_path: Path):
    """Multiple image/media roots are each scanned independently (acceptance
    #3 backend half: multiple roots supported)."""
    front = tmp_path / "front"
    back = tmp_path / "back"
    front.mkdir()
    back.mkdir()
    _img(front, "Example Space Tactics.png")
    _img(back, "Example Space Tactics.png")
    cache = tmp_path / "cache"

    cfg = _typed_cfg(
        cache,
        media_roots=(
            lm.MediaRoot(path=str(front), asset_type="Box - Front"),
            lm.MediaRoot(path=str(back), asset_type="Box - Back"),
        ),
    )
    prov = lm.LocalMediaProvider(cfg, cache)
    n = prov.discover()
    # One image in each of the two roots => two candidates indexed.
    assert n == 2
    categories = {c.category for c in prov._index}
    assert categories == {"Box - Front", "Box - Back"}


def test_typed_media_root_region_and_game_folder_layout(tmp_path: Path):
    """Representative nested LaunchBox layout under a typed media root:
    ``<root>/<Region>/<Game Title>/img.png`` — the per-game folder carries the
    identity, the region folder is skipped (acceptance #11: representative
    layouts)."""
    root = tmp_path / "screens"
    (root / "USA" / "Example Space Tactics").mkdir(parents=True)
    _img(root / "USA" / "Example Space Tactics", "title.png")
    cache = tmp_path / "cache"

    cfg = _typed_cfg(
        cache,
        media_roots=(
            lm.MediaRoot(path=str(root), asset_type="Screenshot - Game Title"),
        ),
    )
    prov = lm.LocalMediaProvider(cfg, cache)
    prov.discover()
    res = prov.resolve(_make_group("Example Space Tactics"))
    assert res.found is True
    assert res.category == "Screenshot - Game Title"


def test_typed_media_root_flat_filename_identity(tmp_path: Path):
    """Flat typed root: the FILENAME stem carries the game identity (no
    per-game folder)."""
    root = tmp_path / "flat"
    root.mkdir()
    _img(root, "Example Space Tactics.png")
    cache = tmp_path / "cache"

    cfg = _typed_cfg(
        cache,
        media_roots=(
            lm.MediaRoot(path=str(root), asset_type="Screenshot - Gameplay"),
        ),
    )
    prov = lm.LocalMediaProvider(cfg, cache)
    prov.discover()
    res = prov.resolve(_make_group("Example Space Tactics"))
    assert res.found is True
    assert res.category == "Screenshot - Gameplay"


# --- 2) backend: manual roots PDF/TXT discovery ------------------------------


def test_discover_manuals_pdf_and_txt(tmp_path: Path):
    """Multiple manual roots; both .pdf and .txt are discovered (acceptance
    #5 backend half: multiple manual roots, PDF + TXT discoverable)."""
    manuals_a = tmp_path / "manuals-a"
    manuals_b = tmp_path / "manuals-b"
    (manuals_a / "Sub").mkdir(parents=True)
    (manuals_a / "Example Space Tactics.pdf").write_bytes(b"%PDF-1.4 x")
    (manuals_a / "Sub" / "Other Game.txt").write_bytes(b"controls text")
    manuals_b.mkdir()
    (manuals_b / "Another Game.pdf").write_bytes(b"%PDF-1.4 y")

    sources = lm.discover_manuals(
        [
            lm.ManualRoot(path=str(manuals_a)),
            lm.ManualRoot(path=str(manuals_b)),
        ]
    )
    stems = {s.path.name for s in sources}
    assert stems == {
        "Example Space Tactics.pdf",
        "Other Game.txt",
        "Another Game.pdf",
    }
    suffixes = {s.suffix for s in sources}
    assert suffixes == {".pdf", ".txt"}
    # Deterministic: sorted by (root, path).
    assert sources == sorted(sources, key=lambda m: (str(m.root), str(m.path)))


def test_discover_manuals_missing_root_skipped(tmp_path: Path):
    """A missing manual root is skipped (not an error); present roots still
    yield their manuals (acceptance #7: unavailable paths retained, not fatal)."""
    present = tmp_path / "present"
    present.mkdir()
    (present / "Example Space Tactics.txt").write_bytes(b"controls")
    sources = lm.discover_manuals(
        [
            lm.ManualRoot(path=str(present)),
            lm.ManualRoot(path=str(tmp_path / "absent-manuals")),
        ]
    )
    assert [s.path.name for s in sources] == ["Example Space Tactics.txt"]


# --- 3) backend: scan_launchbox_roots diagnostics ----------------------------


def test_scan_report_ok_and_missing_roots(tmp_path: Path):
    """scan_launchbox_roots reports ok/missing per root + candidate counts
    (acceptance #7: unavailable paths retained + diagnostic)."""
    media_ok = tmp_path / "media-ok"
    media_ok.mkdir()
    _img(media_ok, "Example Space Tactics.png")
    manual_ok = tmp_path / "manual-ok"
    manual_ok.mkdir()
    (manual_ok / "Example Space Tactics.pdf").write_bytes(b"%PDF-1.4")
    missing_media = tmp_path / "media-gone"
    missing_manual = tmp_path / "manual-gone"

    cfg = _typed_cfg(
        tmp_path / "cache",
        media_roots=(
            lm.MediaRoot(path=str(media_ok), asset_type="Box - Front"),
            lm.MediaRoot(path=str(missing_media), asset_type="Box - Back"),
        ),
        manual_roots=(
            lm.ManualRoot(path=str(manual_ok)),
            lm.ManualRoot(path=str(missing_manual)),
        ),
    )
    report = lm.scan_launchbox_roots(cfg)

    # Two missing roots (one media, one manual) are reported, never deleted.
    assert len(report.missing_roots) == 2
    missing_paths = {r.path for r in report.missing_roots}
    assert missing_paths == {str(missing_media), str(missing_manual)}
    # The present roots are scanned with correct candidate counts.
    ok = {r.path: r for r in report.roots if r.status == "ok"}
    assert ok[str(media_ok)].file_count == 1
    assert ok[str(media_ok)].asset_type == "Box - Front"
    assert ok[str(manual_ok)].file_count == 1
    assert report.total_image_candidates == 1
    assert report.total_manual_files == 1
    # Diagnostics lines name the missing roots explicitly.
    lines = report.to_lines()
    assert any(str(missing_media) in ln for ln in lines)
    assert any(str(missing_manual) in ln for ln in lines)


def test_scan_report_is_read_only_no_network(monkeypatch, tmp_path: Path):
    """No socket is ever opened while scanning/resolving LaunchBox roots
    (acceptance #8: no network egress)."""
    calls = []

    def _blocked_socket(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("network is blocked in this test")

    monkeypatch.setattr(socket, "socket", _blocked_socket)

    media = tmp_path / "media"
    media.mkdir()
    _img(media, "Example Space Tactics.png")
    cfg = _typed_cfg(
        tmp_path / "cache",
        media_roots=(lm.MediaRoot(path=str(media), asset_type="Box - Front"),),
        preferred=("Box - Front",),
    )
    # scan + provider discover + resolve must all run without any socket.
    report = lm.scan_launchbox_roots(cfg)
    prov = lm.LocalMediaProvider(cfg, tmp_path / "cache")
    prov.discover()
    prov.resolve(_make_group("Example Space Tactics"))
    assert report.total_image_candidates == 1
    assert calls == []


# --- 4) backend: regression — legacy roots still work ------------------------


def test_legacy_launchbox_roots_still_work(tmp_path: Path):
    """Acceptance #9: the existing non-LaunchBox (legacy ``roots``) semantics
    are unchanged and still resolve."""
    root = tmp_path / "lb"
    (root / "Images" / "Commodore Amiga" / "Screenshot - Game Title"
     / "Example Space Tactics").mkdir(parents=True)
    _img(
        root / "Images" / "Commodore Amiga" / "Screenshot - Game Title",
        "Example Space Tactics.png",
    )
    cache = tmp_path / "cache"
    cfg = _typed_cfg(cache, roots=(str(root),))
    prov = lm.LocalMediaProvider(cfg, cache)
    prov.discover()
    res = prov.resolve(_make_group("Example Space Tactics"))
    assert res.found is True
    assert res.category == "Screenshot - Game Title"


def test_legacy_roots_coexist_with_typed_media_roots(tmp_path: Path):
    """Acceptance #9: legacy roots and typed media_roots coexist; both are
    scanned and neither shadows the other."""
    root = tmp_path / "lb"
    (root / "Images" / "Commodore Amiga" / "Box - Front").mkdir(parents=True)
    _img(root / "Images" / "Commodore Amiga" / "Box - Front", "Example Space Tactics.png")
    typed = tmp_path / "typed"
    typed.mkdir()
    _img(typed, "Example Space Tactics.png")
    cache = tmp_path / "cache"
    cfg = _typed_cfg(
        cache,
        roots=(str(root),),
        media_roots=(lm.MediaRoot(path=str(typed), asset_type="Fanart"),),
    )
    prov = lm.LocalMediaProvider(cfg, cache)
    n = prov.discover()
    # One candidate from each source.
    assert n == 2


# --- 5) backend: from_dict parsing (string vs table entries) -----------------


def test_from_dict_media_roots_string_and_table(tmp_path: Path):
    """``media_roots`` entries accept bare strings (category inferred from the
    folder name) AND ``{path, asset_type}`` tables."""
    data = {
        "enabled": True,
        "media_roots": [
            # bare string: folder name "Box - Back" is a recognized category.
            str(tmp_path / "Box - Back"),
            # explicit table with a non-inferable folder name.
            {"path": str(tmp_path / "my-media"), "asset_type": "Clear Logo"},
        ],
        "manual_roots": [
            str(tmp_path / "manuals"),
            {"path": str(tmp_path / "more-manuals")},
        ],
    }
    cfg = lm.LocalMediaConfig.from_dict(data)
    assert cfg.enabled is True
    assert len(cfg.media_roots) == 2
    assert cfg.media_roots[0].asset_type == "Box - Back"
    assert cfg.media_roots[1].asset_type == "Clear Logo"
    assert [m.path for m in cfg.manual_roots] == [
        str(tmp_path / "manuals"),
        str(tmp_path / "more-manuals"),
    ]


def test_from_dict_bare_string_unknown_folder_defaults_to_box_front(tmp_path: Path):
    data = {
        "enabled": True,
        "media_roots": [str(tmp_path / "totally-unrelated-name")],
    }
    cfg = lm.LocalMediaConfig.from_dict(data)
    assert cfg.media_roots[0].asset_type == lm.DEFAULT_MEDIA_ROOT_ASSET_TYPE


def test_from_dict_old_file_without_mappings_loads_empty(tmp_path: Path):
    """Backward compatibility: an old config with NO media/manual roots loads
    with empty tuples (no crash, no regression)."""
    cfg = lm.LocalMediaConfig.from_dict({"enabled": True, "roots": ["/x/y"]})
    assert cfg.media_roots == ()
    assert cfg.manual_roots == ()
    assert cfg.roots == ("/x/y",)


# --- 6) GUI: settings persistence + preset participation ---------------------


def test_settings_round_trip_launchbox_mappings(tmp_path: Path):
    """Acceptance #6: LaunchBox mappings persist across save/reload."""
    path = tmp_path / "gui-settings.toml"
    store = SettingsStore(path)
    store.load()
    store.update(
        launchbox_media_roots=[
            {"path": "/data/box/front", "asset_type": "Box - Front"},
            {"path": "/data/box/back", "asset_type": "Box - Back"},
        ],
        launchbox_manual_roots=["/data/manuals", "/data/manuals-2"],
    )
    s2 = SettingsStore(path).load()
    assert s2.launchbox_media_roots == [
        {"path": "/data/box/front", "asset_type": "Box - Front"},
        {"path": "/data/box/back", "asset_type": "Box - Back"},
    ]
    assert s2.launchbox_manual_roots == ["/data/manuals", "/data/manuals-2"]


def test_settings_presets_carry_launchbox_mappings(tmp_path: Path):
    """Acceptance #6 (preset participation): a named preset captures and
    restores the LaunchBox mappings."""
    path = tmp_path / "gui-settings.toml"
    store = SettingsStore(path)
    store.load()
    store.save_preset(
        Preset(
            name="lb-lib",
            library_root="/data/lib",
            launchbox_media_roots=[
                {"path": "/data/fanart", "asset_type": "Fanart"}
            ],
            launchbox_manual_roots=["/data/manuals"],
        )
    )
    s2 = SettingsStore(path).load()
    assert "lb-lib" in s2.presets
    p = s2.presets["lb-lib"]
    assert p.launchbox_media_roots == [{"path": "/data/fanart", "asset_type": "Fanart"}]
    assert p.launchbox_manual_roots == ["/data/manuals"]
    # Delete + reload: gone.
    store.delete_preset("lb-lib")
    assert "lb-lib" not in SettingsStore(path).load().presets


def test_settings_old_file_without_mappings_loads_empty(tmp_path: Path):
    """Acceptance #9 (persistence regression): an old settings file written
    without the GH-33 keys still loads, with empty mapping lists."""
    path = tmp_path / "gui-settings.toml"
    store = SettingsStore(path)
    store.load()
    store.update(theme="dark", default_library_root="/data/lib")
    # Simulate an OLD file: strip the GH-33 keys from the serialized form.
    import tomllib

    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    gui = data.get("gui", {})
    gui.pop("launchbox_media_roots", None)
    gui.pop("launchbox_manual_roots", None)
    import tomli_w

    with open(path, "wb") as fh:
        tomli_w.dump(data, fh)
    s = SettingsStore(path).load()
    assert s.launchbox_media_roots == []
    assert s.launchbox_manual_roots == []
    assert s.theme == "dark"


def test_settings_file_never_carries_secret_keys(tmp_path: Path):
    """The settings file must never serialize secret-shaped keys, even with
    LaunchBox mappings present."""
    path = tmp_path / "gui-settings.toml"
    store = SettingsStore(path)
    store.load()
    store.update(
        launchbox_media_roots=[{"path": "/data/front", "asset_type": "Box - Front"}],
        launchbox_manual_roots=["/data/manuals"],
    )
    text = path.read_text(encoding="utf-8")
    for forbidden in ("token", "api_key", "secret", "password", "bearer"):
        assert forbidden not in text.lower(), f"secret key leaked: {forbidden}"


# --- 7) GUI: state -> provider-config merge ----------------------------------


def test_resolve_local_media_no_mappings_returns_unchanged(tmp_path: Path):
    """Acceptance #9 (CLI<->GUI equivalence): with NO GUI mappings, the
    provider-config path is returned unchanged (pipeline behavior identical)."""
    state = GuiState(library_root="/data/lib", provider_config_path="")
    assert resolve_local_media_config_path(state) is None

    op_cfg = tmp_path / "op-provider.toml"
    op_cfg.write_text("enabled = true\n")
    state2 = GuiState(library_root="/data/lib", provider_config_path=str(op_cfg))
    assert resolve_local_media_config_path(state2) == str(op_cfg)


def test_resolve_local_media_writes_merged_config(tmp_path: Path):
    """With GUI mappings, a GUI-managed merged [local_media] config is written
    to the cache dir and returned; it carries enabled + both mapping sets."""
    import tomllib

    state = GuiState(
        library_root="/data/lib",
        launchbox_media_roots=[{"path": "/data/front", "asset_type": "Box - Front"}],
        launchbox_manual_roots=["/data/manuals"],
    )
    cache = tmp_path / "cache"
    out = resolve_local_media_config_path(state, cache_dir=cache)
    assert out is not None
    outp = Path(out)
    assert outp.parent == cache
    with open(outp, "rb") as fh:
        doc = tomllib.load(fh)
    lm_table = doc["local_media"]
    assert lm_table["enabled"] is True
    assert lm_table["media_roots"] == [{"path": "/data/front", "asset_type": "Box - Front"}]
    assert lm_table["manual_roots"] == ["/data/manuals"]


def test_resolve_local_media_merges_operator_config(tmp_path: Path):
    """When an operator provider config exists, its other tables are preserved
    and its [local_media] is merged with the GUI mappings (GUI wins)."""
    import tomllib

    op_cfg = tmp_path / "op.toml"
    op_cfg.write_text(
        "[rtfm]\nenabled = true\ntemplate = \"controls-first\"\n"
        "\n"
        "[local_media]\nenabled = false\n"
        'roots = ["/legacy"]\n'
    )
    state = GuiState(
        library_root="/data/lib",
        provider_config_path=str(op_cfg),
        launchbox_media_roots=[{"path": "/data/front", "asset_type": "Box - Front"}],
        launchbox_manual_roots=["/data/manuals"],
    )
    out = resolve_local_media_config_path(state, cache_dir=tmp_path / "cache")
    with open(Path(out), "rb") as fh:
        doc = tomllib.load(fh)
    # The operator's unrelated [rtfm] table survives the merge.
    assert doc["rtfm"]["template"] == "controls-first"
    # GUI mappings win for local_media, and legacy roots are preserved.
    lm_table = doc["local_media"]
    assert lm_table["enabled"] is True
    assert lm_table["media_roots"] == [{"path": "/data/front", "asset_type": "Box - Front"}]
    assert lm_table["manual_roots"] == ["/data/manuals"]
    assert lm_table["roots"] == ["/legacy"]


def test_build_pipeline_kwargs_forwards_merged_local_media_path(tmp_path: Path):
    """build_pipeline_kwargs routes the merged GUI local-media config into
    ``local_media_config_path`` (acceptance #6 end-to-end wiring)."""
    state = GuiState(
        library_root="/data/lib",
        launchbox_media_roots=[{"path": "/data/front", "asset_type": "Box - Front"}],
        launchbox_manual_roots=["/data/manuals"],
    )
    cfg = PathConfig(
        library_root=Path("/data/lib"),
        original_dir=Path("/data/lib/original"),
        staging_dir=Path("/data/lib/work/staging"),
        output_dir=Path("/data/lib/output"),
        quarantine_dir=Path("/data/lib/quarantine"),
        approvals_dir=Path("/data/lib/approvals"),
        reports_dir=Path("/data/lib/reports"),
        logs_dir=Path("/data/lib/logs"),
        cache_dir=Path("/data/lib/cache"),
    )
    kwargs = build_pipeline_kwargs(
        state, cfg, config_path=None, activity=None,
        cache_dir=tmp_path / "cache",
    )
    # A merged config file was produced and passed through.
    assert kwargs["local_media_config_path"] is not None
    assert Path(kwargs["local_media_config_path"]).is_file()
    # Without GUI mappings, it stays the operator's config path (None here).
    bare = GuiState(library_root="/data/lib")
    bare_kwargs = build_pipeline_kwargs(
        bare, cfg, config_path=None, activity=None, cache_dir=tmp_path / "cache"
    )
    assert bare_kwargs["local_media_config_path"] is None


# --- 8) GUI: MainWindow LaunchBox tab (offscreen) ----------------------------


@pytest.fixture
def gui_base(tmp_path: Path):
    return tmp_path / "issue33-base"


def _make_window(base_dir: Path):
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui import MainWindow
    from amiga_adf_library_builder.gui.layout import PortablePaths
    from amiga_adf_library_builder.gui.secrets import SecretStore

    app = QApplication.instance() or QApplication([])  # noqa: F841
    pp = PortablePaths(base_dir=base_dir)
    pp.ensure_all()
    return MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=None,
    )


def test_launchbox_tab_present_and_labeled(gui_base):
    """Acceptance #1: the GUI has a clearly-labeled LaunchBox local-media
    configuration area (a tab labeled "LaunchBox media")."""
    mw = _make_window(Path(gui_base))
    tabs = mw.findChild(
        __import__("PySide6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget
    )
    labels = [tabs.tabText(i) for i in range(tabs.count())]
    assert "LaunchBox media" in labels, f"LaunchBox tab missing; tabs={labels}"
    mw.close()


def test_launchbox_add_remove_multiple_mappings(gui_base):
    """Acceptance #3 + #4: multiple image roots can be added/removed (removing
    one keeps the others), and manual roots likewise. The native Browse picker
    is mocked offscreen (it is a QFileDialog.getExistingDirectory call)."""
    from unittest import mock

    mw = _make_window(Path(gui_base))

    front = Path(gui_base) / "front"
    back = Path(gui_base) / "back"
    manuals = Path(gui_base) / "manuals"
    for d in (front, back, manuals):
        d.mkdir(parents=True, exist_ok=True)

    # Mock the native folder picker (acceptance #2) to return our dirs.
    with mock.patch(
        "amiga_adf_library_builder.gui.main_window.QFileDialog.getExistingDirectory",
        side_effect=[str(front), str(back), str(manuals), ""],
    ):
        # Add two image roots.
        mw._lb_add_media_root()
        mw._lb_add_media_root()
        assert mw._lb_media_table.rowCount() == 2
        # Add one manual root.
        mw._lb_add_manual_root()
        assert mw._lb_manual_list.count() == 1
        # Fourth click returns "" (user cancelled) — must be a no-op.
        mw._lb_add_media_root()
        assert mw._lb_media_table.rowCount() == 2

    # Each image row has an explicit asset-type combo (acceptance #4).
    for row in range(mw._lb_media_table.rowCount()):
        combo = mw._lb_media_table.cellWidget(row, 1)
        from PySide6.QtWidgets import QComboBox

        assert isinstance(combo, QComboBox)
        assert combo.count() == len(lm.LAUNCHBOX_IMAGE_CATEGORIES)
        # The default asset type is present and selectable.
        assert combo.findText(lm.DEFAULT_MEDIA_ROOT_ASSET_TYPE) >= 0

    # Remove the FIRST image root; the second must survive (acceptance #3).
    mw._lb_media_table.selectRow(0)
    mw._lb_remove_media_root()
    assert mw._lb_media_table.rowCount() == 1
    assert mw._lb_media_table.item(0, 0).text() == str(back)

    # Remove the manual root.
    mw._lb_manual_list.setCurrentRow(0)
    mw._lb_remove_manual_root()
    assert mw._lb_manual_list.count() == 0
    mw.close()


def test_launchbox_check_roots_diagnostic(gui_base):
    """Acceptance #7: the "Check roots" button runs the read-only scan and
    reports ok/missing per root; missing roots are surfaced, never deleted."""
    from unittest import mock

    mw = _make_window(Path(gui_base))
    present = Path(gui_base) / "present"
    present.mkdir(parents=True, exist_ok=True)
    (present / "Example Space Tactics.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    )
    with mock.patch(
        "amiga_adf_library_builder.gui.main_window.QFileDialog.getExistingDirectory",
        side_effect=[str(present), str(Path(gui_base) / "absent")],
    ):
        mw._lb_add_media_root()
        mw._lb_add_media_root()
    # Set the second (absent) root's asset type so the diagnostic names it.
    from PySide6.QtWidgets import QComboBox  # noqa: F401

    mw._lb_check_roots()
    text = mw._lb_diag_label.text()
    assert str(present) in text
    # The missing root is reported (retained, not deleted).
    assert "absent" in text
    # The mappings are still present (not deleted on absence).
    assert mw._lb_media_table.rowCount() == 2
    mw.close()


def test_launchbox_mappings_persist_across_reopen(gui_base):
    """Acceptance #6 (GUI): mappings added in the GUI persist across a close +
    reopen of the MainWindow (same settings file)."""
    from unittest import mock

    base = Path(gui_base)
    front = base / "front"
    manuals = base / "manuals"
    front.mkdir(parents=True, exist_ok=True)
    manuals.mkdir(parents=True, exist_ok=True)

    mw1 = _make_window(base)
    with mock.patch(
        "amiga_adf_library_builder.gui.main_window.QFileDialog.getExistingDirectory",
        side_effect=[str(front), str(manuals)],
    ):
        mw1._lb_add_media_root()
        mw1._lb_add_manual_root()
    mw1.show()
    mw1.close()  # closeEvent persists

    mw2 = _make_window(base)
    # Restored: one media row, one manual row, correct paths + asset type.
    assert mw2._lb_media_table.rowCount() == 1
    assert mw2._lb_media_table.item(0, 0).text() == str(front)
    combo = mw2._lb_media_table.cellWidget(0, 1)
    assert combo is not None and combo.currentText() == lm.DEFAULT_MEDIA_ROOT_ASSET_TYPE
    assert mw2._lb_manual_list.count() == 1
    assert mw2._lb_manual_list.item(0).text() == str(manuals)
    mw2.close()


def test_launchbox_missing_root_restored_kept_with_diagnostic(gui_base):
    """Acceptance #7 (GUI): a persisted mapping whose path is absent on this
    machine is RESTORED (kept) and surfaced as a status diagnostic — never
    silently cleared."""
    from unittest import mock

    base = Path(gui_base)
    present = base / "present"
    present.mkdir(parents=True, exist_ok=True)
    mw1 = _make_window(base)
    with mock.patch(
        "amiga_adf_library_builder.gui.main_window.QFileDialog.getExistingDirectory",
        side_effect=[str(present), str(base / "gone-later")],
    ):
        mw1._lb_add_media_root()
        mw1._lb_add_media_root()
    mw1.show()
    mw1.close()

    # Simulate the second root's folder no longer existing.
    mw2 = _make_window(base)
    assert mw2._lb_media_table.rowCount() == 2  # both mappings kept
    # The missing path is still in the widget (not deleted).
    rows = [mw2._lb_media_table.item(r, 0).text() for r in range(mw2._lb_media_table.rowCount())]
    assert str(base / "gone-later") in rows
    # A status diagnostic was set.
    assert "not found" in mw2._status_label.text()
    mw2.close()
