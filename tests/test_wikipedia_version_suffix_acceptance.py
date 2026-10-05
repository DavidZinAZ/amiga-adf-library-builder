"""GH-192/#189 RC4: the four titles the packaged Windows GUI failed on.

Production finding (v0.2.40 packaged GUI run): these releases resolved in the
direct/live verification but were reported as misses by the real packaged run:

    Hacker II The Doomsday Papers v1.0
    Dark Queen of Krynn, The v1.0
    Oil Barons
    Ultima VI The False Prophet v1.12

Root cause: NOT query generation. The correct article WAS returned by the API
in every case, but ``candidate_is_same_subject`` rejected it because
``subject_tokens`` tokenized the trailing release-version suffix into its
parts::

    'Hacker II The Doomsday Papers v1.0'
      -> ['hacker','v2','doomsday','papers','v1','0']

The bare '0' is then read as VERSION 0 by ``_version_number``, which is
decisively unequal to the article's version, so acceptance fails. The query
builder had already stripped that suffix from the SEARCH string; acceptance
must use the same release identity or the two disagree by construction.

These tests pin the invariant that query text and acceptance agree, and that
the strictness which protects the library is preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amiga_adf_library_builder.wikipedia_query import (  # noqa: E402
    _VERSION_SUFFIX_RE,
    build_query_variants,
    candidate_is_same_subject,
    subject_tokens,
)

# (release string as it appears in the library, article that IS the answer)
PRODUCTION_CASES = [
    ("Hacker II The Doomsday Papers v1.0", "Hacker II: The Doomsday Papers"),
    ("Dark Queen of Krynn, The v1.0", "The Dark Queen of Krynn"),
    ("Oil Barons", "Oil Barons"),
    ("Ultima VI The False Prophet v1.12", "Ultima VI: The False Prophet"),
]


@pytest.mark.parametrize("requested,article", PRODUCTION_CASES)
def test_production_miss_now_accepts(requested, article):
    """THE regression: each of the four must be accepted as its subject."""
    assert candidate_is_same_subject(requested, article) is True


@pytest.mark.parametrize("requested,article", PRODUCTION_CASES)
def test_version_suffix_is_stripped_from_the_requested_side(requested, article):
    """No bare numeric fragment may survive on the requested side."""
    tokens = subject_tokens(requested, strip_version_suffix=True)
    assert "0" not in tokens, f"{requested!r} left a bare '0' version token: {tokens}"
    # The result must equal the tokens of the same string with the suffix gone.
    assert tokens == subject_tokens(_VERSION_SUFFIX_RE.sub("", requested).strip())


def test_default_tokenizing_is_unchanged():
    """The stripping is opt-in, so every other caller is untouched."""
    assert subject_tokens("Foo v1.0") == ["foo", "v1", "0"]
    assert subject_tokens("Hacker II The Doomsday Papers v1.0") == [
        "hacker", "v2", "doomsday", "papers", "v1", "0"]


@pytest.mark.parametrize("requested,article", PRODUCTION_CASES)
def test_acceptance_identity_matches_the_query_identity(requested, article):
    """The invariant that actually broke: the strings fed to the SEARCH and
    to the ACCEPTANCE test must describe the same release."""
    searched = {v.search for v in build_query_variants(requested)}
    assert _VERSION_SUFFIX_RE.sub("", requested).strip() in searched
    # and the accepted article is acceptable under that same identity
    assert candidate_is_same_subject(
        _VERSION_SUFFIX_RE.sub("", requested).strip(), article) is True


# --- strictness must NOT be weakened ----------------------------------------

@pytest.mark.parametrize("requested,article", [
    ("Hacker", "Hacker II: The Doomsday Papers"),      # version differs
    ("Hacker II", "Hacker (video game)"),              # version differs
    ("Ultima IV", "Ultima V: Quest of the Avatar"),    # version differs
    ("Doom (1993 video game)", "Doom (2016 video game)"),  # year differs
])
def test_genuinely_different_subjects_still_rejected(requested, article):
    """The fix must not turn the strict test into a loose one."""
    assert candidate_is_same_subject(requested, article) is False


def test_version_suffix_stripping_does_not_merge_different_releases():
    """Two releases whose names differ ONLY by version must stay distinct."""
    assert candidate_is_same_subject("Foo v1.0", "Foo v2.0") is False


def test_roman_numeral_alone_is_still_a_version():
    """'II' is a real version marker and must not be stripped as a suffix."""
    assert subject_tokens("Hacker II") == ["hacker", "v2"]
    assert "v2" in subject_tokens("Hacker II")