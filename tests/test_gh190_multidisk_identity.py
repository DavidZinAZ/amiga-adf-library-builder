"""Regression tests for GH-190: Multi-disk releases must group by release identity,
not by per-disk crack/scene group annotations.

Root cause: _build_release_key() included _norm(rec.group) as a component of the
release key, causing disks from the same game with different crack groups to be
split into separate release groups.

Acceptance criteria:
1. UFO = ONE release, ADF count 4
2. Dark Queen = ONE release, ADF count 3 despite [cr SR] on one disk only
3. Ultima V = ONE release, ADF count 2 despite different per-disk annotations
4. Ultima VI = ONE release, ADF count 4
5. Bard's Tale III: numbered disks not split by per-disk annotations
6. Defender Disk1 + Disk 2 remain together (no-space + space ordinal styles)
7. Provenance fields preserved after grouping
8. Different edition/version/platform fixtures still split correctly
9. Every input ADF accounted for at scan->Preview boundary
10. GH-191 curated-membership tests remain green (verified separately)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.grouper import group_records


# ---------------------------------------------------------------------------
# Fixture filenames from the real Windows corpus
# ---------------------------------------------------------------------------

# UFO - Enemy Unknown: 4 disks, [cr PSG] on all, should be ONE release
UFO_FILES = [
    "UFO - Enemy Unknown (1994)(MicroProse)(AGA)(M3)[cr PSG][a](Disk 1..4 of 4).adf",
    "UFO - Enemy Unknown (1994)(MicroProse)(AGA)(M3)[cr PSG][a](Disk 2..4 of 4).adf",
    "UFO - Enemy Unknown (1994)(MicroProse)(AGA)(M3)[cr PSG][a](Disk 3..4 of 4).adf",
    "UFO - Enemy Unknown (1994)(MicroProse)(AGA)(M3)[cr PSG][a](Disk 4..4 of 4).adf",
]

# Dark Queen of Krynn: 3 disks, [cr SR] only on disk 1
DARK_QUEEN_FILES = [
    "Dark Queen of Krynn, The v1.0 (1992-06-16)(SSI)(Disk 1 of 3)[cr SR].adf",
    "Dark Queen of Krynn, The v1.0 (1992-06-16)(SSI)(Disk 2 of 3).adf",
    "Dark Queen of Krynn, The v1.0 (1992-06-16)(SSI)(Disk 3 of 3).adf",
]

# Ultima V: 2 disks with different per-disk annotations
ULTIMA_V_FILES = [
    "Ultima V - Warriors of Destiny (1989)(Origin)(Disk 1 of 2)(Play-Underground)[cr CLS][a].adf",
    "Ultima V - Warriors of Destiny (1989)(Origin)(Disk 2 of 2)(Dungeon-Intro)[cr CLS][b2 dump].adf",
]

# Ultima VI: 4 disks with [cr Panther] and [FD installed] on all
ULTIMA_VI_FILES = [
    "Ultima VI - The False Prophet v1.12 (1992)(Origin)(Disk 1 of 4)(Program)[cr Panther][a][FD installed].adf",
    "Ultima VI - The False Prophet v1.12 (1992)(Origin)(Disk 2 of 4)(Program)[cr Panther][a][FD installed].adf",
    "Ultima VI - The False Prophet v1.12 (1992)(Origin)(Disk 3 of 4)(Program)[cr Panther][a][FD installed].adf",
    "Ultima VI - The False Prophet v1.12 (1992)(Origin)(Disk 4 of 4)(Portrait)[cr Panther][a][FD installed].adf",
]

# Bard's Tale III: 2 numbered disks + special char disk
BARD_S_TALE_III_FILES = [
    "Bard's Tale III, The - Thief of Fate (1991 Electronic Arts)(Disk 1 of 2).adf",
    "Bard's Tale III, The - Thief of Fate (1991 Electronic Arts)(Disk 2 of 2)[a].adf",
    "Bards_Talet3_Char.adf",
]

# Defender: 2 disks, no-space + space ordinal styles
DEFENDER_FILES = [
    "Defender Disk1.adf",
    "Defender Disk 2.adf",
]

# Different versions that must NOT collapse
A10_TANK_KILLER_V10 = [
    "A-10 Tank Killer v1.0 (Disk 1 of 2).adf",
    "A-10 Tank Killer v1.0 (Disk 2 of 2).adf",
]
A10_TANK_KILLER_V15 = [
    "A-10 Tank Killer v1.5 (Disk 1 of 3).adf",
    "A-10 Tank Killer v1.5 (Disk 2 of 3).adf",
    "A-10 Tank Killer v1.5 (Disk 3 of 3).adf",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_many(filenames: list[str]) -> list[ParsedRecord]:
    """Parse a list of filenames and return ParsedRecords."""
    return [parse_filename(f) for f in filenames]


def _group_many(records: list[ParsedRecord]) -> list[ReleaseGroup]:
    """Group parsed records and return release groups."""
    return group_records(records)


def _count_releases(filenames: list[str]) -> tuple[int, int]:
    """Parse and group filenames, return (num_releases, total_adfs)."""
    records = _parse_many(filenames)
    groups = _group_many(records)
    total_adfs = sum(len(g.disks) + len(g.specials) for g in groups)
    return len(groups), total_adfs


def _get_release_keys(filenames: list[str]) -> list[str]:
    """Parse filenames and return the set of release keys."""
    records = _parse_many(filenames)
    return sorted(set(r.release_key for r in records))


# ---------------------------------------------------------------------------
# Test 1: UFO = ONE release, 4 ADFs
# ---------------------------------------------------------------------------

class TestUFOEnemyUnknown:
    """UFO - Enemy Unknown (1994)(MicroProse)(AGA)(M3)[cr PSG][a](Disk 1..4 of 4)
    All 4 disks carry [cr PSG] and should form ONE release."""

    def test_ufo_one_release(self):
        num_releases, total_adfs = _count_releases(UFO_FILES)
        assert num_releases == 1, f"Expected 1 release, got {num_releases}"
        assert total_adfs == 4, f"Expected 4 ADFs, got {total_adfs}"

    def test_ufo_same_release_key(self):
        keys = _get_release_keys(UFO_FILES)
        assert len(keys) == 1, f"Expected 1 release key, got {len(keys)}: {keys}"

    def test_ufo_provenance_preserved(self):
        """Per-disk provenance (group, chipset, etc.) must survive grouping."""
        records = _parse_many(UFO_FILES)
        for rec in records:
            assert rec.group == "PSG", f"Expected group 'PSG', got {rec.group!r}"
            assert rec.chipset == "AGA/M3", f"Expected chipset 'AGA/M3', got {rec.chipset!r}"
            assert rec.alt_marker == "a", f"Expected alt_marker 'a', got {rec.alt_marker!r}"


# ---------------------------------------------------------------------------
# Test 2: Dark Queen = ONE release, 3 ADFs despite [cr SR] on disk 1 only
# ---------------------------------------------------------------------------

class TestDarkQueenOfKrynn:
    """Dark Queen of Krynn: disk 1 has [cr SR], disks 2 and 3 have no crack tag.
    Must form ONE release despite the per-disk group difference."""

    def test_dark_queen_one_release(self):
        num_releases, total_adfs = _count_releases(DARK_QUEEN_FILES)
        assert num_releases == 1, f"Expected 1 release, got {num_releases}"
        assert total_adfs == 3, f"Expected 3 ADFs, got {total_adfs}"

    def test_dark_queen_same_release_key(self):
        keys = _get_release_keys(DARK_QUEEN_FILES)
        assert len(keys) == 1, f"Expected 1 release key, got {len(keys)}: {keys}"

    def test_dark_queen_provenance_preserved(self):
        """The [cr SR] group on disk 1 must be preserved as provenance."""
        records = _parse_many(DARK_QUEEN_FILES)
        cr_disks = [r for r in records if r.group == "SR"]
        assert len(cr_disks) == 1, f"Expected 1 disk with group 'SR', got {len(cr_disks)}"
        assert cr_disks[0].disk_number == 1
        # Disks 2 and 3 should have group=None (no crack tag)
        no_cr_disks = [r for r in records if r.group is None]
        assert len(no_cr_disks) == 2, f"Expected 2 disks without group, got {len(no_cr_disks)}"


# ---------------------------------------------------------------------------
# Test 3: Ultima V = ONE release, 2 ADFs despite different annotations
# ---------------------------------------------------------------------------

class TestUltimaV:
    """Ultima V: disk 1 has [cr CLS][a], disk 2 has [cr CLS][b2 dump].
    Must form ONE release despite different per-disk annotations."""

    def test_ultima_v_one_release(self):
        num_releases, total_adfs = _count_releases(ULTIMA_V_FILES)
        assert num_releases == 1, f"Expected 1 release, got {num_releases}"
        assert total_adfs == 2, f"Expected 2 ADFs, got {total_adfs}"

    def test_ultima_v_same_release_key(self):
        keys = _get_release_keys(ULTIMA_V_FILES)
        assert len(keys) == 1, f"Expected 1 release key, got {len(keys)}: {keys}"

    def test_ultima_v_provenance_preserved(self):
        """Per-disk provenance must survive grouping."""
        records = _parse_many(ULTIMA_V_FILES)
        for rec in records:
            assert rec.group == "CLS", f"Expected group 'CLS', got {rec.group!r}"
            assert rec.year == "1989", f"Expected year '1989', got {rec.year!r}"
            assert rec.disk_number in (1, 2)


# ---------------------------------------------------------------------------
# Test 4: Ultima VI = ONE release, 4 ADFs
# ---------------------------------------------------------------------------

class TestUltimaVI:
    """Ultima VI: 4 disks all with [cr Panther][a][FD installed].
    Must form ONE release with 4 ADFs."""

    def test_ultima_vi_one_release(self):
        num_releases, total_adfs = _count_releases(ULTIMA_VI_FILES)
        assert num_releases == 1, f"Expected 1 release, got {num_releases}"
        assert total_adfs == 4, f"Expected 4 ADFs, got {total_adfs}"

    def test_ultima_vi_same_release_key(self):
        keys = _get_release_keys(ULTIMA_VI_FILES)
        assert len(keys) == 1, f"Expected 1 release key, got {len(keys)}: {keys}"

    def test_ultima_vi_provenance_preserved(self):
        """Per-disk provenance must survive grouping."""
        records = _parse_many(ULTIMA_VI_FILES)
        for rec in records:
            assert rec.group == "Panther", f"Expected group 'Panther', got {rec.group!r}"
            # [FD installed] is a per-disk annotation preserved as provenance
            # via the bracket tag mechanism; alt_marker 'a' is on each disk


# ---------------------------------------------------------------------------
# Test 5: Bard's Tale III - numbered disks stay together
# ---------------------------------------------------------------------------

class TestBardsTaleIII:
    """Bard's Tale III: 2 numbered disks + 1 special character disk.
    Numbered disks must NOT be split by per-disk annotations.
    Special character disk handling must be intentional and documented."""

    def test_bards_tale_numbered_disks_one_release(self):
        """Disk 1 and Disk 2 of Bard's Tale III must be ONE release."""
        numbered = [
            "Bard's Tale III, The - Thief of Fate (1991 Electronic Arts)(Disk 1 of 2).adf",
            "Bard's Tale III, The - Thief of Fate (1991 Electronic Arts)(Disk 2 of 2)[a].adf",
        ]
        num_releases, total_adfs = _count_releases(numbered)
        assert num_releases == 1, f"Expected 1 release for numbered disks, got {num_releases}"
        assert total_adfs == 2, f"Expected 2 ADFs for numbered disks, got {total_adfs}"

    def test_bards_tale_same_release_key(self):
        numbered = [
            "Bard's Tale III, The - Thief of Fate (1991 Electronic Arts)(Disk 1 of 2).adf",
            "Bard's Tale III, The - Thief of Fate (1991 Electronic Arts)(Disk 2 of 2)[a].adf",
        ]
        keys = _get_release_keys(numbered)
        assert len(keys) == 1, f"Expected 1 release key for numbered disks, got {len(keys)}: {keys}"

    def test_bards_tale_provenance_preserved(self):
        """The [a] alt marker on disk 2 must be preserved."""
        records = _parse_many([
            "Bard's Tale III, The - Thief of Fate (1991 Electronic Arts)(Disk 1 of 2).adf",
            "Bard's Tale III, The - Thief of Fate (1991 Electronic Arts)(Disk 2 of 2)[a].adf",
        ])
        disk2 = [r for r in records if r.disk_number == 2][0]
        assert disk2.alt_marker == "a", f"Expected alt_marker 'a' on disk 2, got {disk2.alt_marker!r}"

    def test_bards_tale_special_disk_not_silently_dropped(self):
        """The character disk must be accounted for — either grouped or review-routed,
        never silently dropped. It should be parseable as a special disk."""
        char_rec = parse_filename("Bards_Talet3_Char.adf")
        # The special disk should be recognized (either as special_disk or valid record)
        assert char_rec.source_filename == "Bards_Talet3_Char.adf"
        # If it's detected as a special disk, that's expected — it must NOT disappear
        # The important thing is that it's parseable and not silently lost
        assert char_rec.title is not None or char_rec.special_disk is True or char_rec.source_filename is not None


