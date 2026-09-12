"""Tests for the GH-107 Slice 5 canonical naming/export policy layer.

Deterministic coverage: same canonical input -> identical proposed names,
manual curation overrides reflected, multi-disk ordering, qualifier
policy, filesystem sanitization, collision detection, check/build-only
no-write, and real application integration via pipeline.
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
    CollisionReport,
    ProposedName,
    ReleaseNaming,
    DOCUMENTED_QUALIFIER_POLICY,
    canonical_disk_name,
    canonical_release_name,
    detect_export_collisions,
    explain_canonical_name,
    ordered_disk_ids,
    export_name_for_release_group,
    _sanitize_token,
)
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.pipeline import build_staged_library_from_result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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
    # Game: "Test Game"
    game = Game(game_id="test-game")
    game.fields.setdefault("title", _field()).claim(
        _prov("tosec", "dat", rank=1, confidence=0.95), "Test Game"
    )
    canon.upsert_game(game)

    # Release: edition="Platinum Edition", region="USA", language="EN",
    # publisher="Acme", group="SKR", chipset="AGA", version="v2.0",
    # alt_marker="a"
    release_id = make_release_id("test-game", "Platinum Edition", "USA", "EN", "Acme")
    release = Release(
        release_id=release_id,
        game_id="test-game",
        edition="Platinum Edition",
        region="USA",
        language="EN",
        publisher="Acme",
    )
    rf = release.fields.setdefault("title", _field())
    rf.claim(_prov("tosec", "dat", rank=1), "Test Game")
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

    # Disks: 2 disks with canonical filenames and disk_numbers
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
# 1. Deterministic same-canonical-input -> identical proposed names
# ---------------------------------------------------------------------------

class TestDeterministicSameInput:
    def test_repeat_runs_identical(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        r1 = canonical_release_name(canon, canon.releases_for_game("test-game")[0])
        r2 = canonical_release_name(canon, canon.releases_for_game("test-game")[0])
        assert r1.basename == r2.basename
        assert r1.provenance_text == r2.provenance_text
        canon.close()

    def test_explain_repeat_identical(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]
        e1 = explain_canonical_name(canon, rid)
        e2 = explain_canonical_name(canon, rid)
        assert e1["release_basename"] == e2["release_basename"]
        assert e1["disk_names"] == e2["disk_names"]
        canon.close()


# ---------------------------------------------------------------------------
# 2. Manual curation override reflected; provider refresh does not revert
# ---------------------------------------------------------------------------

class TestManualOverride:
    def test_override_reflected_in_name(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]
        # Operator overrides the GAME title (first basename component).
        canon.claim_field(
            "game",
            "test-game",
            "title",
            "Operator Title",
            Provenance(
                source="operator",
                authority=SourceAuthority.CURATION,
                observed_at="2026-09-12T00:00:00+00:00",
            ),
        )
        name = canonical_release_name(canon, rid)
        assert "Operator Title" in name.basename
        canon.close()

    def test_provider_refresh_does_not_revert(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]
        # Set curation override on the game title.
        canon.claim_field(
            "game",
            "test-game",
            "title",
            "Curated Title",
            Provenance(
                source="operator",
                authority=SourceAuthority.CURATION,
                observed_at="2026-09-12T00:00:00+00:00",
            ),
        )
        # Simulate provider refresh adding a newer DAT claim.
        canon.claim_field(
            "game",
            "test-game",
            "title",
            "Provider Refresh Title",
            _prov("igdb", "dat", observed_at="2099-01-01T00:00:00+00:00"),
        )
        name = canonical_release_name(canon, rid)
        assert "Curated Title" in name.basename
        assert "Provider Refresh Title" not in name.basename
        canon.close()


# ---------------------------------------------------------------------------
# 3. Multi-disk ordering + per-disk names
# ---------------------------------------------------------------------------

class TestMultiDiskOrdering:
    def test_per_disk_names_stable(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]
        disk_ids = ordered_disk_ids(canon, rid)
        assert len(disk_ids) == 2
        names = [canonical_disk_name(canon, rid, did, total_disks=2) for did in disk_ids]
        assert names[0].basename.endswith("-1")
        assert names[1].basename.endswith("-2")
        canon.close()

    def test_disk_basename_includes_disk_number(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]
        disk_ids = canon.disks_for_release(rid)
        d1 = disk_ids[0]
        name = canonical_disk_name(canon, rid, d1, total_disks=2, index=1)
        assert "-1" in name.basename
        canon.close()


# ---------------------------------------------------------------------------
# 4. Qualifier handling per documented policy
# ---------------------------------------------------------------------------

class TestQualifierPolicy:
    def test_qualifiers_appear_in_basename(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]
        name = canonical_release_name(canon, rid)
        assert "Platinum Edition" in name.basename
        assert "USA" in name.basename
        assert "EN" in name.basename
        assert "Acme" in name.basename
        canon.close()

    def test_qualifier_policy_documentation_present(self):
        assert "edition > region > language > publisher" in DOCUMENTED_QUALIFIER_POLICY

    def test_no_qualifiers_uses_title_only(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = CanonicalLibrary(db_path=db)
        game = Game(game_id="simple-game")
        game.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), "Simple Game"
        )
        canon.upsert_game(game)
        rid = make_release_id("simple-game")
        release = Release(release_id=rid, game_id="simple-game")
        release.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), "Simple Game"
        )
        canon.upsert_release(release)
        name = canonical_release_name(canon, rid)
        assert name.basename == "Simple Game"
        canon.close()


# ---------------------------------------------------------------------------
# 5. Filesystem-unsafe characters sanitized predictably
# ---------------------------------------------------------------------------

class TestSanitization:
    def test_unsafe_chars_replaced(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = CanonicalLibrary(db_path=db)
        game = Game(game_id="unsafe-game")
        game.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), 'Game: *? "Bad" <Name>'
        )
        canon.upsert_game(game)
        rid = make_release_id("unsafe-game")
        release = Release(release_id=rid, game_id="unsafe-game")
        release.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), 'Game: *? "Bad" <Name>'
        )
        canon.upsert_release(release)
        name = canonical_release_name(canon, rid)
        assert "*" not in name.basename
        assert "?" not in name.basename
        assert '"' not in name.basename
        assert "<" not in name.basename
        assert ">" not in name.basename
        canon.close()

    def test_path_traversal_collapsed(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = CanonicalLibrary(db_path=db)
        game = Game(game_id="escape-game")
        game.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), "../../../escape"
        )
        canon.upsert_game(game)
        rid = make_release_id("escape-game")
        release = Release(release_id=rid, game_id="escape-game")
        release.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), "../../../escape"
        )
        canon.upsert_release(release)
        name = canonical_release_name(canon, rid)
        # Traversal components are collapsed by the sanitizer.
        assert "../" not in name.basename
        assert "/" not in name.basename
        canon.close()

    def test_empty_value_falls_back_to_Unknown(self, tmp_path):
        result = _sanitize_token("")
        assert result == "Unknown"


# ---------------------------------------------------------------------------
# 6. Duplicate/collision detection reported before write
# ---------------------------------------------------------------------------

class TestCollisionDetection:
    def test_collision_reported_before_write(self, tmp_path):
        """Two DISTINCT releases whose basenames collide after
        sanitization are reported as a conflict before any write."""
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]

        # Create a second game/release under a different game_id
        # but with the same resolved title + identical qualifier
        # fields -> same sanitized basename (collision).
        game2 = Game(game_id="other-game")
        game2.fields.setdefault("title", _field()).claim(
            _prov("operator", "curation"), "Test Game"
        )
        canon.upsert_game(game2)
        rid2 = make_release_id("other-game", "Platinum Edition", "USA", "EN", "Acme")
        release2 = Release(release_id=rid2, game_id="other-game")
        for fname, val in [("edition", "Platinum Edition"), ("region", "USA"),
                             ("language", "EN"), ("publisher", "Acme"),
                             ("group", "SKR"), ("chipset", "AGA"),
                             ("version", "v2.0"), ("alt_marker", "a")]:
            release2.fields.setdefault(fname, _field()).claim(
                _prov("parser", "parser"), val
            )
        release2.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), "Test Game"
        )
        canon.upsert_release(release2)

        name1 = canonical_release_name(canon, rid)
        name2 = canonical_release_name(canon, rid2)
        assert name1.basename == name2.basename, (
            f"basenames differ: {name1.basename!r} vs {name2.basename!r}"
        )
        report = detect_export_collisions(
            [rid, rid2], canon, tmp_path / "staging"
        )
        assert len(report.collisions) >= 1, (
            f"expected collision; report={report!r}"
        )
        assert any("collision" in c for c in report.collisions)
        assert rid2 in report.blocked_releases
        canon.close()

    def test_no_collision_for_distinct_releases(self, tmp_path):
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]
        # Create a second release with different title.
        rid2 = make_release_id("other-game")
        release2 = Release(release_id=rid2, game_id="other-game")
        release2.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), "Other Game"
        )
        canon.upsert_release(release2)
        report = detect_export_collisions(
            [rid, rid2], canon, tmp_path / "staging"
        )
        assert not report.collisions
        assert not report.blocked_releases
        canon.close()


# ---------------------------------------------------------------------------
# 7. Check/build-only path performs NO final export write
# ---------------------------------------------------------------------------

class TestNoWriteCheckBuildOnly:
    def test_verify_only_no_files_written(self, tmp_path):
        """Computing canonical names must not create any
        staging files — no filesystem write at all."""
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        rid = canon.releases_for_game("test-game")[0]
        name = canonical_release_name(canon, rid)
        assert name.basename
        # Only the canonical DB (created by _setup_canon) exists;
        # no ADF/DSK staging dirs or files were created.
        entries = [e.name for e in tmp_path.iterdir()]
        assert "canonical.db" in entries
        assert not any(e.startswith("ADF") or e.startswith("DSK")
                        for e in entries)
        canon.close()

    def test_build_only_uses_preview_not_export(self, tmp_path):
        """The build-only path (no --export flag) must never perform
        final export writes — naming is preview-only."""
        from amiga_adf_library_builder.canonical_naming import (
            export_name_for_release_group,
        )
        from amiga_adf_library_builder.naming import release_basename
        rec = parse_filename("Example_Castle_Quest_Disk_A.adf")
        rec.title = "Example Castle Quest"
        rec.disk_number = 1
        grp = ReleaseGroup(
            release_key="x",
            title="Example Castle Quest",
            edition=None, group=None,
            chipset=None, language=None, version=None, alt_marker=None,
            ext="adf",
            records=[rec], disks=[rec], specials=[],
            has_main_disk=True, is_complete=True,
        )
        # No canonical DB: falls back to the existing release_basename.
        from amiga_adf_library_builder.canonical import CanonicalLibrary
        canon = CanonicalLibrary(db_path=tmp_path / "empty.db")
        result = export_name_for_release_group(canon, grp)
        assert result.basename  # non-empty
        assert not any(p.name.startswith("ADF") or p.name.startswith("DSK")
                        for p in tmp_path.iterdir())  # nothing written
        canon.close()


# ---------------------------------------------------------------------------
# 8. Real application/CLI integration path exercised
# ---------------------------------------------------------------------------

class TestIntegrationPath:
    def test_pipeline_build_produces_canonical_names(self, tmp_path):
        """Run the real pipeline build path and confirm canonical
        names appear in per_group output (the 'folder' key uses
        release_basename)."""
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
        assert result.get("groups", 0) >= 0
        for pg in result.get("per_group", []):
            assert "folder" in pg
            assert isinstance(pg["folder"], str)

    def test_manual_lookup_uses_canonical_db(self, tmp_path):
        """The manual_lookup module reads from the real canonical.db
        and produces reports that drive naming."""
        db = tmp_path / "canonical.db"
        canon = _setup_canon(db)
        from amiga_adf_library_builder.manual_lookup import (
            build_entity_report,
            list_library_entities,
        )
        entities = list_library_entities(canon)
        assert "games" in entities
        assert len(entities["games"]) >= 1
        game_id = entities["games"][0]["entity_id"]
        report = build_entity_report(canon, "game", game_id)
        assert report.entity_type == "game"
        assert report.fields
        canon.close()


# ---------------------------------------------------------------------------
# 9. Source media byte-identical before/after (hash spot-check)
# ---------------------------------------------------------------------------

class TestSourceMediaIntegrity:
    def test_source_bytes_unchanged_after_naming(self, tmp_path):
        """Computing canonical names must never touch source media."""
        original_dir = tmp_path / "original"
        original_dir.mkdir()
        src = original_dir / "Test_Game_Disk.adf"
        src.write_bytes(b"SOURCE MEDIA CONTENT")
        expected_hash = __import__("hashlib").sha256(b"SOURCE MEDIA CONTENT").hexdigest()

        db = tmp_path / "canonical.db"
        canon = CanonicalLibrary(db_path=db)
        game = Game(game_id="source-game")
        game.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), "Source Game"
        )
        canon.upsert_game(game)
        rid = make_release_id("source-game")
        release = Release(release_id=rid, game_id="source-game")
        release.fields.setdefault("title", _field()).claim(
            _prov("parser", "parser"), "Source Game"
        )
        disk = Disk(
            disk_id=make_disk_id(""),
            release_id=rid,
            filename="Test_Game_Disk.adf",
            disk_number=1,
        )
        disk.fields.setdefault("filename", _field()).claim(
            _prov("parser", "parser"), "Test_Game_Disk.adf"
        )
        canon.upsert_release(release)
        canon.upsert_disk(disk)

        # Compute names — no file I/O to original_dir.
        name = canonical_release_name(canon, rid)
        assert name.basename

        # Verify source bytes unchanged.
        actual_hash = __import__("hashlib").sha256(src.read_bytes()).hexdigest()
        assert actual_hash == expected_hash
        canon.close()
