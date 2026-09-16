"""Canonical title normalization for identity/matching correctness (GH-164 C1).

Provides a single shared :func:`canonical_title` function that normalizes
game titles consistently across metadata, local-media, and enrichment
code paths.  This fixes the root causes where:

* franchise phrasing in the disambiguation-phrase list false-fired on
  legitimate game leads (e.g. ``"Ultima IV: Quest of the Avatar"`` described
  as ``"in the series of"``);
* unstripped parenthetical disambiguators (e.g. ``"Neuromancer (video game)"``)
  pushed near-exact candidates into review then discarded as not-found;
* missing article-movement normalization (``"The Untouchables"`` ↔
  ``"Untouchables, The"``) left valid local-media matches at weak fuzzy
  scores.

Precedence preserved: exact-hash > manual decisions > canonical normalization
> fuzzy.  Thresholds are never lowered.
"""
from __future__ import annotations

import re
import unicodedata

# Trailing media ordinal suffix: ``-01``, ``-12``, etc.  Reuses the same
# semantics as ``_LAUNCHBOX_ORDINAL_RE`` in local_media.py.
_LAUNCHBOX_ORDINAL_RE = re.compile(r"-[0-9]{1,3}$")

# Parenthetical disambiguators that should be stripped from online
# candidate titles before comparison.  Only a fixed, game-specific set:
# platform/type qualifiers, not generic franchise phrasing.
_PARENTHEMATICAL_DISAMBIGUATORS = (
    "video game",
    "computer game",
    "game",
    "amiga",
)

# Year-in-parenthesis pattern: ``(1986)``, ``(1986 video game)`` etc.
_YEAR_RE = re.compile(r"\(\d{4}\s*(?:video\s*game)?\)$")

# Articles that can move between the front and the end of a title.
_ARTICLES = frozenset({"the", "a", "an"})


def canonical_title(title: str) -> str:
    """Return a canonical, normalized form of ``title`` suitable for
    identity comparison across all matching code paths.

    Steps applied in order:
      1. Strip trailing media ordinal ``-NN`` (e.g. ``"Bubble Bobble-01"`` →
         ``"Bubble Bobble"``).
      2. Strip parenthetical disambiguators when the remainder is
         non-empty (e.g. ``"Neuromancer (video game)"`` → ``"Neuromancer"``).
      3. Move leading articles to the end: ``"The Untouchables"`` →
         ``"Untouchables, The"``.
      4. Normalize unicode (NFKD), then collapse punctuation/separators
         to spaces, then strip to an alnum-only key.

    The result is stable, deterministic, and identical for equivalent
    titles regardless of formatting differences.
    """
    if not title:
        return ""

    working = title.strip()

    # 1. Strip trailing media ordinal.
    working = _LAUNCHBOX_ORDINAL_RE.sub("", working)

    # 2. Strip parenthetical disambiguators when the remainder is non-empty.
    #    Handles ``"Neuromancer (video game)"``, ``"Foo (amiga)"`` etc.
    stripped = _strip_parenthetical_disambiguators(working)
    if stripped != working and stripped.strip():
        working = stripped.strip()

    # 3. Article movement: ``"The Untouchables"`` → ``"Untouchables, The"``.
    working = _move_articles(working)

    # 4. Unicode normalization + punctuation collapse + alnum key.
    return _to_alnum_key(working)


def _strip_parenthetical_disambiguators(title: str) -> str:
    """Strip trailing parenthetical disambiguators from ``title``.

    Only strips when the parenthetical matches the fixed set of
    game-type qualifiers *and* removing it leaves non-empty text.
    """
    # Try to strip a parenthetical at the end.
    m = re.search(r"\(([^)]*)\)\s*$", title)
    if not m:
        return title

    inner = m.group(1).strip().lower()
    if not inner:
        return title

    # Check for year-only or year-with-qualifier patterns.
    if re.match(r"^\d{4}(\s*video\s*game)?$", inner):
        return title[: m.start()].strip()

    # Check against the fixed disambiguator set.
    if inner in _PARENTHEMATICAL_DISAMBIGUATORS:
        return title[: m.start()].strip()

    # Also handle compound: ``"(1986 video game)"`` or ``"(video game)"``
    parts = inner.split()
    if all(p in _PARENTHEMATICAL_DISAMBIGUATORS or p.isdigit() for p in parts):
        return title[: m.start()].strip()

    return title


def _move_articles(title: str) -> str:
    """Move leading articles to the end for consistent comparison.

    ``"The Untouchables"`` → ``"Untouchables, The"``
    ``"A Team"`` → ``"Team, A"``
    ``"An Example"`` → ``"Example, An"``
    """
    words = title.split()
    if len(words) >= 2 and words[0].lower() in _ARTICLES:
        return ", ".join([", ".join(words[1:]), words[0]]).strip()
    return title


def _to_alnum_key(text: str) -> str:
    """Normalize unicode, collapse separators, and produce an alnum-only key."""
    # NFKD decomposition separates characters from diacritics.
    decomposed = unicodedata.normalize("NFKD", text)
    # Lowercase, replace non-alphanumeric chars with spaces, collapse.
    lowered = decomposed.lower()
    # Replace separators with spaces, then collapse and strip.
    key = re.sub(r"[^a-z0-9]", " ", lowered)
    key = re.sub(r"\s+", " ", key).strip()
    # Remove spaces for the final alnum-only key.
    return key.replace(" ", "")


# Re-export the ordinal regex for use by consumers that need to match
# the same semantics as local_media.py.
LAUNCHBOX_ORDINAL_RE = _LAUNCHBOX_ORDINAL_RE
