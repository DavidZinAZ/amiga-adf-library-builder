"""RTFM v2 sidecar builder — deterministic, offline, NO-AI (M1).

This module produces the first-class GTi-compatible ``.rtfm`` manual sidecar
used by the Amiga ADF Library Builder. Behavior is strictly deterministic and
offline (M1 scope):

  * Local source discovery from operator-configured roots (Manuals,
    Instructions, Cheats). Sources are read-only; nothing under a root is ever
    opened for writing.
  * Reuse of the existing canonical-title / release-group matcher
    (``grouper.group_records`` + ``naming.release_basename``). Multi-disk sets
    get ONE shared ``.rtfm`` keyed by release group.
  * Multi-source composition into one ``.rtfm`` when several categories exist
    for one game; verbatim, no fabrication.
  * Existing ``.rtfm`` / plain-text passthrough: normalize encoding (UTF-8) and
    line endings (LF), emit verbatim — deterministic.
  * Built-in templates incl. default ``controls-first``; empty sections OMITTED.
  * Deterministic rendering: plain text, LF endings, NO hard-wrap, enforce a
    16000-byte hard cap, and NEVER silently truncate — if natural content would
    exceed the (configurable, conservative, default below 16000) target, the
    title is ROUTED FOR REVIEW (no ``.rtfm`` emitted > 16000 bytes).
  * Provenance sidecar ``<basename>.rtfm.provenance.json`` written under
    ``assets/rtfm`` (outside the Gotek export). Provenance stores NO private
    absolute host paths.

GTi v2 contract (authoritative):

  * plain-text, ASCII-targeted ``.rtfm`` output;
  * one ``.rtfm`` per canonical game, SHARED by multi-disk sets;
  * NO hard-wrapping of body paragraphs (blank-line separators; do NOT reflow);
  * OFFICIAL 16000-byte hard limit (``MAX_RTFM_BYTES``);
  * canonical v2 section markers (``[CONTROLS]`` / ``[GETTING STARTED]`` /
    ``[HOW TO PLAY]`` / ``[NOTES]`` / ``[HINTS & CHEATS]``);
  * ``controls-first`` is the DEFAULT builder template;
  * plain-text markers backwards-readable on older GTi firmware and
    GTi 5.6.2+ section-nav compatible;
  * builder-side discovery/extraction/summarization/provenance; GTi stays a
    lean reader.

M1 DETERMINISTIC SYNTHESIS RULE (no AI in M1):

  * Existing ``.rtfm`` input -> normalized passthrough (highest fidelity).
  * Raw ``.txt`` from a discovery root -> relocate verbatim source lines into
    the section dictated by the root category, with NO fabrication /
    summarization:

      - Instructions root -> [CONTROLS]  (controls-first when present)
      - Manuals root      -> [GETTING STARTED]
      - Cheats root       -> [HINTS & CHEATS]

    Source text is relocated VERBATIM into section bodies; controls, keys, or
    cheats are never invented. Multi-source composition concatenates per-section
    without duplication (deterministic filename order within a root, roots in
    config order).

Pluggable AI summarization (1-3 page condensation) is explicitly DEFERRED (not
M1). The secondary targets named in the upstream spec (Instructions -> also
[GETTING STARTED]; Manuals -> also [NOTES]) are reserved for that deferred
summarization pass; M1 places each category's verbatim text under its PRIMARY
section only and never invents a split.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Reuse the local-media provenance privacy helpers (root-relative path + hash).
# They are pure helpers with no network/state dependency.
from .local_media import _relative_to_root, _sha256_file  # noqa: F401  (re-exported for tests)

# --- Constants ---------------------------------------------------------------

#: Official GTi v2 hard limit. NEVER emit a .rtfm larger than this; on overflow
#: route for review instead of silently truncating (M1 rule).
MAX_RTFM_BYTES = 16000

#: Default conservative target used for the review-on-overflow decision. Always
#: strictly below ``MAX_RTFM_BYTES`` so the hard cap is never approached.
#: Configurable via ``[rtfm] max_bytes`` (clamped to <= MAX_RTFM_BYTES).
DEFAULT_RTFM_REVIEW_TARGET = 15360

#: Configurable upper bound on a single source file's bytes (DoS safety). A
#: source larger than this is skipped, never read fully into memory before the
#: cap (we stat first). Verbatim passthrough of a genuinely huge *source* is not
#: the goal; the 16000-byte output cap is the contract that matters.
MAX_SOURCE_BYTES = 8 * 1024 * 1024

#: Canonical v2 section markers (exact text).
MARKER_CONTROLS = "CONTROLS"
MARKER_GETTING_STARTED = "GETTING STARTED"
MARKER_HOW_TO_PLAY = "HOW TO PLAY"
MARKER_NOTES = "NOTES"
MARKER_HINTS_CHEATS = "HINTS & CHEATS"
MARKER_ADDITIONAL_REFERENCE = "ADDITIONAL REFERENCE"

#: The five canonical v2 markers, in canonical (section-nav) order.
CANONICAL_MARKERS: tuple[str, ...] = (
    MARKER_CONTROLS,
    MARKER_GETTING_STARTED,
    MARKER_HOW_TO_PLAY,
    MARKER_NOTES,
    MARKER_HINTS_CHEATS,
)

#: Built-in templates: each is a PREFERRED ORDERING of the canonical markers.
#: ``full-reference`` MAY add a plain ``[ADDITIONAL REFERENCE]`` marker.
#: Templates define preferred order, not mandatory headings; empty sections are
#: OMITTED by the renderer regardless of template.
TEMPLATE_CONTROLS_FIRST = "controls-first"
TEMPLATE_QUICK_START = "quick-start"
TEMPLATE_ADVENTURE_RPG = "adventure-rpg"
TEMPLATE_ARCADE_ACTION = "arcade-action"
TEMPLATE_FULL_REFERENCE = "full-reference"

TEMPLATES: dict[str, tuple[str, ...]] = {
    TEMPLATE_CONTROLS_FIRST: (
        MARKER_CONTROLS,
        MARKER_GETTING_STARTED,
        MARKER_HOW_TO_PLAY,
        MARKER_NOTES,
        MARKER_HINTS_CHEATS,
    ),
    TEMPLATE_QUICK_START: (
        MARKER_GETTING_STARTED,
        MARKER_CONTROLS,
        MARKER_HOW_TO_PLAY,
        MARKER_HINTS_CHEATS,
        MARKER_NOTES,
    ),
    TEMPLATE_ADVENTURE_RPG: (
        MARKER_GETTING_STARTED,
        MARKER_HOW_TO_PLAY,
        MARKER_CONTROLS,
        MARKER_HINTS_CHEATS,
        MARKER_NOTES,
    ),
    TEMPLATE_ARCADE_ACTION: (
        MARKER_CONTROLS,
        MARKER_HOW_TO_PLAY,
        MARKER_GETTING_STARTED,
        MARKER_HINTS_CHEATS,
        MARKER_NOTES,
    ),
    TEMPLATE_FULL_REFERENCE: (
        MARKER_CONTROLS,
        MARKER_GETTING_STARTED,
        MARKER_HOW_TO_PLAY,
        MARKER_NOTES,
        MARKER_HINTS_CHEATS,
        MARKER_ADDITIONAL_REFERENCE,
    ),
}

DEFAULT_TEMPLATE = TEMPLATE_CONTROLS_FIRST

#: Recognized discovery categories. ``primary_marker`` is where M1 verbatim text
#: for that category lands (deterministic; no AI split).
CATEGORY_INSTRUCTIONS = "instructions"
CATEGORY_MANUALS = "manuals"
CATEGORY_CHEATS = "cheats"

CATEGORY_PRIMARY_MARKER: dict[str, str] = {
    CATEGORY_INSTRUCTIONS: MARKER_CONTROLS,
    CATEGORY_MANUALS: MARKER_GETTING_STARTED,
    CATEGORY_CHEATS: MARKER_HINTS_CHEATS,
}

#: Source files we consider as manual text.
RTFM_SUFFIX = ".rtfm"
TXT_SUFFIXES: frozenset[str] = frozenset({".txt", ".text", ".md"})


class RtfmError(Exception):
    """Base error for RTFM builder failures."""


class RtfmDisabled(RtfmError):
    """Raised when the RTFM builder is used while disabled in config."""


# --- Configuration -----------------------------------------------------------


@dataclass(frozen=True)
class RtfmConfig:
    """Typed view of the ``[rtfm]`` TOML table (M1)."""

    enabled: bool = False
    template: str = DEFAULT_TEMPLATE
    # Per-category discovery roots (each is a tuple of root paths).
    manuals_roots: tuple[str, ...] = ()
    instructions_roots: tuple[str, ...] = ()
    cheats_roots: tuple[str, ...] = ()
    # Online providers are DEFERRED in M1; the flag exists only so config is
    # forward-compatible and ignored by the deterministic path.
    online_enabled: bool = False
    # Conservative review-on-overflow target (bytes). Clamped to <= MAX_RTFM_BYTES.
    max_bytes: int = DEFAULT_RTFM_REVIEW_TARGET
    recursive: bool = True

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "RtfmConfig":
        if not data:
            return cls(enabled=False)
        raw = data or {}

        def _as_tuple(v, default):
            if v is None:
                return default
            if isinstance(v, str):
                return (v,)
            try:
                return tuple(str(x) for x in v)
            except TypeError:
                return default

        enabled = bool(raw.get("enabled", False))
        template = str(raw.get("template", DEFAULT_TEMPLATE) or DEFAULT_TEMPLATE)
        if template not in TEMPLATES:
            # Unknown template name -> fall back to default (never guess).
            template = DEFAULT_TEMPLATE

        local = raw.get("local") or {}
        manuals_roots = _as_tuple(local.get("manuals"), ())
        instructions_roots = _as_tuple(local.get("instructions"), ())
        cheats_roots = _as_tuple(local.get("cheats"), ())

        online = raw.get("online") or {}
        online_enabled = bool(online.get("enabled", False))

        try:
            max_bytes = int(raw.get("max_bytes", DEFAULT_RTFM_REVIEW_TARGET))
        except (TypeError, ValueError):
            max_bytes = DEFAULT_RTFM_REVIEW_TARGET
        # Hard guarantee: the review target can never exceed the GTi cap.
        if max_bytes > MAX_RTFM_BYTES:
            max_bytes = MAX_RTFM_BYTES
        if max_bytes <= 0:
            max_bytes = DEFAULT_RTFM_REVIEW_TARGET

        recursive = bool(raw.get("recursive", True))

        return cls(
            enabled=enabled,
            template=template,
            manuals_roots=manuals_roots,
            instructions_roots=instructions_roots,
            cheats_roots=cheats_roots,
            online_enabled=online_enabled,
            max_bytes=max_bytes,
            recursive=recursive,
        )


# --- Data records ------------------------------------------------------------


@dataclass
class RtfmSource:
    """One discovered source file (read-only) and its derived facts."""

    path: Path           # the real (symlink/hardlink-resolved) path
    root: Path           # the configured root the discovery used
    category: str        # one of CATEGORY_* constants
    # Match identity derived for this source (for diagnostics/provenance).
    stem: str = ""


@dataclass
class RtfmProvenanceSource:
    """One provenance entry for a source file (no private host paths)."""

    category: str
    root_index: int
    source_rel: str          # posix path relative to its root (no absolute)
    filename: str
    kind: str                # ".rtfm" passthrough | "txt" verbatim
    sections: list[str]      # markers this source contributed to
    sha256: str
    size: int
    # `.rtfm` passthrough preserves the source marker order when known.
    marker_order: Optional[list[str]] = None
    # Issue #6: deterministic match scoring for this source (auditable decision).
    match_confidence: float = 0.0
    match_kind: str = "none"
    match_evidence: list[str] = field(default_factory=list)


@dataclass
class RtfmResult:
    """Outcome of building one release group's ``.rtfm``."""

    release_key: str
    basename: str
    written: bool = False
    routed_for_review: bool = False
    review_reason: Optional[str] = None
    bytes: int = 0
    rtfm_path: Optional[Path] = None
    provenance_path: Optional[Path] = None
    sections_present: list[str] = field(default_factory=list)
    template_used: str = DEFAULT_TEMPLATE
    sources: list[RtfmProvenanceSource] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- Text normalization -------------------------------------------------------


