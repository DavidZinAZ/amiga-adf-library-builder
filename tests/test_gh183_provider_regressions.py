"""Guard provider-agnostic metadata identity boundaries without network access.

The original file also asserted Hall of Light parser details. That provider has
been removed, so those assertions are gone; the sequel-rejection and
numeral-equivalence guards below are provider-INDEPENDENT
(``validate_metadata_relevance`` is shared by every online lookup) and are kept
because they still protect the accepted/rejected contract.
"""
import pytest

from amiga_adf_library_builder.metadata import (
    MetadataRecord, validate_metadata_relevance,
)


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
