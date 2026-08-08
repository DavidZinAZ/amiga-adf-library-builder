"""Unit tests for the filename parser (documented behavior, A15)."""
from amiga_adf_library_builder.parser import parse_filename


def test_bat_ii_full_set_parses_group_and_disks():
    r = parse_filename(
        "E.X.A.M.P.L.E. II - Galactic Bureau (1994)(UBI Soft)"
        "[cr SKR](Disk 3 of 5).adf"
    )
    assert r.title == "E.X.A.M.P.L.E. II Galactic Bureau"
    assert r.year == "1994"
    assert r.publisher == "UBI Soft"
    assert r.group == "SKR"
    assert r.disk_number == 3
    assert r.total_disks == 5
    assert r.special_disk is False


def test_ufo_base_four_disk():
    r = parse_filename("Example - Space Tactics (Disk 2 of 4).adf")
    assert r.title == "Example Space Tactics"
    assert r.disk_number == 2
    assert r.total_disks == 4
    assert r.group is None
    assert r.edition is None


def test_ufo_platinum_edition_distinct():
    r = parse_filename(
        "Example - Space Tactics Platinum Edition (199x)"
        "(MicroProse - The Hit Squad)(AGA)(M3)[cr Bad Karma](Disk 1 of 4).adf"
    )
    assert r.title == "Example Space Tactics"
    assert r.edition == "Platinum Edition"
    assert r.year == "199x"  # indeterminate preserved verbatim
    assert r.chipset == "AGA/M3"
    assert r.group == "Bad Karma"
    assert r.disk_number == 1
    assert r.total_disks == 4


def test_ufo_platinum_release_key_distinct_from_base():
    base = parse_filename("Example - Space Tactics (Disk 1 of 4).adf")
    plat = parse_filename(
        "Example - Space Tactics Platinum Edition (199x)"
        "(MicroProse - The Hit Squad)(AGA)(M3)[cr Bad Karma](Disk 1 of 4).adf"
    )
    assert base.release_key != plat.release_key


def test_letter_disk_mapping():
    a = parse_filename("Example_Castle_Quest_Disk_A.adf")
    b = parse_filename("Example_Castle_Quest_Disk_B.adf")
    assert a.disk_number == 1
    assert b.disk_number == 2
    assert a.title == "Example Castle Quest"
    assert a.special_disk is False


def test_special_disk_role_detected_underscore_name():
    boot = parse_filename("Example_Quest_III_Boot.adf")
    char = parse_filename("Example_Quest_III_Character.adf")
    assert boot.special_disk is True and boot.special_role == "boot"
    assert char.special_disk is True and char.special_role == "character"
    assert boot.disk_number is None


def test_char_token_stripped_from_title():
    char = parse_filename("Example_Qest3_Char.adf")
    assert char.special_role == "character"
    assert char.title == "Example Qest3"


def test_trainer_and_alt_marker():
    t = parse_filename("Some Game (1990)[cr PDX][t][a2].adf")
    assert t.group == "PDX"
    assert t.trainer is True
    assert t.alt_marker == "a2"


def test_multidisk_up_to_twelve_supported():
    # Architecture supports up to 12 disks.
    for n in range(1, 13):
        r = parse_filename(f"Big Set (Disk {n} of 12).adf")
        assert r.disk_number == n
        assert r.total_disks == 12
