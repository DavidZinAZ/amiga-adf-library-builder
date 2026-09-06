"""Tests for the library_state module (GH-80)."""
import json
import tempfile
from pathlib import Path

import pytest

from amiga_adf_library_builder.library_state import (
    CurationStateManager,
    CurationStateMeta,
    CurationStateFile,
    create_curation_state_manager,
)
from amiga_adf_library_builder.models import (
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
    StagedChange,
    CurationAction,
)


class TestCurationStateMeta:
    def test_default_values(self):
        meta = CurationStateMeta()
        assert meta.schema_version == 1
        assert meta.created_at == ""
        assert meta.updated_at == ""
        assert meta.source_workspace == ""
        assert meta.entry_count == 0

    def test_to_dict(self):
        meta = CurationStateMeta(
            schema_version=1,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-02T00:00:00",
            source_workspace="test",
            entry_count=5,
        )
        d = meta.to_dict()
        assert d["schema_version"] == 1
        assert d["created_at"] == "2024-01-01T00:00:00"
        assert d["updated_at"] == "2024-01-02T00:00:00"
        assert d["source_workspace"] == "test"
        assert d["entry_count"] == 5

    def test_from_dict(self):
        d = {
            "schema_version": 1,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-02T00:00:00",
            "source_workspace": "test",
            "entry_count": 5,
        }
        meta = CurationStateMeta.from_dict(d)
        assert meta.schema_version == 1
        assert meta.created_at == "2024-01-01T00:00:00"
        assert meta.updated_at == "2024-01-02T00:00:00"
        assert meta.source_workspace == "test"
        assert meta.entry_count == 5


class TestCurationStateFile:
    def test_empty_file(self):
        state_file = CurationStateFile()
        assert state_file.meta is not None
        assert state_file.library is not None
        assert len(state_file.library.releases) == 0

    def test_to_dict(self):
        lib = StagedLibrary()
        entry = StagedReleaseEntry(
            release_key="test_001",
            title="Test Game",
            edition=None,
            group=None,
            chipset="OCS",
            language="en",
            version="1.0",
            alt_marker=None,
            ext="adf",
            curation_state=StagedState.PENDING,
        )
        lib.releases["test_001"] = entry

        state_file = CurationStateFile(
            meta=CurationStateMeta(
                created_at="2024-01-01T00:00:00",
                updated_at="2024-01-02T00:00:00",
                source_workspace="test",
                entry_count=1,
            ),
            library=lib,
        )
        d = state_file.to_dict()
        assert "meta" in d
        assert "library" in d
        assert d["library"]["releases"]["test_001"]["release_key"] == "test_001"

    def test_from_dict(self):
        lib = StagedLibrary()
        entry = StagedReleaseEntry(
            release_key="test_001",
            title="Test Game",
            edition=None,
            group=None,
            chipset="OCS",
            language="en",
            version="1.0",
            alt_marker=None,
            ext="adf",
            curation_state=StagedState.PENDING,
        )
        lib.releases["test_001"] = entry

        state_file = CurationStateFile(
            meta=CurationStateMeta(
                created_at="2024-01-01T00:00:00",
                updated_at="2024-01-02T00:00:00",
                source_workspace="test",
                entry_count=1,
            ),
            library=lib,
        )
        d = state_file.to_dict()
        restored = CurationStateFile.from_dict(d)
        assert restored.library.releases["test_001"].release_key == "test_001"
        assert restored.meta.source_workspace == "test"


