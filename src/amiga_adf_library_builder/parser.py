"""Filename parser: convert a messy Amiga disk-image filename into a ParsedRecord.

Supports the corpus conventions:
  * TOSEC-style tags: (year)(publisher)(chipset)(language)(version)[cr group][t][a][a2]
  * ``(Disk N of M)`` numeric multidisk convention
  * ``Disk_X`` letter multidisk convention (A->1, B->2, ...)
  * special disks: Boot / Character / Save / Intro (no main disk determinable)
  * edition tokens such as "Platinum Edition" kept distinct from the base title

Design rule: never guess. Unknown fields stay ``None``.
The indeterminate year ``(199x)`` is preserved verbatim, not coerced.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import LETTER_ORDINAL, ParsedRecord

# Canonical special-disk role mapping. Underscore/space are treated as
# separators so "Example_Quest_III_Boot" is detected correctly.
_SPECIAL_ROLE_TOKENS = {
    "boot": "boot",
    "character": "character",
    "char": "character",
    "save": "save",
    "intro": "intro",
    "utility": "utility",
    "util": "utility",
    "companion": "companion",
}

# Chipset / machine tokens that may appear in parentheses.
_CHIPSET_RE = re.compile(r"^(AGA|CD32|CDTV|ECS|OCS|NTSC|PAL|M\d+)$", re.IGNORECASE)
# Two-letter language codes.
_LANG_RE = re.compile(r"^[A-Za-z]{2}$")
# Version-ish tokens: 1.2, 1.1e, v1.2, etc.
_VERSION_RE = re.compile(r"^(v\d[\d.]*[a-z]?|\d+\.\d+[a-z]?)$", re.IGNORECASE)
# Year: 1994, 199x, 19XX.
_YEAR_RE = re.compile(r"^\d{3}[xX]$|^\d{4}$")
# Numeric multidisk convention.
_DISK_NUM_RE = re.compile(r"\(Disk\s+(\d+)\s+of\s+(\d+)\)", re.IGNORECASE)
# Letter multidisk convention.
_DISK_LETTER_RE = re.compile(r"Disk[_ ]?([A-Za-z])\b", re.IGNORECASE)
# Edition qualifier: a single word immediately before "Edition"
# (e.g. "Platinum Edition", "Gold Edition"). Keeps the surrounding title intact.
_EDITION_RE = re.compile(r"([A-Za-z0-9']+\s+Edition)\s*$", re.IGNORECASE)
# Token splitter: underscores, hyphens, spaces.
_TOKEN_RE = re.compile(r"[_\s\-]+")

# Separators we collapse to a single space for readable titles.
_SEP_RUN_RE = re.compile(r"[_\-]+")


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _detect_special_role(tokens: list[str]) -> tuple[str, str] | None:
    """Return (actual_token, canonical_role) for the first special token found."""
    for tok in tokens:
        role = _SPECIAL_ROLE_TOKENS.get(tok.lower())
        if role:
            return tok, role
    return None


def _strip_token(tokens: list[str], role_token: str) -> list[str]:
    return [t for t in tokens if t.lower() != role_token.lower()]


def _parse_paren_tags(rec: ParsedRecord, tags: list[str]) -> None:
    for raw in tags:
        t = raw.strip()
        if not t:
            continue
        if _YEAR_RE.match(t):
            rec.year = t
        elif _CHIPSET_RE.match(t):
            token = t.upper()
            rec.chipset = (rec.chipset + "/" + token) if rec.chipset else token
        elif _LANG_RE.match(t):
            rec.language = t.upper()
        elif _VERSION_RE.match(t):
            rec.version = t
        else:
            # Publisher-like tag (first one wins; later ones appended).
            if rec.publisher is None:
                rec.publisher = t
            else:
                rec.publisher = rec.publisher + " / " + t


def _parse_bracket_tags(rec: ParsedRecord, tags: list[str]) -> None:
    for raw in tags:
        t = raw.strip()
        if not t:
            continue
        low = t.lower()
        if low.startswith("cr "):
            rec.group = t[3:].strip()
        elif low.startswith("p ") or low == "p":
            if rec.group is None and len(t) > 2:
                rec.group = t[2:].strip()
        elif low == "t" or (low.startswith("t ") and len(t) <= 12):
            rec.trainer = True
        elif re.fullmatch(r"a\d*", low):
            # alt marker: 'a' or 'a2'
            rec.alt_marker = low


def parse_filename(filename: str) -> ParsedRecord:
    """Parse a single intake filename into a structured ParsedRecord."""
    path = Path(filename)
    stem = path.stem
    ext = path.suffix.lower().lstrip(".")
    rec = ParsedRecord(source_filename=filename, ext=ext or "")

    # Pull all bracket/paren segments (preserve order), then strip them.
    paren_tags = re.findall(r"\(([^()]*)\)", stem)
    bracket_tags = re.findall(r"\[([^\[\]]*)\]", stem)
    # Drop the multidisk convention paren tag; it is not a metadata field.
    paren_tags = [t for t in paren_tags if not _DISK_NUM_RE.search(f"({t})")]
    bare = re.sub(r"\([^()]*\)", " ", stem)
    bare = re.sub(r"\[[^\[\]]*\]", " ", bare)
    bare = bare.strip(" _-")
    letter_match = _DISK_LETTER_RE.search(stem) if "(Disk" not in stem else None
    if letter_match:
        letter = letter_match.group(1).upper()
        rec.disk_number = LETTER_ORDINAL.get(letter)
        bare = _DISK_LETTER_RE.sub("", bare).strip(" _-")

    _parse_paren_tags(rec, paren_tags)
    _parse_bracket_tags(rec, bracket_tags)

    # Numeric multidisk convention.
    dm = _DISK_NUM_RE.search(stem)
    if dm:
        rec.disk_number = int(dm.group(1))
        rec.total_disks = int(dm.group(2))

    # Tokenize for special-disk role detection (underscore/hyphen are separators).
    tokens = [t for t in _TOKEN_RE.split(bare) if t]
    role_hit = _detect_special_role(tokens)
    if role_hit and rec.disk_number is None:
        # A special disk only carries meaning when it is NOT a numbered main disk.
        actual_token, canonical_role = role_hit
        rec.special_disk = True
        rec.special_role = canonical_role
        tokens = _strip_token(tokens, actual_token)

    # Edition extraction from remaining tokens (rejoined for the regex).
    title_raw = " ".join(tokens).strip()
    m = _EDITION_RE.search(title_raw)
    if m:
        rec.edition = m.group(1).strip()
        title_raw = title_raw[: m.start()].strip()

    # Normalise display separators in the title.
    rec.title = _SEP_RUN_RE.sub(" ", title_raw).strip() or None

    rec.release_key = _build_release_key(rec)
    rec.group_key = rec.release_key
    return rec


def _build_release_key(rec: ParsedRecord) -> str:
    """Clustering identity: distinguishes editions, groups, languages, versions."""
    parts = [
        _norm(rec.title),
        _norm(rec.edition),
        _norm(rec.group),
        _norm(rec.chipset),
        _norm(rec.language),
        _norm(rec.version),
        _norm(rec.alt_marker),
    ]
    return "|".join(parts)
