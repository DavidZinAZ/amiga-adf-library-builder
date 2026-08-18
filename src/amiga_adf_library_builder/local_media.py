"""Generic offline, read-only local-media artwork provider (local-media provider base app).

This module discovers box/screenshot artwork from operator-configured local
media libraries -- the reference adapter is LaunchBox -- copies the chosen
source into the application's OWN cache, and records provenance that survives
the original library later moving or disappearing.

Hard design constraints (ratified local-media provider security posture):

* **Read-only against the source library.** Nothing under any configured root
  is ever opened for writing, renamed, deleted, or reorganized. Source files
  are opened read-only (``"rb"``) and only ever copied INTO the application's
  own cache directory.
* **Offline.** No network imports, no DNS, no sockets. The provider and its
  tests import nothing from ``socket`` / ``urllib`` / ``requests``. It runs
  with no signup, key, or network.
* **Stdlib-only discovery.** Discovery, checksum, and copy use only the Python
  standard library (``pathlib``, ``hashlib``, ``json``, ``difflib``,
  ``shutil``). ``Pillow`` is NOT required to discover, score, copy, or prove
  provenance -- only the existing ``artwork.py`` resize step needs it, and that
  happens later against the app-owned cache copy.
* **Exact category priority.** Per local-media provider the priority is, and must remain,
  exactly ``Screenshot - Game Title`` -> ``Box - Front`` -> ``Screenshot -
  Gameplay``. The current category is fully searched before the next is
  considered, and a higher-priority confident match always wins.
* **No false merges.** Sequels, similarly named games, editions, and unrelated
  titles must not cross-match. Canonical-title reuse lets one approved image
  serve variants of the SAME canonical game (cracks, trainers, alternate
  dumps, language/chipset variants, multi-disk releases) without pulling in a
  different game.

The module is provider-agnostic: :class:`LocalMediaProvider` indexes a flat
list of candidates produced by a pluggable adapter (``LaunchBoxAdapter``
today; other adapters can be added without touching the scoring logic).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

# --- Constants ---------------------------------------------------------------

#: Default, local-media provider-mandated category priority. Overridable via the
#: ``preferred_image_types`` config key, but the documented default is exactly
#: this order and these names.
DEFAULT_PREFERRED_TYPES: tuple[str, ...] = (
    "Screenshot - Game Title",
    "Box - Front",
    "Screenshot - Gameplay",
)

#: Raster image extensions we will consider as artwork. SVG is deliberately
#: excluded (non-raster; cannot be validated/sized by the Gotek pipeline).
IMAGE_SUFFIXES: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
)

#: Manual document extensions discovered from configured manual roots
#: (issue #33: multiple manual roots for PDF/TXT manuals).
MANUAL_SUFFIXES: frozenset[str] = frozenset({".pdf", ".txt"})

#: Representative LaunchBox image/media categories offered by the GUI asset-type
#: selector (issue #33). Order is the order the GUI presents them; it is NOT a
#: priority order (priority comes from ``preferred_image_types``).
LAUNCHBOX_IMAGE_CATEGORIES: tuple[str, ...] = (
    "Box - Front",
    "Box - Back",
    "Box - 3D",
    "Disc",
    "Clear Logo",
    "Screenshot - Gameplay",
    "Screenshot - Game Title",
    "Screenshot - Game Select",
    "Screenshot - Game Over",
    "Fanart",
    "Banner",
    "Background",
    "Music",
    "Videos",
    "Manual",
)

#: Fallback asset type for a bare-string typed media root whose folder name is
#: not a recognized LaunchBox category.
DEFAULT_MEDIA_ROOT_ASSET_TYPE = "Box - Front"

#: Image-category folder names that carry NO per-game identity. Used by
#: :attr:`LocalMediaCandidate.folder_chain_norm` to exclude the category level
#: (and its variants) from the set of candidate game-title identities, so a
#: category folder is never mistaken for a game title.
CAT_SET_SKIP: frozenset[str] = frozenset(
    {
        "Screenshot - Game Title",
        "Box - Front",
        "Box - Back",
        "Screenshot - Gameplay",
        "Screenshot - Game Select",
        "Screenshot - Game Over",
        "Clear Logo",
        "Banner",
        "Fanart",
        "Box - 3D",
        "Cart - Front",
        "Disc",
        "Logos",
        "Background",
        "Music",
        "Videos",
        "Manual",
    }
)

#: Minimum fuzzy ratio before a candidate is even worth manual review.
FUZZY_MIN_RATIO = 0.80
#: Default confidence floor for auto-acceptance (no manual review). Exact and
#: tag-stripped canonical-reuse matches score >= 0.97 and clear this floor;
#: lower-scoring fuzzy hits route to manual review instead.
AUTO_ACCEPT_MIN_CONF = 0.95

# Release-tag tokens removed when deriving a canonical "base" title for
# cross-variant reuse (cracks, trainers, alt dumps, language, chipset,
# multi-disk). Matched as whole words only, so real title words that happen to
# match are NOT stripped (conservative: prefer a slightly-too-long base over
# silently eating part of the game name).
_RELEASE_TAG_WORDS = (
    "cr", "cream", "cracked", "trainer", "trained", "tr",
    "alt", "alternative", "alternate", "a1", "a2", "a3",
    "m3", "aga", "ecs", "ocs", "rtg", "agaecs",
    "qtx", "skr", "pd", "demo",
    "lang", "language", "ntsc", "pal",
    "cd", "disk", "disks", "disc", "side", "part", "disk1", "disk2", "disk3",
    "en", "de", "fr", "es", "it", "pl", "pl1", "pl2", "us", "uk",
)

# Tokens that are only meaningful as a *suffix* on a disk/side marker, e.g.
# "Disk 2" / "Side B" / "Disk1". These are removed only when attached to a
# leading disk/side word, never as bare words (so "Side" alone in a title is
# left alone).
_TRAILING_DISK_RE = re.compile(
    r"\b(?:dis[ck]|disk|disc|side|part)\s*[0-9]+[ab]?\b", re.IGNORECASE
)


class MatchMethod(str, Enum):
    """How a candidate was matched to a release group."""

    EXACT_CANONICAL = "exact_canonical"
    EXACT_DISK_STEM = "exact_disk_stem"
    NORMALIZED_TITLE = "normalized_title"
    CANONICAL_REUSE = "canonical_reuse"
    FUZZY = "fuzzy"
    FUZZY_MANUAL = "fuzzy_manual"
    MANUAL_REVIEW = "manual_review"
    NONE = "none"


class LocalMediaError(Exception):
    """Base error for local-media provider failures."""


class LocalMediaDisabled(LocalMediaError):
    """Raised when the provider is used while disabled in config."""


# --- Configuration -----------------------------------------------------------


def _parse_media_roots(raw) -> tuple[MediaRoot, ...]:
    """Parse ``media_roots`` entries: bare strings OR ``{path, asset_type}``.

    A bare string becomes a typed root whose asset type is the folder's own
    name when that name is a recognized LaunchBox category, else
    :data:`DEFAULT_MEDIA_ROOT_ASSET_TYPE`. Entries without a usable path are
    dropped (defensive: a malformed mapping must not break config loading).
    """
    if raw is None:
        return ()
    if isinstance(raw, (str, dict)):
        raw = [raw]
    out: list[MediaRoot] = []
    for entry in raw:
        if isinstance(entry, str):
            path = entry.strip()
            if not path:
                continue
            folder = Path(path).name
            asset = folder if folder in CAT_SET_SKIP else DEFAULT_MEDIA_ROOT_ASSET_TYPE
            out.append(MediaRoot(path=path, asset_type=asset))
        elif isinstance(entry, dict):
            path = str(entry.get("path") or "").strip()
            if not path:
                continue
            asset = str(entry.get("asset_type") or "").strip() or DEFAULT_MEDIA_ROOT_ASSET_TYPE
            out.append(MediaRoot(path=path, asset_type=asset))
    return tuple(out)


def _parse_manual_roots(raw) -> tuple[ManualRoot, ...]:
    """Parse ``manual_roots`` entries: strings OR ``{path}`` tables."""
    if raw is None:
        return ()
    if isinstance(raw, (str, dict)):
        raw = [raw]
    out: list[ManualRoot] = []
    for entry in raw:
        if isinstance(entry, str):
            path = entry.strip()
        elif isinstance(entry, dict):
            path = str(entry.get("path") or "").strip()
        else:
            path = ""
        if path:
            out.append(ManualRoot(path=path))
    return tuple(out)


@dataclass(frozen=True)
class MediaRoot:
    """One typed LaunchBox image/media root (issue #33).

    ``path`` is the root directory; ``asset_type`` is the LaunchBox media
    category it holds (e.g. ``"Box - Front"``). Images directly under the root
    (or under region/per-game subfolders) are all attributed to ``asset_type``.
    A root is LOCAL ONLY: discovery is read-only and never touches the network.
    """

    path: str
    asset_type: str


@dataclass(frozen=True)
class ManualRoot:
    """One manual-document root (PDF/TXT) for issue #33 mappings."""

    path: str


@dataclass(frozen=True)
class LocalMediaConfig:
    """Typed view of the ``[local_media]`` TOML table.

    ``roots`` keeps its existing semantics (LaunchBox image-tree roots,
    ``<root>/Images/<Platform>/<Category>/...``) and is backward compatible.
    ``media_roots`` and ``manual_roots`` are the issue #33 LaunchBox mappings:
    multiple image/media roots, each with an explicit asset type, plus multiple
    manual roots for PDF/TXT documents.
    """

    enabled: bool = False
    roots: tuple[str, ...] = ()
    platform_names: tuple[str, ...] = ("Commodore Amiga", "Amiga")
    preferred_image_types: tuple[str, ...] = DEFAULT_PREFERRED_TYPES
    recursive: bool = True
    confidence_threshold: float = AUTO_ACCEPT_MIN_CONF
    media_roots: tuple[MediaRoot, ...] = ()
    manual_roots: tuple[ManualRoot, ...] = ()

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "LocalMediaConfig":
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
        roots = _as_tuple(raw.get("roots"), ())
        platform_names = _as_tuple(
            raw.get("platform_names"), ("Commodore Amiga", "Amiga")
        )
        pit = _as_tuple(raw.get("preferred_image_types"), DEFAULT_PREFERRED_TYPES)
        recursive = bool(raw.get("recursive", True))
        try:
            threshold = float(raw.get("confidence_threshold", AUTO_ACCEPT_MIN_CONF))
        except (TypeError, ValueError):
            threshold = AUTO_ACCEPT_MIN_CONF
        return cls(
            enabled=enabled,
            roots=roots,
            platform_names=platform_names,
            preferred_image_types=pit,
            recursive=recursive,
            confidence_threshold=threshold,
            media_roots=_parse_media_roots(raw.get("media_roots")),
            manual_roots=_parse_manual_roots(raw.get("manual_roots")),
        )


# --- MobyGames Configuration -------------------------------------------------


@dataclass(frozen=True)
class MobyGamesConfig:
    """Typed view of the ``[mobygames]`` TOML table.

    This provider is OPTIONAL and DISABLED BY DEFAULT. The base app must
    continue to work with no MobyGames config, no account, no network,
    and no hard-coded credentials. The API key is read ONLY from the
    environment variable specified by ``api_key_env`` (default:
    ``MOBYGAMES_API_KEY``).
    """

    enabled: bool = False
    api_key_env: str = "MOBYGAMES_API_KEY"
    preferred_image_types: tuple[str, ...] = ("cover", "screenshot", "box")
    timeout: float = 20.0

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "MobyGamesConfig":
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
        api_key_env = str(raw.get("api_key_env", "MOBYGAMES_API_KEY")).strip() or "MOBYGAMES_API_KEY"
        pit = _as_tuple(raw.get("preferred_image_types"), ("cover", "screenshot", "box"))
        try:
            timeout = float(raw.get("timeout", 20.0))
        except (TypeError, ValueError):
            timeout = 20.0
        return cls(
            enabled=enabled,
            api_key_env=api_key_env,
            preferred_image_types=pit,
            timeout=timeout,
        )


# --- Data records ------------------------------------------------------------


@dataclass
class LocalMediaProvenance:
    """Provenance for one cached artwork image (survives the source moving)."""

    source_path: str
    source_sha256: str
    category: str
    match_method: str
    confidence: float
    cached_path: str
    cached_sha256: str
    cached_at: str
    provider: str = "local_media"
    root: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "schema": "local-media-provenance/1",
            "provider": self.provider,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "source_root": self.root,
            "category": self.category,
            "match_method": self.match_method,
            "confidence": self.confidence,
            "cached_path": self.cached_path,
            "cached_sha256": self.cached_sha256,
            "cached_at": self.cached_at,
        }


