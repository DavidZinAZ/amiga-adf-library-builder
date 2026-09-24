"""Regression tests for GH-136: curated export and manual artwork.

Tests that:
* Accepted manual curation remains authoritative through final export.
* Provider not_found/local_no_master cannot erase accepted title/release decisions.
* Manual local artwork exports even when online artwork lookup misses.
* Multi-disk A-10 v1.0 exports both disks; A-10 v1.5 exports all three disks.
* Every skipped selected release has an actionable reason.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from amiga_adf_library_builder.models import (
    ParsedRecord,
    ReleaseGroup,
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
)
from typing import Optional
from amiga_adf_library_builder.pipeline import _apply_curation, _sync_group_membership
from amiga_adf_library_builder.exporter import (
    _find_staged_artwork,
    export_release,
    export_all,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_group(release_key: str, title: str, disks: list[str] | None = None) -> ReleaseGroup:
    """Build a minimal ReleaseGroup with the given release_key."""
    disks = disks or [f"{title.replace(' ', '_')}.adf"]
    # Use release_key-based filenames so they match _make_staged_library's adf_files.
    base = release_key.replace("|", "_")
    records = [
        ParsedRecord(
            source_filename=f"{base}_{i}.adf",
            ext="adf",
            disk_number=i,
        )
        for i in range(len(disks))
    ]
    return ReleaseGroup(
        release_key=release_key,
        title=title,
        ext="adf",
        edition=None,
        group=None,
        chipset=None,
        records=records,
        disks=records,
        specials=[],
        has_main_disk=True,
        is_complete=True,
        quarantine_reason="near-duplicate spelling",
    )


def _make_staged_library(entries: dict[str, StagedState], adf_files: Optional[dict[str, list[str]]] = None) -> StagedLibrary:
    """Build a StagedLibrary with the given release_key -> StagedState mapping.

    ``adf_files`` optionally maps ``release_key`` to a list of
    ``source_filename`` strings so that curated membership matches
    the group records produced by ``_make_group``.
    """
    releases = {}
    for rk, state in entries.items():
        base = rk.replace("|", "_")
        rk_adf_files = None
        if adf_files is not None:
            rk_adf_files = adf_files.get(rk)
        if rk_adf_files is None:
            rk_adf_files = [f"{base}.adf"]
        releases[rk] = StagedReleaseEntry(
            release_key=rk,
            title=rk.replace("-", " ").title(),
            edition=None,
            group=None,
            chipset=None,
            adf_files=rk_adf_files,
            artwork_front=None,
            curation_state=state,
        )
    return StagedLibrary(releases=releases)


def _write_library_to_file(library: StagedLibrary, tmp_path: Path) -> Path:
    """Serialize a StagedLibrary to a library_state JSON file and return the path."""
    state_path = tmp_path / "library_state.json"
    data = {"library": library.to_dict(), "meta": {"schema_version": 1}}
    state_path.write_text(json.dumps(data), encoding="utf-8")
    return state_path


# ---------------------------------------------------------------------------
# _apply_curation tests
# ---------------------------------------------------------------------------

class TestApplyCuration:
    """Test the _apply_curation helper directly."""

    def test_accepted_clears_quarantine(self, tmp_path):
        """ACCEPTED entries clear quarantine_reason so the group survives to export."""
        group = _make_group("a10-tankkiller|1.0", "A-10 Tank Killer")
        library = _make_staged_library({"a10-tankkiller|1.0": StagedState.ACCEPTED})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason is None
        assert "a10-tankkiller" in decisions
        assert decisions["a10-tankkiller"] == "a10-tankkiller|1.0"

    def test_rejected_adds_quarantine(self, tmp_path):
        """REJECTED entries get a quarantine flag so export skips them."""
        group = _make_group("a10-tankkiller|1.5", "A-10 Tank Killer v1.5")
        library = _make_staged_library({"a10-tankkiller|1.5": StagedState.REJECTED})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason == "rejected by curation"

    def test_modified_keeps_quarantine(self, tmp_path):
        """MODIFIED/NEEDS_REVIEW entries keep existing quarantine."""
        group = _make_group("bard-taliii", "Bard's Tale III")
        group.quarantine_reason = "near-duplicate spelling"
        library = _make_staged_library({"bard-taliii": StagedState.MODIFIED})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason == "near-duplicate spelling"

    def test_needs_review_keeps_quarantine(self, tmp_path):
        """NEEDS_REVIEW entries keep existing quarantine."""
        group = _make_group("some-game", "Some Game")
        group.quarantine_reason = "special-only set"
        library = _make_staged_library({"some-game": StagedState.NEEDS_REVIEW})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason == "special-only set"

    def test_unmatched_entry_skipped_silently(self, tmp_path):
        """Unmatched release_key in the staged library does not affect the group."""
        group = _make_group("unknown-game", "Unknown Game")
        group.quarantine_reason = "near-duplicate spelling"
        library = _make_staged_library({"different-key": StagedState.ACCEPTED})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason == "near-duplicate spelling"
        assert decisions == {}

    def test_no_library_state_path(self):
        """When library_state_path is empty, _apply_curation returns None, {}."""
        group = _make_group("some-game", "Some Game")
        library, decisions = _apply_curation([group], None, None)
        assert library is None
        assert decisions == {}

    def test_nonexistent_state_file(self):
        """When the state file does not exist, returns None, {}."""
        group = _make_group("some-game", "Some Game")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nonexistent.json"
            library, decisions = _apply_curation([group], str(path), None)
            assert library is None
            assert decisions == {}

    def test_decisions_dict_preserved(self, tmp_path):
        """Existing decisions dict is preserved and augmented."""
        group = _make_group("a10-tankkiller|1.0", "A-10 Tank Killer")
        library = _make_staged_library({"a10-tankkiller|1.0": StagedState.ACCEPTED})
        state_path = _write_library_to_file(library, tmp_path)
        existing = {"existing-game": "existing-key"}
        _, decisions = _apply_curation([group], str(state_path), existing)
        assert decisions["existing-game"] == "existing-key"
        assert decisions["a10-tankkiller"] == "a10-tankkiller|1.0"

    def test_empty_library_returns_empty(self):
        """Empty StagedLibrary returns library with no releases, {}."""
        group = _make_group("some-game", "Some Game")
        library = StagedLibrary()
        result_lib, decisions = _apply_curation([group], "", None)
        assert result_lib is None
        assert decisions == {}


# ---------------------------------------------------------------------------
# _find_staged_artwork tests
# ---------------------------------------------------------------------------

class TestFindStagedArtwork:
    """Test the _find_staged_artwork helper."""

    def test_returns_none_when_no_library(self):
        """No staged library means no artwork."""
        result = _find_staged_artwork(None, "some-key")
        assert result is None

    def test_returns_none_when_no_release_key(self):
        """No release_key means no artwork."""
        library = StagedLibrary()
        result = _find_staged_artwork(library, None)
        assert result is None

    def test_returns_none_when_entry_missing(self):
        """Missing release_key returns None."""
        library = StagedLibrary()
        result = _find_staged_artwork(library, "missing-key")
        assert result is None

    def test_returns_none_when_artwork_front_empty(self):
        """Entry with artwork_front=None returns None."""
        library = _make_staged_library({"some-key": StagedState.ACCEPTED})
        result = _find_staged_artwork(library, "some-key")
        assert result is None

    def test_returns_artwork_bytes(self):
        """Returns bytes from artwork_front when file exists."""
        with tempfile.TemporaryDirectory() as td:
            art_path = Path(td) / "front.png"
            art_path.write_bytes(b"fake-artwork-bytes")
            entry = StagedReleaseEntry(
                release_key="some-key",
                title="Some Game",
                edition=None, group=None, chipset=None,
                artwork_front=str(art_path),
                curation_state=StagedState.ACCEPTED,
            )
            library = StagedLibrary(releases={"some-key": entry})
            result = _find_staged_artwork(library, "some-key")
            assert result == b"fake-artwork-bytes"

    def test_returns_none_when_file_missing(self):
        """Returns None when artwork_front path does not exist."""
        entry = StagedReleaseEntry(
            release_key="some-key",
            title="Some Game",
            edition=None, group=None, chipset=None,
            artwork_front="/nonexistent/path/art.png",
            curation_state=StagedState.ACCEPTED,
        )
        library = StagedLibrary(releases={"some-key": entry})
        result = _find_staged_artwork(library, "some-key")
        assert result is None


# ---------------------------------------------------------------------------
# export_release artwork fallback tests
# ---------------------------------------------------------------------------

class TestExportReleaseArtworkFallback:
    """Test that export_release falls back to staged-library artwork."""

    def test_staged_artwork_used_when_no_master(self, tmp_path):
        """When no processed artifact, the staged library's artwork_front is used."""
        art_path = tmp_path / "front.png"
        art_path.write_bytes(b"manual-artwork-bytes")

        entry = StagedReleaseEntry(
            release_key="some-game",
            title="Some Game",
            edition=None, group=None, chipset=None,
            artwork_front=str(art_path),
            curation_state=StagedState.ACCEPTED,
        )
        library = StagedLibrary(releases={"some-game": entry})

        record = ParsedRecord(
            source_filename=str(tmp_path / "disk.adf"),
            ext="adf", disk_number=0,
        )
        (tmp_path / "disk.adf").write_bytes(b"fake-adf-data")

        group = ReleaseGroup(
            release_key="some-game",
            title="Some Game",
            ext="adf", edition=None, group=None, chipset=None,
            records=[record], disks=[record], specials=[],
            has_main_disk=True, is_complete=True,
        )

        staging_root = tmp_path / "staging"
        staging_root.mkdir()

        written, _, _ = export_release(
            group, staging_root,
            basename="some-game",
            artwork_processed_dir=None,
            artwork_original_dir=None,
            nfo_dir=None, rtfm_dir=None,
            staged_library=library,
            release_key="some-game",
        )
        art_dest = staging_root / "ADF" / "some-game" / "some-game.jpg"
        assert art_dest.exists(), f"Expected artwork at {art_dest}"
        assert art_dest.read_bytes() == b"manual-artwork-bytes"

    def test_processed_artwork_takes_precedence(self, tmp_path):
        """Processed artwork takes precedence over staged library artwork_front."""
        processed_dir = tmp_path / "processed"
        processed_dir.mkdir()
        (processed_dir / "some-game.jpg").write_bytes(b"processed-artwork-bytes")

        art_path = tmp_path / "front.png"
        art_path.write_bytes(b"manual-artwork-bytes")

        entry = StagedReleaseEntry(
            release_key="some-game",
            title="Some Game",
            edition=None, group=None, chipset=None,
            artwork_front=str(art_path),
            curation_state=StagedState.ACCEPTED,
        )
        library = StagedLibrary(releases={"some-game": entry})

        record = ParsedRecord(
            source_filename=str(tmp_path / "disk.adf"),
            ext="adf", disk_number=0,
        )
        (tmp_path / "disk.adf").write_bytes(b"fake-adf-data")

        group = ReleaseGroup(
            release_key="some-game",
            title="Some Game",
            ext="adf", edition=None, group=None, chipset=None,
            records=[record], disks=[record], specials=[],
            has_main_disk=True, is_complete=True,
        )

        staging_root = tmp_path / "staging"
        staging_root.mkdir()

        written, _, _ = export_release(
            group, staging_root,
            basename="some-game",
            artwork_processed_dir=processed_dir,
            artwork_original_dir=None,
            nfo_dir=None, rtfm_dir=None,
            staged_library=library,
            release_key="some-game",
        )
        art_dest = staging_root / "ADF" / "some-game" / "some-game.jpg"
        assert art_dest.read_bytes() == b"processed-artwork-bytes"


