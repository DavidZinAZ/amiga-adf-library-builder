"""Tests for the Library Preview & Curation workspace (GH-80)."""
import json
import tempfile
from pathlib import Path

import pytest

from amiga_adf_library_builder.models import (
    StagedState,
    CurationAction,
    StagedChange,
    StagedReleaseEntry,
    StagedLibrary,
)


class TestStagedState:
    def test_states_exist(self):
        assert StagedState.PENDING.value == "pending"
        assert StagedState.ACCEPTED.value == "accepted"
        assert StagedState.REJECTED.value == "rejected"
        assert StagedState.MODIFIED.value == "modified"
        assert StagedState.NEEDS_REVIEW.value == "needs_review"

    def test_all_states(self):
        states = list(StagedState)
        assert len(states) == 5
        assert StagedState.PENDING in states
        assert StagedState.ACCEPTED in states
        assert StagedState.REJECTED in states
        assert StagedState.MODIFIED in states
        assert StagedState.NEEDS_REVIEW in states


class TestCurationAction:
    def test_actions_exist(self):
        assert CurationAction.STATE_CHANGE.value == "state_change"
        assert CurationAction.NOTE_ADDED.value == "note_added"
        assert CurationAction.ARTWORK_CHANGED.value == "artwork_changed"
        assert CurationAction.METADATA_EDIT.value == "metadata_edit"
        assert CurationAction.BULK_EDIT.value == "bulk_edit"
        assert CurationAction.RENAME.value == "rename"
        assert CurationAction.MOVE.value == "move"
        assert CurationAction.ACCEPT_MATCH.value == "accept_match"
        assert CurationAction.REJECT_MATCH.value == "reject_match"
        assert CurationAction.ACCEPT_METADATA_ONLY.value == "accept_metadata_only"
        assert CurationAction.KEEP_FILENAME.value == "keep_filename"
        assert CurationAction.ARTWORK_SELECTED.value == "artwork_selected"
        assert CurationAction.MANUAL_SELECTED.value == "manual_selected"

    def test_all_actions(self):
        actions = list(CurationAction)
        assert len(actions) == 13


class TestStagedChange:
    def test_create_change(self):
        change = StagedChange(
            action=CurationAction.STATE_CHANGE,
            timestamp="2024-01-01T00:00:00",
            details="State changed from pending to accepted",
        )
        assert change.action == CurationAction.STATE_CHANGE
        assert change.timestamp == "2024-01-01T00:00:00"
        assert change.details == "State changed from pending to accepted"

    def test_to_dict(self):
        change = StagedChange(
            action=CurationAction.STATE_CHANGE,
            timestamp="2024-01-01T00:00:00",
            details="State changed from pending to accepted",
        )
        d = change.to_dict()
        assert d["action"] == "state_change"
        assert d["timestamp"] == "2024-01-01T00:00:00"
        assert d["details"] == "State changed from pending to accepted"

    def test_from_dict(self):
        d = {
            "action": "state_change",
            "timestamp": "2024-01-01T00:00:00",
            "details": "State changed from pending to accepted",
        }
        change = StagedChange.from_dict(d)
        assert change.action == CurationAction.STATE_CHANGE
        assert change.timestamp == "2024-01-01T00:00:00"
        assert change.details == "State changed from pending to accepted"