@dataclass
class LocalMediaCandidate:
    """One discovered source image and its derived identity.

    LaunchBox images are named after the game at the *folder* level, the
    *file* level, or both (e.g. ``.../Box - Front/Example Space Tactics/001.png`` or
    ``.../Box - Front/Example Space Tactics.png``). We derive match identities from
    BOTH the immediate parent folder name and the filename stem, each in raw,
    normalized, and release-tag-stripped forms, so a match succeeds regardless
    of which convention a given LaunchBox export uses.
    """

    path: Path
    category: str
    root: Path
    # Immediate parent folder (the per-game folder in LaunchBox's layout), when
    # the adapter can determine it; falls back to path.parent.name.
    game_folder: Optional[str] = None

    @property
    def folder_name(self) -> Optional[str]:
        """The resolved per-game folder name, or ``None`` when none exists.

        Prefers the adapter-supplied ``game_folder`` (the genuine per-game
        directory) so match logic sees the game name, while ``category`` holds
        the image type. When the adapter did not resolve a folder, the immediate
        parent is accepted ONLY if it is neither the image category nor a region
        folder -- flat and region-nested LaunchBox layouts have no per-game
        folder, and the category/region MUST NOT be treated as a game title.
        """
        if self.game_folder:
            return self.game_folder
        parent = self.path.parent.name
        if parent in CAT_SET_SKIP or _is_region_name(parent):
            return None
        return parent

    @property
    def norm_stem(self) -> str:
        """Normalized filename stem (punctuation/space/underscore stripped)."""
        return _norm_text(self.path.stem)

    @property
    def raw_ordinal_stem(self) -> str:
        """Filename stem with only the trailing LaunchBox ordinal removed.

        Used so a flat/region-nested file named ``Bubble Bobble-01.png`` yields
        the genuine game name ``Bubble Bobble`` while a filename that IS the game
        (e.g. ``Bubble Bobble.png``) is unchanged.
        """
        return _strip_launchbox_ordinal(self.path.stem)

    @property
    def norm_ordinal_stem(self) -> str:
        """Normalized filename stem after stripping the trailing ordinal."""
        return _norm_text(self.raw_ordinal_stem)

    @property
    def norm_folder(self) -> str:
        """Normalized parent-folder name (empty string when no game folder)."""
        return _norm_text(self.folder_name or "")

    @property
    def raw_ordinal_folder(self) -> str:
        """Parent folder with only the trailing LaunchBox ordinal removed.

        Returns ``""`` when there is no genuine per-game folder (flat/region-
        nested layout), so the folder identity never carries a category/region.
        """
        if not self.folder_name:
            return ""
        return _strip_launchbox_ordinal(self.folder_name)

    @property
    def norm_ordinal_folder(self) -> str:
        """Normalized parent folder after stripping the trailing ordinal."""
        return _norm_text(self.raw_ordinal_folder)

    @property
    def base_stem(self) -> str:
        """Filename stem with release tags stripped (canonical-variant base)."""
        return _strip_release_tags(self.path.stem)

    @property
    def base_folder(self) -> str:
        """Parent-folder name with release tags stripped.

        Returns ``""`` when there is no genuine per-game folder (flat/region-
        nested layout), so the folder identity never carries a category/region.
        """
        if not self.folder_name:
            return ""
        return _strip_release_tags(self.folder_name)

    @property
    def norm_base_stem(self) -> str:
        return _norm_text(self.base_stem)

    @property
    def norm_base_folder(self) -> str:
        return _norm_text(self.base_folder)

    @property
    def folder_chain_norm(self) -> list:
        """Normalized names of every ancestor folder from the category down.

        LaunchBox image files may sit at any depth under a category; the game
        name can appear at any level (e.g. ``.../Screenshot - Game Title/<Game>/
        deep/even/deeper/img.png``). We collect every ancestor folder name so the
        scoring can match against whichever level carries the game title.

        The image *category* (``Screenshot - Game Title`` etc.) and any
        *region* folder (``United States``, ``World`` ...) are excluded, because
        they carry no per-game identity and must never be used as game titles.
        """
        names = []
        for p in self.path.parents:
            if p.name in ("Images",):
                break
            # ``p`` is a Platform directory (direct child of ``Images``): there
            # is no per-game folder above it, so stop ascending.
            if p.parent.name == "Images":
                break
            if p.name in CAT_SET_SKIP or _is_region_name(p.name):
                # Skip category + region levels; do not stop, because a genuine
                # per-game folder may still sit above a region folder.
                continue
            names.append(p.name)
        return [_norm_text(n) for n in names if n]