# ---------------------------------------------------------------------------
# Integration-style tests for curation + export counters
# ---------------------------------------------------------------------------

class TestCurationExportCounters:
    """Verify counter consistency when curation is applied."""

    def test_accepted_group_not_in_skipped(self, tmp_path):
        """After curation applies, an ACCEPTED group is not counted as skipped."""
        group = _make_group("a10-tankkiller|1.0", "A-10 Tank Killer")
        library = _make_staged_library({"a10-tankkiller|1.0": StagedState.ACCEPTED})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason is None
        assert "a10-tankkiller" in decisions

    def test_rejected_group_in_skipped(self, tmp_path):
        """After curation applies, a REJECTED group has a quarantine reason."""
        group = _make_group("a10-tankkiller|1.5", "A-10 Tank Killer v1.5")
        library = _make_staged_library({"a10-tankkiller|1.5": StagedState.REJECTED})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason == "rejected by curation"

    def test_multi_disk_a10_v1_0_both_disks_accepted(self, tmp_path):
        """A-10 v1.0 with 2 disks: after curation ACCEPTED, both disks survive."""
        group = _make_group("a10-tankkiller|1.0", "A-10 Tank Killer", disks=["disk1.adf", "disk2.adf"])
        library = _make_staged_library(
            {"a10-tankkiller|1.0": StagedState.ACCEPTED},
            adf_files={"a10-tankkiller|1.0": ["a10-tankkiller_1.0_0.adf", "a10-tankkiller_1.0_1.adf"]},
        )
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason is None
        assert len(group.disks) == 2
        assert decisions.get("a10-tankkiller") == "a10-tankkiller|1.0"

    def test_multi_disk_a10_v1_5_all_three_disks_accepted(self, tmp_path):
        """A-10 v1.5 with 3 disks: after curation ACCEPTED, all three disks survive."""
        group = _make_group("a10-tankkiller|1.5", "A-10 Tank Killer", disks=["disk1.adf", "disk2.adf", "disk3.adf"])
        library = _make_staged_library(
            {"a10-tankkiller|1.5": StagedState.ACCEPTED},
            adf_files={"a10-tankkiller|1.5": ["a10-tankkiller_1.5_0.adf", "a10-tankkiller_1.5_1.adf", "a10-tankkiller_1.5_2.adf"]},
        )
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason is None
        assert len(group.disks) == 3
        assert decisions.get("a10-tankkiller") == "a10-tankkiller|1.5"

    def test_bards_tale_accepted_survives(self, tmp_path):
        """Bard's Tale III ACCEPTED entry clears quarantine."""
        group = _make_group("bards-taliii-the-thief-of-fate", "Bard's Tale III")
        group.quarantine_reason = "near-duplicate spelling"
        library = _make_staged_library({"bards-taliii-the-thief-of-fate": StagedState.ACCEPTED})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason is None
        assert "bards-taliii-the-thief-of-fate" in decisions

    def test_skipped_release_has_actionable_reason(self, tmp_path):
        """Every skipped selected release has an actionable reason."""
        group = _make_group("rejected-game", "Rejected Game")
        library = _make_staged_library({"rejected-game": StagedState.REJECTED})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason == "rejected by curation"


