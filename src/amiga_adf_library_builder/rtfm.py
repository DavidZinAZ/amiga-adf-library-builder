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
    # ``.rtfm`` passthrough preserves the source marker order when known.
    marker_order: Optional[list[str]] = None


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
    """
    marker_set = set(CANONICAL_MARKERS)
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
    """Deterministic canonical-title / variant match (reuse, no new matcher).

    Matches when ANY of:
      * the source filename stem equals the group title (case/space-insensitive);
      * the source stem equals the group's release basename (multi-disk/variant);
      * the source stem (normalized) equals the group title (normalized);
      * the source stem is a canonical-reuse base of the group title (release tags
        stripped on both sides), serving cracks/trainers/alt-dumps/language/
        chipset/multi-disk variants of the SAME canonical game.
    """
    title = getattr(group, "title", None) or ""
    basename = _group_identity(group)
    stem = src.stem
    if not stem:
        return False
    # Raw identity equality (whitespace-insensitive).
    if stem.strip().lower() == title.strip().lower() and title:
        return True
    if stem.strip().lower() == basename.strip().lower():
        return True
    # Normalized equality.
    if _norm_title(stem) and _norm_title(stem) == _norm_title(title):
        return True
    # Canonical-reuse base match (release tags stripped on both sides).
    base_stem = _strip_release_tags(stem)
    base_title = _strip_release_tags(title)
    if base_stem and base_title and _norm_title(base_stem) == _norm_title(base_title):
        return True
    return False


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
            )
        )

    # Join each section's composed blocks with a blank line separator.
    joined: dict[str, str] = {}
    for marker, blocks in sections.items():
        joined[marker] = "\n\n".join(b for b in blocks if b).rstrip() + "\n"
    return joined, prov_sources, passthrough_order, skipped_notes


# --- Rendering ---------------------------------------------------------------


def render_rtfm(
    *,
    title: str,
    sections: dict[str, str],
    template: str,
    max_bytes: int,
    forced_order: Optional[list[str]] = None,
) -> tuple[Optional[str], list[str], bool, Optional[str]]:
    """Render the ``.rtfm`` text from composed sections.

    Returns (text, sections_present, routed_for_review, review_reason).

    * Empty sections are OMITTED (never emit a marker with no body).
    * Template order sets the PREFERRED order of present sections, unless
      ``forced_order`` is given (existing-``.rtfm`` passthrough: highest-fidelity
      preservation of the author's marker order takes precedence over the
      template). When ``forced_order`` is None, present sections follow the
      template preferred order; any extra present sections append in canonical
      order.
    * NO hard-wrapping of body paragraphs (verbatim content preserved).
    * The result is guaranteed <= ``MAX_RTFM_BYTES``. If the natural content
      would exceed ``max_bytes`` (the conservative target), ``text`` is ``None``
      and ``routed_for_review`` is True (NEVER silently truncated).
    """
    title = (title or "").strip() or "Unknown"

    if forced_order:
        # Passthrough: preserve the source's marker order, dropping empties.
        final_order = [m for m in forced_order if m in sections and sections[m].strip()]
        # Append any other present canonical markers not in the source order
        # (defensive; should not normally happen for a clean passthrough).
        for m in CANONICAL_MARKERS:
            if m in sections and sections[m].strip() and m not in final_order:
                final_order.append(m)
    else:
        # Determine order: template preferred order for present sections, then
        # any extra present sections in canonical order.
        order = list(TEMPLATES.get(template, TEMPLATES[DEFAULT_TEMPLATE]))
        present = [m for m in order if m in sections and sections[m].strip()]
        extra = [
            m for m in CANONICAL_MARKERS
            if m in sections and sections[m].strip() and m not in present
        ]
        # full-reference may have contributed [ADDITIONAL REFERENCE]; keep it last.
        final_order = present + extra

    if not final_order:
        # No content at all: emit a minimal title-only file? No — M1 requires a
        # manual; an empty manual is routed for review (operator decides).
        return None, [], True, "no manual content discovered for this title"

    lines: list[str] = [f"# {title}", ""]
    for i, marker in enumerate(final_order):
        body = sections[marker].rstrip()
        lines.append(f"[{marker}]")
        lines.append(body)
        if i != len(final_order) - 1:
            lines.append("")  # blank-line separator between sections
    text = "\n".join(lines).rstrip() + "\n"

    if len(text.encode("utf-8")) > MAX_RTFM_BYTES:
        # Hard cap breach is a programming-error-class failure; still route review.
        return (
            None,
            final_order,
            True,
            "rendered manual exceeds the 16000-byte GTi hard limit",
        )
    if len(text.encode("utf-8")) > max_bytes:
        # Conservative target breach: route for review, never truncate.
        return None, final_order, True, (
            f"rendered manual ({len(text.encode('utf-8'))} bytes) exceeds the "
            f"configured review target ({max_bytes} bytes); routed for review"
        )
    return text, final_order, False, None


# --- Provenance --------------------------------------------------------------


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

    # Discover + match sources.
    all_sources = sources if sources is not None else discover_sources(cfg)
    matched = [s for s in all_sources if _source_matches_group(s, group)]
    if not matched:
        # Nothing found for this title. Route for review (operator may add a
        # manual) rather than emitting an empty file.
        result.routed_for_review = True
        result.review_reason = "no matching manual source discovered for this title"
        result.notes.append(result.review_reason)
        result.sources = []
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
    text, sections_present, routed, reason = render_rtfm(
        title=title,
        sections=sections,
        template=cfg.template,
        max_bytes=cfg.max_bytes,
        # Highest-fidelity passthrough: preserve the existing .rtfm's marker
        # order when present; otherwise follow the template's preferred order.
        forced_order=passthrough_order,
    )
    result.sections_present = sections_present

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