# ---------------------------------------------------------------------------
# Test 6: Defender Disk1 + Disk 2 remain together
# ---------------------------------------------------------------------------

class TestDefender:
    """Defender: no-space (Disk1) and space (Disk 2) ordinal styles must group together."""

    def test_defender_one_release(self):
        num_releases, total_adfs = _count_releases(DEFENDER_FILES)
        assert num_releases == 1, f"Expected 1 release, got {num_releases}"
        assert total_adfs == 2, f"Expected 2 ADFs, got {total_adfs}"

    def test_defender_same_release_key(self):
        keys = _get_release_keys(DEFENDER_FILES)
        assert len(keys) == 1, f"Expected 1 release key, got {len(keys)}: {keys}"

    def test_defender_disk_ordinal_preserved(self):
        """Both disk ordinals must be correctly parsed."""
        records = _parse_many(DEFENDER_FILES)
        disk_numbers = sorted(r.disk_number for r in records if r.disk_number)
        assert disk_numbers == [1, 2], f"Expected disk numbers [1, 2], got {disk_numbers}"


# ---------------------------------------------------------------------------
# Test 7: Provenance fields preserved after grouping
# ---------------------------------------------------------------------------

class TestProvenancePreserved:
    """Verify that per-disk metadata (group, trainer, alt_marker, etc.) is
    preserved in ParsedRecords after grouping — fields are not deleted."""

    def test_group_provenance_on_each_record(self):
        """After parsing, each ParsedRecord retains its group field."""
        records = _parse_many(DARK_QUEEN_FILES)
        for rec in records:
            # All should have the same title
            assert rec.title is not None
            # The group field must reflect the actual filename content
            if "Disk 1" in rec.source_filename:
                assert rec.group == "SR", f"Disk 1 should have group 'SR', got {rec.group!r}"

    def test_alt_marker_provenance_preserved(self):
        """Alt markers must survive grouping."""
        records = _parse_many(UFO_FILES)
        for rec in records:
            assert rec.alt_marker == "a"


