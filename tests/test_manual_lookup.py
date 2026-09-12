"""Deterministic tests for the Slice 4 unified manual lookup service.

Covers: multi-source candidates, winner/precedence explanation, unresolved
conflict reporting, manual override create/edit/revert, provider refresh
preserving manual choice, and read-only source-media semantics (no media
mutation anywhere in this module).
"""
from __future__ import annotations

import pytest

from amiga_adf_library_builder.canonical import (
    CanonicalLibrary,
    Provenance,
    SourceAuthority,
)
from amiga_adf_library_builder.manual_lookup import (
    apply_manual_override,
    build_entity_report,
    candidates_from_sources,
    disks_for,
    list_library_entities,
    provider_refresh_preserves_override,
    releases_for,
    revert_manual_override,
)
from amiga_adf_library_builder.metadata_source import (
    MetadataSourceManager,
    parse_dat,
)


@pytest.fixture()
def canon(tmp_path):
    with CanonicalLibrary(tmp_path / "canonical.db") as c:
        yield c


def _parser_claim(canon, etype, eid, fname, value, source="parser"):
    canon.claim_field(
        etype, eid, fname, value,
        Provenance(source=source, authority=SourceAuthority.PARSER,
                   observed_at="2026-09-12T00:00:00+00:00"),
    )


def _dat_claim(canon, etype, eid, fname, value, source="tosec", rank=0,
               url="http://example/dat-entry"):
    canon.claim_field(
        etype, eid, fname, value,
        Provenance(source=source, record_key="Entry-Key", url=url,
                   authority=SourceAuthority.DAT, authority_rank=rank,
                   confidence=0.8,
                   observed_at="2026-09-12T01:00:00+00:00"),
    )


# --- candidates from multiple sources --------------------------------------

def test_multi_source_candidates_ordered_by_precedence(canon):
    _parser_claim(canon, "release", "r1", "title", "Parser Title")
    _dat_claim(canon, "release", "r1", "title", "DAT Title")
    rep = build_entity_report(canon, "release", "r1")
    field = next(f for f in rep.fields if f.field_name == "title")
    values = [c.value for c in field.claims]
    assert values == ["DAT Title", "Parser Title"]
    assert field.claims[0].is_winning
    assert not field.claims[1].is_winning
    assert field.claims[0].authority == "dat"
    assert field.claims[0].url == "http://example/dat-entry"
    assert field.claims[0].record_key == "Entry-Key"


def test_winner_explanation_from_slice3_resolution(canon):
    _dat_claim(canon, "release", "r1", "title", "DAT Title")
    rep = build_entity_report(canon, "release", "r1")
    field = rep.fields[0]
    assert field.canonical_value == "DAT Title"
    # Explanation must reflect the real Slice 3 winner, not a local rule.
    value, prov = canon.resolve_field("release", "r1", "title")
    assert field.canonical_value == value
    assert field.winner_explanation == prov.authority.tier


def test_conflict_flagged_when_values_differ(canon):
    _dat_claim(canon, "release", "r1", "title", "DAT Title")
    _parser_claim(canon, "release", "r1", "title", "Parser Title")
    rep = build_entity_report(canon, "release", "r1")
    assert rep.fields[0].conflict
    assert rep.conflicts[0].field_name == "title"
    assert rep.fields[0].distinct_values() == ["DAT Title", "Parser Title"]


def test_no_conflict_when_identical_values(canon):
    _dat_claim(canon, "release", "r1", "title", "Same")
    _parser_claim(canon, "release", "r1", "title", "Same")
    rep = build_entity_report(canon, "release", "r1")
    assert not rep.fields[0].conflict


def test_missing_fields_reported_honestly(canon):
    rep = build_entity_report(canon, "release", "r-empty")
    assert rep.missing_fields  # no claims at all -> all fields "missing"
    assert rep.fields[0].canonical_value is None
    assert rep.fields[0].claims == []


# --- manual override create / edit / revert ---------------------------------

def test_manual_override_wins_and_explains(canon):
    _dat_claim(canon, "release", "r1", "title", "DAT Title")
    apply_manual_override(canon, "release", "r1", "title", "Operator Choice")
    rep = build_entity_report(canon, "release", "r1")
    field = rep.fields[0]
    assert field.canonical_value == "Operator Choice"
    assert field.claims[0].is_manual
    assert field.claims[0].authority == "curation"
    assert field.winner_explanation == "curation"
    assert not field.conflict is False or field.conflict  # claims still preserved


def test_manual_override_edit_then_edit_wins(canon):
    apply_manual_override(canon, "game", "g1", "title", "First")
    apply_manual_override(canon, "game", "g1", "title", "Second")
    value, prov = canon.resolve_field("game", "g1", "title")
    assert value == "Second"
    assert prov.authority == SourceAuthority.CURATION