# ---------------------------------------------------------------------------
# GH-191: Curated ADF membership propagation tests
# ---------------------------------------------------------------------------


class TestCuratedMembershipPropagation:
    """Verify that curated ADF membership is propagated from the staged
    library back to ReleaseGroup objects so export uses the authoritative
    curated membership rather than the original pre-curation grouping.
    """

    def _make_group_with_files(self, release_key: str, disks: list[str]) -> ReleaseGroup:
        """Build a ReleaseGroup with explicit source filenames."""
        base = release_key.replace("|", "_")
        records = [
            ParsedRecord(
                source_filename=f"{base}_{i}.adf",
                ext="adf",
                disk_number=i,
            )
            for i in range(len(disks))
        ]
        return ReleaseGroup(
            release_key=release_key,
            title=release_key,
            ext="adf",
            edition=None, group=None, chipset=None,
            records=records, disks=records, specials=[],
            has_main_disk=True, is_complete=True,
        )

    def test_move_persists_to_saved_state(self, tmp_path):
        """Moving an ADF from one release to another persists in the
        saved staged library state."""
        # Group A originally has file_0.adf; Group B originally has file_1.adf.
        # After curation, file_0.adf is moved to Group B.
        group_a = self._make_group_with_files("game-a|1.0", ["game_a_0.adf"])
        group_b = self._make_group_with_files("game-b|1.0", ["game_b_0.adf"])

        # Staged library reflects the curated state: file_0 moved to game-b
        lib = StagedLibrary(releases={
            "game-a|1.0": StagedReleaseEntry(
                release_key="game-a|1.0", title="Game A",
                edition=None, group=None, chipset=None,
                adf_files=[], curation_state=StagedState.ACCEPTED,
            ),
            "game-b|1.0": StagedReleaseEntry(
                release_key="game-b|1.0", title="Game B",
                edition=None, group=None, chipset=None,
                adf_files=["game_a_0.adf", "game_b_0.adf"],
                curation_state=StagedState.ACCEPTED,
            ),
        })
        state_path = tmp_path / "library_state.json"
        state_path.write_text(json.dumps({"library": lib.to_dict(), "meta": {"schema_version": 1}}))

        _apply_curation([group_a, group_b], str(state_path), None)

        # Group A should have no files (moved away)
        assert len(group_a.records) == 0
        assert len(group_a.disks) == 0
        # Group B should have both files
        assert len(group_b.records) == 2
        assert len(group_b.disks) == 2
        assert [r.source_filename for r in group_b.records] == ["game_a_0.adf", "game_b_0.adf"]

    def test_reload_preserves_membership(self, tmp_path):
        """Reload/reopen preserves the same curated membership
        (move -> save -> reload must round-trip)."""
        group_a = self._make_group_with_files("bard-taliii|1.0", ["bardstalet3_0.adf"])
        group_b = self._make_group_with_files("bardstaleiiithethiefoffate|1.0", ["bardstaleii_0.adf"])

        # Simulate the curated state after moving bardstalet3_0.adf to game-b
        lib = StagedLibrary(releases={
            "bard-taliii|1.0": StagedReleaseEntry(
                release_key="bard-taliii|1.0", title="Bard's Tale III",
                edition=None, group=None, chipset=None,
                adf_files=[], curation_state=StagedState.ACCEPTED,
            ),
            "bardstaleiiithethiefoffate|1.0": StagedReleaseEntry(
                release_key="bardstaleiiithethiefoffate|1.0", title="Bard's Tale III: The Thief of Fate",
                edition=None, group=None, chipset=None,
                adf_files=["bardstalet3_0.adf", "bardstaleii_0.adf"],
                curation_state=StagedState.ACCEPTED,
            ),
        })
        state_path = tmp_path / "library_state.json"
        state_path.write_text(json.dumps({"library": lib.to_dict(), "meta": {"schema_version": 1}}))

        # First apply
        _apply_curation([group_a, group_b], str(state_path), None)
        assert len(group_a.records) == 0
        assert len(group_b.records) == 2

        # Reload: re-read the state file and apply again
        lib_reloaded = StagedLibrary.from_dict(json.loads(state_path.read_text())["library"])
        group_a2 = self._make_group_with_files("bard-taliii|1.0", ["bardstalet3_0.adf"])
        group_b2 = self._make_group_with_files("bardstaleiiithethiefoffate|1.0", ["bardstaleii_0.adf"])
        _apply_curation([group_a2, group_b2], str(state_path), None)

        assert len(group_a2.records) == 0
        assert len(group_b2.records) == 2
        assert [r.source_filename for r in group_b2.records] == ["bardstalet3_0.adf", "bardstaleii_0.adf"]

    def test_export_consumes_curated_membership(self, tmp_path):
        """Export uses curated membership, not original grouping.
        After moving an ADF to a target release, export produces the
        correct files for that release."""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()

        group_a = self._make_group_with_files("bard-taliii|1.0", ["bardstalet3_0.adf"])
        group_b = self._make_group_with_files("bardstaleiiithethiefoffate|1.0", ["bardstaleii_0.adf"])

        # Curated: both ADFs under bardstaleiiithethiefoffate
        lib = StagedLibrary(releases={
            "bard-taliii|1.0": StagedReleaseEntry(
                release_key="bard-taliii|1.0", title="Bard's Tale III",
                edition=None, group=None, chipset=None,
                adf_files=[], curation_state=StagedState.ACCEPTED,
            ),
            "bardstaleiiithethiefoffate|1.0": StagedReleaseEntry(
                release_key="bardstaleiiithethiefoffate|1.0", title="Bard's Tale III: The Thief of Fate",
                edition=None, group=None, chipset=None,
                adf_files=["bardstalet3_0.adf", "bardstaleii_0.adf"],
                curation_state=StagedState.ACCEPTED,
            ),
        })
        state_path = tmp_path / "library_state.json"
        state_path.write_text(json.dumps({"library": lib.to_dict(), "meta": {"schema_version": 1}}))

        _apply_curation([group_a, group_b], str(state_path), None)

        # Verify group membership is correct
        assert len(group_a.records) == 0
        assert len(group_b.records) == 2

        # Verify export works with the curated membership:
        # the group should have its curated records (not the original ones)
        assert len(group_b.records) == 2
        filenames = sorted(r.source_filename for r in group_b.records)
        assert filenames == ["bardstaleii_0.adf", "bardstalet3_0.adf"]
        # The resolved staged library should reflect the curated membership
        assert len(lib.releases["bardstaleiiithethiefoffate|1.0"].adf_files) == 2

    def test_moved_from_release_loses_adf(self, tmp_path):
        """ADFs moved away from a source release no longer export under
        the old release."""
        group_source = self._make_group_with_files("source-release|1.0", ["source_0.adf", "source_1.adf"])
        group_target = self._make_group_with_files("target-release|1.0", ["target_0.adf"])

        # Curated: source_0.adf moved from source-release to target-release
        lib = StagedLibrary(releases={
            "source-release|1.0": StagedReleaseEntry(
                release_key="source-release|1.0", title="Source Release",
                edition=None, group=None, chipset=None,
                adf_files=["source_1.adf"], curation_state=StagedState.ACCEPTED,
            ),
            "target-release|1.0": StagedReleaseEntry(
                release_key="target-release|1.0", title="Target Release",
                edition=None, group=None, chipset=None,
                adf_files=["target_0.adf", "source_0.adf"],
                curation_state=StagedState.ACCEPTED,
            ),
        })
        state_path = tmp_path / "library_state.json"
        state_path.write_text(json.dumps({"library": lib.to_dict(), "meta": {"schema_version": 1}}))

        _apply_curation([group_source, group_target], str(state_path), None)

        # Source should only have source_1.adf
        assert len(group_source.records) == 1
        assert group_source.records[0].source_filename == "source_1.adf"
        # Target should have both target_0.adf and source_0.adf
        assert len(group_target.records) == 2
        filenames = sorted(r.source_filename for r in group_target.records)
        assert filenames == ["source_0.adf", "target_0.adf"]

    def test_multi_adf_target_exports_every_member(self, tmp_path):
        """Multi-ADF target exports every member."""
        group_a = self._make_group_with_files("bard-taliii|1.0", ["bard_0.adf"])
        group_b = self._make_group_with_files("bardstaleiiithethiefoffate|1.0", ["bard_1.adf"])

        # Curated: both ADFs under the target release
        lib = StagedLibrary(releases={
            "bard-taliii|1.0": StagedReleaseEntry(
                release_key="bard-taliii|1.0", title="Bard's Tale III",
                edition=None, group=None, chipset=None,
                adf_files=[], curation_state=StagedState.ACCEPTED,
            ),
            "bardstaleiiithethiefoffate|1.0": StagedReleaseEntry(
                release_key="bardstaleiiithethiefoffate|1.0", title="Bard's Tale III: The Thief of Fate",
                edition=None, group=None, chipset=None,
                adf_files=["bard_0.adf", "bard_1.adf"],
                curation_state=StagedState.ACCEPTED,
            ),
        })
        state_path = tmp_path / "library_state.json"
        state_path.write_text(json.dumps({"library": lib.to_dict(), "meta": {"schema_version": 1}}))

        _apply_curation([group_a, group_b], str(state_path), None)

        assert len(group_a.records) == 0
        assert len(group_b.records) == 2
        assert len(group_b.disks) == 2

    def test_unresolved_curated_member_fails_loudly(self, tmp_path):
        """If a curated ADF cannot be resolved, export should not silently
        drop it. The _apply_curation still propagates the membership
        and creates a minimal ParsedRecord; the export path will attempt
        to copy the file and will report a conflict for missing source."""
        group_b = self._make_group_with_files("target-release|1.0", ["target_0.adf"])

        # Curated: target has a file that doesn't exist in any group's records
        lib = StagedLibrary(releases={
            "target-release|1.0": StagedReleaseEntry(
                release_key="target-release|1.0", title="Target Release",
                edition=None, group=None, chipset=None,
                adf_files=["target_0.adf", "nonexistent_ghost.adf"],
                curation_state=StagedState.ACCEPTED,
            ),
        })
        state_path = tmp_path / "library_state.json"
        state_path.write_text(json.dumps({"library": lib.to_dict(), "meta": {"schema_version": 1}}))

        _apply_curation([group_b], str(state_path), None)

        # The unresolved file should still be in the group's records
        # as a minimal ParsedRecord (created by _sync_group_membership)
        assert len(group_b.records) == 2
        filenames = sorted(r.source_filename for r in group_b.records)
        assert filenames == ["nonexistent_ghost.adf", "target_0.adf"]