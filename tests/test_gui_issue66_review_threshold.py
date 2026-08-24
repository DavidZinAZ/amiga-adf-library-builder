"""GH-66 regression tests: review-band local matches must surface a prompt.

GH-66 ([Bug]): local-asset matches whose score falls in the review band
(``review_threshold <= score < auto_match_threshold``), and near-tie matches,
are correctly routed to the provider's persistent review queue by the core
(``LocalMediaProvider.add_to_review_queue`` -> ``review_queue.json``). But the
GUI never surfaced them: the "Review N ambiguous matches" button was created
DISABLED and nothing after a run ever re-evaluated it, so review-band matches
vanished without a selection prompt.

Root cause (two independent defects, both fixed in ``gui/main_window.py``):

  1. The Review button started ``setEnabled(False)`` and only
     ``MatchReviewDialog._advance_queue`` (unreachable while the button is
     disabled) ever called ``update_review_button`` -- ``_on_finished`` never
     refreshed it, so it stayed "Review 0" / disabled after every run.

  2. ``_open_match_review`` (and the new post-run refresh) must anchor the
     local-media provider to the directory the PIPELINE ran against
     (``cfg.artwork_original_dir``), not the portable cache dir. The pipeline
     builds ``LocalMediaProvider(lm_cfg, cfg.artwork_original_dir)`` and the
     queue lives at ``cfg.artwork_original_dir / "review_queue.json"``. Reading
     a different directory means reading an empty/stale queue.

This test locks in both:

  * after a run that produced review-band matches, the Review button becomes
    ENABLED with the correct count, reading the queue from
    ``cfg.artwork_original_dir`` (NOT the portable cache dir, NOT the
    ``PathConfig.cache_dir``);
  * the button stays disabled when local media is unconfigured, disabled in
    config, or the run produced no review-band matches (no spurious enable);
  * a follow-up run that changes the queue count updates the button (the
    cached provider is invalidated per run);
  * with GH-33 GUI mappings present, the post-run review UI resolves the SAME
    GUI-managed merged provider config the pipeline used.

Headless: relies on ``QT_QPA_PLATFORM=offscreen`` (same pattern as
tests/test_gui_issue43_export_controls.py).
"""

from __future__ import annotations

import json
import os

# Offscreen BEFORE any PySide6 import so the file is runnable standalone as
# well as under CI (CI also sets QT_QPA_PLATFORM; setdefault never overrides).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from amiga_adf_library_builder.gui import MainWindow
from amiga_adf_library_builder.gui.layout import PortablePaths
from amiga_adf_library_builder.gui.secrets import SecretStore
from amiga_adf_library_builder.gui.settings import SettingsStore
from amiga_adf_library_builder.gui.state import GuiState, build_path_config_from_gui_state
from amiga_adf_library_builder.paths import PathConfig

QUEUE_SCHEMA = "local-media-review-queue/1"


# --- fixtures / harness -------------------------------------------------------


