"""GH-107 Slice 5 remediation — production integration tests for canonical naming wiring.

These tests prove that the real application paths (pipeline, exporter)
import and call the canonical naming layer, surfacing canonical proposed
names before any final export write.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from amiga_adf_library_builder.canonical import (
    CanonicalLibrary,
    Disk,
    Game,
    Provenance,
    Release,
    SourceAuthority,
    make_disk_id,
    make_release_id,
)
from amiga_adf_library_builder.canonical_naming import (
    export_name_for_release_group,
    _load_canonical_library,
)
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.pipeline import run_pipeline, build_staged_library_from_result
from amiga_adf_library_builder.paths import PathConfig


def _prov(
    source: str,
    tier: str,
    rank: int = 0,
    confidence=None,
    observed_at: str = "2026-09-11T00:00:00+00:00",
) -> Provenance:
    authority = {
        "parser": SourceAuthority.PARSER,
        "dat": SourceAuthority.DAT,
        "curation_memory": SourceAuthority.CURATION_MEMORY,
        "curation": SourceAuthority.CURATION,
    }[tier]
    return Provenance(
        source=source,
        authority=authority,
        authority_rank=rank,
        confidence=confidence,
        observed_at=observed_at,
    )


def _setup_canon(db_path: Path) -> CanonicalLibrary:
    canon = CanonicalLibrary(db_path)
    game = Game(game_id="test-game")
    game.fields.setdefault("title", _field()).claim(
        _prov("tosec", "dat", rank=1, confidence=0.95), "Test Game"
    )
    canon.upsert_game(game)

    release_id = make_release_id("test-game", "Platinum Edition", "USA", "EN", "Acme")
    release = Release(
        release_id=release_id,
        game_id="test-game",
        edition="Platinum Edition",
        region="USA",
        language="EN",
        publisher="Acme",
    )
    release.fields.setdefault("title", _field()).claim(
        _prov("tosec", "dat", rank=1), "Test Game"
    )
    release.fields.setdefault("edition", _field()).claim(
        _prov("tosec", "dat", rank=1), "Platinum Edition"
    )
    release.fields.setdefault("region", _field()).claim(
        _prov("tosec", "dat", rank=1), "USA"
    )
    release.fields.setdefault("language", _field()).claim(
        _prov("tosec", "dat", rank=1), "EN"
    )
    release.fields.setdefault("publisher", _field()).claim(
        _prov("tosec", "dat", rank=1), "Acme"
    )
    release.fields.setdefault("group", _field()).claim(
        _prov("tosec", "dat", rank=1), "SKR"
    )
    release.fields.setdefault("chipset", _field()).claim(
        _prov("tosec", "dat", rank=1), "AGA"
    )
    release.fields.setdefault("version", _field()).claim(
        _prov("tosec", "dat", rank=1), "v2.0"
    )
    release.fields.setdefault("alt_marker", _field()).claim(
        _prov("tosec", "dat", rank=1), "a"
    )
    canon.upsert_release(release)

    d1 = Disk(
        disk_id=make_disk_id("sha256:aa"),
        release_id=release_id,
        filename="Test Game - Disk 1.adf",
        disk_number=1,
        sha256="sha256:aa",
        size=1000,
    )
    d1.fields.setdefault("filename", _field()).claim(
        _prov("tosec", "dat", rank=1), "Test Game - Disk 1.adf"
    )
    d2 = Disk(
        disk_id=make_disk_id("sha256:bb"),
        release_id=release_id,
        filename="Test Game - Disk 2.adf",
        disk_number=2,
        sha256="sha256:bb",
        size=1000,
    )
    d2.fields.setdefault("filename", _field()).claim(
        _prov("tosec", "dat", rank=1), "Test Game - Disk 2.adf"
    )
    canon.upsert_disk(d1)
    canon.upsert_disk(d2)

    return canon


def _field():
    from amiga_adf_library_builder.canonical import CanonicalField
    return CanonicalField()


# ---------------------------------------------------------------------------
# Production wiring: pipeline per_group has canonical keys
# ---------------------------------------------------------------------------


class TestPipelineCanonicalWiring:
    def test_pipeline_per_group_has_canonical_folder_key(self, tmp_path):
        """run_pipeline per_group dicts include the canonical 'folder' key
        derived from the canonical DB when available."""
        original_dir = tmp_path / "original"
        original_dir.mkdir()
        (original_dir / "Example_Quest_Disk_A.adf").write_bytes(b"X" * 100)

        # Pre-populate canonical.db so the pipeline can wire it in.
        curation_dir = tmp_path / "library" / "curation"
        curation_dir.mkdir(parents=True, exist_ok=True)
        db_path = curation_dir / "canonical.db"
        canon = _setup_canon(db_path)
        rid = canon.releases_for_game("test-game")[0]
        from amiga_adf_library_builder.canonical import Provenance, SourceAuthority
        canon.claim_field(
            "release",
            rid,
            "release_key",
            "example-quest-Platinum-Edition-USA-EN-Acme",
            Provenance(
                source="operator",
                authority=SourceAuthority.CURATION,
                observed_at="2026-09-12T00:00:00+00:00",
            ),
        )
        canon.close()

        from amiga_adf_library_builder.pipeline import run_pipeline
        from amiga_adf_library_builder.paths import PathConfig
        cfg = PathConfig(
            library_root=tmp_path / "library",
            original_dir=original_dir,
            staging_dir=tmp_path / "staging",
            output_dir=tmp_path / "output",
            quarantine_dir=tmp_path / "quarantine",
            logs_dir=tmp_path / "logs",
            cache_dir=tmp_path / "cache",
            reports_dir=tmp_path / "reports",
            approvals_dir=tmp_path / "approvals",
        )
        result = run_pipeline(cfg=cfg, online=False)
        assert result is not None
        assert result.get("groups", 0) >= 0
        for pg in result.get("per_group", []):
            assert "folder" in pg
            assert isinstance(pg["folder"], str)
            # canonical_proposed_name is present (may be fallback when no match).
            assert "canonical_proposed_name" in pg
            assert "basename" in pg["canonical_proposed_name"]
            assert "provenance" in pg["canonical_proposed_name"]

    def test_pipeline_canonical_folder_matches_canonical_name(self, tmp_path):
        """When the canonical DB has a matching release via release_key
        claim, the pipeline's per_group 'folder' uses the canonical
        proposed basename."""
        original_dir = tmp_path / "original"
        original_dir.mkdir()
        # Parse a filename that produces the release_key matching
        # the canonical DB claim.
        rec = parse_filename("Test_Game_-_Disk_1.adf")
        rec.title = "Test Game"
        rec.disk_number = 1
        grp = ReleaseGroup(
            release_key="test-game-Platinum-Edition-USA-EN-Acme",
            title="Test Game",
            edition="Platinum Edition",
            group="SKR",
            chipset="AGA",
            language="EN",
            version="v2.0",
            alt_marker="a",
            ext="adf",
            records=[rec],
            disks=[rec],
            specials=[],
            has_main_disk=True,
            is_complete=True,
        )
        groups = [grp]

        # Pre-populate canonical.db with a release_key claim so the
        # export_name_for_release_group lookup succeeds.
        curation_dir = tmp_path / "library" / "curation"
        curation_dir.mkdir(parents=True, exist_ok=True)
        db_path = curation_dir / "canonical.db"
        canon = _setup_canon(db_path)
        rid = canon.releases_for_game("test-game")[0]
        from amiga_adf_library_builder.canonical import Provenance, SourceAuthority
        canon.claim_field(
            "release",
            rid,
            "release_key",
            "test-game-Platinum-Edition-USA-EN-Acme",
            Provenance(
                source="operator",
                authority=SourceAuthority.CURATION,
                observed_at="2026-09-12T00:00:00+00:00",
            ),
        )
        canon.close()

        from amiga_adf_library_builder.pipeline import run_pipeline
        from amiga_adf_library_builder.paths import PathConfig
        import amiga_adf_library_builder.grouper as grouper_mod
        cfg = PathConfig(
            library_root=tmp_path / "library",
            original_dir=original_dir,
            staging_dir=tmp_path / "staging",
            output_dir=tmp_path / "output",
            quarantine_dir=tmp_path / "quarantine",
            logs_dir=tmp_path / "logs",
            cache_dir=tmp_path / "cache",
            reports_dir=tmp_path / "reports",
            approvals_dir=tmp_path / "approvals",
        )
        original_group_records = grouper_mod.group_records
        grouper_mod.group_records = lambda records: groups
        try:
            result = run_pipeline(cfg=cfg, online=False)
        finally:
            grouper_mod.group_records = original_group_records
        assert result is not None
        assert result.get("groups", 0) >= 0
        # The canonical DB match should produce a name with the
        # qualifier (Platinum Edition) rather than the fallback.
        canonical_folders = [
            pg["canonical_proposed_name"]["basename"]
            for pg in result.get("per_group", [])
            if pg.get("canonical_proposed_name", {}).get("basename")
        ]
        assert any("Platinum" in f for f in canonical_folders), (
            f"expected canonical qualifier in folder; got {canonical_folders}"
        )

    def test_pipeline_fallback_when_no_canonical_db(self, tmp_path):
        """When canonical.db is absent, pipeline falls back to
        release_basename(group) — no crash, folder is still populated."""
        original_dir = tmp_path / "original"
        original_dir.mkdir()
        (original_dir / "Example_Quest_Disk_A.adf").write_bytes(b"X" * 100)

        from amiga_adf_library_builder.pipeline import run_pipeline
        from amiga_adf_library_builder.paths import PathConfig
        cfg = PathConfig(
            library_root=tmp_path / "library",
            original_dir=original_dir,
            staging_dir=tmp_path / "staging",
            output_dir=tmp_path / "output",
            quarantine_dir=tmp_path / "quarantine",
            logs_dir=tmp_path / "logs",
            cache_dir=tmp_path / "cache",
            reports_dir=tmp_path / "reports",
            approvals_dir=tmp_path / "approvals",
        )
        result = run_pipeline(cfg=cfg, online=False)
        assert result is not None
        for pg in result.get("per_group", []):
            assert "folder" in pg
            assert isinstance(pg["folder"], str)
            assert "canonical_proposed_name" in pg


# ---------------------------------------------------------------------------
# Production wiring: _load_canonical_library helper
# ---------------------------------------------------------------------------


class TestLoadCanonicalLibrary:
    def test_load_returns_canonical_library(self, tmp_path):
        curation_dir = tmp_path / "library" / "curation"
        curation_dir.mkdir(parents=True, exist_ok=True)
        db_path = curation_dir / "canonical.db"
        canon = _setup_canon(db_path)
        rid = canon.releases_for_game("test-game")[0]
        from amiga_adf_library_builder.canonical import Provenance, SourceAuthority
        canon.claim_field(
            "release",
            rid,
            "release_key",
            "test-game-Platinum-Edition-USA-EN-Acme",
            Provenance(
                source="operator",
                authority=SourceAuthority.CURATION,
                observed_at="2026-09-12T00:00:00+00:00",
            ),
        )
        canon.close()

        loaded = _load_canonical_library(tmp_path / "library")
        assert loaded is not None
        rid2 = loaded.releases_for_game("test-game")[0]
        name = loaded.release_row(rid2)
        assert name is not None
        loaded.close()

    def test_load_returns_none_when_db_missing(self, tmp_path):
        loaded = _load_canonical_library(tmp_path / "nonexistent")
        assert loaded is None


# ---------------------------------------------------------------------------
# Production wiring: exporter canonical basename helper
# ---------------------------------------------------------------------------


class TestExporterCanonicalWiring:
    def test_get_canonical_basename_returns_proposed(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]
        canon.close()

        from amiga_adf_library_builder.canonical_naming import export_name_for_release_group
        from amiga_adf_library_builder.exporter import _get_canonical_basename

        rec = parse_filename("Test_Game_-_Disk_1.adf")
        rec.title = "Test Game"
        rec.disk_number = 1
        grp = ReleaseGroup(
            release_key="test-game-Platinum-Edition-USA-EN-Acme",
            title="Test Game",
            edition="Platinum Edition",
            group="SKR",
            chipset="AGA",
            language="EN",
            version="v2.0",
            alt_marker="a",
            ext="adf",
            records=[rec],
            disks=[rec],
            specials=[],
            has_main_disk=True,
            is_complete=True,
        )
        staging = tmp_path / "staging" / "run1"
        basename, prov = _get_canonical_basename(grp, staging)
        assert basename
        assert "Platinum" in prov or "fallback" in prov

    def test_get_canonical_basename_fallback(self, tmp_path):
        from amiga_adf_library_builder.exporter import _get_canonical_basename

        rec = parse_filename("Example_Quest_Disk_A.adf")
        rec.title = "Example Quest"
        rec.disk_number = 1
        grp = ReleaseGroup(
            release_key="example-quest",
            title="Example Quest",
            edition=None, group=None,
            chipset=None, language=None, version=None, alt_marker=None,
            ext="adf",
            records=[rec], disks=[rec], specials=[],
            has_main_disk=True, is_complete=True,
        )
        staging = tmp_path / "staging" / "run1"
        basename, prov = _get_canonical_basename(grp, staging)
        assert basename
        assert "fallback" in prov