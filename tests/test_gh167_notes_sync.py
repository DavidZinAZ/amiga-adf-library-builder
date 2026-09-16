"""GH-167 — Notes/state synchronization regression tests (RC-D).

Tests that _apply_lookup_candidate refreshes stale notes and that
_metadata_snapshot/_restore_metadata_snapshot include notes for
undo/redo symmetry.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from amiga_adf_library_builder.models import (
    CurationAction,
    StagedReleaseEntry,
    StagedState,
    StagedLibrary,
)


# ---------------------------------------------------------------------------
# _metadata_snapshot includes notes
# ---------------------------------------------------------------------------

class TestMetadataSnapshotNotes:
    def test_snapshot_includes_notes(self):
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test Release",
            edition="",
            group="",
            chipset=None,
            notes="metadata lookup: not-found\nsome other note",
        )
        from amiga_adf_library_builder.gui.preview_widget import (
            PreviewWidget,
        )
        snap = PreviewWidget._metadata_snapshot(entry)
        assert snap["notes"] == "metadata lookup: not-found\nsome other note"

    def test_snapshot_notes_none(self):
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test Release",
            edition="",
            group="",
            chipset=None,
        )
        from amiga_adf_library_builder.gui.preview_widget import (
            PreviewWidget,
        )
        snap = PreviewWidget._metadata_snapshot(entry)
        assert snap["notes"] is None

    def test_restore_notes(self):
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Old Title",
            edition="",
            group="",
            chipset=None,
            notes="old notes",
        )
        snap = {
            "title": "Old Title",
            "metadata_source": None,
            "match_confidence": None,
            "confidence": 0.0,
            "artwork_front": None,
            "curation_state": "pending",
            "notes": "old notes",
        }
        from amiga_adf_library_builder.gui.preview_widget import (
            PreviewWidget,
        )
        PreviewWidget._restore_metadata_snapshot(entry, snap)
        assert entry.notes == "old notes"


# ---------------------------------------------------------------------------
# _apply_lookup_candidate refreshes stale notes
# ---------------------------------------------------------------------------

class TestApplyLookupCandidateNotes:
    def test_found_candidate_supersedes_not_found(self):
        """A found candidate replaces 'metadata lookup: not-found'."""
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test Release",
            edition="",
            group="",
            chipset=None,
            notes="metadata lookup: not-found\nprocessed artwork: /tmp/a.jpg",
        )
        entry.match_confidence = 0.5
        entry.confidence = 0.5
        entry.curation_state = StagedState.MODIFIED

        widget = _make_preview_widget()
        candidate = {
            "status": "found",
            "kind": "online",
            "provider": "Wikipedia",
            "title": "Test Release (Wikipedia)",
            "confidence": 0.86,
        }
        widget._apply_lookup_candidate(entry, "online", candidate)

        assert entry.notes is not None
        lines = entry.notes.splitlines()
        assert not any("metadata lookup: not-found" in l for l in lines)
        assert any(
            "metadata lookup: accepted" in l for l in lines
        )
        assert any(
            "processed artwork: /tmp/a.jpg" in l for l in lines
        )

    def test_found_candidate_appends_when_no_metadata_line(self):
        """If no metadata lookup line exists, append the accepted line."""
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test Release",
            edition="",
            group="",
            chipset=None,
            notes="processed artwork: /tmp/a.jpg",
        )
        entry.match_confidence = 0.5
        entry.confidence = 0.5
        entry.curation_state = StagedState.MODIFIED

        widget = _make_preview_widget()
        candidate = {
            "status": "found",
            "kind": "online",
            "provider": "Wikipedia",
            "title": "Test Release (Wikipedia)",
            "confidence": 0.86,
        }
        widget._apply_lookup_candidate(entry, "online", candidate)

        assert any(
            "metadata lookup: accepted" in l
            for l in (entry.notes or "").splitlines()
        )

    def test_offline_candidate_preserves_notes(self):
        """Offline candidate does not modify notes."""
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test Release",
            edition="",
            group="",
            chipset=None,
            notes="metadata lookup: not-found",
        )
        entry.match_confidence = 0.5
        entry.confidence = 0.5
        entry.curation_state = StagedState.MODIFIED

        widget = _make_preview_widget()
        candidate = {
            "status": "found",
            "kind": "offline",
            "local_cached_path": "/tmp/artwork.jpg",
            "local_outcome": "match",
            "confidence": 0.7,
        }
        widget._apply_lookup_candidate(entry, "offline", candidate)

        assert entry.notes == "metadata lookup: not-found"

    def test_action_payload_includes_notes(self):
        """The StagedChange payload includes both pre and post notes."""
        entry = StagedReleaseEntry(
            release_key="test|1",
            title="Test Release",
            edition="",
            group="",
            chipset=None,
            notes="metadata lookup: not-found",
        )
        entry.match_confidence = 0.5
        entry.confidence = 0.5
        entry.curation_state = StagedState.MODIFIED

        widget = _make_preview_widget()
        candidate = {
            "status": "found",
            "kind": "online",
            "provider": "Wikipedia",
            "title": "Test Release (Wikipedia)",
            "confidence": 0.86,
        }
        widget._apply_lookup_candidate(entry, "online", candidate)

        assert len(entry.actions) > 0
        last_action = entry.actions[-1]
        payload = last_action.payload
        assert payload is not None
        data = json.loads(payload)
        assert "pre" in data
        assert "post" in data
        assert "notes" in data["pre"]
        assert "notes" in data["post"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_preview_widget():
    """Create a minimal PreviewWidget for testing."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from amiga_adf_library_builder.gui.preview_widget import (
        PreviewWidget,
        PreviewWidgetState,
    )
    from amiga_adf_library_builder.models import StagedLibrary

    widget = PreviewWidget()
    widget._state = PreviewWidgetState()
    widget._state.current_library = StagedLibrary()
    return widget