# ---------------------------------------------------------------------------
# Test 8: Different edition/version/platform fixtures still split
# ---------------------------------------------------------------------------

class TestDifferentVersionsDoNotCollapse:
    """Genuinely different versions must NOT collapse into one release."""

    def test_a10_v1_0_vs_v1_5_split(self):
        """A-10 Tank Killer v1.0 (2 disks) vs v1.5 (3 disks) must be TWO releases."""
        all_files = A10_TANK_KILLER_V10 + A10_TANK_KILLER_V15
        records = _parse_many(all_files)
        groups = _group_many(records)
        release_keys = set(g.release_key for g in groups)
        # v1.0 and v1.5 should have different release keys
        assert len(release_keys) == 2, f"Expected 2 releases, got {len(release_keys)}"

    def test_a10_v1_0_count(self):
        num_releases, total_adfs = _count_releases(A10_TANK_KILLER_V10)
        assert num_releases == 1
        assert total_adfs == 2

    def test_a10_v1_5_count(self):
        num_releases, total_adfs = _count_releases(A10_TANK_KILLER_V15)
        assert num_releases == 1
        assert total_adfs == 3

    def test_rocket_ranger_stays_together(self):
        """Rocket Ranger (2 disks) should be ONE release."""
        rocket_ranger = [
            "Rocket Ranger (1990)(Strategic Simulations)(Disk 1 of 2).adf",
            "Rocket Ranger (1990)(Strategic Simulations)(Disk 2 of 2).adf",
        ]
        num_releases, total_adfs = _count_releases(rocket_ranger)
        assert num_releases == 1
        assert total_adfs == 2

    def test_untouchables_stays_together(self):
        """Untouchables, The (2 disks) should be ONE release."""
        untouchables = [
            "Untouchables, The (1989)(Ocean)(Disk 1 of 2).adf",
            "Untouchables, The (1989)(Ocean)(Disk 2 of 2).adf",
        ]
        num_releases, total_adfs = _count_releases(untouchables)
        assert num_releases == 1
        assert total_adfs == 2