def test_revert_removes_only_manual_claims(canon):
    _dat_claim(canon, "release", "r1", "title", "DAT Title")
    apply_manual_override(canon, "release", "r1", "title", "Operator Choice")
    assert revert_manual_override(canon, "release", "r1", "title") == 1
    value, prov = canon.resolve_field("release", "r1", "title")
    # Falls back to documented automated precedence (DAT beats parser).
    assert value == "DAT Title"
    assert prov.authority == SourceAuthority.DAT
    # Revert again is a no-op.
    assert revert_manual_override(canon, "release", "r1", "title") == 0


# --- provider refresh preserving manual choice -------------------------------

def test_provider_refresh_never_overrides_manual(canon):
    apply_manual_override(canon, "release", "r1", "title", "Operator Choice")
    assert provider_refresh_preserves_override(
        canon, "release", "r1", "title", "tosec", "DAT Title (Refreshed)"
    )
    value, prov = canon.resolve_field("release", "r1", "title")
    assert value == "Operator Choice"
    assert prov.authority == SourceAuthority.CURATION


def test_provider_refresh_after_manual_override_survives_reapply(canon):
    _dat_claim(canon, "release", "r1", "title", "DAT Title")
    apply_manual_override(canon, "release", "r1", "title", "Operator Choice")
    # A second provider pass re-observes the same automated value.
    _dat_claim(canon, "release", "r1", "title", "DAT Title")
    value, prov = canon.resolve_field("release", "r1", "title")
    assert value == "Operator Choice"
    assert prov.authority == SourceAuthority.CURATION


# --- canonical entity browsing ------------------------------------------------

def test_list_browse_releases_and_disks(canon):
    _parser_claim(canon, "game", "g-slug", "title", "Turrican")
    canon._conn.execute(
        "INSERT OR IGNORE INTO game (game_id) VALUES (?)", ("g-slug",)
    )
    canon._conn.execute(
        """
        INSERT OR IGNORE INTO release (release_id, game_id) VALUES (?, ?)
        """,
        ("r-1", "g-slug"),
    )
    canon._conn.execute(
        """
        INSERT OR IGNORE INTO disk (disk_id, release_id, filename)
        VALUES (?, ?, ?)
        """,
        ("d-1", "r-1", "Turrican_Disk1.adf"),
    )
    canon._conn.commit()

    games = list_library_entities(canon)["games"]
    assert games[0]["label"] == "Turrican"
    rels = releases_for(canon, "g-slug")
    assert rels[0]["entity_id"] == "r-1"
    disks = disks_for(canon, "r-1")
    assert disks[0]["label"] == "Turrican_Disk1.adf"


# --- Slice 1 source index candidates (read-only) ------------------------------

def test_candidates_from_sources_by_hash_and_title(tmp_path):
    dat = tmp_path / "sample.dat"
    dat.write_text(
        '<datafile>\n'
        '  <game name="Turrican (1990)(Rainbow Arts)(De)(Disk 1 of 2)">\n'
        '    <rom name="Turrican_Disk1.adf" size="901120" crc="1234ABCD" '
        'md5="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" sha1="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"/>\n'
        '  </game>\n'
        '</datafile>\n',
        encoding="utf-8",
    )
    entries = parse_dat(dat)
    assert entries, "fixture DAT must parse"
    with MetadataSourceManager(tmp_path / "sources.db") as mgr:
        mgr.add_source(dat)
        by_sha1 = candidates_from_sources(mgr, sha1="B" * 40)
        by_sha1_lower = candidates_from_sources(mgr, sha1="b" * 40)
        assert by_sha1 and by_sha1 == by_sha1_lower
        cand = by_sha1[0]
        assert cand["title"].startswith("Turrican")
        assert cand["source_name"]
        assert cand["record_key"] == cand["title"]
        by_title = candidates_from_sources(mgr, title="turrican")
        assert len(by_title) == 1
        assert candidates_from_sources(mgr, sha1="0" * 32) == []
        assert candidates_from_sources(mgr) == []


def test_source_query_is_read_only(tmp_path):
    dat = tmp_path / "sample.dat"
    dat.write_text(
        '<datafile><game name="X"><rom name="x.adf" size="901120" '
        'crc="1234ABCD" md5="' + "a" * 32 + '" sha1="' + "b" * 32 + '"/></game></datafile>',
        encoding="utf-8",
    )
    original = dat.read_bytes()
    with MetadataSourceManager(tmp_path / "sources.db") as mgr:
        mgr.add_source(dat)
        candidates_from_sources(mgr, title="X")
        candidates_from_sources(mgr, sha1="b" * 40)
    assert dat.read_bytes() == original  # raw DAT untouched


def test_no_source_media_mutation(tmp_path, canon):
    media = tmp_path / "media" / "game.adf"
    media.parent.mkdir()
    media.write_bytes(b"ADF-BYTES")
    before = media.read_bytes()
    apply_manual_override(canon, "game", "g1", "title", "Renamed")
    assert media.read_bytes() == before
    revert_manual_override(canon, "game", "g1", "title")
    assert media.read_bytes() == before