class TestCurationStateManager:
    def test_load_no_file(self):
        """Test loading when no state file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            manager = create_curation_state_manager(state_path)
            library = manager.load()
            assert isinstance(library, StagedLibrary)
            assert len(library.releases) == 0
            # Manager should have created empty state
            assert manager.get_current_state() is not None

    def test_load_empty_file(self):
        """Test loading when state file is empty/invalid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            state_path.write_text("")
            manager = create_curation_state_manager(state_path)
            library = manager.load()
            assert isinstance(library, StagedLibrary)
            assert len(library.releases) == 0

    def test_load_malformed_json(self):
        """Test loading when state file has malformed JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            state_path.write_text("{ not valid json")
            manager = create_curation_state_manager(state_path)
            library = manager.load()
            assert isinstance(library, StagedLibrary)
            assert len(library.releases) == 0

    def test_load_invalid_schema(self):
        """Test loading when state file has invalid schema version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            # Write a file with wrong schema version
            data = {
                "meta": {
                    "schema_version": 999,
                    "created_at": "2024-01-01T00:00:00",
                    "updated_at": "2024-01-01T00:00:00",
                    "source_workspace": "test_workspace",
                    "entry_count": 0,
                },
                "library": {"releases": {}},
            }
            state_path.write_text(json.dumps(data))
            manager = create_curation_state_manager(state_path)
            library = manager.load()
            assert isinstance(library, StagedLibrary)
            assert len(library.releases) == 0

    def test_save_and_load(self):
        """Test saving and loading a library."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            manager = create_curation_state_manager(state_path)

            lib = StagedLibrary()
            entry = StagedReleaseEntry(
                release_key="test_001",
                title="Test Game",
                edition=None,
                group=None,
                chipset="OCS",
                language="en",
                version="1.0",
                alt_marker=None,
                ext="adf",
                curation_state=StagedState.PENDING,
            )
            lib.releases["test_001"] = entry

            result = manager.save(lib)
            assert result is True
            assert state_path.exists()

            # Load it back
            library = manager.load()
            assert len(library.releases) == 1
            assert library.releases["test_001"].title == "Test Game"

    def test_save_updates_existing(self):
        """Test saving updates an existing state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            manager = create_curation_state_manager(state_path)

            # Initial save
            lib = StagedLibrary()
            entry = StagedReleaseEntry(
                release_key="test_001",
                title="Test Game",
                edition=None,
                group=None,
                chipset="OCS",
                language="en",
                version="1.0",
                alt_marker=None,
                ext="adf",
                curation_state=StagedState.PENDING,
            )
            lib.releases["test_001"] = entry
            manager.save(lib)

            # Update and save again
            lib2 = manager.load()
            entry2 = StagedReleaseEntry(
                release_key="test_002",
                title="Another Game",
                edition=None,
                group=None,
                chipset="ECS",
                language="en",
                version="2.0",
                alt_marker=None,
                ext="adf",
                curation_state=StagedState.ACCEPTED,
            )
            lib2.releases["test_002"] = entry2
            manager.save(lib2)

            # Load and verify both entries
            library = manager.load()
            assert len(library.releases) == 2
            assert library.releases["test_001"].title == "Test Game"
            assert library.releases["test_002"].title == "Another Game"

    def test_atomic_write(self):
        """Test that writes are atomic (temp file + rename)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            manager = create_curation_state_manager(state_path)

            lib = StagedLibrary()
            entry = StagedReleaseEntry(
                release_key="test_001",
                title="Test Game",
                edition=None,
                group=None,
                chipset="OCS",
                language="en",
                version="1.0",
                alt_marker=None,
                ext="adf",
                curation_state=StagedState.PENDING,
            )
            lib.releases["test_001"] = entry

            result = manager.save(lib)
            assert result is True
            # Temp file should not exist after successful write
            temp_file = state_path.with_suffix(".json.tmp")
            assert not temp_file.exists()

    def test_clear(self):
        """Test clearing the state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            manager = create_curation_state_manager(state_path)

            lib = StagedLibrary()
            entry = StagedReleaseEntry(
                release_key="test_001",
                title="Test Game",
                edition=None,
                group=None,
                chipset="OCS",
                language="en",
                version="1.0",
                alt_marker=None,
                ext="adf",
                curation_state=StagedState.PENDING,
            )
            lib.releases["test_001"] = entry
            manager.save(lib)
            assert state_path.exists()

            result = manager.clear()
            assert result is True
            assert not state_path.exists()
            assert manager.get_current_state() is None

    def test_persistence_roundtrip_with_history(self):
        """Test full persistence roundtrip including change history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            manager = create_curation_state_manager(state_path)

            lib = StagedLibrary()
            entry = StagedReleaseEntry(
                release_key="test_001",
                title="Test Game",
                edition=None,
                group=None,
                chipset="OCS",
                language="en",
                version="1.0",
                alt_marker=None,
                ext="adf",
                curation_state=StagedState.PENDING,
            )
            # Add some actions
            entry.actions.append(StagedChange(
                action=CurationAction.STATE_CHANGE,
                timestamp="2024-01-01T00:00:00",
                details="State changed from pending to accepted",
            ))
            entry.actions.append(StagedChange(
                action=CurationAction.NOTE_ADDED,
                timestamp="2024-01-01T00:01:00",
                details="Note added",
            ))
            lib.releases["test_001"] = entry

            manager.save(lib)

            # Load back
            library = manager.load()
            assert len(library.releases) == 1
            loaded_entry = library.releases["test_001"]
            assert len(loaded_entry.actions) == 2
            assert loaded_entry.actions[0].action == CurationAction.STATE_CHANGE
            assert loaded_entry.actions[1].action == CurationAction.NOTE_ADDED

    def test_get_current_state(self):
        """Test getting the current state file with metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            manager = create_curation_state_manager(state_path)

            lib = StagedLibrary()
            entry = StagedReleaseEntry(
                release_key="test_001",
                title="Test Game",
                edition=None,
                group=None,
                chipset="OCS",
                language="en",
                version="1.0",
                alt_marker=None,
                ext="adf",
                curation_state=StagedState.PENDING,
            )
            lib.releases["test_001"] = entry
            manager.save(lib)

            current_state = manager.get_current_state()
            assert current_state is not None
            assert current_state.meta.entry_count == 1
            assert current_state.meta.source_workspace == "test.library_state"

    def test_export_changes_patch(self):
        """Test exporting changes patch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            manager = create_curation_state_manager(state_path)

            lib = StagedLibrary()
            entry = StagedReleaseEntry(
                release_key="test_001",
                title="Test Game",
                edition=None,
                group=None,
                chipset="OCS",
                language="en",
                version="1.0",
                alt_marker=None,
                ext="adf",
                curation_state=StagedState.ACCEPTED,
            )
            entry.actions.append(StagedChange(
                action=CurationAction.STATE_CHANGE,
                timestamp="2024-01-01T00:00:00",
                details="State changed from pending to accepted",
            ))
            lib.releases["test_001"] = entry

            manager.save(lib)

            patch = manager.export_changes_patch(lib)
            assert patch["patch_format_version"] == 1
            assert patch["total_releases"] == 1
            assert patch["total_changes"] == 1
            assert len(patch["changes"]) == 1
            assert patch["changes"][0]["release_key"] == "test_001"
            assert patch["changes"][0]["title"] == "Test Game"
            assert patch["changes"][0]["action"] == "state_change"


class TestFactoryFunction:
    def test_create_curation_state_manager(self):
        """Test the factory function."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test.library_state.json"
            manager = create_curation_state_manager(state_path)
            assert isinstance(manager, CurationStateManager)
            assert manager.state_path == state_path