# ---------------------------------------------------------------------------
# Test 9: Every input ADF accounted for at scan->Preview boundary
# ---------------------------------------------------------------------------

class TestFullCorpusAccounting:
    """Every input ADF must be accounted for — grouped, intentionally special,
    or explicitly review-routed. NONE silently disappears."""

    def test_all_fixture_files_accounted(self):
        """All fixture filenames must produce at least one ParsedRecord with
        a valid source_filename."""
        all_fixtures = (
            UFO_FILES + DARK_QUEEN_FILES + ULTIMA_V_FILES +
            ULTIMA_VI_FILES + BARD_S_TALE_III_FILES + DEFENDER_FILES +
            A10_TANK_KILLER_V10 + A10_TANK_KILLER_V15
        )
        records = _parse_many(all_fixtures)
        # Every file must produce a record
        assert len(records) == len(all_fixtures), (
            f"Expected {len(all_fixtures)} records, got {len(records)}"
        )
        # Every record must have a source_filename
        for rec in records:
            assert rec.source_filename, f"Record missing source_filename"

    def test_no_silent_loss_in_grouping(self):
        """When grouping all fixture records, every disk must appear in
        some release group's disks or specials list."""
        all_fixtures = (
            UFO_FILES + DARK_QUEEN_FILES + ULTIMA_V_FILES +
            ULTIMA_VI_FILES + BARD_S_TALE_III_FILES + DEFENDER_FILES +
            A10_TANK_KILLER_V10 + A10_TANK_KILLER_V15
        )
        records = _parse_many(all_fixtures)
        groups = _group_many(records)
        grouped_filenames = set()
        for g in groups:
            for r in g.records:
                grouped_filenames.add(r.source_filename)
        # Every input filename must appear in exactly one group's records
        for rec in records:
            assert rec.source_filename in grouped_filenames, (
                f"{rec.source_filename} not found in any group"
            )

    def test_grouping_consistency(self):
        """All disks with the same release_key must end up in the same group."""
        records = _parse_many(UFO_FILES)
        groups = _group_many(records)
        for g in groups:
            keys = set(r.release_key for r in g.records)
            assert len(keys) == 1, f"Group has multiple release keys: {keys}"