def _decode_source(path: Path) -> str:
    """Read a source file as text, deterministically, with LF endings.

    Decode chain (strict, no silent guessing of content):
      1. ``utf-8-sig`` — preferred; preserves existing valid UTF-8 (incl. BOM).
      2. ``cp1252``   — documented legacy Amiga / Western-European fallback.
      3. ``latin-1``  — guaranteed single-byte map (every byte is a code point).

    SAFETY: if the decoded text contains a NUL (``"\\x00"``), the source is
    treated as binary / not safely decodable as text and ``RtfmError`` is
    raised so the caller can route it for review rather than emit garbage.

    The caller has already bounded the size. We never fabricate content or
    silently truncate.
    """
    data = path.read_bytes()
    text: str
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError:
            text = data.decode("latin-1")  # single-byte map: cannot fail
    # A NUL in the decoded text means this is binary, not decodable text.
    if "\x00" in text:
        raise RtfmError(
            f"source appears binary/contains NUL (not decodable as text): {path}"
        )
    # Normalize line endings to LF. Do NOT strip/reflow body content.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _normalize_passthrough(text: str) -> str:
    """Normalize an existing ``.rtfm`` for passthrough: LF endings only.

    We do NOT reflow or wrap. Trailing whitespace on blank lines is collapsed to
    a clean blank line; a single trailing newline is guaranteed.
    """
    # Normalize CRLF/CR -> LF.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse runs of trailing whitespace on otherwise-empty lines so the file
    # is tidy, but keep intentional paragraph blank lines.
    lines = [line.rstrip() for line in text.split("\n")]
    # Drop a single trailing empty line if present (we re-add exactly one).
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _split_existing_rtfm(text: str) -> tuple[Optional[list[str]], Optional[dict]]:
    """Parse an existing ``.rtfm`` into (marker_order, {marker: body}).

    Returns ``(None, None)`` when the text contains no canonical v2 marker, so
    the caller can fall back to treating it as a single verbatim blob.

    Every documented v2 section marker is recognized, including
    ``[ADDITIONAL REFERENCE]`` (used by the ``full-reference`` template). A
    marker not in this set is NOT silently merged into the previous section;
    if we ever encounter an unknown ``[WORD]`` marker we leave it verbatim in
    the body rather than losing its content.
    """
    # All markers that may appear in a v2 .rtfm (union of every template's
    # preferred markers). ADDITIONAL REFERENCE is the only one not in
    # CANONICAL_MARKERS.
    marker_set = set().union(*TEMPLATES.values())
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    current: Optional[str] = None
    for line in text.split("\n"):
        stripped = line.strip()
        m = re.fullmatch(r"\[([A-Z &]+)\]", stripped)
        if m and m.group(1) in marker_set:
            current = m.group(1)
            assert current is not None  # group(1) matched a non-empty marker
            if current not in sections:
                order.append(current)
                sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    if not order:
        return None, None
    return order, sections


