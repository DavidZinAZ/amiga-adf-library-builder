"""Tests for grouper: clustering, completeness, edition separation, quarantine."""
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.parser import parse_filename


def _groups(filenames):
    return group_records([parse_filename(f) for f in filenames])


def test_five_disk_set_clusters_and_orders():
    names = [
        f"E.X.A.M.P.L.E. II - Galactic Bureau (1994)(UBI Soft)"
        f"[cr SKR](Disk {n} of 5).adf"
        for n in range(1, 6)
    ]
    groups = _groups(names)
    assert len(groups) == 1
    g = groups[0]
    assert [d.disk_number for d in g.disks] == [1, 2, 3, 4, 5]
    assert g.is_complete is True
    assert g.group == "SKR"


def test_ufo_base_and_platinum_separate_groups():
    names = [
        "Example - Space Tactics (Disk 1 of 4).adf",
        "Example - Space Tactics (Disk 4 of 4).adf",
        "Example - Space Tactics Platinum Edition (199x)(MicroProse - The Hit Squad)"
        "(AGA)(M3)[cr Bad Karma](Disk 1 of 4).adf",
        "Example - Space Tactics Platinum Edition (199x)(MicroProse - The Hit Squad)"
        "(AGA)(M3)[cr Bad Karma](Disk 4 of 4).adf",
    ]
    groups = _groups(names)
    assert len(groups) == 2
    by_key = {g.release_key: g for g in groups}
    base = next(g for g in groups if g.group is None)
    plat = next(g for g in groups if g.group == "Bad Karma")
    assert base.edition is None
    assert plat.edition == "Platinum Edition"
    assert plat.chipset == "AGA/M3"


def test_letter_disks_ordered():
    groups = _groups(["Example_Castle_Quest_Disk_A.adf", "Example_Castle_Quest_Disk_B.adf"])
    assert len(groups) == 1
    assert [d.disk_number for d in groups[0].disks] == [1, 2]


def test_special_only_set_quarantined_not_complete():
    groups = _groups(["Example_Quest_III_Boot.adf", "Example_Quest_III_Character.adf"])
    assert len(groups) == 1
    g = groups[0]
    assert g.has_main_disk is False
    assert g.is_complete is False
    assert g.quarantine_reason is not None
    assert "Incomplete set" in g.quarantine_reason


def test_incomplete_numeric_set_flagged():
    # Only disks 1 and 3 of 4 -> not complete.
    groups = _groups(
        ["Example - Space Tactics (Disk 1 of 4).adf", "Example - Space Tactics (Disk 3 of 4).adf"]
    )
    g = groups[0]
    assert g.is_complete is False


def test_near_duplicate_spelling_cross_flagged_for_review():
    # Acceptance A8: 'Example_Quest_III_Character' vs 'Example_Qest3_Char' collapse to
    # the same normalized base yet differ in spelling -> both flagged, not merged.
    groups = _groups(["Example_Quest_III_Character.adf", "Example_Qest3_Char.adf"])
    assert len(groups) == 2  # correctly kept as separate release keys
    for g in groups:
        assert g.quarantine_reason is not None
        assert "Near-duplicate spelling" in g.quarantine_reason
        assert g.has_main_disk is False  # never guessed into a game folder


def test_legitimate_edition_not_false_flagged():
    # Example base release vs Platinum edition keep DIFFERENT normalized bases -> no near-dup flag.
    groups = _groups(
        [
            "Example - Space Tactics (Disk 1 of 4).adf",
            "Example - Space Tactics Platinum Edition (199x)(MicroProse - The Hit Squad)"
            "(AGA)(M3)[cr Bad Karma](Disk 1 of 4).adf",
        ]
    )
    assert len(groups) == 2
    for g in groups:
        assert g.quarantine_reason is None
