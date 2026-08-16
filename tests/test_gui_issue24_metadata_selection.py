"""GH-24 regression tests: independent artwork / manuals-RTFM selection.

Two operator choices, each default ON:

  * ``include_artwork``       -- whether to SEARCH for cover artwork at all
    (local lookup, configured local libraries, and online acquisition). Off
    means zero provider/network work for artwork.
  * ``include_manuals_rtfm``  -- whether to build the deterministic RTFM
    manuals at all, even when an [rtfm] config is present and enabled.

Both are DISTINCT from ``require_artwork`` (the export-stop gate). These tests
lock the fixed design in:

  * the per-group enrich gate: artwork off -> no lookup/download/processing and
    an ``ARTWORK_SKIPPED`` "disabled by operator" event; artwork on -> an
    existing approved master is still processed (regression guard).
  * the pipeline RTFM gate: an enabled [rtfm] config + ``include_manuals_rtfm``
    off builds nothing (``selected`` False, ``built`` empty); on builds.
  * the GUI->pipeline kwargs bridge forwards both flags verbatim.
  * the settings store defaults both flags ON and round-trips them.
  * the two plain-language checkboxes exist, default ON, and persist.

Fully synthetic: no maintainer-private corpus, names, hashes, or machine state.
The GUI test relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as the
Issue #22 wording tests).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from amiga_adf_library_builder.enrich import EnrichCategory, enrich_group
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ScanRecord
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.pipeline import run_pipeline
from amiga_adf_library_builder.paths import resolve_config


# --- shared fixtures ---------------------------------------------------------

def _single_group():
    recs = [parse_filename("Example - Space Tactics (Disk 1 of 1).adf")]
    return group_records(recs)[0]


def _scans(group, tmp_path):
    return {
        r.source_filename: ScanRecord(
            path=tmp_path / r.source_filename,
            filename=r.source_filename,
            size=901120,
            sha256="abc",
            scanned_at="t",
        )
        for r in group.records
    }


def _enrich_kwargs(tmp_path):
    return dict(
        nfo_dir=tmp_path / "nfo",
        artwork_original_dir=tmp_path / "art",
        artwork_processed_dir=tmp_path / "proc",
    )


def _categories(res):
    return [e.category for e in res.events]


# --- enrich-level artwork gate ----------------------------------------------

def test_enrich_group_artwork_off_skips_all_artwork_work(tmp_path: Path) -> None:
    """include_artwork=False: no lookup/download/processing, one skipped event."""
    g = _single_group()
    scans = _scans(g, tmp_path)
    kw = _enrich_kwargs(tmp_path)
    # Do NOT create artwork_original_dir: if the gate wrongly ran the lookup it
    # would be a no-op anyway, so this is belt-and-braces.
    res = enrich_group(g, nfo_dir=kw["nfo_dir"], scans=scans, include_artwork=False,
                       artwork_original_dir=kw["artwork_original_dir"],
                       artwork_processed_dir=kw["artwork_processed_dir"])
    assert res.artwork_master is None
    assert res.artwork_resized is None
    assert res.resized is False
    assert res.artwork_missing is True
    # Exactly one artwork event, and it is the operator-disabled skip.
    cats = _categories(res)
    assert EnrichCategory.ARTWORK_SKIPPED in cats
    skipped = [e for e in res.events if e.category == EnrichCategory.ARTWORK_SKIPPED]
    assert len(skipped) == 1
    assert "disabled by operator" in skipped[0].detail
    # No processed artwork file was written anywhere (proc dir never created).
    assert not (tmp_path / "proc").is_dir()
    # NFO is still written (artwork selection must not suppress the NFO).
    assert res.nfo_path is not None and res.nfo_path.exists()


def test_enrich_group_artwork_on_still_processes_existing_master(tmp_path: Path) -> None:
    """Regression guard: with the flag ON the existing-master path is unchanged."""
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        pytest.skip("Pillow not installed; existing-master artwork path untested here")
    g = _single_group()
    scans = _scans(g, tmp_path)
    art_dir = tmp_path / "art"
    art_dir.mkdir(parents=True)
    # A valid, decodable master image matching the release title.
    Image.new("RGB", (320, 240), "red").save(art_dir / "Example Space Tactics.jpg")
    kw = _enrich_kwargs(tmp_path)
    res = enrich_group(g, nfo_dir=kw["nfo_dir"], scans=scans, include_artwork=True,
                       artwork_original_dir=kw["artwork_original_dir"],
                       artwork_processed_dir=kw["artwork_processed_dir"])
    assert res.artwork_master is not None
    assert res.artwork_master.name == "Example Space Tactics.jpg"
    assert res.resized is True
    assert res.artwork_resized is not None
    assert res.artwork_resized.exists()
    assert res.artwork_missing is False
    # The success path must not be the operator-disabled skip.
    assert not any(
        e.category == EnrichCategory.ARTWORK_SKIPPED and "disabled by operator" in e.detail
        for e in res.events
    )


# --- pipeline-level RTFM gate ------------------------------------------------

def _write_rtfm_config(tmp_path: Path) -> tuple[Path, Path]:
    """Synthetic library_root + enabled [rtfm] config. Returns (toml, root)."""
    library_root = tmp_path / "library"
    original_dir = library_root / "original"
    original_dir.mkdir(parents=True)
    (original_dir / "Synthetic Quest III (Disk 1 of 1).adf").write_bytes(b"\x00" * 16)
    instructions_root = tmp_path / "instructions"
    instructions_root.mkdir()
    (instructions_root / "Synthetic Quest III.txt").write_bytes(b"Fire: button 1\n")
    config_toml = tmp_path / "rtfm.toml"
    config_toml.write_text(
        'library_root = "{lr}"\n'
        "\n"
        "[rtfm]\n"
        "enabled = true\n"
        'template = "controls-first"\n'
        "\n"
        "[rtfm.local]\n"
        'instructions = "{ins}"\n'
        "\n".format(lr=library_root, ins=instructions_root)
    )
    return config_toml, library_root


def test_pipeline_rtfm_config_present_but_deselected_builds_nothing(tmp_path: Path) -> None:
    config_toml, _ = _write_rtfm_config(tmp_path)
    data_root = tmp_path / "library"
    result = run_pipeline(
        cfg=resolve_config(library_root=str(data_root))[0],
        online=False,
        rtfm_config_path=str(config_toml),
        include_manuals_rtfm=False,
    )
    assert result["include_manuals_rtfm"] is False
    # An [rtfm] config IS present, but the operator deselected it.
    assert result["rtfm"]["configured"] is True
    assert result["rtfm"]["selected"] is False
    assert result["rtfm"]["built"] == []
    # Nothing was written under assets/rtfm.
    rtfm_dir = data_root / "assets" / "rtfm"
    if rtfm_dir.is_dir():
        assert list(rtfm_dir.iterdir()) == []


def test_pipeline_rtfm_config_present_and_selected_builds(tmp_path: Path) -> None:
    """Regression guard: flag ON (default) still builds the .rtfm sidecar."""
    config_toml, _ = _write_rtfm_config(tmp_path)
    data_root = tmp_path / "library"
    result = run_pipeline(
        cfg=resolve_config(library_root=str(data_root))[0],
        online=False,
        rtfm_config_path=str(config_toml),
        include_manuals_rtfm=True,
    )
    assert result["include_manuals_rtfm"] is True
    assert result["rtfm"]["configured"] is True
    assert result["rtfm"]["selected"] is True
    assert len(result["rtfm"]["built"]) >= 1
    built = Path(result["rtfm"]["built"][0])
    assert built.is_file()
    assert built.parent.name == "rtfm"


def test_pipeline_result_records_both_selection_flags(tmp_path: Path) -> None:
    data_root = tmp_path / "lib2"
    (data_root / "original").mkdir(parents=True)
    (data_root / "original" / "Solo Game (Disk 1 of 1).adf").write_bytes(b"\x00" * 16)
    result = run_pipeline(
        cfg=resolve_config(library_root=str(data_root))[0],
        online=False,
        include_artwork=False,
        include_manuals_rtfm=False,
    )
    assert result["include_artwork"] is False
    assert result["include_manuals_rtfm"] is False
    assert result["rtfm"]["configured"] is False
    assert result["rtfm"]["selected"] is False


# --- GUI -> pipeline kwargs bridge -------------------------------------------

def test_build_pipeline_kwargs_forwards_both_flags() -> None:
    from amiga_adf_library_builder.gui.state import (
        GuiState,
        build_pipeline_kwargs,
    )

    from amiga_adf_library_builder.paths import PathConfig

    state = GuiState(library_root="/data/lib")
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
    kwargs = build_pipeline_kwargs(state, cfg, config_path=None, activity=None)
    # Defaults are ON.
    assert kwargs["include_artwork"] is True
    assert kwargs["include_manuals_rtfm"] is True
    # Toggling the state flows through verbatim.
    off = GuiState(library_root="/data/lib", include_artwork=False, include_manuals_rtfm=False)
    kwargs_off = build_pipeline_kwargs(off, cfg, config_path=None, activity=None)
    assert kwargs_off["include_artwork"] is False
    assert kwargs_off["include_manuals_rtfm"] is False


# --- settings store ----------------------------------------------------------

def test_settings_default_both_flags_on() -> None:
    from amiga_adf_library_builder.gui.settings import Settings

    s = Settings()
    assert s.include_artwork is True
    assert s.include_manuals_rtfm is True


def test_old_settings_file_defaults_both_flags_on(tmp_path: Path) -> None:
    from amiga_adf_library_builder.gui.settings import SettingsStore

    path = tmp_path / "settings.toml"
    store = SettingsStore(path)
    store.load()
    store.update(
        default_library_root="/data/lib",
        online=True,
        # deliberately NOT passing include_artwork / include_manuals_rtfm,
        # simulating a settings file written before GH-24.
    )
    store.save()
    reloaded = SettingsStore(path).load()
    assert reloaded.include_artwork is True
    assert reloaded.include_manuals_rtfm is True


def test_settings_round_trips_both_flags(tmp_path: Path) -> None:
    from amiga_adf_library_builder.gui.settings import SettingsStore

    path = tmp_path / "settings.toml"
    store = SettingsStore(path)
    store.load()
    store.update(default_library_root="/data/lib", include_artwork=False, include_manuals_rtfm=True)
    store.save()
    reloaded = SettingsStore(path).load()
    assert reloaded.include_artwork is False
    assert reloaded.include_manuals_rtfm is True


# --- GUI checkboxes ----------------------------------------------------------

def _make_window(base_dir: Path):
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui import MainWindow
    from amiga_adf_library_builder.gui.layout import PortablePaths
    from amiga_adf_library_builder.gui.secrets import SecretStore
    from amiga_adf_library_builder.gui.settings import SettingsStore

    app = QApplication.instance() or QApplication([])  # noqa: F841
    pp = PortablePaths(base_dir=base_dir)
    pp.ensure_all()
    return MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=None,
    )


def test_gui_independent_metadata_checkboxes_present_default_on(tmp_path: Path) -> None:
    mw = _make_window(tmp_path / "issue24-base")
    assert hasattr(mw, "_cb_include_artwork"), "missing 'Include artwork' checkbox"
    assert hasattr(mw, "_cb_include_manuals"), "missing 'Include manuals (RTFM)' checkbox"
    # Plain-language labels, distinct from the export gate "Require artwork...".
    assert mw._cb_include_artwork.text() == "Include artwork"
    assert mw._cb_include_manuals.text() == "Include manuals (RTFM)"
    # Both default ON.
    assert mw._cb_include_artwork.isChecked() is True
    assert mw._cb_include_manuals.isChecked() is True
    mw.close()


def test_gui_independent_metadata_checkboxes_persist(tmp_path: Path) -> None:
    mw = _make_window(tmp_path / "issue24-persist")
    # Uncheck both, then persist the defaults.
    mw._cb_include_artwork.setChecked(False)
    mw._cb_include_manuals.setChecked(False)
    assert mw._persist_defaults() is True

    from amiga_adf_library_builder.gui.settings import SettingsStore
    from amiga_adf_library_builder.gui.layout import PortablePaths

    pp = PortablePaths(base_dir=tmp_path / "issue24-persist")
    pp.ensure_all()
    reloaded = SettingsStore(pp.settings_file()).load()
    assert reloaded.include_artwork is False
    assert reloaded.include_manuals_rtfm is False
    mw.close()
