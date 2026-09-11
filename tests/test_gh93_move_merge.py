#!/usr/bin/env python3
"""Test suite for GH-93 Move ADF(s) and Merge Release functionality."""
import tempfile
import json
from pathlib import Path
import sys

sys.path.insert(0, '/tmp/amiga-adf-gh106-dev/src')

from amiga_adf_library_builder.models import (
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
    CurationAction,
    StagedChange,
)


def make_entry(release_key: str, title: str, **kwargs) -> StagedReleaseEntry:
    """Helper to create a StagedReleaseEntry with sensible defaults."""
    defaults = {
        "group": "TESTGROUP",
        "adf_files": [],
        "curation_state": StagedState.PENDING,
        "edition": None,
        "chipset": None,
        "language": None,
        "version": None,
        "alt_marker": None,
        "notes": None,
        "actions": [],
        "locked_fields": [],
        "ext": "adf",
    }
    defaults.update(kwargs)
    return StagedReleaseEntry(release_key=release_key, title=title, **defaults)


class TestMoveAdfs:
    """Tests for moving ADFs between releases."""

    def test_move_adfs_basic(self):
        """Test basic move ADF functionality."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Source Game", adf_files=["disk1.adf", "disk2.adf"])
        dst = make_entry("dst_001", "Dest Game", adf_files=["existing.adf"])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        new_src, new_dst = lib.move_adfs("src_001", "dst_001", ["disk1.adf"])

        assert new_src.adf_files == ["disk2.adf"]
        assert new_dst.adf_files == ["existing.adf", "disk1.adf"]
        assert new_dst.adf_files[-1] == "disk1.adf"

        # Check decision log on source
        src_entry = lib.releases["src_001"]
        move_actions = [a for a in src_entry.actions if a.action == CurationAction.MOVE]
        assert len(move_actions) == 1
        assert "Moved 1 ADF(s)" in move_actions[0].details
        assert "dst_001" in move_actions[0].details

        # Check decision log on destination
        dst_entry = lib.releases["dst_001"]
        move_actions = [a for a in dst_entry.actions if a.action == CurationAction.MOVE]
        assert len(move_actions) == 1
        assert "Received 1 ADF(s)" in move_actions[0].details
        assert "src_001" in move_actions[0].details

    def test_move_adfs_all(self):
        """Test moving all ADFs (source becomes empty -> GHOST)."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Source Game", adf_files=["disk1.adf", "disk2.adf"])
        dst = make_entry("dst_001", "Dest Game", adf_files=[])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        new_src, new_dst = lib.move_adfs("src_001", "dst_001", ["disk1.adf", "disk2.adf"])

        assert new_src.adf_files == []
        assert new_dst.adf_files == ["disk1.adf", "disk2.adf"]

        # Source should be marked GHOST when empty (non-actionable ghost)
        src_entry = lib.releases["src_001"]
        assert src_entry.curation_state == StagedState.GHOST

    def test_move_adfs_duplicate_prevention(self):
        """Test that moving duplicate ADF filenames raises ValueError."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Source", adf_files=["disk1.adf", "disk2.adf"])
        dst = make_entry("dst_001", "Dest", adf_files=["disk1.adf"])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        try:
            lib.move_adfs("src_001", "dst_001", ["disk1.adf", "disk2.adf"])
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "already exists in destination" in str(e)

    def test_move_adfs_missing_source(self):
        """Test error when source release doesn't exist."""
        lib = StagedLibrary()
        dst = make_entry("dst_001", "Dest")
        lib.releases[dst.release_key] = dst

        try:
            lib.move_adfs("nonexistent", "dst_001", ["disk1.adf"])
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Source release" in str(e)

    def test_move_adfs_missing_destination(self):
        """Test error when destination release doesn't exist."""
        lib = StagedLibrary()
        src = make_entry("src_001", "Source", adf_files=["disk1.adf"])
        lib.releases[src.release_key] = src

        try:
            lib.move_adfs("src_001", "nonexistent", ["disk1.adf"])
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Destination release" in str(e)

    def test_move_adfs_same_source_dest(self):
        """Test error when source and destination are the same."""
        lib = StagedLibrary()
        src = make_entry("src_001", "Source", adf_files=["disk1.adf"])
        lib.releases[src.release_key] = src

        try:
            lib.move_adfs("src_001", "src_001", ["disk1.adf"])
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "same" in str(e).lower()


