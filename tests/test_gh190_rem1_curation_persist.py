"""Focused reproduction test for GH-190 REM-1 curation persistence regression.

Tests that curation moves persist across save/reload/export after the GH-190
release-key change (group and alt_marker removed from _build_release_key).

The bug: _apply_curation and _sync_group_membership use group.release_key
to look up StagedReleaseEntry objects in the staged library. After GH-190,
the release key changed (removed group and alt_marker), so entries keyed by
the old release key no longer match the new group release keys. This causes:
- Save serializes pre-curation (original) membership
- Load State reconstructs groups the curation layer no longer recognizes
- Preview table returns 0 rows after reload
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from amiga_adf_library_builder.models import (
    ParsedRecord,
    ReleaseGroup,
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
)
from amiga_adf_library_builder.pipeline import _apply_curation, _sync_group_membership
from amiga_adf_library_builder.library_state import CurationStateManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parsed_record(source_filename: str, title: str, group: str = None,
                        alt_marker: str = None, disk_number: int = None,
                        ext: str = "adf") -> ParsedRecord:
    """Build a minimal ParsedRecord."""
    return ParsedRecord(
        source_filename=source_filename,
        ext=ext,
        title=title,
        group=group,
        alt_marker=alt_marker,
        disk_number=disk_number,
    )


def _make_release_group(release_key: str, title: str, records: list[ParsedRecord] | None = None,
                         group: str = None) -> ReleaseGroup:
    """Build a minimal ReleaseGroup."""
    records = records or []
    disks = [r for r in records if not r.special_disk]
    specials = [r for r in records if r.special_disk]
    return ReleaseGroup(
        release_key=release_key,
        title=title,
        edition=None,
        group=group,
        chipset=None,
        language=None,
        region=None,
        version=None,
        alt_marker=None,
        ext="adf",
        records=records,
        disks=disks,
        specials=specials,
        has_main_disk=len(disks) > 0,
        is_complete=True,
        quarantine_reason="near-duplicate spelling",
    )


def _make_staged_library(entries: dict[str, StagedState],
                         adf_map: dict[str, list[str]] | None = None) -> StagedLibrary:
    """Build a StagedLibrary with the given release_key -> StagedState mapping."""
    adf_map = adf_map or {}
    releases = {}
    for rk, state in entries.items():
        releases[rk] = StagedReleaseEntry(
            release_key=rk,
            title=rk.split("|")[0].replace("-", " ").title(),
            edition=None,
            group=None,
            chipset=None,
            adf_files=adf_map.get(rk, [f"{rk.replace('|', '_')}.adf"]),
            artwork_front=None,
            curation_state=state,
        )
    return StagedLibrary(releases=releases)


def _write_library(library: StagedLibrary, tmp_path: Path) -> Path:
    """Serialize a StagedLibrary to a JSON file."""
    state_path = tmp_path / "library_state.json"
    data = {"library": library.to_dict(), "meta": {"schema_version": 1}}
    state_path.write_text(json.dumps(data), encoding="utf-8")
    return state_path


# ---------------------------------------------------------------------------
# Test: _apply_curation with matching keys (should work)
# ---------------------------------------------------------------------------

class TestApplyCurationMatchingKeys:
    """Test _apply_curation when release keys match."""

    def test_accepted_clears_quarantine(self, tmp_path):
        """ACCEPTED entry clears quarantine_reason."""
        release_key = "bardstale3|||"
        group = _make_release_group(release_key, "Bard's Tale III")
        group.quarantine_reason = "near-duplicate spelling"
        library = _make_staged_library({release_key: StagedState.ACCEPTED})
        state_path = _write_library(library, tmp_path)

        loaded_library, decisions = _apply_curation([group], str(state_path), None)

        assert group.quarantine_reason is None
        assert "bardstale3" in decisions  # game_id = release_key.split("|")[0].lower()

    def test_rejected_sets_quarantine(self, tmp_path):
        """REJECTED entry sets quarantine_reason."""
        release_key = "bardstale3|||"
        group = _make_release_group(release_key, "Bard's Tale III")
        library = _make_staged_library({release_key: StagedState.REJECTED})
        state_path = _write_library(library, tmp_path)

        loaded_library, decisions = _apply_curation([group], str(state_path), None)

        assert group.quarantine_reason == "rejected by curation"

    def test_move_and_accept_persists(self, tmp_path):
        """After move + Accept + Save, Load State restores curated membership."""
        # Simulate the pipeline creating a state file with new release keys
        release_key = "bardstale3|||"
        library = _make_staged_library(
            {release_key: StagedState.ACCEPTED},
            adf_map={release_key: ["Bards_Talet3_Char.adf"]}
        )
        state_path = _write_library(library, tmp_path)

        # Load the state back
        manager = CurationStateManager(state_path)
        loaded = manager.load()

        # Create a group with the same release key
        group = _make_release_group(release_key, "Bard's Tale III")
        group.quarantine_reason = "near-duplicate spelling"

        # Apply curation
        _, decisions = _apply_curation([group], str(state_path), None)

        assert group.quarantine_reason is None
        assert "bardstale3" in decisions  # game_id = release_key.split("|")[0].lower()


# ---------------------------------------------------------------------------
# Test: _apply_curation with mismatched keys (THE BUG)
# ---------------------------------------------------------------------------

class TestApplyCurationMismatchedKeys:
    """Test _apply_curation when release keys don't match (GH-190 regression)."""

    def test_old_key_does_not_match_new_group(self, tmp_path):
        """OLD release key (with group) does not match NEW group release key (without group).

        This reproduces the GH-190 regression: after the release-key change,
        _apply_curation can't find the entry because the keys changed.
        """
        # OLD release key format (before GH-190): includes group and alt_marker
        old_release_key = "bardstale3|skr|||a"  # with group and alt_marker
        new_release_key = "bardstale3|||"        # after GH-190: no group, no alt_marker

        # Create a staged library with the OLD release key (as it would exist
        # in a state file from before GH-190)
        library = StagedLibrary(releases={
            old_release_key: StagedReleaseEntry(
                release_key=old_release_key,
                title="Bard's Tale III",
                edition=None, group=None, chipset=None,
                adf_files=["Bards_Talet3_Char.adf"],
                curation_state=StagedState.ACCEPTED,
            )
        })
        state_path = _write_library(library, tmp_path)

        # Create a group with the NEW release key (as it would be created
        # by the post-GH-190 pipeline)
        record = _make_parsed_record("Bards_Talet3_Char.adf", "Bard's Tale III")
        group = _make_release_group(new_release_key, "Bard's Tale III", [record])
        group.quarantine_reason = "near-duplicate spelling"

        # Apply curation — this is where the bug manifests
        loaded_library, decisions = _apply_curation([group], str(state_path), None)

        # BUG: The group's quarantine_reason is NOT cleared because
        # library.releases.get(group.release_key) returns None
        # (old_release_key != new_release_key)
        # After fix: quarantine_reason should be None (ACCEPTED entry found)
        assert group.quarantine_reason is None, (
            f"Expected quarantine to be cleared, got: {group.quarantine_reason!r}. "
            f"Old key: {old_release_key}, New key: {new_release_key}. "
            f"This is the GH-190 regression."
        )
        # decisions dict uses game_id (= first part of release_key before "|")
        assert "bardstale3" in decisions, (
            "Expected the game_id to appear in decisions"
        )

    def test_sync_group_membership_with_mismatched_keys(self, tmp_path):
        """_sync_group_membership can't propagate curated ADF membership
        when release keys don't match."""
        old_release_key = "bardstale3|skr|||a"
        new_release_key = "bardstale3|||"

        library = StagedLibrary(releases={
            old_release_key: StagedReleaseEntry(
                release_key=old_release_key,
                title="Bard's Tale III",
                edition=None, group=None, chipset=None,
                adf_files=["Bards_Talet3_Char.adf"],
                curation_state=StagedState.ACCEPTED,
            )
        })

        record = _make_parsed_record("Bards_Talet3_Char.adf", "Bard's Tale III")
        # Create a group WITHOUT pre-populated disks (simulating fresh pipeline output)
        group = _make_release_group(new_release_key, "Bard's Tale III", [])
        group.quarantine_reason = "near-duplicate spelling"

        _sync_group_membership([group], library)

        # After fix: group should have the curated ADF membership from the staged library
        assert len(group.disks) > 0, (
            "Expected group to have disks after _sync_group_membership, "
            "but got 0. This is the GH-190 regression."
        )
        assert any("Bards_Talet3_Char.adf" in r.source_filename for r in group.disks), (
            "Expected group to contain the curated ADF"
        )


