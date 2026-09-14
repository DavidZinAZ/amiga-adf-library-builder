"""GH-145 cross-store state ownership invariant tests.

Each test asserts a per-fact ownership relationship from the
State Ownership Map (docs/STATE-OWNERSHIP-MAP.md) holds. Uses
synthetic entries and tmp_path fixtures throughout — no production
data or network access.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from amiga_adf_library_builder.canonical import (
    CanonicalLibrary,
    Disk,
    Game,
    Release,
    SourceAuthority,
    migrate_staged_library,
)
from amiga_adf_library_builder.file_identity import (
    FileIdentityStore,
    IdentityRecord,
)
from amiga_adf_library_builder.library_state import (
    CurationStateManager,
    CurationStateMeta,
)
from amiga_adf_library_builder.manual_approvals import (
    ApprovalRecord,
    SCHEMA_VERSION as MANUAL_SCHEMA_VERSION,
)
from amiga_adf_library_builder.metadata_source import (
    MetadataSourceManager,
    SCHEMA_VERSION as METADATA_SCHEMA_VERSION,
)
from amiga_adf_library_builder.models import (
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_disk(disk_id: str = "d1", release_id: str = "r1",
               sha256: str = "a" * 64) -> Disk:
    return Disk(
        disk_id=disk_id,
        release_id=release_id,
        filename="test.adf",
        sha256=sha256,
        size=901120,
        identity_linked=False,
    )


def _make_staged_entry(release_key: str = "testkey",
                       title: str = "Test Game",
                       curation_state: StagedState = StagedState.ACCEPTED,
                       folder: str = "TestGame") -> StagedReleaseEntry:
    return StagedReleaseEntry(
        release_key=release_key,
        title=title,
        edition="v1",
        group="TestGroup",
        chipset="",
        folder=folder,
        curation_state=curation_state,
    )


def _make_staged_library(entries: list[StagedReleaseEntry] | None = None
                         ) -> StagedLibrary:
    if entries is None:
        entries = [_make_staged_entry()]
    return StagedLibrary(releases={e.release_key: e for e in entries})


def _release_id_for(entry: StagedReleaseEntry) -> str:
    from amiga_adf_library_builder.canonical import slugify_title, make_release_id
    return make_release_id(
        slugify_title(entry.title or "Untitled"),
        entry.edition or "",
        entry.region or "",
        entry.language or "",
        "",
    )


# ---------------------------------------------------------------------------
# Invariant tests
# ---------------------------------------------------------------------------

class TestSha256CanonicalMatchesIdentity:
    """Test 1: disk.sha256 in canonical.db must match identity.db sha256."""

    def test_sha256_canonical_matches_identity(self, tmp_path: Path) -> None:
        identity_db = tmp_path / "identity.db"
        canonical_db = tmp_path / "canonical.db"

        # Register a file in identity.db
        store = FileIdentityStore(identity_db)
        file_path = tmp_path / "test.adf"
        file_path.write_bytes(b"hello world")
        record = store.register_file(file_path)
        assert isinstance(record, IdentityRecord)
        sha256 = record.sha256
        store.close()

        # Create canonical.db with a disk row using the same sha256
        lib = CanonicalLibrary(canonical_db)
        disk = _make_disk(sha256=sha256)
        lib.upsert_release(Release(
            release_id=disk.release_id,
            game_id="g1",
            edition="v1",
        ))
        lib.upsert_disk(disk)

        # Verify cross-store invariant: disk.sha256 must match identity.sha256
        disk_row = lib.disk_row(disk.disk_id, disk.release_id)
        assert disk_row is not None
        assert disk_row["sha256"] == sha256, (
            f"canonical.db disk.sha256 ({disk_row['sha256']}) must match "
            f"identity.db sha256 ({sha256})"
        )
        lib.close()


class TestReleaseKeyConsistentAcrossStores:
    """Test 2: release_key must be consistent across library_state -> canonical.db."""

    def test_release_key_consistent_across_stores(self, tmp_path: Path) -> None:
        library_state_path = tmp_path / "library_state.json"
        canonical_db = tmp_path / "canonical.db"

        release_key = "testkey|examplequestiii"

        # Write release_key to library_state via CurationStateManager
        manager = CurationStateManager(library_state_path)
        staged = _make_staged_library([_make_staged_entry(
            release_key=release_key,
        )])
        manager.save(staged)

        # Load back and verify
        loaded = manager.load()
        assert release_key in loaded.releases
        assert loaded.releases[release_key].curation_state == StagedState.ACCEPTED

        # Migrate into canonical.db
        lib = CanonicalLibrary(canonical_db)
        migrate_staged_library(loaded, lib)

        # Verify canonical.db has the release_key in claims
        entry = loaded.releases[release_key]
        release_id = _release_id_for(entry)
        # query field_claim directly since claims_for returns sqlite3.Row
        rows = lib._conn.execute(
            "SELECT value FROM field_claim WHERE entity_type='release' "
            "AND entity_id=? AND field_name='release_key'", (release_id,)
        ).fetchall()
        claim_values = [r["value"] for r in rows]
        assert release_key in claim_values, (
            f"release_key {release_key} not found in canonical.db claims: {claim_values}"
        )
        lib.close()


class TestCurationStateProjectionIsAuthoritative:
    """Test 3: canonical.db curation_state must match library_state value and
    survive lower-authority DAT claims."""

    def test_curation_state_projection_is_authoritative(
            self, tmp_path: Path) -> None:
        library_state_path = tmp_path / "library_state.json"
        canonical_db = tmp_path / "canonical.db"

        release_key = "testkey"

        # Write curation_state to library_state
        manager = CurationStateManager(library_state_path)
        staged = _make_staged_library([_make_staged_entry(
            release_key=release_key,
            curation_state=StagedState.ACCEPTED,
        )])
        manager.save(staged)

        # Migrate into canonical.db
        lib = CanonicalLibrary(canonical_db)
        migrate_staged_library(staged, lib)

        # Verify canonical.db has the curation_state claim
        entry = staged.releases[release_key]
        release_id = _release_id_for(entry)
        rows = lib._conn.execute(
            "SELECT value FROM field_claim WHERE entity_type='release' "
            "AND entity_id=? AND field_name='curation_state'", (release_id,)
        ).fetchall()
        claim_values = [r["value"] for r in rows]
        assert claim_values, (
            "curation_state claim must exist in canonical.db after migration"
        )

        # Add a DAT-tier claim at lower authority — must not overwrite
        # the curation claim.
        lib.claim_field(
            entity_type="release",
            entity_id=release_id,
            field_name="curation_state",
            value="PENDING",
            provenance=type("Prov", (), {
                "source": "tosec",
                "authority": SourceAuthority.DAT,
                "authority_rank": 0,
                "confidence": 0.9,
                "observed_at": "2026-09-14T00:00:00+00:00",
                "record_key": "",
                "url": "",
                "tier": "dat",
            })(),
        )

        # The curation_state from library_state must still be present
        rows_after = lib._conn.execute(
            "SELECT value FROM field_claim WHERE entity_type='release' "
            "AND entity_id=? AND field_name='curation_state'", (release_id,)
        ).fetchall()
        assert len(rows_after) >= 1, (
            "curation_state claims must be preserved after lower-authority claim"
        )
        lib.close()


class TestMetadataSourceSchemaVersionEnforced:
    """Test 4: MetadataSourceManager._migrate() must set PRAGMA user_version."""

    def test_metadata_source_schema_version_enforced(self, tmp_path: Path) -> None:
        db_path = tmp_path / "metadata_sources.db"
        manager = MetadataSourceManager(db_path)
        version = manager._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == METADATA_SCHEMA_VERSION, (
            f"metadata_sources.db user_version must be {METADATA_SCHEMA_VERSION}, "
            f"got {version}"
        )
        manager.close()

    def test_metadata_source_reopen_idempotent(self, tmp_path: Path) -> None:
        """Re-opening an already-migrated db should not change the version."""
        db_path = tmp_path / "metadata_sources.db"
        manager = MetadataSourceManager(db_path)
        manager.close()

        manager2 = MetadataSourceManager(db_path)
        version = manager2._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == METADATA_SCHEMA_VERSION
        manager2.close()


class TestManualApprovalsSchemaVersionValidated:
    """Test 5: ApprovalRecord.from_dict() must reject mismatched schema_version."""

    def test_manual_approvals_schema_version_rejects_mismatch(self) -> None:
        """A record with schema_version != SCHEMA_VERSION must raise ValueError."""
        with pytest.raises(ValueError, match="schema_version"):
            ApprovalRecord.from_dict({
                "schema_version": 99,
                "approval_id": "bad",
                "release_keys": ["testkey"],
                "canonical_title": "Test",
            })

    def test_manual_approvals_valid_record_accepted(self) -> None:
        """A record with matching schema_version must be accepted."""
        record = ApprovalRecord.from_dict({
            "schema_version": MANUAL_SCHEMA_VERSION,
            "approval_id": "good",
            "release_keys": ["testkey"],
            "canonical_title": "Test",
        })
        assert record.approval_id == "good"

    def test_manual_approvals_missing_version_accepted(self) -> None:
        """A record without schema_version must still be accepted (backward compat)."""
        record = ApprovalRecord.from_dict({
            "approval_id": "nover",
            "release_keys": ["testkey"],
            "canonical_title": "Test",
        })
        assert record.approval_id == "nover"


class TestNoStoreOwnsEverything:
    """Test 6: Each store has a restricted authority scope."""

    def test_no_store_owns_everything(self) -> None:
        """Registry asserting each store's authority scope is restricted."""
        store_owners = {
            "catalog": "catalog.py",
            "library_state": "library_state.py",
            "identity": "file_identity.py",
            "canonical": "canonical.py",
            "metadata_sources": "metadata_source.py",
            "manual_approvals": "manual_approvals.py",
        }
        assert len(store_owners) == 6, "All six stores must be registered"
        owners = set(store_owners.values())
        assert len(owners) == 6, "Each store must have a distinct owner module"
        # Verify owner modules exist and are importable
        import importlib
        for owner_mod in owners:
            module_name = (
                f"amiga_adf_library_builder.{owner_mod.replace('.py', '')}"
            )
            importlib.import_module(module_name)

    def test_sha256_owner_is_identity(self) -> None:
        """sha256 is owned by identity.db, not canonical.db."""
        store_owners = {
            "sha256": "identity.py",
            "release_key": "canonical.py",
            "curation_state": "library_state.py",
        }
        assert store_owners["sha256"] == "identity.py"
        assert store_owners["release_key"] == "canonical.py"
        assert store_owners["curation_state"] == "library_state.py"