class TestMergeRelease:
    """Tests for merging releases."""

    def test_merge_release_basic(self):
        """Test basic merge release functionality."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Source Game", adf_files=["disk1.adf", "disk2.adf"])
        dst = make_entry("dst_001", "Dest Game", adf_files=["existing.adf"])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        new_src, new_dst = lib.merge_release("src_001", "dst_001")

        # Source should be empty
        assert new_src.adf_files == []
        assert new_src.curation_state == StagedState.GHOST

        # Destination should have all ADFs
        assert new_dst.adf_files == ["existing.adf", "disk1.adf", "disk2.adf"]

        # Check decision log
        src_entry = lib.releases["src_001"]
        merge_actions = [a for a in src_entry.actions if a.action == CurationAction.MERGE]
        assert len(merge_actions) == 1

        dst_entry = lib.releases["dst_001"]
        merge_actions = [a for a in dst_entry.actions if a.action == CurationAction.MERGE]
        assert len(merge_actions) == 1

    def test_merge_release_metadata_promotion(self):
        """Test that missing metadata is promoted from source to destination."""
        lib = StagedLibrary()

        src = make_entry(
            "src_001",
            "Source Game",
            adf_files=["disk1.adf"],
            edition="AGA",
            chipset="aga",
            language="en",
            version="1.0",
            alt_marker="alt",
        )
        dst = make_entry(
            "dst_001",
            "Dest Game",
            adf_files=["existing.adf"],
            edition=None,
            chipset=None,
            language=None,
            version=None,
            alt_marker=None,
        )

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        new_src, new_dst = lib.merge_release("src_001", "dst_001")

        # Destination should have promoted metadata
        assert new_dst.edition == "AGA"
        assert new_dst.chipset == "aga"
        assert new_dst.language == "en"
        assert new_dst.version == "1.0"
        assert new_dst.alt_marker == "alt"

    def test_merge_release_duplicate_prevention(self):
        """Test that merging with duplicate ADFs raises ValueError."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Source", adf_files=["disk1.adf", "disk2.adf"])
        dst = make_entry("dst_001", "Dest", adf_files=["disk1.adf", "disk3.adf"])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        try:
            lib.merge_release("src_001", "dst_001")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "already exists in destination" in str(e)

    def test_merge_release_missing_source(self):
        """Test error when source release doesn't exist."""
        lib = StagedLibrary()
        dst = make_entry("dst_001", "Dest")
        lib.releases[dst.release_key] = dst

        try:
            lib.merge_release("nonexistent", "dst_001")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Source release" in str(e)

    def test_merge_release_missing_destination(self):
        """Test error when destination release doesn't exist."""
        lib = StagedLibrary()
        src = make_entry("src_001", "Source", adf_files=["disk1.adf"])
        lib.releases[src.release_key] = src

        try:
            lib.merge_release("src_001", "nonexistent")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Destination release" in str(e)

    def test_merge_release_same_source_dest(self):
        """Test error when source and destination are the same."""
        lib = StagedLibrary()
        src = make_entry("src_001", "Source", adf_files=["disk1.adf"])
        lib.releases[src.release_key] = src

        try:
            lib.merge_release("src_001", "src_001")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "same" in str(e).lower()


class TestPersistenceRoundtrip:
    """Tests that move/merge operations survive serialization."""

    def test_move_persistence_roundtrip(self):
        """Test that move operations persist through to_dict/from_dict."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Source Game", adf_files=["disk1.adf", "disk2.adf"])
        dst = make_entry("dst_001", "Dest Game", adf_files=["existing.adf"])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        lib.move_adfs("src_001", "dst_001", ["disk1.adf"])

        # Serialize and deserialize
        data = lib.to_dict()
        lib2 = StagedLibrary.from_dict(data)

        # Verify state preserved
        assert lib2.releases["src_001"].adf_files == ["disk2.adf"]
        assert lib2.releases["dst_001"].adf_files == ["existing.adf", "disk1.adf"]

        # Verify decision log preserved
        src_actions = [a for a in lib2.releases["src_001"].actions if a.action == CurationAction.MOVE]
        assert len(src_actions) == 1

        dst_actions = [a for a in lib2.releases["dst_001"].actions if a.action == CurationAction.MOVE]
        assert len(dst_actions) == 1

    def test_merge_persistence_roundtrip(self):
        """Test that merge operations persist through to_dict/from_dict."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Source Game", adf_files=["disk1.adf", "disk2.adf"])
        dst = make_entry("dst_001", "Dest Game", adf_files=["existing.adf"])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        lib.merge_release("src_001", "dst_001")

        # Serialize and deserialize
        data = lib.to_dict()
        lib2 = StagedLibrary.from_dict(data)

        # Verify state preserved
        assert lib2.releases["src_001"].adf_files == []
        assert lib2.releases["src_001"].curation_state == StagedState.GHOST
        assert lib2.releases["dst_001"].adf_files == ["existing.adf", "disk1.adf", "disk2.adf"]

        # Verify decision log preserved
        src_actions = [a for a in lib2.releases["src_001"].actions if a.action == CurationAction.MERGE]
        assert len(src_actions) == 1

        dst_actions = [a for a in lib2.releases["dst_001"].actions if a.action == CurationAction.MERGE]
        assert len(dst_actions) == 1


class TestEdgeCases:
    """Edge case tests."""

    def test_move_empty_list(self):
        """Test moving empty list of ADFs (no-op)."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Source", adf_files=["disk1.adf"])
        dst = make_entry("dst_001", "Dest", adf_files=[])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        new_src, new_dst = lib.move_adfs("src_001", "dst_001", [])

        assert new_src.adf_files == ["disk1.adf"]
        assert new_dst.adf_files == []

    def test_merge_empty_source(self):
        """Test merging when source has no ADFs."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Empty Source", adf_files=[])
        dst = make_entry("dst_001", "Dest", adf_files=["disk1.adf"])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        new_src, new_dst = lib.merge_release("src_001", "dst_001")

        assert new_src.adf_files == []
        assert new_src.curation_state == StagedState.GHOST
        assert new_dst.adf_files == ["disk1.adf"]

    def test_move_nonexistent_adf(self):
        """Test moving an ADF that doesn't exist in source raises ValueError."""
        lib = StagedLibrary()

        src = make_entry("src_001", "Source", adf_files=["disk1.adf"])
        dst = make_entry("dst_001", "Dest", adf_files=[])

        lib.releases[src.release_key] = src
        lib.releases[dst.release_key] = dst

        try:
            lib.move_adfs("src_001", "dst_001", ["nonexistent.adf"])
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found in source" in str(e)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))