# ---------------------------------------------------------------------------
# Test: Full save/load/reload chain
# ---------------------------------------------------------------------------

class TestCurationPersistenceChain:
    """Test the full save -> reload -> apply chain."""

    def test_move_accept_save_reload_persists_membership(self, tmp_path):
        """Move ADFs + Accept + Save -> Load -> membership persists.

        This reproduces the QA failure: after Save, the saved JSON should
        contain the curated membership, and after Load, the Preview table
        should show non-empty groups.
        """
        # Simulate pipeline creating a state file
        release_key = "bardstale3|||"
        library = _make_staged_library(
            {release_key: StagedState.ACCEPTED},
            adf_map={release_key: ["Bards_Talet3_Char.adf"]}
        )
        state_path = _write_library(library, tmp_path)

        # Simulate the GUI loading the state, doing curation, and saving
        manager = CurationStateManager(state_path)
        loaded_library = manager.load()

        # Simulate Move ADFs (move from source to target)
        src_key = "bardstale3_orig|||"
        dst_key = release_key
        loaded_library.releases[src_key] = StagedReleaseEntry(
            release_key=src_key, title="Bard's Tale III (Original)",
            edition=None, group=None, chipset=None,
            adf_files=["Bards_Talet3_Char.adf"], curation_state=StagedState.PENDING,
        )
        # Ensure destination has no ADFs before the move
        loaded_library.releases[dst_key].adf_files = []
        # Move the ADF
        loaded_library.move_adfs(src_key, dst_key, ["Bards_Talet3_Char.adf"])

        # Accept the target
        loaded_library.releases[dst_key].curation_state = StagedState.ACCEPTED

        # Save
        manager.save(loaded_library)

        # Load back
        manager2 = CurationStateManager(state_path)
        reloaded = manager2.load()

        # Verify: the target should have the ADF, source should be empty
        assert dst_key in reloaded.releases
        assert "Bards_Talet3_Char.adf" in reloaded.releases[dst_key].adf_files
        assert len(reloaded.releases[src_key].adf_files) == 0
        assert reloaded.releases[dst_key].curation_state == StagedState.ACCEPTED

        # Verify: _apply_curation should find the entry and clear quarantine
        record = _make_parsed_record("Bards_Talet3_Char.adf", "Bard's Tale III")
        group = _make_release_group(release_key, "Bard's Tale III", [record])
        group.quarantine_reason = "near-duplicate spelling"

        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason is None, (
            f"Expected quarantine to be cleared after reload, got: {group.quarantine_reason!r}"
        )
        assert "bardstale3" in decisions  # game_id = release_key.split("|")[0].lower()

    def test_load_state_restores_groups_after_gh190(self, tmp_path):
        """Load State restores groups with non-empty tables after reload.

        After GH-190, the Preview table should not return 0 rows because
        the release keys should match between the staged library and the groups.
        """
        release_key = "bardstale3|||"
        library = _make_staged_library(
            {release_key: StagedState.ACCEPTED},
            adf_map={release_key: ["Bards_Talet3_Char.adf"]}
        )
        state_path = _write_library(library, tmp_path)

        # Load the state
        manager = CurationStateManager(state_path)
        loaded = manager.load()

        # Verify the library has the expected releases
        assert release_key in loaded.releases
        assert len(loaded.releases[release_key].adf_files) == 1

        # Verify the entry has ACCEPTED state
        assert loaded.releases[release_key].curation_state == StagedState.ACCEPTED


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x", "--tb=short"])
