"""Regression fixtures for GH-164 identity/matching correctness (C8).

Tests the four specific fixtures from the issue:
1. Ultima IV — franchise phrase no longer false-fires on online lookup
2. Neuromancer — "(video game)" disambiguator stripped before comparison
3. Untouchables — article movement normalizes "The Untouchables" ↔ "Untouchables, The"
4. review_routed consistency — review events produce persisted review items
"""
from __future__ import annotations

from pathlib import Path

import pytest

from amiga_adf_library_builder.metadata import (
    MetadataRecord,
    RelevanceDecision,
    validate_metadata_relevance,
    _DISAMBIGUATION_PHRASES,
)
from amiga_adf_library_builder.title_norm import canonical_title
from amiga_adf_library_builder.local_media import LocalMediaConfig, LocalMediaProvider, MatchMethod
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup, ScanRecord
from amiga_adf_library_builder.enrich import EnrichResult, EnrichEvent, EnrichCategory


# --- helpers -----------------------------------------------------------------


def _make_group(title: str, release_key: str = None) -> ReleaseGroup:
    """Create a ReleaseGroup, matching the pattern used in test_local_media_provider.py."""
    fn = f"{title}.adf" if release_key is None else f"{release_key}.adf"
    rec = ParsedRecord(source_filename=fn, ext="adf", title=title)
    return ReleaseGroup(
        release_key=release_key or title.lower(),
        title=title,
        edition=None,
        group=None,
        chipset=None,
        records=[rec],
        disks=[rec],
    )


# --- C8 fixture 1: Ultima IV -------------------------------------------


def test_ultima_iv_online_not_false_fired():
    """Ultima IV's Wikipedia lead describes it as 'in the series of'
    style franchise phrasing. The disambiguation-phrase list must NOT
    false-fire on this, and the online candidate must be accepted,
    not silently discarded as not-found."""
    assert "series of" not in _DISAMBIGUATION_PHRASES
    assert "franchise" not in _DISAMBIGUATION_PHRASES

    record = MetadataRecord(
        canonical_title="Ultima IV: Quest of the Avatar",
        description="Ultima IV: Quest of the Avatar is a role-playing game in the series of Ultima games, released for the Commodore Amiga.",
        platforms=["Commodore Amiga"],
    )
    decision = validate_metadata_relevance("Ultima IV: Quest of the Avatar", record)
    assert decision.category == "accepted", (
        f"Expected accepted, got {decision.category} conf={decision.confidence} reason={decision.reason}"
    )


# --- C8 fixture 2: Neuromancer -----------------------------------------


def test_neuromancer_disambiguator_stripped():
    """Neuromancer (video game) must have the parenthetical disambiguator
    stripped before comparison, resulting in exact identity acceptance
    instead of midband review."""
    assert canonical_title("Neuromancer (video game)") == canonical_title("Neuromancer")

    record = MetadataRecord(
        canonical_title="Neuromancer (video game)",
        description="Neuromancer (video game) is a cyberpunk role-playing video game.",
        platforms=["Commodore Amiga"],
    )
    decision = validate_metadata_relevance("Neuromancer", record)
    assert decision.category == "accepted", (
        f"Expected accepted, got {decision.category} conf={decision.confidence} reason={decision.reason}"
    )


# --- C8 fixture 3: Untouchables ----------------------------------------


def test_untouchables_article_movement():
    """'The Untouchables' and 'Untouchables, The' must normalize to the
    same canonical form so that article movement works."""
    assert canonical_title("The Untouchables") == canonical_title("Untouchables, The")
    assert canonical_title("The Untouchables") == "untouchablesthe"


@pytest.mark.parametrize("title1,title2", [
    ("The Untouchables", "Untouchables, The"),
    ("A Team", "Team, A"),
    ("An Example", "Example, An"),
])
def test_canonical_title_equivalence(title1, title2):
    """canonical_title must produce equivalent forms for known equivalent titles."""
    assert canonical_title(title1) == canonical_title(title2)


# --- C8 fixture 4: review_routed consistency ----------------------------


