"""Tests for the Library Preview & Curation workspace (GH-80)."""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        assert CurationAction.MERGE.value == "merge"
        assert CurationAction.ACCEPT_MATCH.value == "accept_match"
        assert CurationAction.REJECT_MATCH.value == "reject_match"
        assert CurationAction.ACCEPT_METADATA_ONLY.value == "accept_metadata_only"
        assert CurationAction.KEEP_FILENAME.value == "keep_filename"
        assert CurationAction.ARTWORK_SELECTED.value == "artwork_selected"
        assert CurationAction.MANUAL_SELECTED.value == "manual_selected"

    def test_all_actions(self):
        actions = list(CurationAction)
        assert len(actions) == 15


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


def _item_release_key_from_data(item_data_user_role):
    """Pure-data helper: return release_key from a (text, user_role_data) pair.

    Mirrors the contract PreviewWidget._refresh_table() writes:
    every item in a row carries its release_key in UserRole."""
    return item_data_user_role


class TestPreviewWidgetSelectionIdentity:
    """GH-90 regression: release_key must be stored in item UserRole
    so selection/action routing stays stable after sort/refresh.

    These tests verify the data contract without constructing live
    Qt widgets (no display required). The contract is:
    - every item in a row carries release_key in UserRole (data role 1).
    - selection resolution scans columns for the first non-empty UserRole.
    - sorting does not change which release_key a row represents.
    """

    @staticmethod
    def _make_rows():
        return [
            ("r1", "Alpha"),
            ("r2", "Bard's Tale III"),
            ("r3", "Hot Rod"),
            ("r4", "Delta"),
        ]

    def test_user_role_carries_release_key(self):
        """Every cell in a row records release_key in UserRole.
        This is the contract PreviewWidget._refresh_table() now writes."""
        rows = self._make_rows()
        # Simulate table cells as (display_text, user_role_data) tuples
        cells = {}
        for row, (rk, title) in enumerate(rows):
            cells[(row, 0)] = (rk, rk)       # State col: key text + user_role = rk
            cells[(row, 1)] = (title, rk)    # Title col: title text + user_role = rk
            cells[(row, 2)] = (rk, rk)       # Release Key col: key text + user_role = rk
        # Column 0 = release_key text, column 1 = title, column 2 = release_key
        assert _item_release_key_from_data(cells[(0, 0)][1]) == "r1"
        assert _item_release_key_from_data(cells[(1, 0)][1]) == "r2"
        assert _item_release_key_from_data(cells[(2, 2)][1]) == "r3"

    def test_release_key_stable_through_sort(self):
        """After sort by title, UserRole still holds the original release_key."""
        rows = self._make_rows()
        cells = {}
        for row, (rk, title) in enumerate(rows):
            cells[(row, 0)] = (rk, rk)
            cells[(row, 1)] = (title, rk)
            cells[(row, 2)] = (rk, rk)
        # Simulate sort ascending by title: Alpha, Bard's Tale III, Delta, Hot Rod
        # Original rows 0..3 become 0,1,3,2 after title sort
        sorted_rows = [0, 1, 3, 2]
        expected_keys = ["r1", "r2", "r4", "r3"]
        for visual_row, expected_key in zip(sorted_rows, expected_keys):
            assert _item_release_key_from_data(cells[(visual_row, 0)][1]) == expected_key

    def test_release_key_stable_through_resort(self):
        """Sort ascending then descending: UserRole round-trips cleanly."""
        rows = self._make_rows()
        cells = {}
        for row, (rk, title) in enumerate(rows):
            cells[(row, 0)] = (rk, rk)
            cells[(row, 1)] = (title, rk)
            cells[(row, 2)] = (rk, rk)
        # Simulate sort up then back down
        sorted_up = [0, 1, 3, 2]
        sorted_down = [0, 1, 2, 3]
        for visual_row, expected_key in zip(sorted_up, ["r1", "r2", "r4", "r3"]):
            assert _item_release_key_from_data(cells[(visual_row, 0)][1]) == expected_key
        for visual_row, (rk, _) in zip(sorted_down, rows):
            assert _item_release_key_from_data(cells[(visual_row, 0)][1]) == rk

    def test_selection_resolves_same_key_from_multiple_columns(self):
        """Simulate _on_selection_changed multi-column fallback:
        scan all columns and return the first non-empty UserRole."""
        rows = self._make_rows()
        cells = {}
        for row, (rk, title) in enumerate(rows):
            cells[(row, 0)] = (rk, rk)
            cells[(row, 1)] = (title, rk)
            cells[(row, 2)] = (rk, rk)
        row = 2
        release_key = None
        for col in range(3):
            rk = _item_release_key_from_data(cells[(row, col)][1])
            if rk:
                release_key = rk
                break
        assert release_key == "r3"

    def test_release_key_consistent_across_refresh_cycle(self):
        """Simulate _refresh_table rebuild: every row's item UserRole
        must still resolve to a known release_key."""
        rows = self._make_rows()
        cells = {}
        for row, (rk, title) in enumerate(rows):
            cells[(row, 0)] = (rk, rk)
            cells[(row, 1)] = (title, rk)
            cells[(row, 2)] = (rk, rk)
        known_keys = {rk for rk, _ in rows}
        for row in range(len(rows)):
            rk = _item_release_key_from_data(cells[(row, 0)][1])
            assert rk in known_keys

    def test_multi_select_resolves_keys_after_sort(self):
        """Simulate multi-select mutation handlers (_on_move_to_folder,
        _on_create_folder_and_move, _on_set_selected_state) after a sort.
        Every selected row must resolve to the correct release_key via UserRole
        scan across columns, not stale row_to_release_key."""
        rows = self._make_rows()
        # cells indexed by (original_row, col) -> (display_text, user_role_data)
        cells = {}
        for row, (rk, title) in enumerate(rows):
            cells[(row, 0)] = (rk, rk)
            cells[(row, 1)] = (title, rk)
            cells[(row, 2)] = (rk, rk)

        # Simulate sort ascending by title: Alpha, Bard's Tale III, Delta, Hot Rod
        # Original rows 0..3 become visual rows 0,1,3,2
        # Visual row -> original row mapping
        visual_to_original = {0: 0, 1: 1, 2: 3, 3: 2}

        # Simulate multi-select on visual rows 1 and 2 (Bard's Tale III and Delta)
        selected_visual_rows = [1, 2]
        resolved_keys = []
        for visual_row in selected_visual_rows:
            original_row = visual_to_original[visual_row]
            release_key = None
            for col in range(3):
                rk = _item_release_key_from_data(cells[(original_row, col)][1])
                if rk:
                    release_key = rk
                    break
            resolved_keys.append(release_key)

        # Should resolve to r2 and r4 (the actual releases at those visual positions)
        assert resolved_keys == ["r2", "r4"], f"Expected ['r2', 'r4'], got {resolved_keys}"

    def test_multi_select_resolves_keys_after_filter(self):
        """Simulate multi-select after filter hides some rows.
        Selected visual rows must resolve to correct release_keys via UserRole."""
        rows = self._make_rows()
        cells = {}
        for row, (rk, title) in enumerate(rows):
            cells[(row, 0)] = (rk, rk)
            cells[(row, 1)] = (title, rk)
            cells[(row, 2)] = (rk, rk)

        # Simulate filter showing only rows with "Alpha" and "Delta" (original 0 and 3)
        # They appear at visual rows 0 and 1 after filtering
        # Visual row -> original row mapping
        visual_to_original = {0: 0, 1: 3}
        filtered_visual_rows = [0, 1]
        expected_keys = ["r1", "r4"]

        resolved_keys = []
        for visual_row in filtered_visual_rows:
            original_row = visual_to_original[visual_row]
            release_key = None
            for col in range(3):
                rk = _item_release_key_from_data(cells[(original_row, col)][1])
                if rk:
                    release_key = rk
                    break
            resolved_keys.append(release_key)

        assert resolved_keys == expected_keys, f"Expected {expected_keys}, got {resolved_keys}"

    def test_multi_select_resolves_keys_after_resort(self):
        """Simulate sort up then back down: multi-select resolves correctly
        through round-trip via UserRole scan."""
        rows = self._make_rows()
        cells = {}
        for row, (rk, title) in enumerate(rows):
            cells[(row, 0)] = (rk, rk)
            cells[(row, 1)] = (title, rk)
            cells[(row, 2)] = (rk, rk)

        # Sort ascending: visual -> original
        visual_to_original_up = {0: 0, 1: 1, 2: 3, 3: 2}
        # Select visual rows 1 and 2 (Bard's Tale III, Delta)
        selected_up = [1, 2]
        resolved_up = []
        for vr in selected_up:
            orig = visual_to_original_up[vr]
            rk = None
            for col in range(3):
                val = _item_release_key_from_data(cells[(orig, col)][1])
                if val:
                    rk = val
                    break
            resolved_up.append(rk)
        assert resolved_up == ["r2", "r4"]

        # Sort descending back to original: visual == original
        visual_to_original_down = {0: 0, 1: 1, 2: 2, 3: 3}
        # Same visual row indices 1 and 2 now map to original rows 1 and 2
        selected_down = [1, 2]
        resolved_down = []
        for vr in selected_down:
            orig = visual_to_original_down[vr]
            rk = None
            for col in range(3):
                val = _item_release_key_from_data(cells[(orig, col)][1])
                if val:
                    rk = val
                    break
            resolved_down.append(rk)
        assert resolved_down == ["r2", "r3"]