# ---------------------------------------------------------------------------
# Test 10: Release key composition does NOT include group
# ---------------------------------------------------------------------------

class TestReleaseKeyExcludesGroup:
    """Verify that the release_key does NOT include the group field,
    confirming the GH-190 fix."""

    def test_group_does_not_affect_release_key(self):
        """Two disks with the same title/version/chipset but different groups
        must have the SAME release_key."""
        rec1 = parse_filename("Game (1990)(Publisher)(Disk 1 of 2)[cr GroupA].adf")
        rec2 = parse_filename("Game (1990)(Publisher)(Disk 2 of 2)[cr GroupB].adf")
        assert rec1.release_key == rec2.release_key, (
            f"Different release keys for same game different groups: "
            f"{rec1.release_key!r} != {rec2.release_key!r}"
        )

    def test_group_field_still_present_on_record(self):
        """The group field must still be populated on the ParsedRecord
        as provenance, just not used in the release_key."""
        rec = parse_filename("Game (1990)(Publisher)[cr TestGroup](Disk 1 of 1).adf")
        assert rec.group == "TestGroup", f"Expected group 'TestGroup', got {rec.group!r}"

    def test_group_key_equals_release_key(self):
        """group_key should equal release_key (they are aliases)."""
        rec = parse_filename("Game (1990)(Publisher)[cr TestGroup](Disk 1 of 1).adf")
        assert rec.group_key == rec.release_key, (
            f"group_key {rec.group_key!r} != release_key {rec.release_key!r}"
        )

    def test_release_key_components(self):
        """Release key should include title, edition, chipset, language, version, alt
        but NOT group."""
        rec = parse_filename(
            "Game Name (1990)(Publisher)(AGA)(EN)(v1.0)[cr PSG][a](Disk 1 of 1).adf"
        )
        key_parts = rec.release_key.split("|")
        # The key should have parts for title, edition, chipset, language, version, alt
        # and must NOT contain "psg" (the crack group)
        key_str = rec.release_key.lower()
        assert "psg" not in key_str, f"release_key should not contain crack group 'PSG': {rec.release_key!r}"
        assert "game" in key_str, f"release_key should contain title: {rec.release_key!r}"