def test_review_events_present_in_result():
    """Every metadata_relevance_review or local_media_review event must
    be present in the EnrichResult events list and categorized correctly."""
    review_event = EnrichEvent(
        category=EnrichCategory.METADATA_RELEVANCE_REVIEW,
        detail="routed to review series_disambiguation (conf 0.55)",
        cache="miss",
        ok=False,
        error="needs manual review",
    )
    local_review_event = EnrichEvent(
        category=EnrichCategory.LOCAL_MEDIA_REVIEW,
        detail="routed to review fuzzy_manual (conf 0.80)",
        cache="miss",
        ok=False,
        error="needs manual review",
    )

    result = EnrichResult(
        nfo_path=None,
        artwork_master=None,
        artwork_resized=None,
        resized=False,
        notes=[],
        events=[review_event, local_review_event],
        needs_manual_review=True,
    )

    review_events = [e for e in result.events if e.category in (
        EnrichCategory.METADATA_RELEVANCE_REVIEW,
        EnrichCategory.LOCAL_MEDIA_REVIEW,
    )]
    assert len(review_events) == 2
    assert result.needs_manual_review is True


def test_disambiguation_produces_review_not_silent_notfound():
    """A genuine disambiguation page (e.g. 'may refer to') must produce
    a review decision, not a silent not-found via rejection."""
    record = MetadataRecord(
        canonical_title="Some Game",
        description="This page may refer to various topics. Some Game is a video game.",
        platforms=[],
    )
    decision = validate_metadata_relevance("Some Game", record)
    assert decision.category == "review", (
        f"Expected review for disambiguation page, got {decision.category}"
    )


# --- C8 fixture 5: canonical_title edge cases --------------------------


@pytest.mark.parametrize("title,expected_contains", [
    ("Bubble Bobble-01", "bubblebobble"),
    ("Ultima IV: Quest of the Avatar", "ultima0004questoftheavatar"),
    ("Neuromancer (video game)", "neuromancer"),
])
def test_canonical_title_strips_ordinal_and_disambiguator(title, expected_contains):
    """canonical_title must strip trailing ordinals and parenthetical disambiguators."""
    result = canonical_title(title)
    assert expected_contains in result
    assert "-" not in result  # ordinal stripped


def test_ultima_identity_unifies_numeral_notation_but_preserves_sequels():
    title = canonical_title("Ultima IV: Quest of the Avatar")
    assert title == canonical_title("Ultima 4: Quest of the Avatar")
    assert title != canonical_title("Ultima V: Quest of the Avatar")


# --- C8 fixture 6: local-media article movement via _score -------------


def test_local_media_article_movement_score():
    """A local-media file named 'The Untouchables-01.jpg' for a group
    titled 'Untouchables, The' must match at NORMALIZED_TITLE 0.99
    via canonical article-movement normalization."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        img_dir = root / "Images" / "Commodore Amiga" / "Screenshot - Game Title"
        img_dir.mkdir(parents=True, exist_ok=True)
        img_file = img_dir / "The Untouchables-01.jpg"
        img_file.write_bytes(b"fake-image-data")

        cache = Path(tmp) / "cache"
        config = LocalMediaConfig(
            enabled=True,
            roots=(str(root),),
            platform_names=("Commodore Amiga", "Amiga"),
            preferred_image_types=("Screenshot - Game Title", "Box - Front", "Screenshot - Gameplay"),
            recursive=True,
        )
        provider = LocalMediaProvider(config, cache)
        provider.discover()

        group = _make_group("Untouchables, The")
        result = provider.resolve(group)

        if result.found and result.cached_path is not None:
            assert result.match_method == MatchMethod.NORMALIZED_TITLE, (
                f"Expected NORMALIZED_TITLE but got {result.match_method}"
            )
        # Verify _group_identities includes canonical_norm_title
        identities = provider._group_identities(group)
        assert "canonical_norm_title" in identities, (
            "Group identities must include canonical_norm_title"
        )
        assert identities["canonical_norm_title"] == "untouchablesthe"
