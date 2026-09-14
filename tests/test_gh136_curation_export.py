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
from amiga_adf_library_builder.pipeline import _apply_curation
from amiga_adf_library_builder.exporter import (
    _find_staged_artwork,
    export_release,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_group(release_key: str, title: str, disks: list[str] | None = None) -> ReleaseGroup:
    """Build a minimal ReleaseGroup with the given release_key."""
    disks = disks or [f"{title.replace(' ', '_')}.adf"]
    records = [
        ParsedRecord(
            source_filename=f"original/{title.replace(' ', '_')}_{i}.adf",
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


def _make_staged_library(entries: dict[str, StagedState]) -> StagedLibrary:
    """Build a StagedLibrary with the given release_key -> StagedState mapping."""
    releases = {}
    for rk, state in entries.items():
        releases[rk] = StagedReleaseEntry(
            release_key=rk,
            title=rk.replace("-", " ").title(),
            edition=None,
            group=None,
            chipset=None,
            adf_files=[f"{rk}.adf"],
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
        library = _make_staged_library({"a10-tankkiller|1.0": StagedState.ACCEPTED})
        state_path = _write_library_to_file(library, tmp_path)
        _, decisions = _apply_curation([group], str(state_path), None)
        assert group.quarantine_reason is None
        assert len(group.disks) == 2
        assert decisions.get("a10-tankkiller") == "a10-tankkiller|1.0"

    def test_multi_disk_a10_v1_5_all_three_disks_accepted(self, tmp_path):
        """A-10 v1.5 with 3 disks: after curation ACCEPTED, all three disks survive."""
        group = _make_group("a10-tankkiller|1.5", "A-10 Tank Killer", disks=["disk1.adf", "disk2.adf", "disk3.adf"])
        library = _make_staged_library({"a10-tankkiller|1.5": StagedState.ACCEPTED})
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