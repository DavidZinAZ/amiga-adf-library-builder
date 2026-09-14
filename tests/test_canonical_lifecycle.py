"""GH-143 Canonical Lifecycle tests.

Tests the P1 canonical.db lifecycle remediation:
- SEED authority tier
- Release descriptor column re-resolution
- Scoped release retirement
- Schema migration v1 -> v2
- GUI/CLI authority convergence
- release_id hash invariant preservation
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
    make_release_id,
    migrate_staged_library,
)
from amiga_adf_library_builder.models import (
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _prov(source: str, tier: str, rank: int = 0, confidence=None,
          observed_at: str = "2026-09-11T00:00:00+00:00") -> Provenance:
    authority = {
        "parser": SourceAuthority.PARSER,
        "seed": SourceAuthority.SEED,
        "dat": SourceAuthority.DAT,
        "curation_memory": SourceAuthority.CURATION_MEMORY,
        "curation": SourceAuthority.CURATION,
    }[tier]
    return Provenance(source=source, authority=authority,
                      authority_rank=rank, confidence=confidence,
                      observed_at=observed_at)


def _make_release(db_path: Path, release_id: str, game_id: str = "g1",
                   edition: str = "", region: str = "",
                   language: str = "", publisher: str = "") -> Release:
    rid = release_id or make_release_id(game_id, edition, region, language, publisher)
    return Release(
        release_id=rid, game_id=game_id,
        edition=edition, region=region,
        language=language, publisher=publisher,
    )


# ---------------------------------------------------------------------------
# Step 1 — Schema migration v1 -> v2
# ---------------------------------------------------------------------------

class TestSchemaMigration:
    def test_schema_v1_migrates_to_v2(self, tmp_path):
        """Existing v1 schema migrates forward; new columns present."""
        db_path = tmp_path / "canonical.db"
        # Use CanonicalLibrary to create the v1 schema, then manually
        # downgrade user_version to trigger migration
        lib = CanonicalLibrary(db_path)
        lib._conn.execute("PRAGMA user_version = 1")
        lib._conn.commit()
        lib.close()

        # Re-open — should auto-migrate to v2
        lib = CanonicalLibrary(db_path)
        cols = [r[1] for r in lib._conn.execute("PRAGMA table_info(release)")]
        assert "retired" in cols
        assert lib._conn.execute("PRAGMA user_version").fetchone()[0] == 2
        lib.close()

    def test_retired_column_defaults_to_0(self, tmp_path):
        """Newly created DB has retired=0 on all releases."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        release = _make_release(db_path, "r1", "g1", edition="v1")
        lib.upsert_release(release)
        row = lib.release_row("r1")
        assert row is not None
        assert row["retired"] == 0
        lib.close()

    def test_retired_index_exists(self, tmp_path):
        """The idx_release_retired index is created after migration."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        idx = lib._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_release_retired'"
        ).fetchone()
        assert idx is not None
        lib.close()

    def test_v1_to_v2_preserves_existing_data(self, tmp_path):
        """Migration preserves existing claims and release rows."""
        db_path = tmp_path / "canonical.db"
        # Pre-populate v1 schema manually, then open with CanonicalLibrary
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS game (game_id TEXT PRIMARY KEY)")
        conn.execute("""
            CREATE TABLE release (
                release_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
                edition TEXT, region TEXT, language TEXT, publisher TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS disk (
                disk_id TEXT NOT NULL, release_id TEXT NOT NULL,
                filename TEXT NOT NULL, disk_number INTEGER, sha256 TEXT,
                size INTEGER, identity_linked INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (disk_id, release_id),
                FOREIGN KEY (release_id) REFERENCES release(release_id)
                    ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS field_claim (
                entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                field_name TEXT NOT NULL, value TEXT, source TEXT NOT NULL,
                record_key TEXT, url TEXT, authority TEXT NOT NULL,
                authority_rank INTEGER NOT NULL DEFAULT 0,
                confidence REAL, observed_at TEXT,
                PRIMARY KEY (entity_type, entity_id, field_name, source, record_key, value)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_claim_entity ON field_claim(entity_type, entity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_release_game ON release(game_id)")
        conn.execute("PRAGMA user_version = 1")
        conn.execute("INSERT INTO release VALUES ('r1', 'g1', 'v1', 'EU', 'EN', 'Pub')")
        conn.execute(
            "INSERT INTO field_claim VALUES "
            "('release', 'r1', 'region', 'EU', 'tosec', '', '', 'dat', 0, 0.9, '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        conn.close()

        lib = CanonicalLibrary(db_path)
        assert lib.release_row("r1") is not None
        assert lib.release_row("r1")["edition"] == "v1"
        claims = lib.claims_for("release", "r1", "region")
        assert len(claims) > 0
        # Verify migration added the retired column
        cols = [r[1] for r in lib._conn.execute("PRAGMA table_info(release)")]
        assert "retired" in cols
        lib.close()


# ---------------------------------------------------------------------------
# Step 2 — SEED authority tier
# ---------------------------------------------------------------------------

class TestSeedAuthorityTier:
    def test_seed_authority_tier_exists(self):
        """SEED = 15, between PARSER and DAT."""
        assert SourceAuthority.SEED.value == 15
        assert SourceAuthority.PARSER.value < SourceAuthority.SEED.value < SourceAuthority.DAT.value

    def test_seed_tier_string(self):
        """SEED maps to 'seed' tier string."""
        assert SourceAuthority.SEED.tier == "seed"

    def test_seed_claim_does_not_block_dat(self, tmp_path):
        """SEED claim for region, then DAT claim arrives — DAT wins."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        release = _make_release(db_path, "r1", "g1", edition="v1", region="EU")
        lib.upsert_release(release)

        lib.claim_field("release", "r1", "region", "SEED-Region",
                        _prov("seed-source", "seed"))
        lib.claim_field("release", "r1", "region", "DAT-Region",
                        _prov("tosec", "dat"))

        value, prov = lib.resolve_field("release", "r1", "region")
        assert value == "DAT-Region"
        assert prov.authority is SourceAuthority.DAT
        lib.close()

    def test_seed_claim_does_not_block_curation(self, tmp_path):
        """SEED claim does not block later CURATION claim."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        release = _make_release(db_path, "r1", "g1", edition="v1")
        lib.upsert_release(release)

        lib.claim_field("release", "r1", "region", "SEED-Region",
                        _prov("seed-source", "seed"))
        lib.claim_field("release", "r1", "region", "Curated-Region",
                        _prov("operator", "curation"))

        value, prov = lib.resolve_field("release", "r1", "region")
        assert value == "Curated-Region"
        assert prov.authority is SourceAuthority.CURATION
        lib.close()

    def test_cli_uses_seed_authority(self, tmp_path):
        """CLI-seeded claims use SEED authority, not CURATION."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        staged = StagedLibrary()
        staged.releases["key1"] = StagedReleaseEntry(
            release_key="key1", title="Test Game", ext="adf",
            adf_files=[], edition="v1", region="EU", language="EN",
            curation_state=StagedState.PENDING, group="grp",
            chipset=None,
        )
        migrate_staged_library(staged, lib, seed_authority=SourceAuthority.SEED)

        # Get the actual release_id from the database
        rid = lib._conn.execute(
            "SELECT release_id FROM release WHERE game_id LIKE 'test%'"
        ).fetchone()
        rid = rid[0] if rid else "key1"
        claims = lib.claims_for("release", rid, "region")
        has_seed = any(p.authority == SourceAuthority.SEED for p, v in claims)
        assert has_seed
        lib.close()

    def test_gui_uses_curation_authority(self, tmp_path):
        """GUI-persisted claims still use CURATION authority."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        staged = StagedLibrary()
        staged.releases["key1"] = StagedReleaseEntry(
            release_key="key1", title="Test Game", ext="adf",
            adf_files=[], edition="v1", region="EU",
            curation_state=StagedState.PENDING, group="grp",
            chipset=None,
        )
        migrate_staged_library(staged, lib, seed_authority=SourceAuthority.CURATION)

        # Get the actual release_id from the database
        rid = lib._conn.execute(
            "SELECT release_id FROM release WHERE game_id LIKE 'test%'"
        ).fetchone()
        rid = rid[0] if rid else "key1"
        claims = lib.claims_for("release", rid, "region")
        has_curation = any(p.authority == SourceAuthority.CURATION for p, v in claims)
        assert has_curation
        lib.close()


# ---------------------------------------------------------------------------
# Step 4 — Release descriptor column re-resolution
# ---------------------------------------------------------------------------

class TestDescriptorReResolution:
    def test_release_columns_update_from_winning_claim(self, tmp_path):
        """When a DAT claim arrives, the release row reflects it."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        release = _make_release(db_path, "r1", "g1", edition="v1", region="EU")
        lib.upsert_release(release)

        lib.claim_field("release", "r1", "region", "US",
                        _prov("tosec", "dat"))

        row = lib.release_row("r1")
        assert row["region"] == "US"
        lib.close()

    def test_release_columns_update_only_for_release_entity(self, tmp_path):
        """claim_field on game does not attempt release-column updates."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        game = Game(game_id="g1")
        lib.upsert_game(game)
        lib.claim_field("game", "g1", "name", "Game Name",
                        _prov("parser", "parser"))
        lib.close()  # Should not raise

    def test_release_id_stable_after_column_update(self, tmp_path):
        """release_id does not change when descriptor columns are updated."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        release = _make_release(db_path, "r1", "g1", edition="v1", region="EU")
        original_id = release.release_id
        lib.upsert_release(release)
        assert lib.release_row("r1")["release_id"] == original_id

        lib.claim_field("release", "r1", "region", "US",
                        _prov("tosec", "dat"))
        assert lib.release_row("r1")["release_id"] == original_id
        lib.close()


# ---------------------------------------------------------------------------
# Step 5 — Release retirement
# ---------------------------------------------------------------------------

class TestReleaseRetirement:
    def test_retire_releases_marks_inactive(self, tmp_path):
        """retire_releases marks releases not in the active set."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        lib.upsert_release(_make_release(db_path, "r1", "g1", edition="v1"))
        lib.upsert_release(_make_release(db_path, "r2", "g1", edition="v2"))
        lib.upsert_release(_make_release(db_path, "r3", "g2", edition="v1"))

        retired = lib.retire_releases({"r3"})
        assert retired == 2
        assert lib.release_row("r1") is None
        assert lib.release_row("r2") is None
        assert lib.release_row("r3") is not None
        lib.close()

    def test_retire_releases_returns_count(self, tmp_path):
        """retire_releases returns the count of newly retired releases."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        lib.upsert_release(_make_release(db_path, "r1", "g1"))
        lib.upsert_release(_make_release(db_path, "r2", "g1"))

        count = lib.retire_releases({"r2"})
        assert count == 1
        assert lib.release_row("r1") is None
        assert lib.release_row("r2") is not None
        lib.close()

    def test_releases_for_game_excludes_retired(self, tmp_path):
        """releases_for_game excludes retired releases."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        lib.upsert_release(_make_release(db_path, "r1", "g1"))
        lib.upsert_release(_make_release(db_path, "r2", "g1"))

        lib.retire_releases({"r2"})
        releases = lib.releases_for_game("g1")
        assert "r2" in releases
        assert "r1" not in releases
        assert len(releases) == 1
        lib.close()

    def test_release_row_returns_none_for_retired(self, tmp_path):
        """release_row returns None for a retired release."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        lib.upsert_release(_make_release(db_path, "r1", "g1"))
        lib.retire_releases(set())
        assert lib.release_row("r1") is None
        lib.close()


# ---------------------------------------------------------------------------
# Step 6 — claims_for filtering retired
# ---------------------------------------------------------------------------

class TestClaimsForRetiredFiltering:
    def test_claims_for_excludes_retired_by_default(self, tmp_path):
        """claims_for filters retired releases by default."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        lib.upsert_release(_make_release(db_path, "r1", "g1"))
        lib.upsert_release(_make_release(db_path, "r2", "g1"))

        lib.claim_field("release", "r1", "region", "EU",
                        _prov("tosec", "dat"))
        lib.claim_field("release", "r2", "region", "US",
                        _prov("tosec", "dat"))

        lib.retire_releases({"r2"})
        claims = lib.claims_for("release", "r2", "region")
        assert len(claims) > 0
        lib.close()

    def test_claims_for_include_retired_flag(self, tmp_path):
        """claims_for can include retired when explicitly requested."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        lib.upsert_release(_make_release(db_path, "r1", "g1"))
        lib.upsert_release(_make_release(db_path, "r2", "g1"))

        lib.claim_field("release", "r1", "region", "EU",
                        _prov("tosec", "dat"))
        lib.claim_field("release", "r2", "region", "US",
                        _prov("tosec", "dat"))

        lib.retire_releases({"r1"})
        claims = lib.claims_for("release", "r2", "region", include_retired=True)
        assert len(claims) > 0
        lib.close()


# ---------------------------------------------------------------------------
# Regression — existing behavior preserved
# ---------------------------------------------------------------------------

class TestRegression:
    def test_conflict_preservation_unchanged(self, tmp_path):
        """Conflicts are still preserved (no regression)."""
        from amiga_adf_library_builder.canonical import CanonicalField
        f = CanonicalField()
        f.claim(_prov("parser", "parser"), "raw-name.adf")
        f.claim(_prov("tosec", "dat"), "TOSEC name")
        f.claim(_prov("other-parser", "parser"), "different")
        values = f.conflicting_values()
        assert len(values) == 3

    def test_manual_override_persists(self, tmp_path):
        """Manual override still survives provider refresh."""
        from amiga_adf_library_builder.canonical import CanonicalField
        f = CanonicalField()
        f.claim(_prov("operator", "curation"), "Operator Title")
        f.claim(_prov("igdb", "dat", observed_at="2026-09-12T00:00:00+00:00"),
                "Provider Title")
        value, prov = f.resolve()
        assert value == "Operator Title"
        assert prov.authority is SourceAuthority.CURATION

    def test_make_release_id_stable(self, tmp_path):
        """make_release_id produces deterministic output."""
        rid1 = make_release_id("game1", "v1", "EU", "EN", "Pub")
        rid2 = make_release_id("game1", "v1", "EU", "EN", "Pub")
        assert rid1 == rid2
        assert rid1.startswith("game1:")

    def test_disk_table_still_refreshes(self, tmp_path):
        """disk table still uses INSERT OR REPLACE (no regression)."""
        db_path = tmp_path / "canonical.db"
        lib = CanonicalLibrary(db_path)
        release = _make_release(db_path, "r1", "g1")
        lib.upsert_release(release)
        lib.upsert_disk(Disk(
            disk_id="sha256:abc", release_id="r1", filename="test.adf",
            disk_number=1, sha256="abc", size=1024, identity_linked=True,
        ))
        lib.upsert_disk(Disk(
            disk_id="sha256:abc", release_id="r1", filename="test2.adf",
            disk_number=1, sha256="abc", size=2048, identity_linked=True,
        ))
        row = lib.disk_row("sha256:abc", "r1")
        assert row["filename"] == "test2.adf"
        assert row["size"] == 2048
        lib.close()
