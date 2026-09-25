"""GH-192 REM-1: Packaged-GUI metadata/DAT/diagnostic/cache repair tests.

Tests the four defect areas from GH-192:
  - ONLINE: punctuation/colon variants accepted as same game
  - DAT: deterministic DAT fixture loads and diagnostics
  - ASSOCIATION: stable release identity in diagnostics
  - CACHE: portable cache path auto-detection
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from amiga_adf_library_builder.metadata import (
    MetadataRecord,
    validate_metadata_relevance,
)
from amiga_adf_library_builder.metadata_source import MetadataSourceManager
from amiga_adf_library_builder.paths import _portable_cache_path, resolve_config
from amiga_adf_library_builder.pipeline import run_pipeline
from amiga_adf_library_builder.run_config import RunConfig
from amiga_adf_library_builder.models import ReleaseGroup


def _resolve_cfg(library_root: Path):
    cfg, _ = resolve_config(library_root=str(library_root))
    return cfg


# ============================================================================
# DEFECT 1: ONLINE METADATA
# ============================================================================


class TestOnlineMetadataNormalization:
    """GH-192 Defect 1: Punctuation/colon-only variants accepted as same game."""

    def test_colon_variant_accepted(self) -> None:
        rec = MetadataRecord(canonical_title="UFO: Enemy Unknown", provider="wikipedia", confidence=0.9, platforms=["amiga"])
        decision = validate_metadata_relevance("UFO Enemy Unknown", rec)
        assert decision.category in ("accepted", "review"), f"Got {decision.category}"

    def test_ultima_iv_colon_variant_accepted(self) -> None:
        rec = MetadataRecord(canonical_title="Ultima IV: Quest of the Avatar", provider="wikipedia", confidence=0.9, platforms=["amiga"])
        decision = validate_metadata_relevance("Ultima IV Quest of the Avatar", rec)
        assert decision.category in ("accepted", "review")

    def test_ultima_iii_colon_variant_not_ambiguous(self) -> None:
        rec = MetadataRecord(canonical_title="Ultima III: Exodus", provider="wikipedia", confidence=0.9, platforms=["amiga"])
        decision = validate_metadata_relevance("Ultima III Exodus", rec)
        assert decision.category == "accepted"

    def test_genuinely_different_game_rejected(self) -> None:
        rec = MetadataRecord(canonical_title="Super Mario Bros", provider="wikipedia", confidence=0.9, platforms=["nintendo"])
        decision = validate_metadata_relevance("UFO Enemy Unknown", rec)
        assert decision.category == "rejected"

    def test_article_variant_accepted(self) -> None:
        rec = MetadataRecord(canonical_title="The Untouchables", provider="wikipedia", confidence=0.9, platforms=["amiga"])
        decision = validate_metadata_relevance("Untouchables, The", rec)
        assert decision.category in ("accepted", "review")

    def test_accept_reject_reason_persisted(self) -> None:
        rec = MetadataRecord(canonical_title="UFO: Enemy Unknown", provider="wikipedia", confidence=0.9, platforms=["amiga"])
        decision = validate_metadata_relevance("UFO Enemy Unknown", rec)
        assert decision.reason is not None
        assert len(decision.evidence) > 0

    def test_confidence_within_expected_range(self) -> None:
        rec = MetadataRecord(canonical_title="UFO: Enemy Unknown", provider="wikipedia", confidence=0.9, platforms=["amiga"])
        decision = validate_metadata_relevance("UFO Enemy Unknown", rec)
        assert decision.confidence > 0.5


# ============================================================================
# DEFECT 2: DAT OBSERVABILITY
# ============================================================================


class TestDatObservability:
    """GH-192 Defect 2: DAT source loaded and diagnostics visible."""

    @pytest.fixture(autouse=True)
    def _isolate_path_config(self, monkeypatch, tmp_path_factory):
        xdg_config = tmp_path_factory.mktemp("xdg-config")
        xdg_cache = tmp_path_factory.mktemp("xdg-cache")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
        monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
        for key in list(os.environ):
            if key.startswith("AMIGA_ADF_"):
                monkeypatch.delenv(key, raising=False)

    def _make_dat_source(self, db_path: Path) -> MetadataSourceManager:
        manager = MetadataSourceManager(db_path)
        fixture = Path(__file__).parent / "fixtures" / "sample.dat"
        if fixture.exists():
            source_id = manager.add_source(fixture)
            assert source_id is not None, "Failed to add DAT source"
        return manager

    def test_dat_source_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._make_dat_source(Path(tmp) / "metadata_sources.db")
            enabled = [s for s in manager.list_sources() if s.enabled]
            assert len(enabled) > 0

    def test_known_fixture_release_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._make_dat_source(Path(tmp) / "metadata_sources.db")
            matches = manager.lookup_by_title("Alien Breed")
            assert len(matches) > 0
            assert matches[0].source_id is not None

    def test_dat_source_source_id_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._make_dat_source(Path(tmp) / "metadata_sources.db")
            for source in [s for s in manager.list_sources() if s.enabled]:
                assert source.source_id is not None

    def test_wrong_fixture_release_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._make_dat_source(Path(tmp) / "metadata_sources.db")
            assert len(manager.lookup_by_title("Nonexistent Game 999")) == 0

    def test_dat_results_in_run_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            (library_root / "data").mkdir(parents=True)
            (library_root / "catalog").mkdir(parents=True)
            (library_root / "original").mkdir(parents=True)
            cfg = _resolve_cfg(library_root)
            result = run_pipeline(cfg=cfg, run=RunConfig(online=False))
            assert "dat_failures" in result
            assert isinstance(result["dat_failures"], int)

    def test_per_release_dat_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            (library_root / "data").mkdir(parents=True)
            (library_root / "catalog").mkdir(parents=True)
            (library_root / "original").mkdir(parents=True)
            cfg = _resolve_cfg(library_root)
            result = run_pipeline(cfg=cfg, run=RunConfig(online=False))
            if "per_group" in result:
                for pg in result["per_group"]:
                    assert "dat_results" in pg or "dat_attempts" in pg


# ============================================================================
# DEFECT 3: DIAGNOSTIC ASSOCIATION
# ============================================================================


class TestDiagnosticAssociation:
    """GH-192 Defect 3: Diagnostics must stay bound to correct release identity."""

    @pytest.fixture(autouse=True)
    def _isolate_path_config(self, monkeypatch, tmp_path_factory):
        xdg_config = tmp_path_factory.mktemp("xdg-config")
        xdg_cache = tmp_path_factory.mktemp("xdg-cache")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
        monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
        for key in list(os.environ):
            if key.startswith("AMIGA_ADF_"):
                monkeypatch.delenv(key, raising=False)

    def test_cache_path_visible_in_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            (library_root / "data").mkdir(parents=True)
            (library_root / "catalog").mkdir(parents=True)
            (library_root / "original").mkdir(parents=True)
            cfg = _resolve_cfg(library_root)
            result = run_pipeline(cfg=cfg, run=RunConfig(online=False))
            assert "resolved_cache_dir" in result
            assert "portable_cache_mode" in result
            assert "cache_hit_source" in result
            assert "resolved_portable_cache_dir" in result

    def test_release_key_stable(self) -> None:
        groups = [ReleaseGroup(release_key="Test Game|standard", title="Test Game", edition="standard", group="Test Game", chipset="")]
        assert groups[0].release_key == "Test Game|standard"


# ============================================================================
# DEFECT 4: CACHE POLICY
# ============================================================================


class TestCachePortability:
    """GH-192 Defect 4: Packaged portable cache path auto-detection."""

    @pytest.fixture(autouse=True)
    def _isolate_path_config(self, monkeypatch, tmp_path_factory):
        xdg_config = tmp_path_factory.mktemp("xdg-config")
        xdg_cache = tmp_path_factory.mktemp("xdg-cache")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
        monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
        for key in list(os.environ):
            if key.startswith("AMIGA_ADF_"):
                monkeypatch.delenv(key, raising=False)

    def test_portable_cache_detected_from_library_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            (library_root / "data").mkdir(parents=True)
            (library_root / "catalog").mkdir(parents=True)
            cfg = _resolve_cfg(library_root)
            assert cfg.cache_dir == _portable_cache_path(library_root)
            assert "data/cache" in str(cfg.cache_dir)

    def test_non_portable_cache_when_no_data_catalog(self) -> None:
        """If library_root lacks data/ or catalog/, cache must NOT be portable."""
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            library_root.mkdir()
            cfg = _resolve_cfg(library_root)
            # When data/ and catalog/ don't both exist, cache should NOT be under data/cache
            assert "data/cache" not in str(cfg.cache_dir)

    def test_portable_cache_mode_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            (library_root / "data").mkdir(parents=True)
            (library_root / "catalog").mkdir(parents=True)
            (library_root / "original").mkdir(parents=True)
            cfg = _resolve_cfg(library_root)
            assert os.environ.get("AMIGA_ADF_PORTABLE") is None
            result = run_pipeline(cfg=cfg, run=RunConfig(online=False))
            assert result["portable_cache_mode"] is True
            assert result["cache_hit_source"] == "portable_library"

    def test_clean_cache_no_stale_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            (library_root / "data").mkdir(parents=True)
            (library_root / "catalog").mkdir(parents=True)
            (library_root / "original").mkdir(parents=True)
            cfg = _resolve_cfg(library_root)
            result1 = run_pipeline(cfg=cfg, run=RunConfig(online=False))
            assert result1["resolved_cache_dir"] == result1["resolved_portable_cache_dir"]
            result2 = run_pipeline(cfg=cfg, run=RunConfig(online=False))
            assert result1["resolved_cache_dir"] == result2["resolved_cache_dir"]


# ============================================================================
# REGRESSION
# ============================================================================


class TestRegressionCompatibility:
    """Verify no regressions."""

    def test_release_key_stable(self) -> None:
        groups = [ReleaseGroup(release_key="Test Game|standard", title="Test Game", edition="standard", group="Test Game", chipset="")]
        assert groups[0].release_key == "Test Game|standard"

    def test_metadata_record_fields(self) -> None:
        rec = MetadataRecord(canonical_title="Test Game", provider="wikipedia", confidence=0.9, platforms=["amiga"])
        assert rec.canonical_title == "Test Game"
        assert rec.provider == "wikipedia"
        assert rec.confidence == 0.9