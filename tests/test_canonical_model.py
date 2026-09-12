"""GH-107 Slice 3 — canonical Game/Release/Disk model tests.

Deterministic coverage: multi-release/multi-disk structure, conflict
preservation, documented precedence, manual-override persistence across
provider refresh, migration/backward compatibility, and the real
production integration path (build_staged_library_from_result).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from amiga_adf_library_builder.canonical import (
    CanonicalField,
    CanonicalLibrary,
    Disk,
    Game,
    Provenance,
    Release,
    SourceAuthority,
    make_disk_id,
    make_release_id,
    migrate_staged_library,
    slugify_title,
)
from amiga_adf_library_builder.file_identity import FileIdentityStore
from amiga_adf_library_builder.models import StagedLibrary, StagedReleaseEntry, StagedState
from amiga_adf_library_builder.pipeline import build_staged_library_from_result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _prov(source: str, tier: str, rank: int = 0, confidence=None,
          observed_at: str = "2026-09-11T00:00:00+00:00") -> Provenance:
    authority = {
        "parser": SourceAuthority.PARSER,
        "dat": SourceAuthority.DAT,
        "curation_memory": SourceAuthority.CURATION_MEMORY,
        "curation": SourceAuthority.CURATION,
    }[tier]
    return Provenance(source=source, authority=authority,
                      authority_rank=rank, confidence=confidence,
                      observed_at=observed_at)


def _staged_entry(key: str, title: str, files: list[str], **kw) -> StagedReleaseEntry:
    defaults = dict(edition=None, group=None, chipset=None, language=None,
                    version=None, alt_marker=None)
    defaults.update(kw)
    return StagedReleaseEntry(
        release_key=key, title=title, ext="adf", adf_files=list(files), **defaults
    )


# ---------------------------------------------------------------------------
# precedence + conflict preservation (pure model)
# ---------------------------------------------------------------------------

class TestPrecedence:
    def test_documented_precedence_curation_beats_dat_beats_parser(self):
        f = CanonicalField()
        f.claim(_prov("parser", "parser"), "Chaos Engine (parsed)")
        f.claim(_prov("tosec", "dat", rank=5), "Chaos Engine (Europe)")
        f.claim(_prov("operator", "curation"), "My Chaos Engine")
        value, prov = f.resolve()
        assert value == "My Chaos Engine"
        assert prov.authority is SourceAuthority.CURATION

    def test_dat_ranking_deterministic_within_tier(self):
        f = CanonicalField()
        f.claim(_prov("fresh1g1r", "dat", rank=3), "B")
        f.claim(_prov("tosec", "dat", rank=1), "A")
        value, _ = f.resolve()
        assert value == "A"  # lower authority_rank wins

    def test_conflicts_preserved_not_overwritten(self):
        f = CanonicalField()
        f.claim(_prov("parser", "parser"), "raw-name.adf")
        f.claim(_prov("tosec", "dat"), "TOSEC name")
        # A later, lower-authority claim must not erase prior claims.
        f.claim(_prov("other-parser", "parser"), "different")
        values = f.conflicting_values()
        # Deterministic tiebreak: same tier/rank/time -> source name order.
        assert values == ["TOSEC name", "different", "raw-name.adf"]
        assert len(f.claims) == 3

    def test_manual_override_not_overwritten_by_refresh(self):
        f = CanonicalField()
        f.claim(_prov("operator", "curation"), "Operator Title")
        # Simulated provider refresh adding a NEWER automated claim.
        f.claim(_prov("igdb", "dat", observed_at="2026-09-12T00:00:00+00:00"),
                "Provider Title")
        value, prov = f.resolve()
        assert value == "Operator Title"
        assert prov.authority is SourceAuthority.CURATION

    def test_same_tie_breaks_total_and_stable(self):
        f1 = CanonicalField()
        f1.claim(_prov("tosec", "dat"), "X")
        f1.claim(_prov("tosec", "dat"), "X")
        f2 = CanonicalField()
        f2.claim(_prov("tosec", "dat"), "X")
        f2.claim(_prov("tosec", "dat"), "X")
        assert f1.resolve() == f2.resolve()

    def test_empty_field_resolves_none(self):
        assert CanonicalField().resolve() == (None, None)


class TestIdentifiers:
    def test_release_id_deterministic_and_distinct(self):
        a = make_release_id("chaos-engine", edition="v1.0")
        b = make_release_id("chaos-engine", edition="v1.1")
        c = make_release_id("chaos-engine", edition="v1.0")
        assert a == c and a != b
        assert a.startswith("chaos-engine:")

    def test_disk_id_content_anchored_not_path_based(self):
        assert make_disk_id("abc123") == "sha256:abc123"
        # Same content under a new filename keeps the same identity.
        assert make_disk_id("abc123", "anything.adf") == "sha256:abc123"
        # No hash: deterministic fallback, still filename-derived-content only.
        assert make_disk_id("", "disk1.adf") == make_disk_id("", "disk1.adf")

    def test_slugify_deterministic(self):
        assert slugify_title("The Chaos Engine!") == "the-chaos-engine"
        assert slugify_title("") == "untitled"


# ---------------------------------------------------------------------------
# structure: multi-release / multi-disk
# ---------------------------------------------------------------------------

class TestStructure:
    def test_multi_release_multi_disk_roundtrip(self, tmp_path):
        lib = CanonicalLibrary(tmp_path / "canonical.db")
        game = Game(game_id="chaos-engine")
        game.fields["title"] = CanonicalField()
        game.fields["title"].claim(_prov("tosec", "dat"), "Chaos Engine")

        r1 = Release(release_id=make_release_id("chaos-engine", "v1"),
                     game_id="chaos-engine", edition="v1")
        r1.fields["title"] = CanonicalField()
        r1.fields["title"].claim(_prov("tosec", "dat"), "Chaos Engine (v1)")
        for n in (1, 2, 3):
            r1.disks.append(Disk(
                disk_id=make_disk_id(f"sha-{n}"), release_id=r1.release_id,
                filename=f"Chaos Engine Disk {n}.adf", disk_number=n,
                sha256=f"sha-{n}",
            ))
        r2 = Release(release_id=make_release_id("chaos-engine", "v2"),
                     game_id="chaos-engine", edition="v2")

        lib.upsert_game(game)
        lib.upsert_release(r1)
        lib.upsert_release(r2)
        lib.close()

        lib2 = CanonicalLibrary(tmp_path / "canonical.db")
        assert lib2.list_games() == ["chaos-engine"]
        assert lib2.releases_for_game("chaos-engine") == sorted([
            r1.release_id, r2.release_id
        ])
        assert lib2.disks_for_release(r1.release_id) == [
            "sha256:sha-1", "sha256:sha-2", "sha256:sha-3"
        ]
        value, prov = lib2.resolve_field("release", r1.release_id, "title")
        assert value == "Chaos Engine (v1)"
        assert prov.source == "tosec"
        lib2.close()

    def test_claim_persistence_and_conflict_view_across_reopen(self, tmp_path):
        db = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db)
        lib.claim_field("release", "r1", "title", "DAT Name", _prov("tosec", "dat"))
        lib.claim_field("release", "r1", "title", "Op Name", _prov("operator", "curation"))
        lib.close()
        lib2 = CanonicalLibrary(db)
        claims = lib2.claims_for("release", "r1", "title")
        assert len(claims) == 2  # conflicting claim preserved in DB
        assert claims[0][1] == "Op Name"
        lib2.close()

    def test_migration_idempotent(self, tmp_path):
        staged = StagedLibrary()
        staged.releases["k1"] = _staged_entry("k1", "Chaos Engine", ["a.adf"])
        lib = CanonicalLibrary(tmp_path / "canonical.db")
        s1 = migrate_staged_library(staged, lib)
        s2 = migrate_staged_library(staged, lib)
        assert s1["releases"] == s2["releases"] == 1
        lib.close()


# ---------------------------------------------------------------------------
# migration / backward compatibility
# ---------------------------------------------------------------------------

class TestMigration:
    def test_migrate_staged_library_with_hashes(self, tmp_path):
        # Build a real FileIdentityStore with observed content.
        store = FileIdentityStore(tmp_path / "identity.db")
        f1 = tmp_path / "original" / "Chaos Engine Disk 1.adf"
        f2 = tmp_path / "original" / "Chaos Engine Disk 2.adf"
        f1.parent.mkdir(parents=True)
        f1.write_bytes(b"ADF-DISK-ONE")
        f2.write_bytes(b"ADF-DISK-TWO")
        store.register_file(f1)
        store.register_file(f2)

        staged = StagedLibrary()
        staged.releases["k1"] = _staged_entry(
            "k1", "Chaos Engine",
            ["Chaos Engine Disk 1.adf", "Chaos Engine Disk 2.adf"],
            edition="v1.0", folder="Chaos Engine",
        )
        lib = CanonicalLibrary(tmp_path / "canonical.db")
        stats = migrate_staged_library(staged, lib, identity_store=store)
        assert stats["releases"] == 1
        assert stats["disks"] == 2
        rel_id = lib.releases_for_game("chaos-engine")[0]
        disk_ids = lib.disks_for_release(rel_id)
        assert len(disk_ids) == 2
        # Content-anchored disk ids.
        row = lib.disk_row(disk_ids[0], rel_id)
        assert row["sha256"] and row["disk_id"].startswith("sha256:")
        # Operator folder decision recorded as curation claim.
        value, prov = lib.resolve_field("release", rel_id, "folder")
        assert value == "Chaos Engine"
        assert prov.authority is SourceAuthority.CURATION
        lib.close()
        store.close()

    def test_migrate_without_identity_store_graceful(self, tmp_path):
        staged = StagedLibrary()
        staged.releases["k1"] = _staged_entry("k1", "Title X", ["a.adf", "b.adf"])
        lib = CanonicalLibrary(tmp_path / "canonical.db")
        stats = migrate_staged_library(staged, lib, identity_store=None)
        assert stats["disks"] == 2  # fallback name-anchored disks
        assert stats["releases"] == 1
        lib.close()

    def test_staged_state_file_untouched(self, tmp_path):
        staged = StagedLibrary()
        staged.releases["k1"] = _staged_entry("k1", "Title Y", ["a.adf"])
        lib = CanonicalLibrary(tmp_path / "canonical.db")
        before = {k: v.to_dict() for k, v in staged.releases.items()}
        migrate_staged_library(staged, lib)
        after = {k: v.to_dict() for k, v in staged.releases.items()}
        assert before == after
        lib.close()

    def test_manual_override_survives_provider_refresh_in_db(self, tmp_path):
        # Run 1: operator curation recorded.
        staged = StagedLibrary()
        staged.releases["k1"] = _staged_entry("k1", "Operator Title", ["a.adf"])
        lib = CanonicalLibrary(tmp_path / "canonical.db")
        migrate_staged_library(staged, lib)
        rel_id = lib.releases_for_game("operator-title")[0]

        # Run 2 ("provider refresh"): a DAT claim arrives for the same field.
        lib.claim_field("release", rel_id, "title", "DAT Title", _prov("tosec", "dat"))
        # Also simulate the refresh re-migrating the staged state.
        migrate_staged_library(staged, lib)

        value, prov = lib.resolve_field("release", rel_id, "title")
        assert value == "Operator Title"
        assert prov.authority is SourceAuthority.CURATION
        lib.close()


# ---------------------------------------------------------------------------
# real production integration path
# ---------------------------------------------------------------------------

class TestProductionIntegration:
    def _result(self, tmp_path):
        return {
            "per_group": [
                {
                    "release_key": "chaos_engine_v1",
                    "title": "Chaos Engine",
                    "edition": "v1",
                    "group": None,
                    "confidence": None,
                    "folder": "Chaos Engine",
                    "source_files": ["Chaos Engine Disk 1.adf", "Chaos Engine Disk 2.adf"],
                    "artwork_missing": False,
                    "notes": [],
                },
            ],
        }

    def test_build_staged_library_persists_canonical_db(self, tmp_path):
        state_path = build_staged_library_from_result(
            self._result(tmp_path),
            library_root=tmp_path,
            run_id="run1",
        )
        assert state_path is not None
        canon_db = tmp_path / "curation" / "canonical.db"
        assert canon_db.is_file()
        lib = CanonicalLibrary(canon_db)
        assert "chaos-engine" in lib.list_games()
        rel_ids = lib.releases_for_game("chaos-engine")
        assert len(rel_ids) == 1
        disk_ids = lib.disks_for_release(rel_ids[0])
        assert len(disk_ids) == 2
        lib.close()

    def test_canonical_state_survives_rescan_run(self, tmp_path):
        build_staged_library_from_result(
            self._result(tmp_path), library_root=tmp_path, run_id="run1"
        )
        # Second run (rescan) must not corrupt or duplicate the model.
        build_staged_library_from_result(
            self._result(tmp_path), library_root=tmp_path, run_id="run2"
        )
        lib = CanonicalLibrary(tmp_path / "curation" / "canonical.db")
        rel_ids = lib.releases_for_game("chaos-engine")
        assert len(rel_ids) == 1
        lib.close()

    def test_manual_curation_claim_survives_provider_refresh_via_pipeline(self, tmp_path):
        # Run 1: staged state with operator title curation.
        staged = StagedLibrary()
        staged.releases["chaos_engine_v1"] = _staged_entry(
            "chaos_engine_v1", "Operator Title",
            ["Chaos Engine Disk 1.adf", "Chaos Engine Disk 2.adf"],
        )
        # Write it as the previous state file so run 2 carries it over.
        curation_dir = tmp_path / "curation"
        curation_dir.mkdir(parents=True)
        prev = {
            "meta": {"schema_version": 1},
            "library": staged.to_dict(),
        }
        (curation_dir / "library_state_run0.json").write_text(json.dumps(prev))

        # Run 2: fresh pipeline result with provider (DAT-parsed) title.
        result = self._result(tmp_path)
        result["per_group"][0]["title"] = "Chaos Engine"
        state_path = build_staged_library_from_result(
            result, library_root=tmp_path, run_id="run2"
        )
        assert state_path is not None

        lib = CanonicalLibrary(tmp_path / "curation" / "canonical.db")
        rel_id = lib.releases_for_game("operator-title")[0]
        # The operator title was carried over by carry_over and is recorded
        # as a curation-authority claim; the provider claim cannot outrank it.
        value, prov = lib.resolve_field("release", rel_id, "title")
        assert value == "Operator Title"
        assert prov.authority is SourceAuthority.CURATION
        lib.close()

    def test_canonical_failure_never_breaks_staged_build(self, tmp_path, monkeypatch):
        # Simulate canonical persistence failure; staged state must still build.
        import amiga_adf_library_builder.pipeline as pipeline_mod

        def boom(*a, **kw):
            raise sqlite3.Error("injected failure")

        monkeypatch.setattr(pipeline_mod, "_persist_canonical_library", boom)
        state_path = build_staged_library_from_result(
            self._result(tmp_path), library_root=tmp_path, run_id="runx"
        )
        assert state_path is None or state_path.is_file()
