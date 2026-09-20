"""Guard GH-183 provider parsing and identity boundaries without network access."""
import pytest

from amiga_adf_library_builder.metadata import (
    MetadataRecord, _HallOfLightDetailParser, validate_metadata_relevance,
)


@pytest.mark.parametrize("href", [
    "/games/view/1234/hacker", "/doc/hacker/763", "/cheat/hacker/480",
])
def test_hol_links_do_not_require_lemon_document_parser_state(href):
    parser = _HallOfLightDetailParser()
    parser.feed(
        '<h1 class="game-title">Hacker</h1>'
        '<dl><dt class="game-platform">Platform</dt><dd>Amiga</dd></dl>'
        f'<div class="docs"><a href="{href}">Link</a></div>'
    )
    assert parser.canonical_title == "Hacker"
    assert parser.platforms == ["Amiga"]
    assert parser.game_id == ("1234" if "/games/view/" in href else "")


@pytest.mark.parametrize("base,sequel", [
    ("Hacker", "Hacker II"),
    ("Sonic the Hedgehog", "Sonic the Hedgehog 2"),
    ("Lemmings", "Lemmings 2"),
    ("Star Voyage", "Star Voyage II"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_sequel_identity_rejection_does_not_depend_on_padded_similarity(base, sequel, reverse):
    requested, returned = (sequel, base) if reverse else (base, sequel)
    result = validate_metadata_relevance(
        requested, MetadataRecord(canonical_title=returned, platforms=["Amiga"]),
    )
    assert result.category == "rejected"
    assert result.reason == "different_game"


@pytest.mark.parametrize("requested,returned", [
    ("Hacker II: The Doomsday Papers", "Hacker 2: The Doomsday Papers"),
    ("Ultima IV: Quest of the Avatar", "Ultima 4: Quest of the Avatar"),
])
def test_equivalent_numeral_notations_remain_accepted(requested, returned):
    result = validate_metadata_relevance(
        requested, MetadataRecord(canonical_title=returned, platforms=["Amiga"]),
    )
    assert result.category == "accepted"
    assert result.reason == "exact_match"