# --- Discovery (read-only, confinement-safe) ---------------------------------


def _confined_candidates(
    root: Path, *, recursive: bool, root_index: int, category: str,
) -> list[RtfmSource]:
    """Discover source files under ``root`` for ``category``, confinement-safe.

    Mirrors the local-media provider's adversarial guarantees: every candidate's
    REAL path must lie within ``root`` (symlink/hardlink escape rejected), only
    regular files are accepted, and discovery is strictly read-only enumeration.
    """
    candidates: list[RtfmSource] = []
    rpath = Path(root)
    if not rpath.is_dir():
        return candidates
    root_resolved = rpath.resolve()
    iterator = rpath.rglob("*") if recursive else rpath.glob("*")
    for entry in iterator:
        try:
            real = entry.resolve()
        except OSError:
            continue
        # Reject anything whose real path escapes the configured root.
        if not real.is_relative_to(root_resolved):
            continue
        try:
            st = os.lstat(real)
        except OSError:
            continue
        # Reject non-regular files (symlinks/devices/FIFOs/sockets).
        if not stat.S_ISREG(st.st_mode):
            continue
        if real.suffix.lower() != RTFM_SUFFIX and real.suffix.lower() not in TXT_SUFFIXES:
            continue
        candidates.append(
            RtfmSource(
                path=real,
                root=root_resolved,
                category=category,
                stem=real.stem,
            )
        )
    # Deterministic order: path components then filename (stable across runs).
    candidates.sort(key=lambda c: (str(c.path.parent), c.path.name))
    return candidates


def discover_sources(cfg: RtfmConfig) -> list[RtfmSource]:
    """Discover all RTFM source files across the configured category roots.

    Returns a flat list ordered by (category root order, root, filename) so
    composition is deterministic.
    """
    roots: list[tuple[str, str]] = [
        (CATEGORY_MANUALS, r) for r in cfg.manuals_roots
    ] + [
        (CATEGORY_INSTRUCTIONS, r) for r in cfg.instructions_roots
    ] + [
        (CATEGORY_CHEATS, r) for r in cfg.cheats_roots
    ]
    out: list[RtfmSource] = []
    for root_index, (category, root) in enumerate(roots):
        out.extend(
            _confined_candidates(
                Path(root), recursive=cfg.recursive, root_index=root_index,
                category=category,
            )
        )
    return out


# --- Matching (reuse canonical-title / variant logic) ------------------------


def _group_identity(group) -> str:
    """Best human-readable identity for a release group (for matching + naming).

    Prefers ``release_basename`` (the canonical export name reused by NFO/art),
    so one shared ``.rtfm`` is produced for multi-disk / variant sets. Falls back
    to the title when basename resolution is unavailable.
    """
    try:
        from .naming import release_basename

        return release_basename(group)
    except Exception:
        return (getattr(group, "title", None) or "Unknown").strip() or "Unknown"


def _norm_title(title: str) -> str:
    """Lowercased, punctuation/space/underscore stripped form for matching."""
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def _source_matches_group(src: RtfmSource, group) -> bool:
    """Deterministic canonical-title / variant match.

    DEPRECATED: kept for backward compatibility (the existing
    ``test_canonical_title_match`` still calls it). It now delegates to the
    Issue #6 scorer ``score_source_match`` and returns its ``matched`` flag.
    New consumers should use ``score_source_match`` directly to get the
    confidence / kind / evidence needed for the auto-accept vs. review decision.

    Matches when ANY of:
      * the source filename stem equals the group title (case/space-insensitive);
      * the source stem equals the group's release basename (multi-disk/variant);
      * the source stem (normalized) equals the group title (normalized);
      * the source stem is a canonical-reuse base of the group title (release tags
        stripped on both sides), serving cracks/trainers/alt-dumps/language/
        chipset/multi-disk variants of the SAME canonical game.
    """
    return bool(score_source_match(src, group).matched)


