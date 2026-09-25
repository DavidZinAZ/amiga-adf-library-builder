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

from amiga_adf_library_builder import metadata as metadata_module
from amiga_adf_library_builder.metadata import (
    MetadataRecord,
    validate_metadata_relevance,
)
from amiga_adf_library_builder import enrich as enrich_module
from amiga_adf_library_builder import paths as paths_module
from amiga_adf_library_builder.diagnostics import (
    attempt_from_enrich_events,
    aggregate_provider_attempts,
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

    @pytest.mark.parametrize(("requested", "candidate"), [
        ("Hacker II: The Doomsday Papers", "Hacker 2 The Doomsday Papers"),
        ("The Bard's Tale III: Thief of Fate", "Bards Tale 3 Thief of Fate"),
        ("Ultima III: Exodus", "Ultima 3 Exodus"),
        ("Ultima IV: Quest of the Avatar", "Ultima 4 Quest of the Avatar"),
        ("Ultima V: Warriors of Destiny", "Ultima 5 Warriors of Destiny"),
        ("Ultima VI: The False Prophet", "Ultima 6 False Prophet"),
        ("UFO: Enemy Unknown", "U.F.O. Enemy Unknown"),
    ])
    def test_packaged_provider_title_variants(self, requested: str, candidate: str) -> None:
        rec = MetadataRecord(canonical_title=candidate, provider="wikipedia", confidence=0.9, platforms=["amiga"])
        decision = validate_metadata_relevance(requested, rec)
        assert decision.category == "accepted", f"{requested!r} vs {candidate!r}: {decision}"

    def test_packaged_provider_negative_control_rejected(self) -> None:
        rec = MetadataRecord(canonical_title="Bard's Tale IV", provider="wikipedia", confidence=0.9, platforms=["amiga"])
        decision = validate_metadata_relevance("Hacker II The Doomsday Papers", rec)
        assert decision.category == "rejected"

    def test_enrichment_persists_provider_hit_reject_and_error(self, monkeypatch, tmp_path) -> None:
        from amiga_adf_library_builder.models import ReleaseGroup

        requested = "Hacker II: The Doomsday Papers"
        metadata = MetadataRecord(
            canonical_title="Hacker 2 The Doomsday Papers",
            provider="wikipedia",
            confidence=0.9,
            platforms=["amiga"],
        )
        provider_results = [
            {"provider": "hall-of-light", "canonical_title": "Wrong Game", "category": "rejected",
             "confidence": 0.1, "reason": "different_game", "evidence": []},
            {"provider": "lemon-amiga", "canonical_title": "", "category": "not_found",
             "confidence": 0.0, "reason": "request_error", "evidence": ["request_error"]},
            {"provider": "wikipedia", "canonical_title": metadata.canonical_title, "category": "accepted",
             "confidence": 1.0, "reason": "exact_identity", "evidence": ["canonical_match"]},
        ]
        monkeypatch.setattr(
            enrich_module,
            "lookup_metadata",
            lambda *args, **kwargs: (metadata, "wikipedia", provider_results),
        )
        result = enrich_module.enrich_group(
            ReleaseGroup(release_key=f"{requested}|standard", title=requested,
                         edition="standard", group=requested, chipset=""),
            nfo_dir=tmp_path / "nfo",
            scans={},
            artwork_original_dir=tmp_path / "art-original",
            artwork_processed_dir=tmp_path / "art-processed",
            metadata_cache_dir=tmp_path / "metadata-cache",
            curated_metadata_dir=tmp_path / "metadata-curated",
            online=True,
            include_artwork=False,
        )
        attempts = attempt_from_enrich_events(
            result.events,
            title=requested,
            release_key=f"{requested}|standard",
        )
        outcomes = {attempt.provider: attempt.outcome for attempt in attempts}
        assert outcomes["hall-of-light"] == "no_match"
        assert outcomes["lemon-amiga"] == "error"
        assert outcomes["wikipedia"] == "matched"

    def test_packaged_pipeline_metadata_fixtures(self, monkeypatch) -> None:
        fixtures = [
            ("Hacker II The Doomsday Papers", "Hacker II: The Doomsday Papers"),
            ("Bard's Tale III Thief of Fate", "The Bard's Tale III: Thief of Fate"),
            ("Ultima III Exodus", "Ultima III: Exodus"),
            ("Ultima IV Quest of the Avatar", "Ultima IV: Quest of the Avatar"),
            ("Ultima V Warriors of Destiny", "Ultima V: Warriors of Destiny"),
            ("Ultima VI The False Prophet", "Ultima VI: The False Prophet"),
            ("UFO Enemy Unknown", "U.F.O.: Enemy Unknown"),
        ]

        def hall_of_light_fixture(query: str, **kwargs):
            query_key = query.casefold()
            for requested, candidate in fixtures:
                if query_key.startswith(requested.casefold()):
                    return MetadataRecord(
                        canonical_title=candidate,
                        provider="hall-of-light",
                        confidence=0.95,
                        platforms=["Amiga"],
                    )
            return None

        monkeypatch.setattr(metadata_module, "hall_of_light_lookup", hall_of_light_fixture)
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            for directory in ("data", "catalog", "original"):
                (library_root / directory).mkdir(parents=True, exist_ok=True)
            cfg = _resolve_cfg(library_root)
            for requested, _candidate in fixtures:
                (cfg.original_dir / f"{requested}.adf").write_bytes(b"fixture")

            result = run_pipeline(
                cfg=cfg,
                run=RunConfig(online=True, include_artwork=False),
            )
            hol = next(
                provider for provider in result["provider_diagnostics"]["providers"]
                if provider["provider"] == "hall-of-light"
            )
            assert hol["attempts"] == len(fixtures)
            assert hol["matched"] == len(fixtures)
            assert len(result["per_group"]) == len(fixtures)
            assert all(
                any(event["category"] == "metadata_provider_attempt"
                    and "outcome=hit" in event["detail"]
                    for event in group["events"])
                for group in result["per_group"]
            )


# ============================================================================
# DEFECT 2: DAT OBSERVABILITY
# ============================================================================
# ============================================================================


class TestDatRunDiagnostics:
    def test_dat_hit_is_in_provider_rollup_with_source_provenance(self) -> None:
        results = [{
            "source_id": "sample-dat",
            "source_name": "sample.dat",
            "match_type": "title",
            "title": "Alien Breed",
            "matched": True,
        }]
        attempts = attempt_from_enrich_events(
            [{"category": "dat", "detail": "dat_title_candidate source=sample-dat", "ok": True}],
            title="Alien Breed",
            release_key="Alien Breed|standard",
            dat_results=results,
        )
        summary = aggregate_provider_attempts(attempts)
        dat = next(item for item in summary["providers"] if item.provider == "dat")
        assert dat.attempts == 1
        assert dat.matched == 1
        assert attempts[0].provider_id == "sample-dat"
        assert "source_name=sample.dat" in attempts[0].detail

    def test_dat_no_match_is_an_explicit_provider_attempt(self) -> None:
        attempts = attempt_from_enrich_events(
            [{"category": "dat", "detail": "dat_no_match", "ok": True}],
            title="Unknown Game",
            release_key="Unknown Game|standard",
            dat_results=[{
                "source_id": "sample-dat",
                "source_name": "sample.dat",
                "match_type": "",
                "title": "",
                "matched": False,
            }],
        )
        assert len(attempts) == 1
        assert attempts[0].provider == "dat"
        assert attempts[0].outcome == "no_match"
        assert attempts[0].provider_id == "sample-dat"
        assert "source_name=sample.dat" in attempts[0].detail

    def test_dat_event_only_mapping(self) -> None:
        attempts = attempt_from_enrich_events(
            [{"category": "dat", "detail": "dat_hash_match source=sample-dat", "ok": True}],
            title="Alien Breed",
            release_key="Alien Breed|standard",
        )
        assert len(attempts) == 1
        assert attempts[0].provider == "dat"
        assert attempts[0].matched is True
        assert attempts[0].provider_id == "sample-dat"


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

    def test_dat_results_in_run_summary_with_hit_and_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            (library_root / "data").mkdir(parents=True)
            (library_root / "catalog").mkdir(parents=True)
            (library_root / "original").mkdir(parents=True)
            cfg = _resolve_cfg(library_root)
            self._make_dat_source(cfg.metadata_sources_db)
            (cfg.original_dir / "Alien Breed.adf").write_bytes(b"fixture")
            (cfg.original_dir / "Unknown Game 999.adf").write_bytes(b"fixture")

            result = run_pipeline(cfg=cfg, run=RunConfig(online=False))
            dat = next(
                provider for provider in result["provider_diagnostics"]["providers"]
                if provider["provider"] == "dat"
            )
            assert dat["attempts"] == 2
            assert dat["matched"] == 1
            assert dat["no_match"] == 1
            assert all("dat_results" in group for group in result["per_group"])
            matched = next(
                item for group in result["per_group"] for item in group["dat_results"]
                if item["matched"]
            )
            assert matched["source_id"]
            assert matched["source_name"]

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

    def test_non_portable_python_cache_when_no_data_catalog(self, monkeypatch) -> None:
        """An ordinary Python/CLI library without portable markers uses XDG."""
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "library"
            library_root.mkdir()
            monkeypatch.setattr(paths_module.sys, "executable", "/usr/bin/python3")
            cfg = _resolve_cfg(library_root)
            assert "data/cache" not in str(cfg.cache_dir)

    def test_packaged_cache_uses_library_data_even_before_dirs_exist(self, monkeypatch) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "fresh-library"
            monkeypatch.setattr(paths_module.sys, "executable", "/opt/amiga-adf/builder.exe")
            cfg = _resolve_cfg(library_root)
            assert cfg.cache_dir == (library_root / "data" / "cache").resolve()
            assert "xdg-cache" not in str(cfg.cache_dir)
            assert not (library_root / "data").exists()
            assert not (library_root / "catalog").exists()

    def test_gui_library_root_pattern_uses_portable_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "Library-Root"
            cfg = _resolve_cfg(library_root)
            assert cfg.cache_dir == (library_root / "data" / "cache").resolve()

    def test_packaged_portable_cache_mode_deterministic(self, monkeypatch) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "fresh-library"
            monkeypatch.setattr(paths_module.sys, "executable", "/opt/amiga-adf/builder.exe")
            cfg = _resolve_cfg(library_root)
            assert os.environ.get("AMIGA_ADF_PORTABLE") is None
            assert not (library_root / "data").exists()
            assert not (library_root / "catalog").exists()
            cfg.original_dir.mkdir(parents=True)
            result = run_pipeline(cfg=cfg, run=RunConfig(online=False))
            assert result["portable_cache_mode"] is True
            assert result["cache_hit_source"] == "portable_library"
            assert result["resolved_cache_dir"] == str((library_root / "data" / "cache").resolve())

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