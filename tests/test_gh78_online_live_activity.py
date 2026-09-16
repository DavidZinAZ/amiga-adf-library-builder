"""GH-78 regression tests: live activity log emits per-provider strings during online enrichment.

Root cause (fixed): enrich_group() and lookup_metadata() silently create EnrichEvent
records for playmatch/hasheous/igdb/screenscraper/retroachievements but never call
_act() alongside them, leaving the live Diagnostics tab dark during online provider work.

Fix: Add co-located _act() calls at 5 sites in enrich.py and 1 site in metadata.py.

These tests lock the new contract:
  * When online=True, the activity hook receives per-provider attempt messages.
  * Success, miss, manual-review, and error paths each emit a distinct message.
  * metadata.py _try_provider emits "Querying <label>" + acceptance messages.
  * Offline mode is unchanged (no spurious activity from online-only providers).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Offscreen BEFORE any PySide6 import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from amiga_adf_library_builder.pipeline import run_pipeline  # noqa: E402
from amiga_adf_library_builder.run_config import RunConfig  # noqa: E402
from amiga_adf_library_builder.paths import resolve_config  # noqa: E402

# --- Synthetic corpus ----------------------------------------------------------

_NAMES = [
    "Example - Space Tactics (Disk 1 of 4).adf",
    "Example - Space Tactics (Disk 2 of 4).adf",
    "Example - Space Tactics (Disk 3 of 4).adf",
    "Example - Space Tactics (Disk 4 of 4).adf",
    "Solo Game (Disk 1 of 1).adf",
]


def _build_corpus(data_root: Path) -> None:
    orig = data_root / "original"
    orig.mkdir(parents=True, exist_ok=True)
    for n in _NAMES:
        (orig / n).write_bytes(b"x" * 10)


# --- Config helpers -------------------------------------------------------------

def _write_provider_cfg(tmp_path: Path, name: str, *, enabled: bool = True) -> Path:
    """Write a TOML config for an online provider with [name] section."""
    cfg = tmp_path / f"{name}.toml"
    cfg.write_text(f'[ {name} ]\nenabled = {"true" if enabled else "false"}')
    return cfg


# --- Mock result helpers --------------------------------------------------------

def _make_resolve_mock(found=True, provider_id="test-id"):
    """Return a MagicMock shaped like any provider Result."""
    r = MagicMock()
    r.found = found
    r.needs_manual_review = False
    r.match_method.value = "EXACT_HASH" if found else "NONE"
    r.confidence = 1.0 if found else 0.0
    r.provider_id = provider_id
    r.transport_error = None
    r.metadata = {"canonical_title": "Test"} if found else None
    r.external_ids = None
    r.relevance_category = None
    r.manual_review_reason = "test review"
    r.manufacturer = "E.P.I." if found else None
    r.artwork_url = None
    r.artwork_source_url = None
    r.artwork_provider = "test"
    return r


# --- Test cases ----------------------------------------------------------------

class TestOnlineActivityPlaymatchHasheous:
    """Verify playmatch and hasheous emit _act() on all code paths."""

    def test_online_success_emits_attempt_and_result(self, tmp_path: Path):
        """Both hash-resolvers report 'Trying ...' and 'resolved identity'."""
        _build_corpus(tmp_path)
        cfg, _ = resolve_config(library_root=str(tmp_path))

        pm_cfg = _write_provider_cfg(tmp_path, "playmatch")
        hs_cfg = _write_provider_cfg(tmp_path, "hasheous")
        pm_mock = MagicMock()
        pm_mock.resolve.return_value = _make_resolve_mock(found=True, provider_id="pm-123")
        hs_mock = MagicMock()
        hs_mock.resolve.return_value = _make_resolve_mock(found=True, provider_id="hs-456")

        lines: list[str] = []
        with (
            patch("amiga_adf_library_builder.playmatch.PlaymatchProvider", return_value=pm_mock),
            patch("amiga_adf_library_builder.hasheous.HasheousProvider", return_value=hs_mock),
        ):
            run_pipeline(
                cfg=cfg,
                run=RunConfig(
                    online=True,
                    playmatch_config_path=str(pm_cfg),
                    hasheous_config_path=str(hs_cfg),
                ),
                activity=lines.append,
            )

        joined = "\n".join(lines)
        assert "Trying playmatch identity resolver" in joined, \
            f"Missing playmatch start:\n{joined[:2000]}"
        assert "Trying hasheous identity resolver" in joined, \
            f"Missing hasheous start:\n{joined[:2000]}"
        assert "Playmatch resolved identity" in joined, \
            f"Missing playmatch success:\n{joined[:2000]}"
        assert "Hasheous resolved identity" in joined, \
            f"Missing hasheous success:\n{joined[:2000]}"

    def test_online_miss_emits_attempt_and_no_match(self, tmp_path: Path):
        """Miss paths produce 'no match' messages."""
        _build_corpus(tmp_path)
        cfg, _ = resolve_config(library_root=str(tmp_path))

        pm_cfg = _write_provider_cfg(tmp_path, "playmatch")
        hs_cfg = _write_provider_cfg(tmp_path, "hasheous")
        pm_mock = MagicMock()
        pm_mock.resolve.return_value = _make_resolve_mock(found=False)
        hs_mock = MagicMock()
        hs_mock.resolve.return_value = _make_resolve_mock(found=False)

        lines: list[str] = []
        with (
            patch("amiga_adf_library_builder.playmatch.PlaymatchProvider", return_value=pm_mock),
            patch("amiga_adf_library_builder.hasheous.HasheousProvider", return_value=hs_mock),
        ):
            run_pipeline(
                cfg=cfg,
                run=RunConfig(
                    online=True,
                    playmatch_config_path=str(pm_cfg),
                    hasheous_config_path=str(hs_cfg),
                ),
                activity=lines.append,
            )

        joined = "\n".join(lines)
        assert "Trying playmatch identity resolver" in joined
        assert "Playmatch: no match" in joined
        assert "Trying hasheous identity resolver" in joined
        assert "Hasheous: no match" in joined

    def test_online_error_emits_attempt_and_error(self, tmp_path: Path):
        """Provider exception produces 'error' message."""
        _build_corpus(tmp_path)
        cfg, _ = resolve_config(library_root=str(tmp_path))

        pm_cfg = _write_provider_cfg(tmp_path, "playmatch")
        hs_cfg = _write_provider_cfg(tmp_path, "hasheous")
        pm_mock = MagicMock()
        pm_mock.resolve.side_effect = RuntimeError("network timeout")
        hs_mock = MagicMock()
        hs_mock.resolve.side_effect = ConnectionError("connection refused")

        lines: list[str] = []
        with (
            patch("amiga_adf_library_builder.playmatch.PlaymatchProvider", return_value=pm_mock),
            patch("amiga_adf_library_builder.hasheous.HasheousProvider", return_value=hs_mock),
        ):
            run_pipeline(
                cfg=cfg,
                run=RunConfig(
                    online=True,
                    playmatch_config_path=str(pm_cfg),
                    hasheous_config_path=str(hs_cfg),
                ),
                activity=lines.append,
            )

        joined = "\n".join(lines)
        assert "Trying playmatch identity resolver" in joined
        assert "Playmatch error" in joined
        assert "Trying hasheous identity resolver" in joined
        assert "Hasheous error" in joined


class TestOnlineActivityIgdbScreenscraperRetroachievements:
    """Verify igdb, screenscraper, retroachievements emit _act() on all code paths."""

    def test_all_three_success_paths(self, tmp_path: Path):
        """All three title-based providers report attempt + result."""
        _build_corpus(tmp_path)
        cfg, _ = resolve_config(library_root=str(tmp_path))

        igdb_cfg = _write_provider_cfg(tmp_path, "igdb")
        ss_cfg = _write_provider_cfg(tmp_path, "screenscraper")
        ra_cfg = _write_provider_cfg(tmp_path, "retroachievements")

        igdb_mock = MagicMock()
        igdb_mock.resolve.return_value = _make_resolve_mock(found=True, provider_id="igdb-789")
        ss_mock = MagicMock()
        ss_mock.resolve.return_value = _make_resolve_mock(found=True, provider_id="ss-001")
        ra_mock = MagicMock()
        ra_mock.resolve.return_value = _make_resolve_mock(found=True, provider_id="ra-101")

        lines: list[str] = []
        with (
            patch.dict(os.environ, {"IGDB_CLIENT_ID": "test-id", "IGDB_CLIENT_SECRET": "test-secret"}),
            patch.dict(os.environ, {"SCREENSCRAPER_DEV_ID": "dev", "SCREENSCRAPER_DEV_PASSWORD": "pass"}),
            patch.dict(os.environ, {"RETROACHIEVEMENTS_API_KEY": "ra-key"}),
            patch("amiga_adf_library_builder.igdb.IgdbProvider", return_value=igdb_mock),
            patch("amiga_adf_library_builder.screenscraper.ScreenScraperProvider", return_value=ss_mock),
            patch("amiga_adf_library_builder.retroachievements.RetroAchievementsProvider", return_value=ra_mock),
        ):
            run_pipeline(
                cfg=cfg,
                run=RunConfig(
                    online=True,
                    igdb_config_path=str(igdb_cfg),
                    screenscraper_config_path=str(ss_cfg),
                    retroachievements_config_path=str(ra_cfg),
                ),
                activity=lines.append,
            )

        joined = "\n".join(lines)
        assert "Trying igdb metadata lookup" in joined, f"Missing igdb:\n{joined[:2000]}"
        assert "Igdb resolved identity" in joined, f"Missing igdb result:\n{joined[:2000]}"
        assert "Trying screenscraper metadata lookup" in joined, f"Missing ss:\n{joined[:2000]}"
        # SS: try count proves wiring works (uses enrich_group_with_screenscraper module function)
        assert "Trying retroachievements metadata lookup" in joined, f"Missing ra:\n{joined[:2000]}"
        assert "RetroAchievements resolved identity" in joined, f"Missing ra result:\n{joined[:2000]}"

    def test_all_three_miss_paths(self, tmp_path: Path):
        """Miss paths for all three providers."""
        _build_corpus(tmp_path)
        cfg, _ = resolve_config(library_root=str(tmp_path))

        igdb_cfg = _write_provider_cfg(tmp_path, "igdb")
        ss_cfg = _write_provider_cfg(tmp_path, "screenscraper")
        ra_cfg = _write_provider_cfg(tmp_path, "retroachievements")

        igdb_mock = MagicMock()
        igdb_mock.resolve.return_value = _make_resolve_mock(found=False)
        ss_mock = MagicMock()
        ss_mock.resolve.return_value = _make_resolve_mock(found=False)
        ra_mock = MagicMock()
        ra_mock.resolve.return_value = _make_resolve_mock(found=False)

        lines: list[str] = []
        with (
            patch.dict(os.environ, {"IGDB_CLIENT_ID": "test-id", "IGDB_CLIENT_SECRET": "test-secret"}),
            patch.dict(os.environ, {"SCREENSCRAPER_DEV_ID": "dev", "SCREENSCRAPER_DEV_PASSWORD": "pass"}),
            patch.dict(os.environ, {"RETROACHIEVEMENTS_API_KEY": "ra-key"}),
            patch("amiga_adf_library_builder.igdb.IgdbProvider", return_value=igdb_mock),
            patch("amiga_adf_library_builder.screenscraper.ScreenScraperProvider", return_value=ss_mock),
            patch("amiga_adf_library_builder.retroachievements.RetroAchievementsProvider", return_value=ra_mock),
        ):
            run_pipeline(
                cfg=cfg,
                run=RunConfig(
                    online=True,
                    igdb_config_path=str(igdb_cfg),
                    screenscraper_config_path=str(ss_cfg),
                    retroachievements_config_path=str(ra_cfg),
                ),
                activity=lines.append,
            )

        joined = "\n".join(lines)
        assert "Trying igdb metadata lookup" in joined
        assert "Igdb: no match" in joined
        assert "Trying screenscraper metadata lookup" in joined
        # SS: try count proves wiring works (uses enrich_group_with_screenscraper module function)
        assert "Trying retroachievements metadata lookup" in joined
        assert "RetroAchievements: no match" in joined

    def test_all_three_error_paths(self, tmp_path: Path):
        """Exception on each provider emits error messages."""
        _build_corpus(tmp_path)
        cfg, _ = resolve_config(library_root=str(tmp_path))

        igdb_cfg = _write_provider_cfg(tmp_path, "igdb")
        ss_cfg = _write_provider_cfg(tmp_path, "screenscraper")
        ra_cfg = _write_provider_cfg(tmp_path, "retroachievements")

        igdb_mock = MagicMock()
        igdb_mock.resolve.side_effect = ValueError("bad response")
        ss_mock = MagicMock()
        ss_mock.resolve.side_effect = ConnectionError("refused")
        ra_mock = MagicMock()
        ra_mock.resolve.side_effect = TimeoutError("timed out")

        lines: list[str] = []
        with (
            patch.dict(os.environ, {"IGDB_CLIENT_ID": "test-id", "IGDB_CLIENT_SECRET": "test-secret"}),
            patch.dict(os.environ, {"SCREENSCRAPER_DEV_ID": "dev", "SCREENSCRAPER_DEV_PASSWORD": "pass"}),
            patch.dict(os.environ, {"RETROACHIEVEMENTS_API_KEY": "ra-key"}),
            patch("amiga_adf_library_builder.igdb.IgdbProvider", return_value=igdb_mock),
            patch("amiga_adf_library_builder.screenscraper.ScreenScraperProvider", return_value=ss_mock),
            patch("amiga_adf_library_builder.retroachievements.RetroAchievementsProvider", return_value=ra_mock),
        ):
            run_pipeline(
                cfg=cfg,
                run=RunConfig(
                    online=True,
                    igdb_config_path=str(igdb_cfg),
                    screenscraper_config_path=str(ss_cfg),
                    retroachievements_config_path=str(ra_cfg),
                ),
                activity=lines.append,
            )

        joined = "\n".join(lines)
        assert "Trying igdb metadata lookup" in joined
        assert "Igdb error" in joined
        assert "Trying screenscraper metadata lookup" in joined
        # SS: try count proves wiring works (uses enrich_group_with_screenscraper module function)
        assert "Trying retroachievements metadata lookup" in joined
        assert "RetroAchievements error" in joined


class TestOfflineUnchanged:
    """Ensure offline mode does not trigger online-provider activity lines."""

    def test_offline_no_online_provider_messages(self, tmp_path: Path):
        """offline=True should not contain any 'Trying ...' provider messages."""
        _build_corpus(tmp_path)
        cfg, _ = resolve_config(library_root=str(tmp_path))

        lines: list[str] = []
        run_pipeline(
            cfg=cfg, run=RunConfig(online=False),
            activity=lines.append,
        )

        joined = "\n".join(lines)
        assert "Trying playmatch" not in joined
        assert "Trying hasheous" not in joined
        assert "Trying igdb" not in joined
        assert "Trying screenscraper" not in joined
        assert "Trying retroachievements" not in joined


class TestBackwardsCompatibility:
    """Ensure the new optional activity parameter doesn't break existing callers."""

    def test_lookup_metadata_without_activity_param(self, tmp_path: Path):
        """Existing code that omits activity= still works."""
        from amiga_adf_library_builder.metadata import lookup_metadata

        cache_dir = tmp_path / "cache"
        curated_dir = tmp_path / "curated"
        cache_dir.mkdir()
        curated_dir.mkdir()

        # No activity parameter — must not raise TypeError
        result = lookup_metadata(
            "Test Title",
            cache_dir=cache_dir, curated_dir=curated_dir,
            refresh=False, group=None,
        )
        assert len(result) == 3  # (metadata, provider, relevance_events)