def _strip_release_tags(title: str) -> str:
    """Strip crack/trainer/alt/lang/chipset/disk tags to a canonical base.

    Conservative (whole-word only, mirroring local_media._strip_release_tags but
    inlined here to avoid importing that module's full surface). Used only for
    the canonical-reuse match, never to alter emitted content.
    """
    if not title:
        return ""
    t = re.sub(r"\s+", " ", title).strip()
    # Remove disk/side markers ("Disk 2", "Side B").
    t = re.sub(r"\b(?:dis[ck]|disk|disc|side|part)\s*[0-9]+[ab]?\b", " ", t, flags=re.IGNORECASE)
    # Remove parenthetical annotations.
    t = re.sub(r"\([^)]*\)", " ", t)
    # Remove known whole-word release tags.
    tag_words = (
        "cr", "cream", "cracked", "trainer", "trained", "tr",
        "alt", "alternative", "alternate", "a1", "a2", "a3",
        "m3", "aga", "ecs", "ocs", "rtg", "agaecs",
        "qtx", "skr", "pd", "demo",
        "lang", "language", "ntsc", "pal",
        "cd", "disk", "disks", "disc", "side", "part",
        "en", "de", "fr", "es", "it", "pl", "us", "uk",
    )
    toks = re.split(r"(\s+)", t)
    kept = []
    for tok in toks:
        if re.fullmatch(r"\s+", tok or ""):
            kept.append(tok)
            continue
        if tok.strip().lower() in tag_words:
            continue
        kept.append(tok)
    t = "".join(kept)
    t = re.sub(r"\s+[a-z]\s*$", " ", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


# --- Issue #6: deterministic match SCORING (superset of the boolean matcher) --
#
# The legacy ``_source_matches_group`` was a boolean filter. Issue #6 replaces
# the *consumer* decision (auto-accept vs. route-for-review) with a confidence
# score so we can (a) auto-accept only unambiguous high-confidence matches and
# (b) route ambiguous/near-tie/low-confidence matches to human review without
# emitting a possibly-wrong ``.rtfm``. The scorer is fully deterministic (no
# randomness) and read-only against sources.

#: Minimum confidence required to AUTO-ACCEPT a match (emit ``.rtfm`` today).
HIGH_CONFIDENCE = 0.95
#: Two candidates within this confidence of the best are considered a near-tie.
NEAR_TIE_EPSILON = 0.05

# Article words whose position (leading ``The X`` / trailing ``X, The``) is
# canonicalized for comparison. The article is PRESERVED (moved to a canonical
# trailing position), not discarded, so a game and its ``The``-prefixed twin
# stay distinct (per issue #6: "The Quest" vs "Quest" must NOT merge).
_ARTICLE_WORDS = ("the", "a", "an")


@dataclass
class MatchScore:
    """Deterministic match scoring for one (source, group) pair.

    ``matched`` is True when the source is a plausible manual for the group
    (used to filter candidates). ``confidence`` (0.0..1.0), ``kind``, and
    ``evidence`` let the caller decide auto-accept vs. human review and make
    the decision auditable in provenance.
    """

    matched: bool
    confidence: float
    kind: str                 # exact|basename|normalized|canonical_reuse|
                               # roman_arabic|minor_spelling|none
    evidence: list[str]       # human-readable deterministic reasons


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein edit distance (deterministic, stdlib only)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for ca in a:
        cur = [prev[0] + 1]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[lb]


def _roman_to_arabic(tok: str) -> Optional[int]:
    """Return the arabic value of ``tok`` if it is a valid roman numeral.

    Handles standard subtractive forms (IV, IX, XL, XC, CD, CM) and the common
    Amiga-era uppercase forms. Non-roman or non-standard (e.g. ``IIII``) tokens
    return None so we never fabricate a numeral equivalence.
    """
    if not tok:
        return None
    up = tok.upper()
    if not re.fullmatch(r"[IVXLCDM]+", up):
        return None
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    try:
        for ch in reversed(up):
            v = vals[ch]
            total += -v if v < prev else v
            prev = v
    except KeyError:
        return None
    # Reject non-standard forms by confirming a clean re-encode round-trips.
    if _arabic_to_roman(total) != up:
        return None
    return total


def _arabic_to_roman(n: int) -> str:
    """Encode an arabic integer (1..3999) to its standard roman numeral."""
    if n <= 0 or n >= 4000:
        return ""
    table = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    out = []
    for value, sym in table:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def _pad_roman_token(tok: str) -> str:
    """Map a single token to a zero-padded 4-digit arabic string when numeric.

    Roman numerals (``III``) and arabic integers (``3``) both map to ``0003``
    so they compare equal on the canonical base; non-numeric tokens pass
    through unchanged. This is what lets ``Synthetic Quest III`` == ``Synthetic
    Quest 3`` (issue #6 roman<->arabic equivalence).
    """
    a = _roman_to_arabic(tok)
    if a is not None:
        return f"{a:04d}"
    if tok.isdigit():
        try:
            return f"{int(tok):04d}"
        except ValueError:
            return tok
    return tok


def _pad_roman_to_4_tokens(norm: str) -> str:
    """Apply roman<->arabic normalization across all tokens of a normalized title."""
    return " ".join(_pad_roman_token(t) for t in norm.split(" "))


def _normalize_title_tokens(title: str) -> str:
    """Canonical normalized title for matching (strict superset of ``_norm_title``).

    Lowercases; turns subtitle separators (``:`` ``-`` ``—`` ``/``) into spaces;
    collapses underscores and repeated whitespace to one space; moves a
    leading/trailing article to a canonical trailing position (``The X`` ==
    ``X, The``); and strips punctuation/symbols. The article is preserved (not
    discarded), so ``The Quest`` and ``Quest`` remain distinct games.
    """
    if not title:
        return ""
    t = (title or "").lower()
    t = re.sub(r"[:\-—/]", " ", t)          # subtitle separators -> word breaks
    t = re.sub(r"[_\s]+", " ", t)            # underscores + whitespace collapse
    toks = [w for w in t.split(" ") if w]
    # Canonicalize article position: "the x" -> "x the"; "x, the" -> "x the".
    if len(toks) >= 2 and toks[0] in _ARTICLE_WORDS:
        toks = toks[1:] + [toks[0]]
    elif len(toks) >= 2 and toks[-1] in _ARTICLE_WORDS and toks[-1] != toks[0]:
        toks = toks[:-1] + [toks[-1]]
    # Final alphanumeric+space comparison key (punctuation removed).
    return re.sub(r"[^a-z0-9 ]", "", " ".join(toks)).strip()


def _article_moved(stem: str) -> bool:
    """True when ``stem`` carried a leading/trailing article (for evidence)."""
    toks = [w for w in re.sub(r"[_\s]+", " ", (stem or "").lower()).strip().split(" ") if w]
    return len(toks) >= 2 and (toks[0] in _ARTICLE_WORDS or toks[-1] in _ARTICLE_WORDS)


def _single_minor_spelling_fix(norm_a: str, norm_b: str):
    """Detect a single unambiguous minor-spelling difference.

    Returns ``(token_index, a_token, b_token)`` when exactly ONE token differs
    and that token pair is at Levenshtein distance 1 (a single char insert /
    delete / substitution). Returns None otherwise (0 or >=2 differing tokens,
    or a distance > 1). The caller still requires the candidate to be the
    UNIQUE best match before auto-accepting — a fuzzy *tie* is never merged.
    """
    if not norm_a or not norm_b:
        return None
    a = norm_a.split(" ")
    b = norm_b.split(" ")
    if len(a) != len(b):
        return None
    diffs = []
    for i, (ta, tb) in enumerate(zip(a, b)):
        if ta != tb:
            if _levenshtein(ta, tb) == 1:
                diffs.append((i, ta, tb))
            else:
                return None  # a token differs by more than one edit
    if len(diffs) == 1:
        return diffs[0]
    return None


def _canonical_game_key(src: "RtfmSource") -> str:
    """Stable per-game key for ``src`` (release tags + roman numerals unified)."""
    base = _strip_release_tags(src.stem)
    return _pad_roman_to_4_tokens(_normalize_title_tokens(base))


def _match_candidates(stem: str, title: str, basename: str) -> "MatchScore":
    """Score ``stem`` against ONE identity form (``title`` or ``basename``)."""
    if not stem:
        return MatchScore(False, 0.0, "none", [])
    # 1) Exact raw identity (whitespace-insensitive).
    if stem.strip().lower() == title.strip().lower() and title:
        return MatchScore(True, 1.00, "exact", ["exact_title_identity"])
    if stem.strip().lower() == basename.strip().lower() and basename:
        return MatchScore(True, 1.00, "basename", ["basename_identity"])
    # 2) Normalized full-title equality (articles canonicalized, symbols stripped).
    ns_stem = _normalize_title_tokens(stem)
    ns_title = _normalize_title_tokens(title)
    if ns_stem and ns_title and ns_stem == ns_title:
        ev = ["normalized_title_identity"]
        if _article_moved(stem):
            ev.append("article_moved_for_canonical_compare")
        return MatchScore(True, 0.98, "normalized", ev)
    # 3) Canonical-reuse base equality (articles + release tags stripped).
    base_stem = _strip_release_tags(stem)
    base_title = _strip_release_tags(title)
    nb_stem = _normalize_title_tokens(base_stem)
    nb_title = _normalize_title_tokens(base_title)
    rb_stem = _pad_roman_to_4_tokens(nb_stem)
    rb_title = _pad_roman_to_4_tokens(nb_title)
    base_eq = bool(nb_stem and nb_title and nb_stem == nb_title)
    roman_eq = bool(nb_stem and nb_title and rb_stem == rb_title)
    if roman_eq:
        ev = ["canonical_reuse_base"]
        if base_stem != stem or base_title != title:
            ev.append("release_tags_stripped")
        if not base_eq:
            # Equality only holds after roman<->arabic normalization.
            ev.append("roman_arabic_equivalence")
            return MatchScore(True, 0.95, "roman_arabic", ev)
        return MatchScore(True, 0.95, "canonical_reuse", ev)
    # 4) Roman<->arabic equivalence on the NORMALIZED full title (numeral only).
    if ns_stem and ns_title and (
        _pad_roman_to_4_tokens(ns_stem) == _pad_roman_to_4_tokens(ns_title)
    ):
        return MatchScore(True, 0.95, "roman_arabic", ["roman_arabic_equivalence"])
    # 5) Single unambiguous minor-spelling fix (Levenshtein == 1 on one token).
    fix = _single_minor_spelling_fix(ns_stem, ns_title)
    if fix is not None:
        i, ta, tb = fix
        return MatchScore(
            True, 0.92, "minor_spelling",
            [f"minor_spelling_distance_1:token{i}:{ta}->{tb}"],
        )
    return MatchScore(False, 0.0, "none", [])


def score_source_match(src: "RtfmSource", group) -> "MatchScore":
    """Score how well one source manual matches a release group.

    Deterministic. Compares the source stem against BOTH the group title and its
    release basename (multi-disk/variant identity) and returns the stronger of
    the two comparisons. This powers the auto-accept vs. route-for-review
    decision in ``build_rtfm_for_group``.
    """
    title = (getattr(group, "title", None) or "").strip()
    try:
        basename = _group_identity(group)
    except Exception:
        basename = title
    stem = (src.stem or "").strip()
    if not stem:
        return MatchScore(False, 0.0, "none", ["empty_source_stem"])
    by_title = _match_candidates(stem, title, title)
    by_basename = _match_candidates(stem, basename, basename)
    # Stronger match wins; on a tie keep the already-matched side.
    if by_basename.confidence > by_title.confidence:
        return by_basename
    return by_title


# --- Synthesis ---------------------------------------------------------------


def _section_for_category(category: str) -> str:
    return CATEGORY_PRIMARY_MARKER[category]


def _compose_sections(sources: list[RtfmSource], group) -> tuple[dict, list[RtfmProvenanceSource], Optional[list[str]], list[str]]:
    """Compose section bodies from matched sources (verbatim, deterministic).

    Returns (sections: {marker: body_text}, provenance_sources,
    passthrough_order, skipped_notes). Existing ``.rtfm`` sources take
    precedence (normalized passthrough, highest fidelity); raw ``.txt`` sources
    are relocated verbatim into their category's primary section. Composition
    concatenates per-section without duplication, deterministically ordered.

    A source that cannot be decoded (binary / NUL) is SKIPPED: it is not added
    to ``sections`` or ``provenance_sources``, and a corresponding note is
    recorded in ``skipped_notes`` so the caller can route it for review. This
    keeps one bad source from aborting the whole group.
    """
    sections: dict[str, list[str]] = {}
    prov_sources: list[RtfmProvenanceSource] = []
    skipped_notes: list[str] = []
    # When an existing .rtfm passthrough with a valid marker order is the
    # dominant source, we preserve its author's marker order (highest fidelity)
    # instead of reordering by template.
    passthrough_order: Optional[list[str]] = None

    # Existing .rtfm passthrough first (highest fidelity), preserving order.
    for src in [s for s in sources if s.path.suffix.lower() == RTFM_SUFFIX]:
        try:
            text = _decode_source(src.path)
        except RtfmError as exc:
            skipped_notes.append(
                f"source skipped (decode failed, routed for review): {src.path.name}"
            )
            continue
        norm = _normalize_passthrough(text)
        order, parsed = _split_existing_rtfm(norm)
        parsed_sections: dict[str, list[str]] = parsed or {}
        if order is None:
            # No canonical marker: treat the whole file as a single verbatim blob
            # under the source's primary section (no fabrication of markers).
            marker = _section_for_category(src.category)
            sections.setdefault(marker, []).append(norm)
            contributed = [marker]
        else:
            for mk in order:
                body = "\n".join(parsed_sections[mk]).rstrip()
                if body:
                    sections.setdefault(mk, []).append(body)
            contributed = [mk for mk in order if mk in sections and sections[mk]]
            # A passthrough with an explicit marker order wins for ordering.
            if contributed:
                passthrough_order = contributed
        root_index = getattr(src, "root_index", 0)
        sc = score_source_match(src, group)
        prov_sources.append(
            RtfmProvenanceSource(
                category=src.category,
                root_index=root_index,
                source_rel=_relative_to_root(src.path, src.root),
                filename=src.path.name,
                kind=".rtfm passthrough",
                sections=contributed,
                sha256=_sha256_file(src.path),
                size=src.path.stat().st_size,
                marker_order=order,
                match_confidence=sc.confidence,
                match_kind=sc.kind,
                match_evidence=list(sc.evidence),
            )
        )

    # Raw .txt sources: verbatim relocation into the category's primary section.
    for src in [s for s in sources if s.path.suffix.lower() in TXT_SUFFIXES]:
        try:
            text = _decode_source(src.path)
        except RtfmError:
            skipped_notes.append(
                f"source skipped (decode failed, routed for review): {src.path.name}"
            )
            continue
        # Trim a single leading/trailing blank line; keep internal structure.
        lines = text.split("\n")
        while lines and lines[0] == "":
            lines.pop(0)
        while lines and lines[-1] == "":
            lines.pop()
        body = "\n".join(lines).rstrip()
        if not body:
            continue
        marker = _section_for_category(src.category)
        sections.setdefault(marker, []).append(body)
        root_index = getattr(src, "root_index", 0)
        sc = score_source_match(src, group)
        prov_sources.append(
            RtfmProvenanceSource(
                category=src.category,
                root_index=root_index,
                source_rel=_relative_to_root(src.path, src.root),
                filename=src.path.name,
                kind="txt verbatim",
                sections=[marker],
                sha256=_sha256_file(src.path),
                size=src.path.stat().st_size,
                match_confidence=sc.confidence,
                match_kind=sc.kind,
                match_evidence=list(sc.evidence),
            )
        )

    # Join each section's composed blocks with a blank line separator.
    joined: dict[str, str] = {}
    for marker, blocks in sections.items():
        joined[marker] = "\n\n".join(b for b in blocks if b).rstrip() + "\n"
    return joined, prov_sources, passthrough_order, skipped_notes


# --- Rendering ---------------------------------------------------------------


def _order_sections(
    template: str,
    forced_order: Optional[list[str]],
    sections: dict[str, str],
) -> list[str]:
    """Return present section markers in the order they should be emitted.

    Honors ``forced_order`` (existing-``.rtfm`` passthrough: highest-fidelity
    preservation of the author's marker order) when given; otherwise follows
    the template's preferred order with any extra present canonical markers
    appended in canonical order. Empty sections are always omitted.
    """
    if forced_order:
        # Passthrough: preserve the source's marker order, dropping empties.
        final_order = [m for m in forced_order if m in sections and sections[m].strip()]
        # Append any other present canonical markers not in the source order
        # (defensive; should not normally happen for a clean passthrough).
        for m in CANONICAL_MARKERS:
            if m in sections and sections[m].strip() and m not in final_order:
                final_order.append(m)
        return final_order

    # Determine order: template preferred order for present sections, then any
    # extra present sections in canonical order.
    order = list(TEMPLATES.get(template, TEMPLATES[DEFAULT_TEMPLATE]))
    present = [m for m in order if m in sections and sections[m].strip()]
    extra = [
        m for m in CANONICAL_MARKERS
        if m in sections and sections[m].strip() and m not in present
    ]
    # full-reference may have contributed [ADDITIONAL REFERENCE]; keep it last.
    return present + extra


def _assemble_rtfm(title: str, sections: dict[str, str], final_order: list[str]) -> str:
    """Assemble the final ``.rtfm`` text from ordered, non-empty section bodies.

    Header ``# <title>`` + one blank line + each ``[MARKER]`` + body, separated
    by a blank line. NO hard-wrapping of body paragraphs (verbatim preserved).
    """
    title = (title or "").strip() or "Unknown"
    lines: list[str] = [f"# {title}", ""]
    for i, marker in enumerate(final_order):
        body = sections[marker].rstrip()
        lines.append(f"[{marker}]")
        lines.append(body)
        if i != len(final_order) - 1:
            lines.append("")  # blank-line separator between sections
    return "\n".join(lines).rstrip() + "\n"


#: Priority for SIZE-AWARE CONDENSATION (controls-first default). Higher = kept
#: longer. CONTROLS is always the most important; ADDITIONAL REFERENCE the least.
#: HINTS & CHEATS (50) outranks NOTES (40) so low-value notes/reference are
#: dropped before player-useful hints, matching the issue's stated priorities.
_CONDENSE_PRIORITY: dict[str, int] = {
    MARKER_CONTROLS: 100,
    MARKER_GETTING_STARTED: 80,
    MARKER_HOW_TO_PLAY: 70,
    MARKER_HINTS_CHEATS: 50,
    MARKER_NOTES: 40,
    MARKER_ADDITIONAL_REFERENCE: 10,
}


#: Conservative heuristics for LOW-VALUE content we may safely drop first under
#: size pressure. These are deliberately high-precision (whole-line matches
#: only) — never matched against a line that carries real control/mechanic/cheat
#: evidence, and we never invent content. Blank lines are handled separately by
#: the caller (``_condense_sections`` Stage 2), so this pattern never relies on
#: a zero-width ``\s*`` alternative — that would wrongly match every line.
_LOW_VALUE_LINE_RE = re.compile(
    r"^(?:"                                      # whole-line match only
    r"\*+\s*$|={3,}\s*$|"                        # decorative rules / asterisks
    r"(?:table of contents|contents?)\b.*|"      # TOC headers
    r"(?:credits?|thank you|thanks to)\b.*|"     # credits
    r"copyright.*|\(c\)\s*\d|all rights reserved|"  # legal
    r"published by .*|licen[cs]e[ds]? .*|"       # publisher / license
    r"reprinted .*|distributed by .*|"           # distribution
    r"page\s*\d+|"                               # page numbers
    r"\d+\s*$)"                                  # lines that are only a number
    , re.IGNORECASE,
)


def _is_low_value_line(line: str) -> bool:
    """True for a low-value, non-evidence line safe to drop under pressure.

    Conservative: only blank lines, decorative rules, TOC/credits/legal/
    publisher/license/page-number lines. Never matches a line that carries
    control/mechanic/cheat evidence.
    """
    return bool(_LOW_VALUE_LINE_RE.match(line))


def _condense_sections(
    *,
    title: str,
    sections: dict[str, str],
    order: list[str],
    template: str,
    max_bytes: int,
) -> tuple[Optional[str], list[str], bool, Optional[str], list[str]]:
    """Deterministically reduce ``sections`` to fit ``max_bytes`` (<= 16000).

    Progressive, boundary-safe compaction with CONTROLS-first priority.
    Returns ``(text, final_order, routed_for_review, review_reason, notes)``.

    Stages:
      1. Drop lowest-priority WHOLE sections first (never CONTROLS; always keep
         at least one section). Stop once the budget is met.
      2. Within remaining sections, drop exact-duplicate lines (global dedup)
         and low-value boilerplate lines; drop any section that becomes empty
         (except CONTROLS).
      3. If still over budget, trim trailing lines one at a time from the
         lowest-priority non-CONTROLS section (line-boundary only; never
         truncate mid-line / mid-section).

    If nothing fits within ``max_bytes`` (e.g. a single giant CONTROLS block),
    returns ``(None, order, True, reason, notes)`` so the caller routes for
    review — exactly the prior behavior for irreducible oversize.
    """
    notes: list[str] = []

    def _render(secs: dict[str, list[str]], ordr: list[str]) -> Optional[str]:
        if not ordr:
            return None
        assembled = {m: "\n".join(secs[m]).rstrip() + "\n" for m in ordr if m in secs}
        return _assemble_rtfm(title, assembled, ordr)

    def _budget_ok(t: Optional[str]) -> bool:
        return t is not None and len(t.encode("utf-8")) <= max_bytes

    # Work on line lists so trimming is boundary-safe; never mutate caller state.
    work: dict[str, list[str]] = {
        m: list(sections[m].split("\n")) for m in order if m in sections
    }
    ordr = [m for m in order if m in work]

    # Stage 1: greedily drop whole lowest-priority non-CONTROLS sections until
    # the budget is met (keep at least one section; CONTROLS never dropped).
    text = _render(work, ordr)
    while not _budget_ok(text) and len(ordr) > 1:
        candidates = [
            m for m in sorted(ordr, key=lambda m: _CONDENSE_PRIORITY.get(m, 0))
            if m in work and m != MARKER_CONTROLS
        ]
        if not candidates:
            break  # only CONTROLS (or nothing else) remains
        victim = candidates[0]
        work.pop(victim, None)
        ordr = [x for x in ordr if x != victim]
        notes.append(f"condensation: dropped whole section [{victim}]")
        text = _render(work, ordr)
    if _budget_ok(text):
        return text, ordr, False, None, notes

    # Stage 2: global dedup + drop low-value boilerplate lines within remaining
    # sections (we never invent content; only verbatim lines are removed).
    seen: set[str] = set()
    for m in ordr:
        if m not in work:
            continue
        new_lines: list[str] = []
        for ln in work[m]:
            s = ln.strip()
            if not s:
                continue  # collapse blank lines; assembly re-adds spacing
            if _is_low_value_line(ln):
                continue
            if s in seen:
                continue  # exact-duplicate line across the whole manual
            seen.add(s)
            new_lines.append(ln)
        work[m] = new_lines
        if m != MARKER_CONTROLS and not new_lines:
            notes.append(
                f"condensation: section [{m}] emptied by dedup/boilerplate removal"
            )
            work.pop(m, None)
    ordr = [m for m in ordr if m in work]
    text = _render(work, ordr)
    if _budget_ok(text):
        notes.append("condensation: reduced via duplicate/boilerplate-line removal")
        return text, ordr, False, None, notes

    # Stage 3: line-boundary trim of the lowest-priority non-CONTROLS sections.
    # Trim one trailing line at a time; CONTROLS is preserved as long as
    # possible. Never truncates mid-line.
    while True:
        candidates = [
            m for m in sorted(ordr, key=lambda m: _CONDENSE_PRIORITY.get(m, 0))
            if m in work and m != MARKER_CONTROLS and work[m]
        ]
        if not candidates:
            break  # only CONTROLS remains (or everything trimmed)
        victim = candidates[0]
        work[victim] = work[victim][:-1]
        if not work[victim]:
            notes.append(f"condensation: section [{victim}] fully trimmed")
            work.pop(victim, None)
            ordr = [m for m in ordr if m in work]
        text = _render(work, ordr)
        if _budget_ok(text):
            notes.append(
                f"condensation: trimmed trailing lines from [{victim}] to fit "
                f"{max_bytes} bytes"
            )
            return text, ordr, False, None, notes

    # Could not fit within max_bytes using deterministic, no-AI reduction.
    text = _render(work, ordr)
    reason = (
        f"rendered manual ({len(text.encode('utf-8')) if text else 0} bytes) "
        f"could not be deterministically condensed to <= {max_bytes} bytes; "
        f"routed for review"
    )
    return None, ordr, True, reason, notes


def render_rtfm(
    *,
    title: str,
    sections: dict[str, str],
    template: str,
    max_bytes: int,
    forced_order: Optional[list[str]] = None,
) -> tuple[Optional[str], list[str], bool, Optional[str], list[str]]:
    """Render the ``.rtfm`` text from composed sections.

    Returns ``(text, sections_present, routed_for_review, review_reason,
    condensation_notes)``.

    * Empty sections are OMITTED (never emit a marker with no body).
    * Template order sets the PREFERRED order of present sections, unless
      ``forced_order`` is given (existing-``.rtfm`` passthrough: highest-fidelity
      preservation of the author's marker order takes precedence over the
      template). When ``forced_order`` is None, present sections follow the
      template preferred order; any extra present sections append in canonical
      order.
    * NO hard-wrapping of body paragraphs (verbatim content preserved).
    * The result is guaranteed <= ``max_bytes`` (<= ``MAX_RTFM_BYTES``). If the
      natural content would exceed ``max_bytes``, the pipeline attempts
      deterministic, no-AI SIZE-AWARE CONDENSATION (controls-first priority,
      never invents facts). If condensation still cannot fit, ``text`` is
      ``None`` and ``routed_for_review`` is True (NEVER silently truncated).
    """
    title = (title or "").strip() or "Unknown"

    final_order = _order_sections(template, forced_order, sections)

    if not final_order:
        # No content at all: an empty manual is routed for review (operator decides).
        return None, [], True, "no manual content discovered for this title", []

    text = _assemble_rtfm(title, sections, final_order)

    if len(text.encode("utf-8")) <= max_bytes:
        # Fits naturally — no condensation needed.
        return text, final_order, False, None, []

    # Over budget: attempt deterministic, no-AI size-aware condensation.
    cond_text, cond_order, routed, reason, notes = _condense_sections(
        title=title,
        sections=sections,
        order=final_order,
        template=template,
        max_bytes=max_bytes,
    )
    if cond_text is not None and not routed:
        # Hard-cap guard (defensive: max_bytes is already clamped <= 16000).
        if len(cond_text.encode("utf-8")) > MAX_RTFM_BYTES:
            return (
                None,
                final_order,
                True,
                "rendered manual exceeds the 16000-byte GTi hard limit",
                notes,
            )
        return cond_text, cond_order, False, None, notes

    # Condensation failed or produced an over-budget result -> route for review,
    # never emit a > max_bytes / > 16000-byte file.
    reason = reason or (
        f"rendered manual ({len(text.encode('utf-8'))} bytes) exceeds the "
        f"configured review target ({max_bytes} bytes); routed for review"
    )
    return None, final_order, True, reason, notes


# --- Provenance --------------------------------------------------------------


def _provenance_source_from_scored(src: "RtfmSource", group, *, kind: str) -> "RtfmProvenanceSource":
    """Build a provenance source entry for a scored candidate (auditable match).

    Carries the deterministic ``match_confidence`` / ``match_kind`` /
    ``match_evidence`` so review routing is explainable in provenance even when
    the source was NOT composed into a ``.rtfm`` (e.g. routed-for-review
    branches, where no section body exists yet). ``kind`` is the file kind
    label (``txt verbatim`` / ``.rtfm passthrough``).
    """
    sc = score_source_match(src, group)
    root_index = getattr(src, "root_index", 0)
    return RtfmProvenanceSource(
        category=src.category,
        root_index=root_index,
        source_rel=_relative_to_root(src.path, src.root),
        filename=src.path.name,
        kind=kind,
        sections=[],
        sha256=_sha256_file(src.path),
        size=src.path.stat().st_size,
        match_confidence=sc.confidence,
        match_kind=sc.kind,
        match_evidence=list(sc.evidence),
    )


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON to ``path`` via a temp file + atomic replace (no partial reads)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def _build_provenance(group, result: RtfmResult, *, max_bytes: int, mode: str) -> dict:
    """Build the durable RTFM provenance record (no private host paths)."""
    return {
        "schema": "rtfm-provenance/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_key": getattr(group, "release_key", "") or "",
        "title": (getattr(group, "title", None) or "Unknown"),
        "basename": result.basename,
        "template": result.template_used,
        "mode": mode,
        "max_bytes_target": max_bytes,
        "hard_limit_bytes": MAX_RTFM_BYTES,
        "written": result.written,
        "routed_for_review": result.routed_for_review,
        "review_reason": result.review_reason,
        "sections_present": result.sections_present,
        "output_bytes": result.bytes,
        # Provenance sources store ONLY root-relative posix paths + category +
        # root index. No absolute host path is ever embedded.
        "sources": [s.__dict__ for s in result.sources],
        "notes": list(result.notes),
    }


# --- Public build API --------------------------------------------------------


def build_rtfm_for_group(
    group,
    *,
    cfg: RtfmConfig,
    rtfm_dir: Path,
    sources: Optional[list[RtfmSource]] = None,
) -> RtfmResult:
    """Build the ``.rtfm`` + provenance sidecar for one release group.

    Deterministic, offline, read-only against source roots. Writes the
    ``.rtfm`` and its provenance sidecar under ``rtfm_dir`` ONLY when enabled.
    On overflow or ambiguous match, the title is routed for review (no ``.rtfm``
    larger than ``MAX_RTFM_BYTES`` is ever emitted) but the provenance sidecar is
    still written so the routing decision is auditable.

    Ambiguous match (group flagged near-duplicate spelling, or quarantined) is
    routed for review and NO ``.rtfm`` is emitted.
    """
    if not cfg.enabled:
        raise RtfmDisabled("rtfm builder is disabled in config")

    basename = _group_identity(group)
    result = RtfmResult(
        release_key=getattr(group, "release_key", "") or "",
        basename=basename,
        template_used=cfg.template,
    )
    rtfm_dir = Path(rtfm_dir)
    rtfm_path = rtfm_dir / f"{basename}.rtfm"
    prov_path = rtfm_dir / f"{basename}.rtfm.provenance.json"

    # Ambiguous / quarantined match => route for review, do not emit.
    qr = getattr(group, "quarantine_reason", None)
    if qr:
        reason = "ambiguous match (routed for review): " + (qr or "")
        result.routed_for_review = True
        result.review_reason = reason
        assert result.review_reason is not None
        result.notes.append(result.review_reason)
        result.sources = []
        _write_json_atomic(
            prov_path, _build_provenance(group, result, max_bytes=cfg.max_bytes, mode="deterministic")
        )
        result.provenance_path = prov_path
        return result

    # Discover + SCORE sources (Issue #6: deterministic confidence scoring).
    all_sources = sources if sources is not None else discover_sources(cfg)
    scored = [(s, score_source_match(s, group)) for s in all_sources]
    matched = [s for s, sc in scored if sc.matched]
    # Deterministic sort by (descending confidence, ascending canonical key) so
    # the "best" and "near-tie" candidates are stable and reproducible.
    ranked = sorted(
        ((sc, s) for s, sc in scored if sc.matched),
        key=lambda scs: (-round(scs[0].confidence, 6), _canonical_game_key(scs[1])),
    )
    if not matched:
        # Nothing found. Route for review (operator may add a manual) rather
        # than emitting an empty file. Record the best non-matching candidate
        # stems (if any) for auditability without leaking host paths.
        best_stems = sorted(
            (s.stem for s, sc in scored),
            key=lambda x: x.lower(),
        )[:5]
        result.routed_for_review = True
        result.review_reason = (
            "no matching manual source discovered for this title"
            + (f"; nearest stems: {best_stems}" if best_stems else "")
        )
        result.notes.append(result.review_reason)
        result.sources = [
            _provenance_source_from_scored(s, group, kind=(
                ".rtfm passthrough" if s.path.suffix.lower() == RTFM_SUFFIX
                else "txt verbatim"
            ))
            for s in matched
        ]
        _write_json_atomic(
            prov_path, _build_provenance(group, result, max_bytes=cfg.max_bytes, mode="deterministic")
        )
        result.provenance_path = prov_path
        return result

    # Decide AUTO-ACCEPT vs. ROUTE-FOR-REVIEW from the confidence ranking.
    # Near-tie detection dedupes by canonical game key so multiple copies of the
    # SAME game (separate category roots, disk/alt tags) count once — only a
    # genuinely DISTINCT rival manual forces review.
    top_conf = ranked[0][0].confidence
    if top_conf >= HIGH_CONFIDENCE:
        top_key = _canonical_game_key(ranked[0][1])
        near_ties = [
            s.stem for sc, s in ranked[1:]
            if top_conf - sc.confidence <= NEAR_TIE_EPSILON
            and _canonical_game_key(s) != top_key
        ]
        if near_ties:
            result.routed_for_review = True
            result.review_reason = (
                "ambiguous high-confidence match (near-tie candidates within "
                f"{NEAR_TIE_EPSILON} of best {top_conf:.2f}): {near_ties}"
            )
            result.notes.append(result.review_reason)
            result.sources = [
                _provenance_source_from_scored(s, group, kind=(
                    ".rtfm passthrough" if s.path.suffix.lower() == RTFM_SUFFIX
                    else "txt verbatim"
                ))
                for s, sc in scored if sc.matched
            ]
            _write_json_atomic(
                prov_path, _build_provenance(group, result, max_bytes=cfg.max_bytes, mode="deterministic")
            )
            result.provenance_path = prov_path
            return result
        # Unambiguous high-confidence: fall through to build + emit (auto-accept).
    else:
        # Best candidate is in the plausible-but-not-high band [0.5, 0.95) OR too
        # low to trust: route to review naming the ambiguity. Never auto-accept
        # a fuzzy/low-confidence match (issue #6: never merge a fuzzy tie).
        reason_band = "plausible-but-not-high-confidence" if top_conf >= 0.5 else "low-confidence"
        other_stems = [s.stem for sc, s in ranked[1:]]
        tie_note = ""
        if len(ranked) >= 2 and (top_conf - ranked[1][0].confidence) <= NEAR_TIE_EPSILON:
            reason_band = "near-tie ambiguous"
            tie_note = f"; near-tie stems: {other_stems}"
        result.routed_for_review = True
        result.review_reason = (
            f"{reason_band} match (best confidence {top_conf:.2f} < "
            f"auto-accept threshold {HIGH_CONFIDENCE}); candidate stems: "
            f"{[s.stem for sc, s in ranked]}{tie_note}"
        )
        result.notes.append(result.review_reason)
        result.sources = [
            _provenance_source_from_scored(s, group, kind=(
                ".rtfm passthrough" if s.path.suffix.lower() == RTFM_SUFFIX
                else "txt verbatim"
            ))
            for s, sc in scored if sc.matched
        ]
        _write_json_atomic(
            prov_path, _build_provenance(group, result, max_bytes=cfg.max_bytes, mode="deterministic")
        )
        result.provenance_path = prov_path
        return result

    # Size safety: reject oversized sources before reading (DoS guard).
    safe_matched: list[RtfmSource] = []
    for s in matched:
        try:
            st = s.path.stat()
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if st.st_size > MAX_SOURCE_BYTES:
            result.notes.append(
                f"source skipped (exceeds {MAX_SOURCE_BYTES} byte cap): {s.path.name}"
            )
            continue
        safe_matched.append(s)
    matched = safe_matched

    sections, prov_sources, passthrough_order, skipped_notes = _compose_sections(matched, group)
    result.sources = prov_sources
    result.notes.extend(skipped_notes)

    if not sections and skipped_notes:
        # Every matched source failed to decode (e.g. binary-only title). Route
        # for review rather than aborting the run; provenance is still written.
        result.routed_for_review = True
        result.review_reason = "all matched sources failed to decode; routed for review"
        result.notes.append(result.review_reason)
        result.provenance_path = prov_path
        _write_json_atomic(
            prov_path, _build_provenance(group, result, max_bytes=cfg.max_bytes, mode="deterministic")
        )
        return result

    title = (getattr(group, "title", None) or "").strip() or basename
    text, sections_present, routed, reason, condensation_notes = render_rtfm(
        title=title,
        sections=sections,
        template=cfg.template,
        max_bytes=cfg.max_bytes,
        # Highest-fidelity passthrough: preserve the existing .rtfm's marker
        # order when present; otherwise follow the template's preferred order.
        forced_order=passthrough_order,
    )
    result.sections_present = sections_present
    # Record deterministic condensation audit trail (auditable in provenance).
    for note in condensation_notes:
        result.notes.append(note)

    if routed or text is None:
        result.routed_for_review = True
        result.review_reason = reason or "routed for review"
        result.notes.append(result.review_reason)
        # Provenance still written (auditable); no .rtfm emitted over the cap.
        _write_json_atomic(
            prov_path, _build_provenance(group, result, max_bytes=cfg.max_bytes, mode="deterministic")
        )
        result.provenance_path = prov_path
        return result

    # Emit the .rtfm (atomic write) + provenance sidecar.
    rtfm_dir.mkdir(parents=True, exist_ok=True)
    tmp = rtfm_path.with_suffix(rtfm_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(rtfm_path)
    result.rtfm_path = rtfm_path
    result.written = True
    result.bytes = len(text.encode("utf-8"))
    _write_json_atomic(
        prov_path, _build_provenance(group, result, max_bytes=cfg.max_bytes, mode="deterministic")
    )
    result.provenance_path = prov_path
    result.notes.append(f"rtfm written: {rtfm_path}")
    return result


def build_rtfm_all(
    groups: list,
    *,
    cfg: RtfmConfig,
    rtfm_dir: Path,
) -> list[RtfmResult]:
    """Build ``.rtfm`` sidecars for every release group (deterministic, offline)."""
    rtfm_dir = Path(rtfm_dir)
    # Discover once, then match per group (one shared .rtfm per group key).
    sources = discover_sources(cfg)
    results: list[RtfmResult] = []
    seen_keys: set[str] = set()
    for g in groups:
        key = getattr(g, "release_key", "") or _group_identity(g)
        if key in seen_keys:
            # Multi-disk set already emitted its shared .rtfm; skip the duplicate.
            continue
        seen_keys.add(key)
        try:
            results.append(build_rtfm_for_group(g, cfg=cfg, rtfm_dir=rtfm_dir, sources=sources))
        except RtfmDisabled:
            continue
        except Exception as exc:  # a single group failure must not abort the run
            res = RtfmResult(
                release_key=key,
                basename=_group_identity(g),
                routed_for_review=True,
                review_reason=f"rtfm build error: {exc}",
                template_used=cfg.template,
            )
            results.append(res)
    return results