@pytest.fixture
def qt_app():
    """Ensure a single QApplication for the whole module (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


def _make_window(base_dir: Path, config_path: str | None) -> MainWindow:
    pp = PortablePaths(base_dir=base_dir)
    pp.ensure_all()
    return MainWindow(
        portable_paths=pp,
        settings_store=SettingsStore(pp.settings_file()),
        secret_store=SecretStore.with_vault(pp.vault_file()),
        config_path=config_path,
    )


def _write_queue(dir_path: Path, items: list[dict]) -> Path:
    """Write a review_queue.json in the exact format the provider persists."""
    dir_path.mkdir(parents=True, exist_ok=True)
    qfile = dir_path / "review_queue.json"
    qfile.write_text(
        json.dumps(
            {"schema": QUEUE_SCHEMA, "items": items},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return qfile


def _item(title: str, key: str, path: str, conf: float = 0.78,
          category: str = "Box - Front", reason: str = "score in review band") -> dict:
    return {
        "group_title": title,
        "group_release_key": key,
        "candidate_path": path,
        "category": category,
        "confidence": conf,
        "reason": reason,
    }


def _enabled_config_toml(base: Path) -> Path:
    """A provider config whose [local_media] table is enabled (no roots)."""
    cfg = base / "provider-config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "[local_media]\nenabled = true\n",
        encoding="utf-8",
    )
    return cfg


def _disabled_config_toml(base: Path) -> Path:
    cfg = base / "provider-config-disabled.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "[local_media]\nenabled = false\n",
        encoding="utf-8",
    )
    return cfg


def _cfg_for(lib_root: Path) -> PathConfig:
    """Build the same PathConfig the worker would (CLI<->GUI equivalence)."""
    return build_path_config_from_gui_state(
        GuiState(library_root=str(lib_root))
    )


# --- 1. pre-run state ---------------------------------------------------------


def test_review_button_disabled_before_any_run(qt_app, tmp_path: Path):
    base = tmp_path / "portable-base"
    lib = tmp_path / "library"
    lib.mkdir()
    mw = _make_window(base, None)
    assert mw._review_button.isEnabled() is False
    assert mw._review_button.text() == "Review 0 ambiguous matches"
    # No run yet -> falls back to the portable cache dir (well-defined state).
    assert mw._last_run_local_media_dir is None
    assert mw._local_media_cache_dir() == mw._paths.cache_dir
    mw.close()


# --- 2. THE regression: a run's review-band queue surfaces ---------------------


def test_review_button_enables_with_run_queue(qt_app, tmp_path: Path):
    base = tmp_path / "portable-base"
    lib = tmp_path / "library"
    lib.mkdir()
    cfg_toml = _enabled_config_toml(base)
    mw = _make_window(base, str(cfg_toml))

    cfg = _cfg_for(lib)
    # The PIPELINE anchors the provider here; the real run queue lives here.
    _write_queue(cfg.artwork_original_dir, [
        _item("Bubble Bobble", "bubble-bobble", "/lb/Bubble Bobble.png", 0.78),
        _item("Lemmings", "lemmings", "/lb/Lemmings.png", 0.72),
    ])
    # A DECOY queue in the portable cache dir (the pre-fix, WRONG directory).
    # The fix must NOT read this one.
    _write_queue(mw._paths.cache_dir, [_item(f"Decoy {i}", f"dec-{i}", f"/d/{i}.png")
                                      for i in range(5)])

    # Simulate the worker finishing a run that produced the queue above.
    mw._last_run_state = GuiState(library_root=str(lib))
    mw._on_finished({"groups": 2, "files_scanned": 4}, "", False, cfg)

    assert mw._review_button.isEnabled() is True
    assert mw._review_button.text() == "Review 2 ambiguous matches"
    # Anchored to the pipeline dir, NOT the portable cache dir, NOT cfg.cache_dir.
    assert mw._local_media_cache_dir() == cfg.artwork_original_dir
    assert mw._local_media_cache_dir() != mw._paths.cache_dir
    assert mw._local_media_cache_dir() != cfg.cache_dir
    # The provider the review UI reads is anchored to the run dir with 2 items.
    provider = mw._build_local_media_provider(mw._local_media_cache_dir())
    assert provider is not None
    assert Path(provider.cache_dir) == cfg.artwork_original_dir
    assert len(provider.get_review_queue()) == 2
    # Resolved the operator provider config (no GUI mappings in this state).
    assert mw._last_run_local_media_config_path == str(cfg_toml)
    mw.close()


def test_review_button_count_updates_on_follow_up_run(qt_app, tmp_path: Path):
    base = tmp_path / "portable-base"
    lib = tmp_path / "library"
    lib.mkdir()
    cfg_toml = _enabled_config_toml(base)
    mw = _make_window(base, str(cfg_toml))
    cfg = _cfg_for(lib)
    mw._last_run_state = GuiState(library_root=str(lib))

    # First run: 2 review-band matches.
    _write_queue(cfg.artwork_original_dir, [
        _item("A", "a", "/lb/A.png"), _item("B", "b", "/lb/B.png"),
    ])
    mw._on_finished({"groups": 2}, "", False, cfg)
    assert mw._review_button.text() == "Review 2 ambiguous matches"

    # Second run: the operator resolved one; queue now holds 1.
    _write_queue(cfg.artwork_original_dir, [_item("B", "b", "/lb/B.png")])
    mw._on_finished({"groups": 2}, "", False, cfg)
    assert mw._review_button.isEnabled() is True
    assert mw._review_button.text() == "Review 1 ambiguous match"
    mw.close()


# --- 3. no spurious enable -----------------------------------------------------


def test_review_button_stays_disabled_without_local_media_config(qt_app, tmp_path: Path):
    base = tmp_path / "portable-base"
    lib = tmp_path / "library"
    lib.mkdir()
    mw = _make_window(base, None)  # no operator provider config at all
    cfg = _cfg_for(lib)
    _write_queue(cfg.artwork_original_dir, [_item("A", "a", "/lb/A.png")])
    mw._last_run_state = GuiState(library_root=str(lib))
    mw._on_finished({"groups": 1}, "", False, cfg)
    # No provider config -> provider is None -> button must stay disabled.
    assert mw._review_button.isEnabled() is False
    assert mw._review_button.text() == "Review 0 ambiguous matches"
    assert mw._build_local_media_provider(mw._local_media_cache_dir()) is None
    mw.close()


def test_review_button_stays_disabled_when_local_media_disabled(qt_app, tmp_path: Path):
    base = tmp_path / "portable-base"
    lib = tmp_path / "library"
    lib.mkdir()
    cfg_toml = _disabled_config_toml(base)
    mw = _make_window(base, str(cfg_toml))
    cfg = _cfg_for(lib)
    _write_queue(cfg.artwork_original_dir, [_item("A", "a", "/lb/A.png")])
    mw._last_run_state = GuiState(library_root=str(lib))
    mw._on_finished({"groups": 1}, "", False, cfg)
    # Config present but [local_media] disabled -> provider None -> disabled.
    assert mw._review_button.isEnabled() is False
    assert mw._review_button.text() == "Review 0 ambiguous matches"
    mw.close()


def test_review_button_stays_disabled_with_empty_queue(qt_app, tmp_path: Path):
    base = tmp_path / "portable-base"
    lib = tmp_path / "library"
    lib.mkdir()
    cfg_toml = _enabled_config_toml(base)
    mw = _make_window(base, str(cfg_toml))
    cfg = _cfg_for(lib)
    # Enabled local media, but the run produced NO review-band matches.
    cfg.artwork_original_dir.mkdir(parents=True, exist_ok=True)
    mw._last_run_state = GuiState(library_root=str(lib))
    mw._on_finished({"groups": 3}, "", False, cfg)
    assert mw._review_button.isEnabled() is False
    assert mw._review_button.text() == "Review 0 ambiguous matches"
    mw.close()


# --- 4. GH-33 GUI mappings resolve the same merged provider config ------------


def test_run_with_gui_mappings_resolves_merged_config(qt_app, tmp_path: Path):
    base = tmp_path / "portable-base"
    lib = tmp_path / "library"
    lib.mkdir()
    cfg_toml = _enabled_config_toml(base)
    mw = _make_window(base, str(cfg_toml))
    cfg = _cfg_for(lib)

    # The run held a LaunchBox media mapping (GH-33). The pipeline merged it
    # into a GUI-managed config under cfg.cache_dir; the review UI must resolve
    # that SAME file (not the operator config) so the enabled flag matches.
    state = GuiState(
        library_root=str(lib),
        launchbox_media_roots=[{"path": "/lb/Images", "asset_type": "Box - Front"}],
    )
    mw._last_run_state = state
    mw._on_finished({"groups": 1}, "", False, cfg)

    expected = cfg.cache_dir / "gui-local-media.toml"
    assert mw._last_run_local_media_config_path == str(expected)
    assert expected.is_file()
    text = expected.read_text(encoding="utf-8")
    assert "enabled = true" in text
    # Local media is enabled via the merged file, so a queue WOULD surface --
    # but this run produced none, so the button stays disabled (no spurious).
    assert mw._review_button.isEnabled() is False
    mw.close()


# --- 5. the match-review dialog reads the run's queue --------------------------


def test_open_match_review_reads_run_queue(qt_app, tmp_path: Path, monkeypatch):
    """The Match Review window must show the run's queue, not a decoy.

    We stub QMessageBox.information/exec so the (modal) dialog never blocks the
    test, and assert the provider handed to it is anchored to the run dir with
    the run's items.
    """
    base = tmp_path / "portable-base"
    lib = tmp_path / "library"
    lib.mkdir()
    cfg_toml = _enabled_config_toml(base)
    mw = _make_window(base, str(cfg_toml))
    cfg = _cfg_for(lib)
    run_items = [
        _item("Bubble Bobble", "bubble-bobble", "/lb/Bubble Bobble.png", 0.78),
        _item("Lemmings", "lemmings", "/lb/Lemmings.png", 0.72),
    ]
    _write_queue(cfg.artwork_original_dir, run_items)
    _write_queue(mw._paths.cache_dir, [_item("Decoy", "dec", "/d/d.png")] * 3)

    mw._last_run_state = GuiState(library_root=str(lib))
    mw._on_finished({"groups": 2}, "", False, cfg)

    # Capture the queue the dialog is constructed with.
    from amiga_adf_library_builder.gui.main_window import MatchReviewDialog

    captured: dict = {}

    def _fake_dialog(parent, provider, queue):
        captured["provider"] = provider
        captured["queue"] = queue

        class _NoExec:
            def exec(self):  # pragma: no cover - not called
                return 0

        return _NoExec()

    monkeypatch.setattr(
        "amiga_adf_library_builder.gui.main_window.MatchReviewDialog", _fake_dialog
    )
    # noqa: F841 - _fake_dialog replaces the class; keep MatchReviewDialog name
    # bound for clarity.
    _ = MatchReviewDialog

    mw._open_match_review()

    assert "provider" in captured, "dialog was not constructed (early return)"
    assert Path(captured["provider"].cache_dir) == cfg.artwork_original_dir
    got_keys = [it.group_release_key for it in captured["queue"]]
    assert got_keys == ["bubble-bobble", "lemmings"]
    # The 3-item decoy in the portable cache dir must NOT be what is shown.
    assert "dec" not in got_keys
    mw.close()