@dataclass
class LocalMediaResult:
    """Outcome of resolving artwork for one release group."""

    group_title: Optional[str]
    group_release_key: str
    found: bool = False
    cached_path: Optional[Path] = None
    category: Optional[str] = None
    match_method: MatchMethod = MatchMethod.NONE
    confidence: float = 0.0
    needs_manual_review: bool = False
    manual_review_reason: Optional[str] = None
    provenance: Optional[LocalMediaProvenance] = None
    # Structured per-candidate diagnostics for QA and security reviewers audit.
    candidates_evaluated: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "group_title": self.group_title,
            "group_release_key": self.group_release_key,
            "found": self.found,
            "cached_path": str(self.cached_path) if self.cached_path else None,
            "category": self.category,
            "match_method": self.match_method.value,
            "confidence": self.confidence,
            "needs_manual_review": self.needs_manual_review,
            "manual_review_reason": self.manual_review_reason,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "candidates_evaluated": list(self.candidates_evaluated),
        }


@dataclass
class ManualReviewItem:
    """An uncertain match routed to the operator curation queue."""

    group_title: Optional[str]
    group_release_key: str
    candidate_path: str
    category: str
    confidence: float
    reason: str


# --- Text normalization helpers ----------------------------------------------


def _norm_text(text: str) -> str:
    """Lowercased, punctuation/space/underscore stripped form for comparison."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# LaunchBox appends a trailing image ordinal to the *filename* (not the game
# title) in flat and region-nested layouts, e.g. ``Bubble Bobble-01.png``,
# ``Bubble Bobble-02.png``. The ordinal is a pure ``-<digits>`` suffix. We
# strip ONLY that, and ONLY when it is a trailing hyphen+digits, so genuine
# numbered/sequel titles are never damaged:
#   * "Bubble Bobble-01"  -> "Bubble Bobble"   (ordinal removed)
#   * "Bubble Bobble 2"   -> "Bubble Bobble 2" (space-separated, untouched)
#   * "1942"              -> "1942"            (no hyphen, untouched)
#   * "Bubble Bobble-1x"  -> "Bubble Bobble-1x" (letter after digits, untouched)
_LAUNCHBOX_ORDINAL_RE = re.compile(r"-[0-9]{1,3}$")


def _strip_launchbox_ordinal(name: str) -> str:
    """Remove a single trailing LaunchBox image ordinal (``-NN``) if present.

    Returns the name unchanged when there is no trailing ``-<digits>`` suffix,
    so legitimate numeric/sequel titles are preserved exactly.
    """
    if not name:
        return ""
    return _LAUNCHBOX_ORDINAL_RE.sub("", name)


# Filenames that carry NO game identity of their own (LaunchBox uses them as
# generic "this is the box/title/screenshot for the parent game" markers). When
# the immediate file stem is one of these -- or is purely numeric -- the game
# identity must be derived from the PARENT FOLDER, not the filename. Descriptive
# filenames (e.g. ``Bubble Bobble-01.png``) keep their own identity, which is
# how flat and region-nested LaunchBox layouts name games at the *file* level.
_GENERIC_IMAGE_STEMS = frozenset(
    {
        "title", "box", "front", "back", "cart", "disc", "disk", "cover",
        "art", "image", "screenshot", "game", "logo", "fanart", "clearlogo",
        "snapshot", "banner", "default", "standard", "thumb", "thumbnail",
    }
)


def _is_generic_stem(stem: str) -> bool:
    """True when ``stem`` carries no game identity (generic LaunchBox marker)."""
    s = _strip_launchbox_ordinal(stem or "").strip().lower()
    if not s:
        return True
    if s in _GENERIC_IMAGE_STEMS:
        return True
    if re.fullmatch(r"[0-9]+", s):
        return True
    return False


# Region-name folders that LaunchBox inserts between a category and the game
# image (e.g. ``.../Screenshot - Game Title/United States/<file>``). These carry
# NO game identity and must never be used as a game title or matched against a
# group title. Compared by normalized form (lower + alnum only) so spacing and
# punctuation do not matter. Only EXACT region names are excluded, so a real
# game titled ``European Soccer`` (normalized ``europeansoccer``) is never
# mistaken for the region ``europe``.
_REGION_NAMES_RAW = (
    "World", "Worldwide", "US", "USA", "U.S.", "U.S.A.", "United States",
    "United Kingdom", "UK", "Europe", "Asia", "Japan", "Australia", "Canada",
    "France", "Germany", "Italy", "Spain", "Brazil", "Netherlands",
    "Scandinavia", "Korea", "Taiwan", "China", "Russia", "Poland",
    "North America", "South America", "East Asia", "New Zealand",
)
_REGION_NAMES_NORM = frozenset(_norm_text(r) for r in _REGION_NAMES_RAW)


def _is_region_name(name: str) -> bool:
    """True when ``name`` is a LaunchBox region/territory folder (no game id)."""
    if not name:
        return False
    return _norm_text(name) in _REGION_NAMES_NORM


def _raw_eq(a: str, b: str) -> bool:
    """Case-insensitive, internal-whitespace-normalized equality of two titles.

    Collapses any internal whitespace run to a single space ("Example  Space
    Tactics" == "Example Space Tactics") without dropping release tags, so a raw
    exact-title match still distinguishes "Example Space Tactics" from its variant
    "Example Space Tactics M3".
    """
    if not a or not b:
        return False
    na = re.sub(r"\s+", " ", (a or "").strip()).lower()
    nb = re.sub(r"\s+", " ", (b or "").strip()).lower()
    return na == nb


def _strip_release_tags(title: str) -> str:
    """Strip crack/trainer/alt/lang/chipset/disk tags to a canonical base.

    Conservative by design: only whole-word tokens from the audited
    ``_RELEASE_TAG_WORDS`` list are removed, so real title words are never eaten.
    ``"Example Space Tactics M3 cr QTX alt a"`` -> ``"Example Space Tactics"``
    (the bare ``a`` here is removed only because it is the alt-marker token
    ``alt a``'s residue; a standalone ``a`` in the middle of a title is left).
    A ``Disk N`` / ``Side B`` marker and a parenthetical language tag
    (``(English)``) are removed (applied to the original string before word-tag
    stripping so the trailing number after ``Disk`` is not left behind). The
    sequels guard lives in scoring: ``"Example Space Tactics 2"`` keeps its ``2`` and
    is NOT collapsed onto the base game (scoring uses a strict length-guard).
    """
    if not title:
        return ""
    # 1) Remove disk/side markers ("Disk 2", "Side B", "Disk1") from the original,
    #    so any trailing ordinal they carried is not left dangling.
    t = _TRAILING_DISK_RE.sub(" ", title)
    # 2) Remove parenthetical language / edition annotations, e.g. "(English)".
    t = re.sub(r"\([^)]*\)", " ", t)
    # 3) Remove whole-word release tags from the audited list.
    toks = re.split(r"(\s+)", t)
    kept = []
    for tok in toks:
        if re.fullmatch(r"\s+", tok or ""):
            kept.append(tok)
            continue
        word = tok.strip().lower()
        if word in _RELEASE_TAG_WORDS:
            continue
        kept.append(tok)
    t = "".join(kept)
    # Remove "alt <letter/number>" residue that wasn't a standalone tag word.
    t = re.sub(r"\balt\s+[a-z0-9]{1,3}\b", " ", t, flags=re.IGNORECASE)
    # Drop a final lone alt-marker letter left dangling (e.g. "... QTX a").
    t = re.sub(r"\s+[a-z]\s*$", " ", t, flags=re.IGNORECASE)
    # Collapse repeated whitespace and strip.
    t = re.sub(r"\s+", " ", t).strip()
    return t


# --- Adapter protocol --------------------------------------------------------


class LocalMediaAdapter:
    """Base adapter: turns a root + platform mapping into candidates.

    Subclasses implement :meth:`iter_candidates`. The provider is adapter
    agnostic and scores uniformly.
    """

    name: str = "base"

    def iter_candidates(
        self, root: Path, platform_names: Iterable[str], *, recursive: bool,
        categories: Iterable[str] = (),
    ) -> Iterable[LocalMediaCandidate]:
        raise NotImplementedError


class LaunchBoxAdapter(LocalMediaAdapter):
    """LaunchBox image-tree adapter.

    LaunchBox stores images under::

        <root>/Images/<Platform>/<Category>/<possibly nested>/<file>

    where ``<Category>`` is one of the configured ``preferred_image_types``
    (e.g. ``"Screenshot - Game Title"``). We match by the *folder name* so we
    never rely on LaunchBox's internal path depth, and we recurse through any
    nested subfolders when ``recursive`` is set.
    """

    name = "launchbox"

    def iter_candidates(
        self, root: Path, platform_names: Iterable[str], *, recursive: bool,
        categories: Iterable[str] = (),
    ) -> Iterable[LocalMediaCandidate]:
        root = Path(root)
        if not root.is_dir():
            return
        images_dir = root / "Images"
        if not images_dir.is_dir():
            return
        platforms = set(platform_names)
        cat_set = list(categories)
        for platform in platforms:
            pdir = images_dir / platform
            if not pdir.is_dir():
                continue
            yield from self._walk_categories(pdir, recursive=recursive, categories=cat_set)

    def _resolve_game_folder(self, parent: Path, category: str) -> Optional[str]:
        """Return the genuine per-game folder for an image, or ``None``.

        LaunchBox stores the game name at the *folder* level, the *file* level,
        or both. The immediate parent of an image is a game title ONLY when it
        is neither the image category nor a region/territory folder. When it is
        the category (flat layout ``<cat>/<Game>-NN.png``) or a region (region-
        nested ``<cat>/<Region>/<file>``), the game title is NOT the parent, and
        we ascend toward the category to find the nearest folder that actually
        names a game.

        Returns ``None`` when no genuine per-game folder could be resolved
        (e.g. flat layout with ``<cat>/<Region>/<generic>.png``), in which case
        the caller derives the game identity from the filename stem instead.
        """
        if not parent or not category:
            return None
        # Immediate parent is a game title if it is not the category and not a
        # region folder.
        if parent.name != category and not _is_region_name(parent.name):
            return parent.name
        # Immediate parent IS the category or a region: the per-game name is not
        # this level. Ascend toward the category to find a genuine per-game
        # folder sitting above a region. Stop at the category or the platform
        # directory (direct child of ``Images``) -- neither is a game title, so
        # we return ``None`` and let the filename stem carry the identity.
        node = parent.parent
        while node and node.name != category and node.parent.name != node.name:
            if _is_region_name(node.name) or node.parent.name == "Images":
                # Another region level, or we reached the platform dir just under
                # Images: stop -- there is no genuine per-game folder.
                return None
            return node.name
        return None

    def _walk_categories(self, platform_dir: Path, *, recursive: bool,
                         categories: Iterable[str]):
        """Enumerate image files and attribute each to its image *category*.

        LaunchBox layout is ``<Category>/<Game>/<file>`` (the image type is an
        ancestor folder, the game is the immediate parent). We therefore search
        for the nearest ancestor folder whose name is a recognized category and
        use that as ``category``; the immediate parent (game folder) is recorded
        as ``game_folder``. This is robust to extra nesting depth and does not
        depend on the category being a direct child of the platform dir.

        Confinement: the LaunchBox root is treated as UNTRUSTED. Every candidate
        is resolved to its real (symlink/hardlink-free) path and skipped unless
        it lies *within* the configured root, and only regular files are
        accepted (symlinks, device nodes, FIFOs, and sockets are rejected). A
        candidate whose real path escapes the root -- e.g. a symlink inside the
        tree pointing at a file elsewhere on disk -- is never indexed.
        """
        cat_set = set(categories)
        root_resolved = platform_dir.parent.parent.resolve()
        if recursive:
            iterator = platform_dir.rglob("*")
        else:
            iterator = platform_dir.glob("*")
        for entry in iterator:
            # Resolve symlinks/hardlinks to the real path; the (untrusted) tree
            # may contain links that escape the configured root.
            try:
                real = entry.resolve()
            except OSError:
                continue
            # Reject anything whose real path escapes the configured root.
            if not real.is_relative_to(root_resolved):
                continue
            # Reject non-regular files (symlinks, device nodes, FIFOs, sockets).
            # os.lstat on the resolved path reports the final target's type; a
            # plain is_file() would follow the link and could open a special
            # file or disclose out-of-root content.
            try:
                st = os.lstat(real)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if entry.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            # Walk ancestors from parent upward to find the category folder.
            category = None
            node = entry.parent
            while node != platform_dir and node != node.parent:
                if node.name in cat_set:
                    category = node.name
                    break
                node = node.parent
            if category is None:
                # Not under a recognized category; skip (cannot be prioritized).
                continue
            # Resolve the genuine per-game folder. The immediate parent is only
            # a game title when it is NOT the category and NOT a region folder;
            # otherwise we ascend toward the category and use the nearest folder
            # that actually names the game. Flat and region-nested LaunchBox
            # layouts (``<cat>/<Game>-NN.png`` and ``<cat>/<Region>/<file>``)
            # therefore yield the correct game identity instead of the
            # category/region name.
            game_folder = self._resolve_game_folder(entry.parent, category)
            yield LocalMediaCandidate(
                path=entry,
                category=category,
                root=platform_dir.parent.parent,
                game_folder=game_folder,
            )


class TypedMediaRootAdapter(LocalMediaAdapter):
    """Adapter for issue #33 typed media roots.

    A typed media root is a directory whose ENTIRE image set belongs to one
    explicit LaunchBox asset type (the GUI records ``asset_type`` per mapping,
    e.g. ``"Box - Front"``). The root's own folder name is NOT a category and
    must not be mistaken for one: every image under the root is attributed to
    the configured ``asset_type``.

    Per-game identity resolution reuses the LaunchBox conventions: the
    immediate parent folder is a game title when it is not a region and not a
    recognized category (per-game subfolders inside the root are respected);
    otherwise the filename stem carries the identity (flat root layout).

    Confinement is identical to :class:`LaunchBoxAdapter`: the root is treated
    as UNTRUSTED, every candidate's real path must stay inside the root, and
    only regular files are accepted.
    """

    name = "typed_media_root"

    def iter_candidates(
        self, root: Path, platform_names: Iterable[str], *, recursive: bool,
        categories: Iterable[str] = (),
    ) -> Iterable[LocalMediaCandidate]:
        root = Path(root)
        if not root.is_dir():
            return
        # The configured asset type is the FIRST element of ``categories`` when
        # the provider passes the single-type list it builds per typed root;
        # fall back to the root's own name only for a direct (non-provider)
        # call, which never happens in production paths.
        asset_type = next(iter(categories), None) or root.name
        root_resolved = root.resolve()
        iterator = root.rglob("*") if recursive else root.glob("*")
        for entry in iterator:
            try:
                real = entry.resolve()
            except OSError:
                continue
            if not real.is_relative_to(root_resolved):
                continue
            try:
                st = os.lstat(real)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if entry.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            parent = entry.parent
            if parent.name != root.name and not _is_region_name(parent.name) \
                    and parent.name not in CAT_SET_SKIP:
                game_folder = parent.name
            else:
                game_folder = None
            yield LocalMediaCandidate(
                path=entry,
                category=asset_type,
                root=root,
                game_folder=game_folder,
            )


# --- Provider ----------------------------------------------------------------


class LocalMediaProvider:
    """Read-only, offline, stdlib-only local-media artwork provider.

    Usage::

        cfg = load_local_media_config(config_path)
        if cfg.enabled:
            provider = LocalMediaProvider(cfg, cache_dir)
            provider.discover()                      # read-only walk
            result = provider.resolve(group)         # find + cache if confident
    """

    def __init__(
        self,
        config: LocalMediaConfig,
        cache_dir: Path,
        *,
        adapter: Optional[LocalMediaAdapter] = None,
        max_image_bytes: int = 25_000_000,
    ) -> None:
        if not config.enabled:
            raise LocalMediaDisabled("local_media provider is disabled in config")
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.adapter = adapter or LaunchBoxAdapter()
        self.max_image_bytes = max_image_bytes
        # Flat candidate index, populated by discover().
        self._index: list[LocalMediaCandidate] = []
        self._discovered = False

    # -- discovery (read-only) ------------------------------------------------

    def discover(self) -> int:
        """Walk every configured root and build the candidate index.

        Strictly read-only: only ``is_dir`` / directory enumeration is used.
        Returns the number of candidate images discovered.

        Scans, in order: (1) the legacy ``roots`` (LaunchBox image-tree
        layout, existing adapter) and (2) the issue #33 typed ``media_roots``
        (each root's whole image set belongs to its explicit asset type).
        Missing roots are skipped (they are retained in config and surfaced
        by :func:`scan_launchbox_roots`, never deleted).
        """
        self._index = []
        for root in self.config.roots:
            rpath = Path(root)
            try:
                for cand in self.adapter.iter_candidates(
                    rpath,
                    self.config.platform_names,
                    recursive=self.config.recursive,
                    categories=self.config.preferred_image_types,
                ):
                    self._index.append(cand)
            except OSError:
                # A single unreadable root must not abort the whole run.
                continue
        typed_adapter = TypedMediaRootAdapter()
        for media_root in self.config.media_roots:
            try:
                for cand in typed_adapter.iter_candidates(
                    Path(media_root.path),
                    self.config.platform_names,
                    recursive=self.config.recursive,
                    categories=(media_root.asset_type,),
                ):
                    self._index.append(cand)
            except OSError:
                # A single unreadable root must not abort the whole run.
                continue
        self._discovered = True
        return len(self._index)

    # -- resolution ----------------------------------------------------------

    def resolve(self, group) -> LocalMediaResult:
        """Find the best artwork for ``group`` and copy it into the cache.

        Returns a :class:`LocalMediaResult`. On a confident match the selected
        source is copied into ``cache_dir`` and a provenance sidecar is written
        next to it. On an uncertain match the result is flagged for manual
        review and NOTHING is copied. Never modifies the source library.
        """
        if not self.config.enabled:
            raise LocalMediaDisabled("local_media provider is disabled in config")
        if not self._discovered:
            self.discover()

        title = getattr(group, "title", None)
        release_key = getattr(group, "release_key", "") or ""
        result = LocalMediaResult(
            group_title=title, group_release_key=release_key
        )

        best = self._select(group)
        result.candidates_evaluated = best.evaluated

        if best.candidate is None:
            return result

        method = best.method
        conf = best.confidence

        # Manual review bucket: fuzzy below the auto-accept floor.
        if method in (MatchMethod.FUZZY_MANUAL, MatchMethod.MANUAL_REVIEW):
            result.needs_manual_review = True
            result.manual_review_reason = best.reason
            result.match_method = method
            result.confidence = conf
            result.category = best.candidate.category
            return result

        # Confident enough to cache.
        if conf < self.config.confidence_threshold:
            result.needs_manual_review = True
            result.manual_review_reason = (
                f"confidence {conf:.3f} below threshold "
                f"{self.config.confidence_threshold:.3f}"
            )
            result.match_method = MatchMethod.MANUAL_REVIEW
            result.confidence = conf
            result.category = best.candidate.category
            return result

        cached = self._cache_candidate(best.candidate, method, best.confidence)
        result.found = True
        result.cached_path = cached.path
        result.category = best.candidate.category
        result.match_method = method
        result.confidence = conf
        result.provenance = cached.provenance
        return result

    # -- selection / scoring -------------------------------------------------

    def _select(self, group):
        """Return the highest-priority confident candidate for ``group``.

        Honors exact category priority: every candidate in category 1 is scored
        before category 2 is considered. Returns a small record
        ``(candidate, method, confidence, evaluated, reason)``.
        """

        @dataclass
        class _Pick:
            candidate: Optional[LocalMediaCandidate] = None
            method: MatchMethod = MatchMethod.NONE
            confidence: float = 0.0
            evaluated: list = field(default_factory=list)
            reason: Optional[str] = None

        pick = _Pick()
        evaluated: list = []

        # Precompute the group's matching identities.
        identities = self._group_identities(group)

        # Iterate categories in strict priority order. For each category we
        # score ALL its candidates first; only if none is confident do we move
        # on. A confident hit in an earlier category wins immediately.
        for category in self.config.preferred_image_types:
            cat_cands = [c for c in self._index if c.category == category]
            if not cat_cands:
                continue
            cat_cands.sort(key=lambda c: str(c.path))
            for cand in cat_cands:
                method, score = self._score(cand, identities)
                diag = {
                    "path": str(cand.path),
                    "category": cand.category,
                    "method": method.value,
                    "score": round(score, 4),
                    "norm_stem": cand.norm_stem,
                }
                evaluated.append(diag)
                if method != MatchMethod.NONE and score >= self.config.confidence_threshold:
                    pick.candidate = cand
                    pick.method = method
                    pick.confidence = score
                    pick.evaluated = evaluated
                    return pick
            # Finished current category with no confident match; advance.

        # No confident hit. If we ever had a fuzzy candidate worth review,
        # surface the best one for manual review (deterministic: highest score).
        best_fuzzy = None
        best_fuzzy_score = 0.0
        for cand in self._index:
            method, score = self._score(cand, identities)
            if method in (MatchMethod.FUZZY, MatchMethod.FUZZY_MANUAL) and score > best_fuzzy_score:
                best_fuzzy = cand
                best_fuzzy_score = score
        if best_fuzzy is not None:
            pick.candidate = best_fuzzy
            pick.method = MatchMethod.FUZZY_MANUAL
            pick.confidence = best_fuzzy_score
            pick.reason = (
                f"fuzzy candidate {best_fuzzy.path.name!r} scored "
                f"{best_fuzzy_score:.3f} (below auto-accept floor)"
            )
            pick.evaluated = evaluated
            return pick

        pick.evaluated = evaluated
        return pick

    def _group_identities(self, group) -> dict:
        """Derive the match identities for a release group."""
        title = (getattr(group, "title", None) or "").strip()
        disk_stems = []
        for rec in getattr(group, "records", []) or []:
            fn = getattr(rec, "source_filename", None)
            if fn:
                disk_stems.append(Path(fn).stem)
        base_title = _strip_release_tags(title)
        return {
            "title": title,
            "norm_title": _norm_text(title),
            "norm_base_title": _norm_text(base_title),
            "disk_stems": [s for s in disk_stems if s],
            "norm_disk_stems": {_norm_text(s) for s in disk_stems if s},
        }

    def _cand_identities(self, cand: LocalMediaCandidate) -> dict:
        """Folder + file identities a candidate may match against."""
        # All ancestor folder names (any depth) may carry the game title.
        folder_chain = cand.folder_chain_norm
        # Same chain with only trailing LaunchBox ordinals stripped from each
        # level (so ``<cat>/Bubble Bobble-01/<file>`` matches ``bubblebobble``).
        ordinal_chain = [
            _norm_text(_strip_launchbox_ordinal(n)) for n in folder_chain
        ]
        return {
            "raw_stem": cand.path.stem,
            "raw_folder": cand.folder_name,
            "norm_stem": cand.norm_stem,
            "norm_folder": cand.norm_folder,
            "raw_ordinal_stem": cand.raw_ordinal_stem,
            "raw_ordinal_folder": cand.raw_ordinal_folder,
            "norm_ordinal_stem": cand.norm_ordinal_stem,
            "norm_ordinal_folder": cand.norm_ordinal_folder,
            "norm_base_stem": cand.norm_base_stem,
            "norm_base_folder": cand.norm_base_folder,
            "folder_chain": folder_chain,
            "ordinal_chain": ordinal_chain,
            "norm_base_chain": [_norm_text(_strip_release_tags(n)) for n in folder_chain],
        }

    def _score(self, cand: LocalMediaCandidate, identities: dict):
        """Score ``cand`` against a group's identities.

        Returns ``(method, score)``. ``method == NONE`` means no match.
        Order follows local-media provider requirement 5 exactly. The candidate's filename
        stem, its immediate parent folder, and ANY ancestor folder in the path
        (LaunchBox nests arbitrarily deep) are considered at each tier.
        """
        title = identities["title"]
        ci = self._cand_identities(cand)
        chain = ci["folder_chain"]
        base_chain = ci["norm_base_chain"]
        # 1) exact canonical game title (raw; folder or file level, any ancestor).
        if title and (
            _raw_eq(ci["raw_stem"], title)
            or _raw_eq(ci["raw_folder"], title)
            or any(_raw_eq(f, title) for f in chain)
        ):
            return MatchMethod.EXACT_CANONICAL, 1.0
        # 2) exact original ROM/disk filename stem (file level only).
        if ci["norm_stem"] in identities["norm_disk_stems"]:
            return MatchMethod.EXACT_DISK_STEM, 1.0
        # 3) normalized title (punctuation + separators removed; folder or file).
        if identities["norm_title"] and (
            ci["norm_stem"] == identities["norm_title"]
            or ci["norm_folder"] == identities["norm_title"]
            or identities["norm_title"] in chain
        ):
            return MatchMethod.NORMALIZED_TITLE, 0.99
        # 3b) normalized title WITH the trailing LaunchBox ordinal stripped
        # (flat / region-nested layouts name the game at the file or folder
        # level as ``Bubble Bobble-01`` / ``Bubble Bobble-01``). Only the
        # trailing ``-<digits>`` ordinal is removed, so genuine numbered/sequel
        # titles (``Bubble Bobble 2``, ``1942``) are unaffected. Category and
        # region names are excluded from the folder/chain identities upstream,
        # so they can never be matched as a game title here.
        if identities["norm_title"] and (
            ci["norm_ordinal_stem"] == identities["norm_title"]
            or ci["norm_ordinal_folder"] == identities["norm_title"]
            or identities["norm_title"] in ci["ordinal_chain"]
        ):
            return MatchMethod.NORMALIZED_TITLE, 0.99
        # 4) canonical-title reuse across cracks/trainers/alt-dumps/language/
        #    chipset/multi-disk variants (release tags stripped from both).
        if identities["norm_base_title"] and (
            ci["norm_base_stem"] == identities["norm_base_title"]
            or ci["norm_base_folder"] == identities["norm_base_title"]
            or identities["norm_base_title"] in base_chain
        ):
            return MatchMethod.CANONICAL_REUSE, 0.97
        # 5) carefully scored fuzzy match (guarded against false merges).
        if identities["norm_title"]:
            score = self._fuzzy_score(cand, identities)
            if score >= FUZZY_MIN_RATIO:
                if score >= AUTO_ACCEPT_MIN_CONF:
                    return MatchMethod.FUZZY, score
                return MatchMethod.FUZZY_MANUAL, score
        return MatchMethod.NONE, 0.0

    def _fuzzy_score(self, cand: LocalMediaCandidate, identities: dict) -> float:
        """Best difflib ratio vs the group's title / disk stems / folder names.

        False-merge guard: if the candidate is a strict *extension* of the game
        title (e.g. a sequel like ``Example Space Tactics 2``), it is NOT a match
        for the base game -- return 0.0 so it cannot auto- or manually-merge.
        Considers both the filename stem and the parent folder name.
        """
        game = identities["norm_title"]
        if not game:
            return 0.0
        # Consider both the filename stem and the parent folder name for fuzzy,
        # including the ordinal-stripped variants (so ``Bubble Bobble-01`` still
        # fuzzy-matches a game titled ``Bubble Bobble`` even when not an exact
        # normalized hit).
        ci = self._cand_identities(cand)
        cand_norms = [
            ci["norm_stem"],
            ci["norm_folder"],
            ci["norm_ordinal_stem"],
            ci["norm_ordinal_folder"],
        ]
        if not any(cand_norms):
            return 0.0
        best = 0.0
        for c_norm in cand_norms:
            if not c_norm:
                continue
            # Extension / inclusion guards (strict, both directions).
            if c_norm.startswith(game) and len(c_norm) > len(game):
                return 0.0
            if game.startswith(c_norm) and len(game) > len(c_norm):
                return 0.0
            for ref in (game, *identities["norm_disk_stems"]):
                if not ref:
                    continue
                r = difflib.SequenceMatcher(None, c_norm, ref).ratio()
                if r > best:
                    best = r
        return best

    # -- caching (write only into app-owned cache) ----------------------------

    def _cache_candidate(self, cand: LocalMediaCandidate, method: MatchMethod, confidence: float):
        """Copy the source read-only into the app cache and write provenance.

        The source is opened read-only (``"rb"``). The destination lives under
        ``self.cache_dir`` (app-owned); never under any LaunchBox root.
        """
        import datetime

        # Defense-in-depth: even after discovery confinement, re-verify the
        # resolved source stays within its configured root and is a regular
        # file before opening it. Discovery may have been bypassed (e.g.
        # index built elsewhere), or the tree may have changed between
        # discover() and resolve().
        root_resolved = Path(cand.root).resolve()
        try:
            src_real = cand.path.resolve()
            if not src_real.is_relative_to(root_resolved):
                raise LocalMediaError(
                    f"refusing to read source outside configured root: {cand.path}"
                )
            if not stat.S_ISREG(os.lstat(src_real).st_mode):
                raise LocalMediaError(
                    f"refusing to read non-regular file: {cand.path}"
                )
        except OSError as exc:
            raise LocalMediaError(
                f"source unavailable for copy: {cand.path}: {exc}"
            ) from exc

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        dest = self.cache_dir / f"{cand.path.stem}{cand.path.suffix.lower()}"
        # Deterministic de-dupe by source hash avoids clobbering distinct files
        # that happen to share a stem; fall back with a short hash suffix.
        source_sha = _sha256_file(cand.path)
        if dest.exists():
            if _sha256_file(dest) == source_sha:
                pass  # already cached identically
            else:
                dest = self.cache_dir / f"{cand.path.stem}.{source_sha[:8]}{cand.path.suffix.lower()}"

        # Bounded, DoS-safe copy from the source library into app cache.
        #
        # The safety cap must be enforced BEFORE any allocation: stat the
        # source (not S_ISREG) and compare st_size. A symlink to a multi-GB
        # file, /dev/zero, or any special file reports st_size == 0 yet reads
        # unbounded bytes, so non-regular files are rejected up front. Only
        # after the cap passes do we stream the bytes through, never
        # materializing the whole source solely to measure it.
        st = cand.path.stat()
        if not stat.S_ISREG(st.st_mode):
            raise LocalMediaError(
                f"source is not a regular file (refusing to read): {cand.path}"
            )
        if st.st_size > self.max_image_bytes:
            raise LocalMediaError(
                f"source image exceeds safety cap: {cand.path} "
                f"({st.st_size} > {self.max_image_bytes})"
            )
        # Stream the (already-bounded) source into the cache without holding
        # the full file in memory. shutil.copyfileobj caps each chunk at
        # COPY_BUFSIZE (typically 1 MiB) regardless of source size.
        with open(cand.path, "rb") as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        with open(dest, "rb") as fh:
            cached_sha = hashlib.sha256(fh.read()).hexdigest()

        cached_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Privacy: the durable sidecar must not embed absolute, host-specific
        # paths (T6.3 checklist item 5). Persist every path RELATIVE to its
        # root, never an absolute /home/<user> layout. The in-memory
        # LocalMediaResult.cached_path (used by enrich.py as a live path) stays
        # absolute; only the persisted provenance is sanitized.
        source_rel = _relative_to_root(cand.path, cand.root)
        cached_rel = _relative_to_root(dest, self.cache_dir)
        provenance = LocalMediaProvenance(
            source_path=source_rel,
            source_sha256=source_sha,
            category=cand.category,
            match_method=method.value,
            confidence=confidence,
            cached_path=cached_rel,
            cached_sha256=cached_sha,
            cached_at=cached_at,
            # The configured root itself is referenced opaquely as ".", never
            # an absolute path. A consumer that knows the root can re-anchor.
            root=".",
        )
        sidecar = dest.with_suffix(dest.suffix + ".prov.json")
        sidecar.write_text(
            json.dumps(provenance.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return _Cached(path=dest, provenance=provenance)


@dataclass
class _Cached:
    path: Path
    provenance: LocalMediaProvenance


# --- Config loader -----------------------------------------------------------


def load_local_media_config(config_path) -> LocalMediaConfig:
    """Return the ``[local_media]`` table from a TOML config as a typed config.

    Preserves the existing precedence chain in :mod:`paths`: only the file at
    ``config_path`` is consulted here (the caller decides which file wins).
    Returns a disabled config when the table is absent.
    """
    try:
        import tomllib
    except ModuleNotFoundError as exc:  # pragma: no cover - 3.11+ always has it
        raise LocalMediaError(
            "local_media config requires Python 3.11+ (tomllib)"
        ) from exc

    config_path = Path(config_path)
    if not config_path.is_file():
        return LocalMediaConfig(enabled=False)
    try:
        with open(config_path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:  # malformed TOML
        raise LocalMediaError(f"could not read config {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        return LocalMediaConfig(enabled=False)
    return LocalMediaConfig.from_dict(data.get("local_media"))


def load_mobygames_config(config_path) -> MobyGamesConfig:
    """Return the ``[mobygames]`` table from a TOML config as a typed config.

    The MobyGames provider is OPTIONAL and DISABLED BY DEFAULT. A missing file
    or an absent ``[mobygames]`` table yields a disabled config (no-op), so the
    base app is unaffected. The API key itself is never read here -- only the
    name of the environment variable holding it (``api_key_env``).
    """
    try:
        import tomllib
    except ModuleNotFoundError as exc:  # pragma: no cover - 3.11+ always has it
        raise LocalMediaError(
            "mobygames config requires Python 3.11+ (tomllib)"
        ) from exc

    config_path = Path(config_path)
    if not config_path.is_file():
        return MobyGamesConfig(enabled=False)
    try:
        with open(config_path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:  # malformed TOML
        raise LocalMediaError(f"could not read config {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        return MobyGamesConfig(enabled=False)
    return MobyGamesConfig.from_dict(data.get("mobygames"))


def _relative_to_root(path: Path, root: Path) -> str:
    """Return ``path`` expressed RELATIVE to ``root`` as a POSIX string.

    Used for the durable provenance sidecar so it never embeds an absolute,
    host-specific path (T6.3 checklist item 5: no ``/home/<user>`` leak). A
    consumer that knows the configured root can re-anchor; the relative form
    carries no operator-environment disclosure on its own.

    Falls back defensively to the bare filename if ``path`` is not under
    ``root`` (should not happen for source/cache paths in normal operation).
    """
    path = Path(path)
    root = Path(root)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_read_only_roots(config: LocalMediaConfig) -> None:
    """Stat-only read-only proof: confirm each configured root is stat-able.

    The authoritative read-only guarantee is enforced by the provider itself:
    source images are opened with ``"rb"`` and are only ever copied INTO the
    application's own cache directory (see ``LocalMediaProvider._cache_candidate``
    and the design doc). This function is a *lightweight pre-flight visibility
    check*, not a file open: it ``os.stat``s each root and raises
    :class:`LocalMediaError` on the first root it cannot stat (e.g. a parent
    directory the process cannot traverse). It proves the root **exists and is
    visible to this process** — NOT that the process could open it read-only, and
    NOT that the contents are write-safe. A missing root is skipped (no error).

    Callers and tests (QA and security review) use this as an auditable
    existence/visibility receipt; the real read-only proof is the ``"rb"`` open of
    individual source images elsewhere in the provider.

    Issue #33: the check now covers the typed ``media_roots`` and ``manual_roots``
    as well as the legacy ``roots``.
    """
    for root in tuple(config.roots) + tuple(m.path for m in config.media_roots) \
            + tuple(m.path for m in config.manual_roots):
        rpath = Path(root)
        try:
            rpath.stat()
        except FileNotFoundError:
            # Absent root: nothing to prove against; skip (preserves prior behavior).
            continue
        except OSError as exc:
            raise LocalMediaError(
                f"local-media root not readable (read-only proof failed): {rpath}: {exc}"
            ) from exc


# --- Issue #33: LaunchBox root diagnostics + manual discovery -----------------


@dataclass(frozen=True)
class RootStatus:
    """Diagnostics entry for one configured image or manual root."""

    path: str
    kind: str  # "media" | "manual" | "legacy"
    asset_type: str  # empty for manual/legacy roots
    status: str  # "ok" | "missing"
    file_count: int  # candidate images (media) or manual files (manual)


@dataclass(frozen=True)
class LaunchboxScanReport:
    """Read-only scan report over all configured LaunchBox roots (issue #33).

    Diagnostics only: identifies which configured roots were scanned
    (``status == "ok"``) and which are currently missing or inaccessible
    (``status == "missing"`` — retained in config, never deleted).
    """

    roots: tuple[RootStatus, ...]

    @property
    def scanned_media_roots(self) -> tuple[RootStatus, ...]:
        return tuple(r for r in self.roots if r.kind == "media" and r.status == "ok")

    @property
    def scanned_manual_roots(self) -> tuple[RootStatus, ...]:
        return tuple(r for r in self.roots if r.kind == "manual" and r.status == "ok")

    @property
    def missing_roots(self) -> tuple[RootStatus, ...]:
        return tuple(r for r in self.roots if r.status == "missing")

    @property
    def total_image_candidates(self) -> int:
        return sum(r.file_count for r in self.roots if r.kind in ("media", "legacy"))

    @property
    def total_manual_files(self) -> int:
        return sum(r.file_count for r in self.roots if r.kind == "manual")

    def to_lines(self) -> list[str]:
        """Human-readable diagnostics lines (deterministic order)."""
        lines = []
        for r in self.roots:
            if r.status == "missing":
                lines.append(
                    f"LaunchBox root not found (kept in config): {r.path}"
                    + (f" [{r.asset_type}]" if r.asset_type else "")
                )
                continue
            if r.kind == "media":
                lines.append(
                    f"LaunchBox media root ok: {r.path} "
                    f"[{r.asset_type}] ({r.file_count} image candidate(s))"
                )
            elif r.kind == "manual":
                lines.append(
                    f"LaunchBox manual root ok: {r.path} "
                    f"({r.file_count} manual file(s))"
                )
            else:
                lines.append(
                    f"Local media root ok: {r.path} "
                    f"({r.file_count} image candidate(s))"
                )
        return lines


def _count_files_under(
    base: Path, suffixes: frozenset[str], *, recursive: bool,
) -> int:
    """Read-only count of regular files with ``suffixes`` under ``base``.

    Symlink-safe: a candidate must resolve back inside ``base`` and be a
    regular file (mirrors the adapter confinement rules). Never opens a file.
    """
    count = 0
    if not base.is_dir():
        return 0
    try:
        base_resolved = base.resolve()
    except OSError:
        return 0
    iterator = base.rglob("*") if recursive else base.glob("*")
    for entry in iterator:
        try:
            real = entry.resolve()
        except OSError:
            continue
        if not real.is_relative_to(base_resolved):
            continue
        try:
            st = os.lstat(real)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if entry.suffix.lower() in suffixes:
            count += 1
    return count


def scan_launchbox_roots(
    config: LocalMediaConfig, *, recursive: bool = True
) -> LaunchboxScanReport:
    """Read-only diagnostics scan of all configured LaunchBox roots.

    For every legacy ``roots`` entry, typed ``media_roots`` entry, and
    ``manual_roots`` entry: report ``ok``/``missing`` status plus a candidate
    count (images for media roots, ``.pdf``/``.txt`` files for manual roots).

    Pure stdlib, stat + ``os.scandir`` only: no file contents are read, no
    writes happen, and no socket is ever opened. Missing roots are reported,
    NEVER deleted from the config (issue #33 acceptance criterion 7).
    """
    entries: list[RootStatus] = []
    for root in config.roots:
        base = Path(root)
        ok = base.is_dir()
        entries.append(
            RootStatus(
                path=root,
                kind="legacy",
                asset_type="",
                status="ok" if ok else "missing",
                file_count=_count_files_under(base, IMAGE_SUFFIXES, recursive=recursive)
                if ok
                else 0,
            )
        )
    for media_root in config.media_roots:
        base = Path(media_root.path)
        ok = base.is_dir()
        entries.append(
            RootStatus(
                path=media_root.path,
                kind="media",
                asset_type=media_root.asset_type,
                status="ok" if ok else "missing",
                file_count=_count_files_under(base, IMAGE_SUFFIXES, recursive=recursive)
                if ok
                else 0,
            )
        )
    for manual_root in config.manual_roots:
        base = Path(manual_root.path)
        ok = base.is_dir()
        entries.append(
            RootStatus(
                path=manual_root.path,
                kind="manual",
                asset_type="",
                status="ok" if ok else "missing",
                file_count=_count_files_under(base, MANUAL_SUFFIXES, recursive=recursive)
                if ok
                else 0,
            )
        )
    return LaunchboxScanReport(roots=tuple(entries))


@dataclass(frozen=True)
class ManualSource:
    """One discovered manual document (PDF/TXT) from a manual root."""

    path: Path
    root: Path
    suffix: str  # ".pdf" | ".txt"


def discover_manuals(
    manual_roots: Iterable[ManualRoot], *, recursive: bool = True
) -> list[ManualSource]:
    """Read-only discovery of ``.pdf``/``.txt`` manuals under manual roots.

    Returns a deterministic (path-sorted) list of :class:`ManualSource`.
    Symlink-safe and confined to each root, exactly like the image adapters:
    a candidate whose real path escapes the root is skipped, and only regular
    files are accepted. Never writes, never opens file contents, no network.
    """
    out: list[ManualSource] = []
    for manual_root in manual_roots:
        base = Path(manual_root.path)
        if not base.is_dir():
            continue
        try:
            base_resolved = base.resolve()
        except OSError:
            continue
        iterator = base.rglob("*") if recursive else base.glob("*")
        for entry in iterator:
            try:
                real = entry.resolve()
            except OSError:
                continue
            if not real.is_relative_to(base_resolved):
                continue
            try:
                st = os.lstat(real)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if entry.suffix.lower() in MANUAL_SUFFIXES:
                out.append(
                    ManualSource(
                        path=entry, root=base, suffix=entry.suffix.lower()
                    )
                )
    out.sort(key=lambda m: (str(m.root), str(m.path)))
    return out

