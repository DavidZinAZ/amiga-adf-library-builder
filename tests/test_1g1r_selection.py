#!/usr/bin/env python3
"""Tests for the GH-107 Slice 6 1G1R selection engine."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from amiga_adf_library_builder.models import (
    ParsedRecord,
    ReleaseGroup,
    StagedState,
)
from amiga_adf_library_builder.selection import (
    SelectionRecord,
    SelectionResult,
    _score_completeness,
    _score_edition,
    _score_version,
    rank_group,
    select_one_per_game,
    explain_selection,
    write_selection_manifest,
    load_selection_manifest,
    persist_operator_decisions,
    load_operator_decisions,
    operator_decision_key,
)


def make_record(
    filename: str = "Test_Game_Disk.adf",
    release_key: str = "testgame",
    title: str = "Test Game",
    disk_number: int = 1,
    total_disks: int = 1,
    special_disk: bool = False,
    edition: str = None,
    version: str = None,
    group: str = None,
    language: str = None,
) -> ParsedRecord:
    return ParsedRecord(
        source_filename=filename,
        ext="adf",
        title=title,
        release_key=release_key,
        disk_number=disk_number,
        total_disks=total_disks,
        special_disk=special_disk,
        edition=edition,
        version=version,
        group=group,
        language=language,
    )


def make_group(
    release_key: str = "testgame",
    title: str = "Test Game",
    disks: list = None,
    specials: list = None,
    edition: str = None,
    version: str = None,
    language: str = None,
    is_complete: bool = True,
    has_main_disk: bool = True,
) -> ReleaseGroup:
    records = disks or [make_record(release_key=release_key)]
    return ReleaseGroup(
        release_key=release_key,
        title=title,
        edition=edition,
        group=None,
        chipset=None,
        language=language,
        version=version,
        alt_marker=None,
        ext="adf",
        records=records,
        disks=disks or [make_record(release_key=release_key)],
        specials=specials or [],
        is_complete=is_complete,
        has_main_disk=has_main_disk,
    )


# ---------------------------------------------------------------------------
# 1. Completeness scoring
# ---------------------------------------------------------------------------

class TestCompletenessScoring:
    def test_main_disk_present(self):
        g = make_group(has_main_disk=True, is_complete=True)
        assert _score_completeness(g) == 80.0  # 50 + 30

    def test_no_main_disk(self):
        g = make_group(has_main_disk=False, is_complete=False)
        assert _score_completeness(g) == 0.0

    def test_multi_disk_complete(self):
        records = [
            make_record(filename="d1.adf", disk_number=1, total_disks=2),
            make_record(filename="d2.adf", disk_number=2, total_disks=2),
        ]
        g = make_group(disks=records, has_main_disk=True)
        assert _score_completeness(g) == 80.0

    def test_multi_disk_incomplete(self):
        records = [
            make_record(filename="d1.adf", disk_number=1, total_disks=2),
        ]
        g = make_group(disks=records, has_main_disk=True)
        assert _score_completeness(g) == 50.0  # no bonus: missing disk 2


# ---------------------------------------------------------------------------
# 2. Edition scoring
# ---------------------------------------------------------------------------

class TestEditionScoring:
    def test_platinum(self):
        g = make_group(edition="Platinum Edition")
        assert _score_edition(g) == 40.0

    def test_enhanced(self):
        g = make_group(edition="Enhanced")
        assert _score_edition(g) == 30.0

    def test_normal_edition(self):
        g = make_group(edition="Standard")
        assert _score_edition(g) == 20.0

    def test_no_edition(self):
        g = make_group(edition=None)
        assert _score_edition(g) == 10.0


# ---------------------------------------------------------------------------
# 3. Version scoring
# ---------------------------------------------------------------------------

class TestVersionScoring:
    def test_v1_0(self):
        g = make_group(version="1.0")
        # New scheme: numeric segments -> 50 + min(1,10)*5 = 55
        assert _score_version(g) == 55.0

    def test_v2_0(self):
        g = make_group(version="2.0")
        # New scheme: 50 + min(2,10)*5 = 60
        assert _score_version(g) == 60.0

    def test_unknown_version(self):
        g = make_group(version="9.9")
        # New scheme: 50 + min(9,10)*5 = 95
        assert _score_version(g) == 95.0

    def test_v10_no_collision_with_v1_0(self):
        """Version "10" must NOT score equal to "1.0" (the bug that was fixed)."""
        g10 = make_group(version="10")
        g1_0 = make_group(version="1.0")
        assert _score_version(g10) != _score_version(g1_0)
        assert _score_version(g10) > _score_version(g1_0)

    def test_no_version(self):
        g = make_group(version=None)
        assert _score_version(g) == 10.0


# ---------------------------------------------------------------------------
# 4. Ranking (unit level)
# ---------------------------------------------------------------------------

class TestRankGroup:
    def test_deterministic_same_input(self):
        g = make_group()
        s1, r1 = rank_group(g)
        s2, r2 = rank_group(g)
        assert s1 == s2
        assert r1 == r2

    def test_platinum_scores_higher_than_none(self):
        plat = make_group(edition="Platinum Edition")
        plain = make_group(edition=None)
        s_plant, _ = rank_group(plat)
        s_plain, _ = rank_group(plain)
        assert s_plant > s_plain

    def test_complete_scores_higher_than_incomplete(self):
        complete = make_group(has_main_disk=True, is_complete=True)
        incomplete = make_group(has_main_disk=False, is_complete=False)
        s_c, _ = rank_group(complete)
        s_i, _ = rank_group(incomplete)
        assert s_c > s_i


# ---------------------------------------------------------------------------
# 5. Selection: operator override wins
# ---------------------------------------------------------------------------

class TestOperatorOverride:
    def test_approval_overrides_ranking(self):
        """An operator approval always selects that release regardless of score."""
        # Both candidates share the same game id (same base key "game")
        # so only one wins — the override wins over the ranked candidate.
        gold = make_group(release_key="game|gold", title="Gold Edition", edition="Platinum Edition")
        silver = make_group(release_key="game|silver", title="Silver Edition")
        groups = [gold, silver]

        from amiga_adf_library_builder.manual_approvals import ApprovalRecord
        approval = ApprovalRecord(
            approval_id="apr_test",
            release_keys=["game|silver"],
            canonical_title="Silver Edition",
            approved_folder="Silver-Edition",
        )
        approvals = {"game|silver": approval, "silver": approval}

        result = select_one_per_game(groups, approvals=approvals)
        # Same game => only 1 selected; silver wins via operator override.
        assert len(result.selected) == 1
        assert result.selected[0].release_key == "game|silver"
        assert result.provenance["decisions"][0]["decision"] == "operator_override"
        # Gold is rejected, eliminated by the override winner.
        assert len(result.rejected) == 1
        assert result.rejected[0].release_key == "game|gold"

    def test_no_approvals_ranks(self):
        gold = make_group(release_key="game|gold", title="Gold", edition="Platinum Edition")
        silver = make_group(release_key="game|silver", title="Silver")
        groups = [silver, gold]  # reversed order
        result = select_one_per_game(groups)
        # One winner per game; gold (Platinum) wins over silver.
        assert len(result.selected) == 1
        assert result.selected[0].release_key == "game|gold"  # Platinum ranks higher

    def test_single_candidate(self):
        g = make_group(release_key="solo")
        result = select_one_per_game([g])
        assert len(result.selected) == 1
        assert result.provenance["decisions"][0]["decision"] == "only_candidate"


# ---------------------------------------------------------------------------
# 6. Multi-game isolation
# ---------------------------------------------------------------------------

class TestMultiGameIsolation:
    def test_selects_one_per_game(self):
        g1a = make_group(release_key="game1|a", title="Game One", edition="Platinum Edition")
        g1b = make_group(release_key="game1|b", title="Game One", edition=None)
        g2a = make_group(release_key="game2|a", title="Game Two")
        g2b = make_group(release_key="game2|b", title="Game Two", edition="Platinum Edition")
        groups = [g1a, g1b, g2a, g2b]
        result = select_one_per_game(groups)
        assert len(result.selected) == 2
        selected_keys = {s.release_key for s in result.selected}
        assert "game1|a" in selected_keys
        assert "game2|b" in selected_keys


# ---------------------------------------------------------------------------
# 7. Rejected records
# ---------------------------------------------------------------------------

class TestRejectedRecords:
    def test_rejected_list_populated(self):
        a = make_group(release_key="game|a", edition="Platinum Edition")
        b = make_group(release_key="game|b", edition=None)
        result = select_one_per_game([a, b])
        assert len(result.rejected) == 1
        assert result.rejected[0].release_key == "game|b"
        assert result.rejected[0].selected is False

    def test_rejected_reason_mentions_winner(self):
        a = make_group(release_key="game|a", edition="Platinum Edition")
        b = make_group(release_key="game|b", edition=None)
        result = select_one_per_game([a, b])
        assert "game|a" in result.rejected[0].reason


# ---------------------------------------------------------------------------
# 8. Manifests
# ---------------------------------------------------------------------------

class TestManifests:
    def test_write_load_roundtrip(self, tmp_path: Path):
        a = make_group(release_key="game|a", title="Game A", edition="Platinum Edition")
        b = make_group(release_key="game|b", title="Game B")
        result = select_one_per_game([a, b])
        manifest_path = tmp_path / "selection.json"
        write_selection_manifest(result, manifest_path)
        loaded = load_selection_manifest(manifest_path)
        assert loaded is not None
        assert loaded["schema_version"] == 1
        assert len(loaded["selection_manifest"]) == 1
        assert loaded["provenance"]["selected_count"] == 1
        assert loaded["provenance"]["rejected_count"] == 1

    def test_manifest_deterministic(self, tmp_path: Path):
        a = make_group(release_key="game|a", title="Game A", edition="Platinum Edition")
        b = make_group(release_key="game|b", title="Game B")
        r1 = select_one_per_game([a, b])
        r2 = select_one_per_game([a, b])
        p1 = tmp_path / "m1.json"
        p2 = tmp_path / "m2.json"
        write_selection_manifest(r1, p1)
        write_selection_manifest(r2, p2)
        assert p1.read_text() == p2.read_text()

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert load_selection_manifest(tmp_path / "missing.json") is None


# ---------------------------------------------------------------------------
# 9. Persistence across restarts
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_persist_load_cycle(self, tmp_path: Path):
        a = make_group(release_key="game|a", title="Game A", edition="Platinum Edition")
        b = make_group(release_key="game|b", title="Game B")
        result = select_one_per_game([a, b])
        lib_root = tmp_path / "library"
        lib_root.mkdir()
        (lib_root / "curation").mkdir()
        decisions_path = persist_operator_decisions(result, lib_root)
        loaded = load_operator_decisions(lib_root)
        assert loaded is not None
        assert loaded["provenance"]["selected_count"] == 1

    def test_operator_decision_key_path(self, tmp_path: Path):
        lib_root = tmp_path / "library"
        key = operator_decision_key(lib_root)
        assert key.name == "1g1r_decisions.json"
        assert "curation" in str(key)


# ---------------------------------------------------------------------------
# 10. Explain selection
# ---------------------------------------------------------------------------

class TestExplainSelection:
    def test_explain_includes_score(self):
        g = make_group(edition="Platinum Edition")
        out = explain_selection(g)
        assert out["score"] > 0
        assert out["release_key"] == "testgame"
        assert "completeness" in out

    def test_explain_shows_canonical_flag(self):
        g = make_group()
        out = explain_selection(g, canon=None)
        assert out["canonical_available"] is False


# ---------------------------------------------------------------------------
# 11. Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_same_groups_same_selection(self):
        groups = [
            make_group(release_key="game|a", edition="Platinum Edition"),
            make_group(release_key="game|b"),
        ]
        r1 = select_one_per_game(groups)
        r2 = select_one_per_game(groups)
        assert r1.selection_manifest == r2.selection_manifest
        assert r1.provenance["decisions"] == r2.provenance["decisions"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))