class TestStagedReleaseEntry:
    def create_basic_entry(self) -> StagedReleaseEntry:
        """Create a basic test entry."""
        return StagedReleaseEntry(
            release_key="test_001",
            title="Test Game",
            edition="Special Edition",
            group="CRACKERS",
            chipset="OCS",
            language="en",
            version="1.0",
            alt_marker=None,
            ext="adf",
            curation_state=StagedState.PENDING,
        )

    def test_create_entry(self):
        entry = self.create_basic_entry()
        assert entry.release_key == "test_001"
        assert entry.title == "Test Game"
        assert entry.edition == "Special Edition"
        assert entry.group == "CRACKERS"
        assert entry.curation_state == StagedState.PENDING

    def test_to_dict(self):
        entry = self.create_basic_entry()
        d = entry.to_dict()
        assert d["release_key"] == "test_001"
        assert d["title"] == "Test Game"
        assert d["edition"] == "Special Edition"
        assert d["group"] == "CRACKERS"
        assert d["curation_state"] == "pending"
        assert "adf_files" in d
        assert "artwork_front" in d
        assert "actions" in d

    def test_from_dict(self):
        entry = self.create_basic_entry()
        d = entry.to_dict()
        restored = StagedReleaseEntry.from_dict(d)
        assert restored.release_key == "test_001"
        assert restored.title == "Test Game"
        assert restored.curation_state == StagedState.PENDING

    def test_state_transitions(self):
        entry = self.create_basic_entry()
        assert entry.curation_state == StagedState.PENDING

        entry.curation_state = StagedState.ACCEPTED
        assert entry.curation_state == StagedState.ACCEPTED

        entry.curation_state = StagedState.REJECTED
        assert entry.curation_state == StagedState.REJECTED

        entry.curation_state = StagedState.MODIFIED
        assert entry.curation_state == StagedState.MODIFIED

    def test_actions_log(self):
        entry = self.create_basic_entry()
        assert len(entry.actions) == 0

        entry.actions.append(StagedChange(
            action=CurationAction.STATE_CHANGE,
            timestamp="2024-01-01T00:00:00",
            details="State changed from pending to accepted",
        ))
        assert len(entry.actions) == 1
        assert entry.actions[0].action == CurationAction.STATE_CHANGE

        entry.actions.append(StagedChange(
            action=CurationAction.NOTE_ADDED,
            timestamp="2024-01-01T00:01:00",
            details="Note added",
        ))
        assert len(entry.actions) == 2
        assert entry.actions[1].action == CurationAction.NOTE_ADDED

    def test_adf_files(self):
        entry = self.create_basic_entry()
        entry.adf_files = ["disk1.adf", "disk2.adf"]
        assert len(entry.adf_files) == 2

    def test_artwork_paths(self):
        entry = self.create_basic_entry()
        entry.artwork_front = "/path/to/front.jpg"
        entry.artwork_back = "/path/to/back.jpg"
        entry.artwork_spine = "/path/to/spine.jpg"
        entry.artwork_other = ["/path/to/extra1.jpg", "/path/to/extra2.jpg"]
        assert entry.artwork_front == "/path/to/front.jpg"
        assert entry.artwork_back == "/path/to/back.jpg"
        assert entry.artwork_spine == "/path/to/spine.jpg"
        assert len(entry.artwork_other) == 2

    def test_notes(self):
        entry = self.create_basic_entry()
        assert entry.notes is None

        entry.notes = "This is a test note"
        assert entry.notes == "This is a test note"


class TestStagedLibrary:
    def test_empty_library(self):
        lib = StagedLibrary()
        assert len(lib.releases) == 0

    def test_add_entries(self):
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
        assert len(lib.releases) == 1
        assert "test_001" in lib.releases

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
        d = lib.to_dict()
        assert "releases" in d
        assert "test_001" in d["releases"]
        assert d["releases"]["test_001"]["title"] == "Test Game"

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
        d = lib.to_dict()
        restored = StagedLibrary.from_dict(d)
        assert len(restored.releases) == 1
        assert restored.releases["test_001"].title == "Test Game"

    def test_multiple_entries(self):
        lib = StagedLibrary()
        for i in range(5):
            entry = StagedReleaseEntry(
                release_key=f"test_{i:03d}",
                title=f"Game {i}",
                edition=None,
                group=None,
                chipset="OCS",
                language="en",
                version="1.0",
                alt_marker=None,
                ext="adf",
                curation_state=StagedState.PENDING,
            )
            lib.releases[f"test_{i:03d}"] = entry
        assert len(lib.releases) == 5

    def test_persistence_roundtrip(self):
        """Test full library persistence roundtrip via JSON."""
        lib = StagedLibrary()
        entry = StagedReleaseEntry(
            release_key="test_001",
            title="Test Game",
            edition="Special Edition",
            group="CRACKERS",
            chipset="OCS",
            language="en",
            version="1.0",
            alt_marker=None,
            ext="adf",
            curation_state=StagedState.ACCEPTED,
        )
        entry.adf_files = ["disk1.adf", "disk2.adf"]
        entry.artwork_front = "/path/to/front.jpg"
        entry.notes = "Test note"
        entry.actions.append(StagedChange(
            action=CurationAction.STATE_CHANGE,
            timestamp="2024-01-01T00:00:00",
            details="State changed from pending to accepted",
        ))
        lib.releases["test_001"] = entry

        # Serialize and deserialize
        json_str = json.dumps(lib.to_dict(), indent=2)
        restored = StagedLibrary.from_dict(json.loads(json_str))

        assert len(restored.releases) == 1
        restored_entry = restored.releases["test_001"]
        assert restored_entry.title == "Test Game"
        assert restored_entry.edition == "Special Edition"
        assert restored_entry.curation_state == StagedState.ACCEPTED
        assert len(restored_entry.adf_files) == 2
        assert restored_entry.artwork_front == "/path/to/front.jpg"
        assert restored_entry.notes == "Test note"
        assert len(restored_entry.actions) == 1
        assert restored_entry.actions[0].action == CurationAction.STATE_CHANGE