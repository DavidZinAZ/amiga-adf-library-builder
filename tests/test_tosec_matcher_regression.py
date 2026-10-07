"""Regression tests for TOSEC DAT title matching (GH: DAT matcher defect).

The matcher previously ran a naive substring scan over the RAW TOSEC
``<description>`` (which embeds ``(year)(publisher)(Disk N of M)`` and
``[tag]`` text). That produced both false negatives (hyphen/spacing/article
differences in the true title) and false positives (a bare substring inside an
unrelated game title or a TOSEC ``[h ...]`` tag field).

This suite locks in the fixed, structural matching contract:
  - the real game-title portion of the TOSEC entry is compared, with
    insignificant punctuation/spacing/roman-numeral differences normalized;
  - publisher/year/region/disk/demo metadata and ``[...]`` tags never create
    a title match;
  - version differences (v1.0 / v1.5 / v1.12) are preserved;
  - a bare substring is never a title match.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amiga_adf_library_builder.metadata_source import (
    MetadataSourceManager,
    clean_tosec_title,
    title_match_keys,
)
from amiga_adf_library_builder.manual_lookup import candidates_from_sources

# (source release title, expected matching TOSEC <description>)
SHOULD_MATCH = [
    ("A 10 Tank Killer v1.0", "A-10 Tank Killer v1.0 (1991)(Dynamix)"),
    ("A 10 Tank Killer v1.5", "A-10 Tank Killer v1.5 (1992)(Dynamix)"),
    ("Bard's Tale III, The Thief of Fate",
     "Bard's Tale III, The - Thief of Fate (1990)(EA)(Disk 1 of 4)"),
    ("Hacker II The Doomsday Papers v1.0",
     "Hacker II - The Doomsday Papers v1.0 (1987)(Activision)"),
    ("UFO Enemy Unknown", "UFO - Enemy Unknown (1994)(MicroProse)(Disk 1 of 8)"),
    ("Ultima III Exodus", "Ultima III - Exodus (1985)(Origin)(Disk 1 of 4)"),
    ("Ultima IV Quest of the Avatar",
     "Ultima IV - Quest of the Avatar (1986)(Origin)"),
    ("Ultima V Warriors of Destiny",
     "Ultima V - Warriors of Destiny (1988)(Origin)"),
    ("Ultima VI The False Prophet v1.12",
     "Ultima VI - The False Prophet v1.12 (1992)(Origin)"),
]

# (source release title, unrelated DAT entry that must NOT match)
MUST_NOT_MATCH = [
    ("Defender", "Centurion - Defender of Rome (demo-rolling)"),
    ("Hacker", "Castle Warrior (1991)(Ariolasoft)[h Happy Hacker]"),
]

DAT_GAMES = [
    "A-10 Tank Killer v1.0 (1991)(Dynamix)",
    "A-10 Tank Killer v1.5 (1992)(Dynamix)",
    "Bard's Tale III, The - Thief of Fate (1990)(EA)(Disk 1 of 4)",
    "Hacker II - The Doomsday Papers v1.0 (1987)(Activision)",
    "UFO - Enemy Unknown (1994)(MicroProse)(Disk 1 of 8)",
    "Ultima III - Exodus (1985)(Origin)(Disk 1 of 4)",
    "Ultima IV - Quest of the Avatar (1986)(Origin)",
    "Ultima V - Warriors of Destiny (1988)(Origin)",
    "Ultima VI - The False Prophet v1.12 (1992)(Origin)",
    "Centurion - Defender of Rome (demo-rolling)",
    "Castle Warrior (1991)(Ariolasoft)[h Happy Hacker]",
    "Alien Breed (1991)(Team 17)(Disk 1 of 2)",
    "Alien Breed (1991)(Team 17)(Disk 2 of 2)",
]


def _build_dat(tmp_path: Path) -> Path:
    dat = tmp_path / "tosec_games.dat"
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<datafile>"]
    for game in DAT_GAMES:
        rom = f"{game}.adf"
        lines.append(f'  <game name="{game}">')
        lines.append(f"    <description>{game}</description>")
        lines.append(
            f'    <rom name="{rom}" size="901120" crc="a1b2c3d4" '
            f'md5="e99a18c428cb38d5f260853678922e03" '
            f'sha1="b1d5781111d84f7b3fe45a0852e59758cd7a87e5"/>'
        )
        lines.append("  </game>")
    lines.append("</datafile>")
    dat.write_text("\n".join(lines), encoding="utf-8")
    return dat


@pytest.fixture()
def dat_manager(tmp_path):
    dat = _build_dat(tmp_path)
    db = tmp_path / "sources.db"
    mgr = MetadataSourceManager(db)
    sid = mgr.add_source(dat)
    assert sid is not None
    yield mgr
    mgr.close()


# --- unit tests on the normalization helpers --------------------------------

def test_clean_tosec_title_strips_metadata_and_tags():
    assert clean_tosec_title(
        "Castle Warrior (1991)(Ariolasoft)[h Happy Hacker]"
    ) == "Castle Warrior"
    assert clean_tosec_title(
        "A-10 Tank Killer v1.0 (1991)(Dynamix)"
    ) == "A-10 Tank Killer v1.0"
    assert clean_tosec_title(
        "UFO - Enemy Unknown (1994)(MicroProse)(Disk 1 of 8)"
    ) == "UFO - Enemy Unknown"


def test_title_match_keys_hyphen_and_spacing():
    assert title_match_keys("A 10 Tank Killer v1.0") & title_match_keys(
        "A-10 Tank Killer v1.0 (1991)(Dynamix)"
    )


def test_title_match_keys_roman_numeral_equivalence():
    assert title_match_keys("Hacker II The Doomsday Papers v1.0") & title_match_keys(
        "Hacker II - The Doomsday Papers v1.0 (1987)(Activision)"
    )


def test_title_match_keys_preserves_version():
    assert not (title_match_keys("A 10 Tank Killer v1.0") & title_match_keys(
        "A 10 Tank Killer v1.5"
    ))


# --- integration: MetadataSourceManager.lookup_by_title ---------------------

@pytest.mark.parametrize("source_title,expected_desc", SHOULD_MATCH,
                         ids=[s for s, _ in SHOULD_MATCH])
def test_lookup_by_title_should_match(dat_manager, source_title, expected_desc):
    results = dat_manager.lookup_by_title(source_title, limit=20)
    got = {r.title for r in results}
    assert expected_desc in got, (
        f"source {source_title!r} failed to match DAT {expected_desc!r}; "
        f"got {sorted(got)}"
    )


@pytest.mark.parametrize("source_title,bad_candidate", MUST_NOT_MATCH,
                         ids=[s for s, _ in MUST_NOT_MATCH])
def test_lookup_by_title_must_not_match(dat_manager, source_title, bad_candidate):
    results = dat_manager.lookup_by_title(source_title, limit=20)
    got = {r.title for r in results}
    assert bad_candidate not in got, (
        f"source {source_title!r} spuriously matched {bad_candidate!r}"
    )


def test_exact_title_still_matches(dat_manager):
    # A plain exact title with no punctuation quirks must still match.
    results = dat_manager.lookup_by_title("Alien Breed", limit=20)
    titles = {r.title for r in results}
    assert "Alien Breed (1991)(Team 17)(Disk 1 of 2)" in titles


def test_multidisk_grouping_matches_all_disks(dat_manager):
    results = dat_manager.lookup_by_title("Alien Breed", limit=20)
    assert len(results) == 2
    assert {r.disk_number for r in results} == {1, 2}


def test_version_lookup_does_not_cross_match(dat_manager):
    v10 = {r.title for r in dat_manager.lookup_by_title(
        "A 10 Tank Killer v1.0", limit=20)}
    assert "A-10 Tank Killer v1.0 (1991)(Dynamix)" in v10
    assert all("v1.5" not in t for t in v10)

    v15 = {r.title for r in dat_manager.lookup_by_title(
        "A 10 Tank Killer v1.5", limit=20)}
    assert "A-10 Tank Killer v1.5 (1992)(Dynamix)" in v15
    assert all("v1.0" not in t for t in v15)


def test_tag_field_never_creates_a_match(dat_manager):
    # "Happy Hacker" only ever appears inside the TOSEC [h ...] tag field.
    results = dat_manager.lookup_by_title("Happy Hacker", limit=20)
    assert results == []


def test_publisher_field_never_creates_a_match(dat_manager):
    # "Ariolasoft" is a publisher in parentheses, never a game title.
    results = dat_manager.lookup_by_title("Ariolasoft", limit=20)
    assert results == []


# --- integration: manual_lookup.candidates_from_sources ---------------------

def test_candidates_from_sources_title_match(dat_manager):
    cands = candidates_from_sources(dat_manager, title="UFO Enemy Unknown")
    titles = {c["title"] for c in cands}
    assert "UFO - Enemy Unknown (1994)(MicroProse)(Disk 1 of 8)" in titles


def test_candidates_from_sources_defender_does_not_pollute(dat_manager):
    cands = candidates_from_sources(dat_manager, title="Defender")
    assert cands == []
