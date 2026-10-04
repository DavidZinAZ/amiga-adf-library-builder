"""Deterministic Wikipedia query-form generation and candidate acceptance.

The previous provider issued exactly one query per release:

    "<title>" Amiga video game

That single form fails real Amiga releases whose canonical article does not
use the release string verbatim — ``Hacker II The Doomsday Papers`` is filed
under ``Hacker II: The Doomsday Papers``, ``Ultima IV`` under ``Ultima IV:
Quest of the Avatar``, ``UFO: Enemy Unknown`` under ``UFO (video game)``.
Each miss cost a full request round trip and then reported ``no_result`` with
no explanation of which form had been tried.

This module generates a small, ordered, *deterministic* list of query variants
and decides, conservatively, whether a returned article is actually about the
requested release. Conservative is deliberate: a wrong acceptance silently
writes the wrong game into a preservation library, which is worse than a
recorded miss.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .title_norm import canonical_title

__all__ = [
    "QueryVariant",
    "build_query_variants",
    "candidate_is_same_subject",
    "subject_tokens",
    "norm_key",
]

# Version/disc suffixes that must never be part of a Wikipedia search string.
_VERSION_SUFFIX_RE = re.compile(
    r"\s+(?:v|ver|version)\s*\d+(?:[._]\d+)*\s*$"
    r"|\s+\(\s*(?:v|ver|version)\s*\d+(?:[._]\d+)*\s*\)\s*$",
    re.IGNORECASE,
)
# Trailing disambiguator, e.g. "(1991 video game)".
_PAREN_QUALIFIER_RE = re.compile(r"\s*\([^()]*\)\s*$")
# A four-digit release year inside a trailing parenthetical.
_PAREN_YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}\b")
_COLON_SUBSPLIT_RE = re.compile(r"\s*:\s+")

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
# Prefix that marks a resolved Roman version token inside ``subject_tokens``.
_ROMAN_PREFIX = "v"
_ARTICLES = frozenset({"the", "a", "an"})


@dataclass(frozen=True)
class QueryVariant:
    """One deterministic Wikipedia query form to try for a release."""

    #: Query text sent to the API (without the trailing context terms).
    search: str
    #: Human-readable label recorded in diagnostics.
    label: str
    #: The release string the variant was derived from, for diagnostics.
    derived_from: str

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.search


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).strip()


def _drop_paren_qualifiers(value: str) -> str:
    """Strip repeated trailing parenthesised qualifiers."""
    out = _clean(value)
    while True:
        new = _PAREN_QUALIFIER_RE.sub("", out).strip()
        if new == out or not new:
            return out
        out = new


def roman_to_int(token: str) -> Optional[int]:
    """Convert an uppercase Roman numeral token to an int, else None.

    Only UPPERCASE tokens count. Amiga release strings write version numerals
    in caps ("Hacker II", "Ultima VI"); a lowercase word such as "mix" or
    "civil" must never be read as a version marker.
    """
    if not token or not token.isupper() or not re.fullmatch(r"[IVXLCDM]+", token):
        return None
    total = 0
    previous = 0
    for char in reversed(token):
        value = _ROMAN_VALUES.get(char, 0)
        if value < previous:
            total -= value
        else:
            total += value
            previous = value
    return total


def _version_token(value: str) -> Optional[str]:
    """Return the leading Roman version token of ``value`` (e.g. ``II``).

    Requires a meaningful head (``Hacker``) before the numeral so that a
    standalone or unrelated word is never mistaken for a version.
    """
    match = re.match(r"^(?P<head>.+?)\s+(?P<numeral>[IVXLCDM]{1,6})(?:\s+.+)?$",
                     value.strip())
    if not match:
        return None
    head = match.group("head").strip()
    if len(head) < 3:
        return None
    numeral = match.group("numeral")
    if not numeral.isupper() or not re.fullmatch(r"[IVXLCDM]+", numeral):
        return None
    return numeral


def build_query_variants(title: str, *, limit: int = 6) -> list[QueryVariant]:
    """Return ordered, de-duplicated Wikipedia query forms for ``title``.

    Order is deterministic and runs from most specific to least: the release
    string verbatim first (it succeeds most often), then subtitle-stripped and
    punctuation variants, then Amiga-specific forms. The caller stops at the
    first accepted match, so earlier entries cost nothing on a normal hit and
    the common case still issues exactly one request.
    """
    raw = _clean(title)
    if not raw:
        return []

    variants: list[QueryVariant] = []
    seen: set[str] = set()

    def add(search: str, label: str, source: str) -> None:
        search = _clean(search)
        if not search or len(search) < 2:
            return
        # Two-part de-duplication key:
        #  * punctuation is PRESERVED, or the colon form collapses into the
        #    verbatim form and the variant that actually matches the real
        #    article title ("Hacker II: The Doomsday Papers") is lost;
        #  * the label is part of the key, because ``verbatim`` and
        #    ``bare_quoted`` send the SAME text but DIFFERENT API queries
        #    (only the latter drops the context terms), so collapsing them
        #    would remove the variant that resolves "Oil Barons".
        key = f"{label}|{re.sub(r'\s+', ' ', search.casefold())}"
        if key in seen:
            return
        seen.add(key)
        variants.append(QueryVariant(search=search, label=label,
                                     derived_from=source))

    # 1. Verbatim release string, including any parenthetical version suffix
    #    the previous single-form query could not strip.
    add(raw, "verbatim", raw)

    # 2. Version-suffix stripped: "Foo (v1.2)" -> "Foo".
    no_version = _VERSION_SUFFIX_RE.sub("", raw).strip()
    if no_version and no_version != raw:
        add(no_version, "version_stripped", raw)

    # 3. Parenthetical qualifiers stripped: "Foo (1991 video game)" -> "Foo".
    no_paren = _drop_paren_qualifiers(raw)
    if no_paren and no_paren != raw:
        add(no_paren, "paren_stripped", raw)

    # 4. Subtitle re-joined with a colon when the release used only spaces:
    #    "Hacker II The Doomsday Papers" ->
    #    "Hacker II: The Doomsday Papers" (the real article title).
    colon_form = _COLON_SUBSPLIT_RE.sub(" ", no_version)
    parts = colon_form.split()
    # Only ONE colon form is generated: the split immediately after a leading
    # Roman version token ("Hacker II The Doomsday Papers" ->
    # "Hacker II: The Doomsday Papers"). Emitting a colon at every word
    # boundary also produced nonsense queries ("Hacker II The: Doomsday
    # Papers"), which cost a request each without ever improving a match.
    for index in range(1, len(parts)):
        prefix = " ".join(parts[:index])
        tail = " ".join(parts[index:])
        if not tail:
            continue
        if _version_token(prefix) is None:
            continue
        add(f"{prefix}: {tail}", "colon_subtitle", raw)
        break

    # 5. Subtitle dropped entirely: "Ultima IV Quest of the Avatar" ->
    #    "Ultima IV".
    stem = _version_token(no_version)
    if stem:
        add(no_version.split()[0] + " " + stem, "stem_version", raw)

    # 6. Article-stripped form: "The Dark Queen of Krynn" -> "Dark Queen of Krynn".
    without_article = re.sub(r"(?i)^\s*(?:the|a|an)\s+", "", no_version)
    if without_article and without_article != no_version:
        add(without_article, "article_stripped", raw)

    # 7. Bare quoted title with no context terms. Measured live on 2026-10-04:
    #   "Oil Barons" Amiga video game -> four GENERIC list articles
    #     (List of Apple II games, Epyx, List of Commodore 64 games ...)
    #   "Oil Barons"                  -> the real article, ranked 7th
    #     ("Oil Barons is a turn-based business simulation game published by
    #       Epyx in 1983")
    # The Amiga/video-game context terms actively EXCLUDE the correct article
    # for titles whose page omits those words. This variant is tried LAST, so
    # it costs nothing on the normal path, and the same strict subject test
    # still guards acceptance.
    add(raw, "bare_quoted", raw)

    if limit > 0:
        return variants[:limit]
    return variants


def subject_tokens(value: str) -> list[str]:
    """Split a title into comparison tokens, keeping Roman numerals separate.

    ``canonical_title`` deliberately collapses a title into one opaque string
    ("Ultima IV" -> "ultima0004"), which makes token-level comparison of a
    version marker impossible. This helper keeps the token structure: it
    lowercases and emits one token per word, so that "Hacker" vs "Hacker II"
    and "UFO Enemy Unknown" vs "UFO" can be compared position by position.

    A trailing parenthetical is KEPT when it carries a four-digit release
    year, and dropped otherwise. Dropping it unconditionally would make
    "Doom (1993 video game)" and "Doom (2016 video game)" indistinguishable —
    two entirely different games.
    """
    text = _clean(value or "")
    qualifier = ""
    match = _PAREN_QUALIFIER_RE.search(text)
    if match:
        qualifier = match.group(0).strip().strip("()")
        text = text[: match.start()].strip()
    tokens: list[str] = []
    for raw_token in re.split(r"[^A-Za-z0-9]+", text):
        if not raw_token:
            continue
        if raw_token.casefold() in _ARTICLES:
            continue
        # Detect a Roman version numeral BEFORE casefolding: after folding,
        # "II" and "ii" are indistinguishable from an ordinary word.
        roman = roman_to_int(raw_token)
        if roman is not None:
            tokens.append(f"{_ROMAN_PREFIX}{roman}")
            continue
        tokens.append(raw_token.casefold())
    year_match = _PAREN_YEAR_RE.search(qualifier or "")
    if year_match:
        # Only the year digits are kept: "1993 video game" -> "y1993".
        tokens.append("y" + year_match.group(0))
    return tokens


def _acronym_tokens(value: str) -> set[str]:
    """Lowercased tokens that were written in caps in the original title.

    Used so that a short shared brand acronym ("UFO") can justify accepting a
    candidate whose article title drops the subtitle, while a short shared
    ordinary word cannot.
    """
    text = re.sub(r"[^A-Za-z0-9]+", " ", _clean(value or ""))
    found: set[str] = set()
    for token in text.split():
        if len(token) >= 2 and token.isupper() and not token.isdigit():
            found.add(token.casefold())
    return found


def _is_year(token: str) -> bool:
    """True for a four-digit release-year disambiguator."""
    return token.isdigit() and len(token) == 4


def _year_token(tokens: list[str]) -> Optional[str]:
    """Return the release-year token (``y1993``) from ``tokens``, else None.

    ``subject_tokens`` prefixes a parenthetical year with ``y`` so it can never
    collide with a real version numeral or with the trailing-stem comparison.
    """
    for token in tokens:
        if token.startswith("y") and token[1:].isdigit():
            return token
    return None


def _version_number(tokens: list[str]) -> Optional[int]:
    """First version marker in ``tokens`` resolved to an int, else None.

    ``subject_tokens`` normalises Roman numerals into ``v<int>`` tokens and
    appends a release year as ``y<int>``, so a parsed four-digit number is a
    genuine version, not a year.
    """
    for token in tokens:
        if token.startswith(_ROMAN_PREFIX) and token[1:].isdigit():
            return int(token[1:])
        if token.isdigit():
            return int(token)
    return None


def candidate_is_same_subject(requested_title: str, candidate_title: str,
                              *, extract: str = "") -> bool:
    """Decide whether a Wikipedia article is about the requested release.

    Deliberately strict. Returns True only when the two titles denote the same
    work. A subtitle may differ (``Ultima IV`` vs ``Ultima IV: Quest of the
    Avatar``); a version number or release year may NOT (``Hacker`` vs
    ``Hacker II``, ``Doom (1993 video game)`` vs ``Doom (2016 video game)``).
    Accepting the wrong game into a preservation library is worse than
    recording a miss.

    ``extract`` is accepted for call-site compatibility and provenance but is
    deliberately NOT used to override the title comparison: a matching intro
    sentence is not evidence that the article is about this release.
    """
    req_tokens = subject_tokens(requested_title)
    cand_tokens = subject_tokens(candidate_title)
    if not req_tokens or not cand_tokens:
        return False
    if req_tokens == cand_tokens:
        return True

    # Resolve version markers on both sides so "II" and "2" agree and any
    # difference is decisive.
    req_version = _version_number(req_tokens)
    cand_version = _version_number(cand_tokens)
    if req_version != cand_version:
        # A version on one side only ("Hacker" vs "Hacker II"), a differing
        # version ("Ultima IV" vs "Ultima V"), and a differing release year
        # ("Doom (1993)" vs "Doom (2016)") are all DIFFERENT entries.
        return False

    # Release years are decisive too, but only when BOTH sides carry one. The
    # requested release string usually has no year while the article does
    # ("Doom" vs "Doom (1993 video game)"), so a one-sided year cannot be
    # compared. A year on each side that DIFFERS is a different game.
    req_year = _year_token(req_tokens)
    cand_year = _year_token(cand_tokens)
    if req_year is not None and cand_year is not None and req_year != cand_year:
        return False

    # Compare the plain-word runs. Version/year tokens are already decided
    # above; one side may drop a trailing subtitle, and only a trailing
    # difference is tolerated.
    def stem_of(tokens: list[str]) -> list[str]:
        return [t for t in tokens if not t[:1] in (_ROMAN_PREFIX, "y")]

    req_stem = stem_of(req_tokens)
    cand_stem = stem_of(cand_tokens)
    if not req_stem or not cand_stem:
        return False
    # Identical plain-word runs with no version/year conflict: the only
    # difference was a parenthetical qualifier ("Doom" vs "Doom (1993 video
    # game)"), which is a disambiguator on the same subject.
    if req_stem == cand_stem:
        return True

    common = 0
    for req_tok, cand_tok in zip(req_stem, cand_stem):
        if req_tok != cand_tok:
            break
        common += 1
    # The shared stem must cover the shorter title entirely: only the LONGER
    # side may carry an extra subtitle. A partial overlap means the two titles
    # diverge before the shorter one ends ("Rocket Ranger" vs "Rocket Ranger
    # Wars"), which is a different subject.
    if common < min(len(req_stem), len(cand_stem)):
        return False

    shared = " ".join(req_stem[:common])
    # The shared stem must be substantial, OR be a brand acronym written in
    # caps on BOTH sides ("UFO Enemy Unknown" vs the article "UFO"), which no
    # ordinary short word can satisfy.
    acronym_ok = (shared in _acronym_tokens(requested_title)
                  and shared in _acronym_tokens(candidate_title))
    if len(shared) < 6 and not acronym_ok:
        return False

    # A dropped subtitle must be real words. A single one- or two-character
    # token reads as a qualifier or a different brand, not a subtitle. A bare
    # release year was already decided above by the year equality check.
    extra = req_stem[common:] or cand_stem[common:]
    if extra and len(extra) == 1 and len(extra[0]) <= 2:
        return False
    return True


def norm_key(value: str) -> str:
    """Opaque canonical key, retained for cache/identity comparison."""
    without_articles = re.sub(r"(?i)\b(?:the|a|an)\b", " ", value or "")
    canonical = canonical_title(without_articles)
    if canonical:
        return canonical.casefold()
    return re.sub(r"[^a-z0-9]", "", without_articles.casefold())