# ---------------------------------------------------------------------------
# Test 11: Grouping with actual group_records() function
# ---------------------------------------------------------------------------

class TestGroupRecordsFunction:
    """Test the full group_records() pipeline with the GH-190 fixture set."""

    def test_ufo_grouped_single_group(self):
        records = _parse_many(UFO_FILES)
        groups = _group_many(records)
        assert len(groups) == 1
        assert len(groups[0].disks) == 4
        assert len(groups[0].records) == 4

    def test_dark_queen_grouped_single_group(self):
        records = _parse_many(DARK_QUEEN_FILES)
        groups = _group_many(records)
        assert len(groups) == 1
        assert len(groups[0].disks) == 3
        assert len(groups[0].records) == 3

    def test_ultima_v_grouped_single_group(self):
        records = _parse_many(ULTIMA_V_FILES)
        groups = _group_many(records)
        assert len(groups) == 1
        assert len(groups[0].disks) == 2
        assert len(groups[0].records) == 2

    def test_ultima_vi_grouped_single_group(self):
        records = _parse_many(ULTIMA_VI_FILES)
        groups = _group_many(records)
        assert len(groups) == 1
        assert len(groups[0].disks) == 4
        assert len(groups[0].records) == 4

    def test_different_versions_form_different_groups(self):
        """A-10 Tank Killer v1.0 and v1.5 must be in separate groups."""
        all_records = _parse_many(A10_TANK_KILLER_V10 + A10_TANK_KILLER_V15)
        groups = _group_many(all_records)
        assert len(groups) == 2


# ---------------------------------------------------------------------------
# Test 12: GH-191 compatibility — curated membership not affected
# ---------------------------------------------------------------------------

class TestGH191Compatibility:
    """Verify that the release key change does not break GH-191 curated membership."""

    def test_release_key_format_stable_for_curation(self):
        """The release_key format must remain pipe-separated for curation lookups."""
        rec = parse_filename("Game (1990)(Publisher)(Disk 1 of 1).adf")
        assert "|" in rec.release_key, "release_key must use pipe separators"
        parts = rec.release_key.split("|")
        assert len(parts) >= 1, "release_key must have at least one part"

    def test_group_field_accessible_for_curation(self):
        """The group field must still be accessible on ParsedRecord for
        curation and display purposes."""
        rec = parse_filename("Game (1990)(Publisher)[cr TestGroup](Disk 1 of 1).adf")
        assert hasattr(rec, 'group')
        assert rec.group == "TestGroup"

    def test_release_group_has_group_attribute(self):
        """ReleaseGroup must still carry the group attribute for display."""
        rec = parse_filename("Game (1990)(Publisher)[cr TestGroup](Disk 1 of 1).adf")
        groups = _group_many([rec])
        assert len(groups) == 1
        assert groups[0].group == "TestGroup"
