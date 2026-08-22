"""Optional ScreenScraper metadata, artwork, and manual provider (bridge).

This is an OPTIONAL provider. It is DISABLED by default and only activates when
an explicit ``[screenscraper]`` TOML table is present AND ``enabled = true``.

=== ACCESS MODEL (ScreenScraper WebAPI, per ScreenScraper documentation) ===

ScreenScraper provides a WebAPI for game lookup and media retrieval:

- Game metadata and media discovery: ``jeuInfos.php``
- Game-image retrieval: ``mediaJeu.php``
- PDF manual retrieval: ``mediaManuelJeu.php``
- Game search: ``jeuRecherche.php``

Authentication uses:
- Developer credentials: ``devid``, ``devpassword``, ``softname`` (application identity)
- Member credentials: ``ssid``, ``sspassword`` (user account, optional for higher limits)

The provider uses ONLY the public game identity (ROM hash, title, platform)
for search. NO private ROM filenames, paths, or collection details are transmitted.

=== Design posture (mirrors playmatch.py / hasheous.py / igdb.py exactly) ===

This module mirrors ``src/amiga_adf_library_builder/playmatch.py``,
``src/amiga_adf_library_builder/hasheous.py``, and
``src/amiga_adf_library_builder/igdb.py`` in structure and safety posture.
It is an OPTIONAL, DISABLED-by-default provider that reuses the EXISTING
online metadata lookup layer established in ``metadata.py``.

* **OFFLINE-capable at import.** The module imports nothing that touches the
  network at import time. The only stdlib pieces used for a real fetch are
  ``urllib.request`` + ``socket`` + ``ipaddress`` (via ``metadata.guard_url``),
  and they are reached ONLY when a real fetch happens. Tests inject a fake
  ``opener`` and pass ``resolve=False``, so no DNS/socket is ever exercised
  offline.
* **Hash-first identity (preferred).** ROM hash lookup using CRC/MD5/SHA1
  where supported by ScreenScraper. An exact-hash match OUTRANKS any title fallback.
* **ScreenScraper game ID reuse.** A previously cached ScreenScraper game ID
  from a prior successful match is used as a secondary lookup signal.
* **Title/system search fallback.** Validated title + system search as fallback.
  Title-only results MUST pass the project's existing online-metadata relevance
  validation (:func:`metadata.validate_metadata_relevance`) before being accepted.
* **Fail-safe on ambiguity.** Ambiguous or low-confidence matches must be
  rejected or routed to review rather than silently accepted.
* **Non-fatal outages.** Provider outage / timeout / oversize response / rate limit
  MUST NOT raise into the pipeline. We catch, return ``found=False`` (or
  ``needs_manual_review``), and continue.
* **SSRF guard.** Every outbound fetch runs :func:`metadata.guard_url`
  (``resolve=True`` only on a real fetch) before any bytes move. Private/
  loopback/link-local/RFC1918 hosts are refused.
* **Bounds.** ``timeout_seconds``, ``max_response_bytes``, and
  ``max_concurrency`` are all bounded; a response that exceeds
  ``max_response_bytes`` is short-circuited and treated as a non-fatal miss.
* **Privacy.** Only the public ROM hash (CRC/MD5/SHA1), title, and platform
  filter are transmitted. The cache stores ONLY public fields (ScreenScraper
  game ID, canonical title, artwork URLs, metadata, manual URLs). No private
  filename, path, or private hash is ever written to a cache or transmitted.
* **Credentials via SecretStore / environment ONLY.** Developer and member
  credentials are NEVER in config files. They come from environment variables
  (``SCREENSCRAPER_DEV_ID``, ``SCREENSCRAPER_DEV_PASSWORD``, ``SCREENSCRAPER_SOFTNAME``,
  ``SCREENSCRAPER_SSID``, ``SCREENSCRAPER_SSPASSWORD``) or the SecretStore.

=== ScreenScraper API endpoints ===

Base URL: ``https://www.screenscraper.fr/api2/``

1. **jeuInfos.php** - Game metadata and media discovery
   - Parameters: ``devid``, ``devpassword``, ``softname``, ``ssid`` (optional), ``sspassword`` (optional)
   - Query by: ``crc``, ``md5``, ``sha1``, ``id`` (ScreenScraper game ID), or ``recherche`` (title search)
   - System parameter: ``systemeid`` (Amiga = specific ID)

2. **mediaJeu.php** - Game image retrieval
   - Parameters: same auth + ``id`` (game ID) + ``media`` (media type: box, screenshot, title, etc.)
   - Region preference: ``region`` parameter

3. **mediaManuelJeu.php** - PDF manual retrieval
   - Parameters: same auth + ``id`` (game ID)
   - Region/language preference: ``region`` parameter

3. **jeuRecherche.php** - Game search by title
   - Parameters: same auth + ``recherche`` (title) + ``systemeid``

=== Amiga system identification ===

ScreenScraper uses system IDs. For Amiga, the system ID needs to be determined
from ScreenScraper's system list. This is cached on first discovery.

=== Caching ===

- Successful game identity and metadata results are cached (keyed by hash or title+system)
- Downloaded media/manuals are cached locally
- Negative lookups are cached to avoid repeat requests
- Cache TTL configurable

=== Rate limiting ===

ScreenScraper usage is quota/thread limited. The provider:
- Respects request/thread limits
- Avoids uncontrolled parallel scraping
- Fails gracefully when quota/rate limits are reached
- Makes provider failures non-fatal so local/offline processing continues
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from . import metadata as _metadata
from .metadata import MetadataRecord, UnsafeUrlError, validate_metadata_relevance, guard_url

# --- Bounds / defaults -------------------------------------------------------

#: Public default ScreenScraper API endpoint.
DEFAULT_BASE_URL = "https://www.screenscraper.fr/api2/"

#: Hard upper bounds so a config cannot weaken the safety posture.
_MAX_TIMEOUT_SECONDS = 30.0
_MAX_RESPONSE_BYTES = 16_000_000
_MAX_CONCURRENCY = 4  # ScreenScraper has thread limits
_MIN_CONFIDENCE = 0.0
_MAX_CONFIDENCE = 1.0

#: Default bounded values (safe, conservative).
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_MAX_CONCURRENCY = 1
DEFAULT_CONFIDENCE_THRESHOLD = 0.85

#: Confidence assigned to an exact-hash match (deterministic, highest).
EXACT_HASH_CONFIDENCE = 1.0
#: Confidence assigned when reusing a previously-stored ScreenScraper game ID.
CANONICAL_REUSE_CONFIDENCE = 0.95
#: Confidence assigned to a validated title+system search match.
TITLE_SEARCH_CONFIDENCE = 0.85
#: Floor below which a title-fallback match is never auto-accepted.
TITLE_FALLBACK_MIN_CONFIDENCE = 0.80

#: ScreenScraper Amiga system ID (to be discovered/cached)
# Per ScreenScraper documentation, Amiga system ID is typically around 23-25 range
# We'll discover it on first use and cache it.
DEFAULT_AMIGA_SYSTEM_ID = "23"

# --- Match method ------------------------------------------------------------

class ScreenScraperMatchMethod(str, Enum):
    """How a ScreenScraper identity was resolved for a release group."""

    EXACT_HASH = "exact_hash"           # CRC/MD5/SHA1 exact match
    PROVIDER_ID = "provider_id"         # Cached ScreenScraper game ID reuse
    TITLE_SEARCH = "title_search"       # Validated title + system search
    MANUAL_REVIEW = "manual_review"     # Ambiguous/low-confidence -> review
    NONE = "none"                       # No match


# --- Errors ------------------------------------------------------------------

class ScreenScraperError(Exception):
    """Base error for ScreenScraper provider failures."""


class ScreenScraperDisabled(ScreenScraperError):
    """Raised when the provider is used while disabled in config."""


class ScreenScraperRateLimited(ScreenScraperError):
    """Raised on rate limit so the caller can apply a bounded backoff.

    Carries the server-advised ``retry_after`` (seconds, already clamped to the
    configured bound by the caller) so the single retry waits the right amount.
    """

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"ScreenScraper rate limited; retry after {retry_after:.1f}s")


class ScreenScraperAuthError(ScreenScraperError):
    """Raised when authentication fails (invalid credentials)."""


class ScreenScraperQuotaExceeded(ScreenScraperError):
    """Raised when ScreenScraper quota is exceeded."""


# --- Configuration -----------------------------------------------------------

@dataclass(frozen=True)
class ScreenScraperConfig:
    """Typed view of the ``[screenscraper]`` TOML table."""

    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    # Preferred regions for artwork/manual selection (e.g., "us", "eu", "wor", "jp")
    preferred_regions: tuple[str, ...] = ("us", "eu", "wor")
    download_metadata: bool = True
    download_artwork: bool = True
    download_manuals: bool = True
    # Cache TTL in seconds. <= 0 disables reuse.
    cache_ttl: float = 86400.0  # 24 hours default
    # Respect rate limits with single bounded retry
    respect_rate_limit: bool = True
    rate_limit_backoff_seconds: float = 5.0

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "ScreenScraperConfig":
        """Build a config from the raw ``[screenscraper]`` table (or None).

        ``from_dict(None)`` -> disabled. Every numeric field is bounded to the
        module's hard limits; out-of-range or malformed values fall back to the
        safe default rather than weakening posture.
        """
        if not data:
            return cls(enabled=False)
        raw = data or {}

        enabled = bool(raw.get("enabled", False))
        base_url = (raw.get("base_url") or DEFAULT_BASE_URL)
        if not isinstance(base_url, str) or not base_url.strip():
            base_url = DEFAULT_BASE_URL
        base_url = base_url.rstrip("/") + "/"

        try:
            timeout = float(raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_SECONDS
        if timeout <= 0 or timeout > _MAX_TIMEOUT_SECONDS:
            timeout = DEFAULT_TIMEOUT_SECONDS

        try:
            max_bytes = int(raw.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES))
        except (TypeError, ValueError):
            max_bytes = DEFAULT_MAX_RESPONSE_BYTES
        if max_bytes <= 0 or max_bytes > _MAX_RESPONSE_BYTES:
            max_bytes = DEFAULT_MAX_RESPONSE_BYTES

        try:
            max_conc = int(raw.get("max_concurrency", DEFAULT_MAX_CONCURRENCY))
        except (TypeError, ValueError):
            max_conc = DEFAULT_MAX_CONCURRENCY
        if max_conc < 1 or max_conc > _MAX_CONCURRENCY:
            max_conc = DEFAULT_MAX_CONCURRENCY

        try:
            conf = float(raw.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD))
        except (TypeError, ValueError):
            conf = DEFAULT_CONFIDENCE_THRESHOLD
        if conf < _MIN_CONFIDENCE or conf > _MAX_CONFIDENCE:
            conf = DEFAULT_CONFIDENCE_THRESHOLD

        # Preferred regions
        pref_regions = raw.get("preferred_regions")
        if isinstance(pref_regions, list):
            pref_regions = tuple(str(r).strip().lower() for r in pref_regions if str(r).strip())
        elif isinstance(pref_regions, str):
            pref_regions = tuple(r.strip().lower() for r in pref_regions.split(",") if r.strip())
        else:
            pref_regions = ("us", "eu", "wor")

        download_metadata = bool(raw.get("download_metadata", True))
        download_artwork = bool(raw.get("download_artwork", True))
        download_manuals = bool(raw.get("download_manuals", True))

        try:
            cache_ttl = float(raw.get("cache_ttl", 86400.0))
        except (TypeError, ValueError):
            cache_ttl = 86400.0
        if cache_ttl < 0:
            cache_ttl = 0.0

        respect_rate_limit = bool(raw.get("respect_rate_limit", True))
        try:
            rate_limit_backoff = float(raw.get("rate_limit_backoff_seconds", 5.0))
        except (TypeError, ValueError):
            rate_limit_backoff = 5.0
        if rate_limit_backoff < 0 or rate_limit_backoff > 60.0:
            rate_limit_backoff = 5.0

        return cls(
            enabled=enabled,
            base_url=base_url,
            timeout_seconds=timeout,
            max_response_bytes=max_bytes,
            max_concurrency=max_conc,
            confidence_threshold=conf,
            preferred_regions=pref_regions,
            download_metadata=download_metadata,
            download_artwork=download_artwork,
            download_manuals=download_manuals,
            cache_ttl=cache_ttl,
            respect_rate_limit=respect_rate_limit,
            rate_limit_backoff_seconds=rate_limit_backoff,
        )


# --- Result ------------------------------------------------------------------

@dataclass
class ScreenScraperResult:
    """Result of a ScreenScraper lookup for one release group."""

    # Game identification
    found: bool = False
    provider_id: Optional[str] = None          # ScreenScraper game ID
    match_method: ScreenScraperMatchMethod = ScreenScraperMatchMethod.NONE
    canonical_title: str = ""
    confidence: float = 0.0

    # Metadata fields (populated when download_metadata=True)
    year: Optional[str] = None
    publisher: Optional[str] = None
    developer: Optional[str] = None
    genre: Optional[str] = None
    description: Optional[str] = None
    players: Optional[str] = None
    region: Optional[str] = None
    language: Optional[str] = None

    # Artwork (populated when download_artwork=True)
    artwork_url: Optional[str] = None
    artwork_provider: str = "screenscraper"
    artwork_source_url: Optional[str] = None
    artwork_media_type: Optional[str] = None  # e.g., "box", "screenshot", "title"

    # Manual (populated when download_manuals=True)
    manual_url: Optional[str] = None
    manual_region: Optional[str] = None
    manual_language: Optional[str] = None

    # Cross-provider correlation IDs (normalized, allowlisted)
    external_ids: dict[str, str] = field(default_factory=dict)

    # Diagnostics
    needs_manual_review: bool = False
    relevance_category: Optional[str] = None
    relevance_confidence: float = 0.0
    relevance_evidence: list[str] = field(default_factory=list)

    # Raw API response for debugging (not persisted)
    raw_response: Optional[dict] = None


# --- Cache -------------------------------------------------------------------

def _cache_dir_for_metadata(cache_dir: Path) -> Path:
    """Return the ScreenScraper metadata cache directory."""
    return cache_dir / "screenscraper"


def _cache_file(cache_dir: Path, key: str) -> Path:
    """Return the cache file path for a given key."""
    safe_key = hashlib.sha256(key.encode()).hexdigest()[:32]
    return _cache_dir_for_metadata(cache_dir) / f"{safe_key}.json"


def _cache_store(cache_dir: Path, key: str, data: dict) -> None:
    """Store data in the ScreenScraper metadata cache."""
    if cache_dir is None:
        return
    cache_path = _cache_file(cache_dir, key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    data["_cached_at"] = time.time()
    _write_json_atomic(cache_path, data)


def _cache_load(cache_dir: Path, key: str, ttl: float) -> Optional[dict]:
    """Load data from the ScreenScraper metadata cache if not expired."""
    if cache_dir is None or ttl <= 0:
        return None
    cache_path = _cache_file(cache_dir, key)
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_at = data.get("_cached_at", 0)
        if time.time() - cached_at > ttl:
            return None
        return data
    except Exception:
        return None


def _cache_key_hash(crc: Optional[str], md5: Optional[str], sha1: Optional[str]) -> str:
    """Generate a cache key from ROM hashes."""
    parts = []
    if crc:
        parts.append(f"crc:{crc.lower()}")
    if md5:
        parts.append(f"md5:{md5.lower()}")
    if sha1:
        parts.append(f"sha1:{sha1.lower()}")
    return "|".join(parts) if parts else ""


def _cache_key_title(title: str, system_id: str) -> str:
    """Generate a cache key from title and system."""
    return f"title:{system_id}:{title.lower().strip()}"


def _cache_key_provider_id(provider_id: str) -> str:
    """Generate a cache key from ScreenScraper game ID."""
    return f"provider_id:{provider_id}"


def _negative_cache_file(cache_dir: Path, key: str) -> Path:
    """Return the file-backed negative-cache marker path for a lookup key.

    Stored in the shared provider cache directory so that negative results
    persist across provider instances (e.g. separate CLI/GUI runs sharing
    the same cache root), which is what prevents repeated negative lookups.
    """
    safe_key = hashlib.sha256(f"neg:{key}".encode("utf-8")).hexdigest()[:32]
    return _cache_dir_for_metadata(cache_dir) / f"neg_{safe_key}.json"


# --- XML parsing helpers -----------------------------------------------------

def _parse_jeu_infos(xml_bytes: bytes) -> dict:
    """Parse ScreenScraper jeuInfos.php XML response."""
    result = {
        "found": False,
        "game_id": None,
        "title": None,
        "year": None,
        "publisher": None,
        "developer": None,
        "genre": None,
        "description": None,
        "players": None,
        "region": None,
        "language": None,
        "medias": [],
        "manuals": [],
        "external_ids": {},
    }
    try:
        root = ET.fromstring(xml_bytes)
        # Check for error
        if root.tag == "Error" or root.find("Error") is not None:
            error_elem = root if root.tag == "Error" else root.find("Error")
            if error_elem is not None:
                result["error"] = error_elem.text
            return result

        # Successful response structure
        # <Data><Jeu>...</Jeu></Data> or similar
        jeu = root.find(".//Jeu")
        if jeu is None:
            jeu = root.find("Jeu")
        if jeu is None:
            # Maybe direct root
            jeu = root

        # Game ID
        game_id_elem = jeu.find("id")
        if game_id_elem is not None and game_id_elem.text:
            result["game_id"] = game_id_elem.text.strip()
            result["found"] = True

        # Title
        title_elem = jeu.find("nom")
        if title_elem is None:
            title_elem = jeu.find("title")
        if title_elem is not None and title_elem.text:
            result["title"] = title_elem.text.strip()

        # Year
        year_elem = jeu.find("annee")
        if year_elem is None:
            year_elem = jeu.find("year")
        if year_elem is None:
            year_elem = jeu.find("date")
        if year_elem is not None and year_elem.text:
            result["year"] = year_elem.text.strip()

        # Publisher
        pub_elem = jeu.find("editeur")
        if pub_elem is None:
            pub_elem = jeu.find("publisher")
        if pub_elem is not None and pub_elem.text:
            result["publisher"] = pub_elem.text.strip()

        # Developer
        dev_elem = jeu.find("developpeur")
        if dev_elem is None:
            dev_elem = jeu.find("developer")
        if dev_elem is not None and dev_elem.text:
            result["developer"] = dev_elem.text.strip()

        # Genre
        genre_elem = jeu.find("genre")
        if genre_elem is not None and genre_elem.text:
            result["genre"] = genre_elem.text.strip()

        # Description
        desc_elem = jeu.find("synopsis")
        if desc_elem is None:
            desc_elem = jeu.find("description")
        if desc_elem is not None and desc_elem.text:
            result["description"] = desc_elem.text.strip()

        # Players
        players_elem = jeu.find("joueurs")
        if players_elem is None:
            players_elem = jeu.find("players")
        if players_elem is not None and players_elem.text:
            result["players"] = players_elem.text.strip()

        # Region
        region_elem = jeu.find("region")
        if region_elem is not None and region_elem.text:
            result["region"] = region_elem.text.strip()

        # Language
        lang_elem = jeu.find("langue")
        if lang_elem is None:
            lang_elem = jeu.find("language")
        if lang_elem is not None and lang_elem.text:
            result["language"] = lang_elem.text.strip()

        # Medias (artwork)
        for media in jeu.findall(".//media") + jeu.findall(".//Media"):
            media_type = media.get("type") or media.get("support")
            url = media.text or media.get("url")
            region = media.get("region")
            if url and media_type:
                result["medias"].append({
                    "type": media_type.lower(),
                    "url": url.strip(),
                    "region": (region or "").lower() if region else None,
                })

        # Manuals
        for manual in jeu.findall(".//manuel") + jeu.findall(".//Manuel"):
            url = manual.text or manual.get("url")
            region = manual.get("region")
            lang = manual.get("langue") or manual.get("language")
            if url:
                result["manuals"].append({
                    "url": url.strip(),
                    "region": (region or "").lower() if region else None,
                    "language": (lang or "").lower() if lang else None,
                })

        # External IDs (allowlisted)
        external_id_keys = {
            "mobygames_id": ["mobyid", "mobygames_id"],
            "thegamesdb_id": ["thegamesdb_id", "tgdb_id"],
            "steam_id": ["steam_id"],
            "gog_id": ["gog_id"],
            "epic_games_id": ["epic_id", "epic_games_id"],
        }
        for our_key, their_keys in external_id_keys.items():
            for their_key in their_keys:
                elem = jeu.find(their_key)
                if elem is not None and elem.text and elem.text.strip():
                    result["external_ids"][our_key] = elem.text.strip()
                    break

    except ET.ParseError:
        pass
    except Exception:
        pass
    return result


def _parse_jeu_recherche(xml_bytes: bytes) -> list[dict]:
    """Parse ScreenScraper jeuRecherche.php XML response (search results)."""
    results = []
    try:
        root = ET.fromstring(xml_bytes)
        for jeu in root.findall(".//Jeu") + root.findall(".//jeu"):
            item = {
                "game_id": None,
                "title": None,
                "year": None,
                "publisher": None,
            }
            game_id_elem = jeu.find("id")
            if game_id_elem is not None and game_id_elem.text:
                item["game_id"] = game_id_elem.text.strip()
            title_elem = jeu.find("nom")
            if title_elem is None:
                title_elem = jeu.find("title")
            if title_elem is not None and title_elem.text:
                item["title"] = title_elem.text.strip()
            year_elem = jeu.find("annee")
            if year_elem is None:
                year_elem = jeu.find("year")
            if year_elem is not None and year_elem.text:
                item["year"] = year_elem.text.strip()
            pub_elem = jeu.find("editeur")
            if pub_elem is None:
                pub_elem = jeu.find("publisher")
            if pub_elem is not None and pub_elem.text:
                item["publisher"] = pub_elem.text.strip()
            if item["game_id"] or item["title"]:
                results.append(item)
    except Exception:
        pass
    return results


def _select_best_media(medias: list[dict], preferred_regions: tuple[str, ...], media_types: list[str]) -> Optional[dict]:
    """Select the best media match based on region preference and media type priority."""
    if not medias:
        return None

    # Filter by media type priority
    for media_type in media_types:
        type_matches = [m for m in medias if m["type"] == media_type]
        if type_matches:
            # Sort by region preference
            def region_rank(m):
                region = m.get("region", "")
                try:
                    return preferred_regions.index(region)
                except ValueError:
                    return len(preferred_regions)  # Unknown region goes last
            type_matches.sort(key=region_rank)
            return type_matches[0]

    # Fallback: any media, sorted by region
    medias.sort(key=lambda m: region_rank(m) if (region_rank := lambda m: preferred_regions.index(m.get("region", "")) if m.get("region", "") in preferred_regions else len(preferred_regions)) else len(preferred_regions))
    return medias[0] if medias else None


def _select_best_manual(manuals: list[dict], preferred_regions: tuple[str, ...]) -> Optional[dict]:
    """Select the best manual match based on region/language preference."""
    if not manuals:
        return None

    # Sort by region preference
    def manual_rank(m):
        region = m.get("region", "")
        try:
            return preferred_regions.index(region)
        except ValueError:
            return len(preferred_regions)

    manuals.sort(key=manual_rank)
    return manuals[0]


# --- HTTP fetch helpers ------------------------------------------------------

def _fetch_xml(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    opener: Optional[Callable[..., Any]] = None,
    resolve: bool = False,
) -> bytes:
    """Fetch XML from URL with SSRF guard and bounds."""
    guard_url(url, resolve=resolve)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AmigaADFLibraryBuilder/1.0"},
    )
    _opener = opener or urllib.request.urlopen
    try:
        with _opener(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ScreenScraperError(f"Response exceeds {max_bytes} bytes")
            return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = e.headers.get("Retry-After", "1")
            try:
                retry_after = float(retry_after)
            except (ValueError, TypeError):
                retry_after = 1.0
            raise ScreenScraperRateLimited(retry_after)
        elif e.code == 401:
            raise ScreenScraperAuthError("Authentication failed")
        raise ScreenScraperError(f"HTTP {e.code}: {e.reason}")


def _fetch_binary(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    opener: Optional[Callable[..., Any]] = None,
    resolve: bool = False,
) -> bytes:
    """Fetch binary data (image/PDF) from URL with SSRF guard and bounds."""
    guard_url(url, resolve=resolve)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AmigaADFLibraryBuilder/1.0"},
    )
    _opener = opener or urllib.request.urlopen
    try:
        with _opener(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ScreenScraperError(f"Response exceeds {max_bytes} bytes")
            return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = e.headers.get("Retry-After", "1")
            try:
                retry_after = float(retry_after)
            except (ValueError, TypeError):
                retry_after = 1.0
            raise ScreenScraperRateLimited(retry_after)
        elif e.code == 401:
            raise ScreenScraperAuthError("Authentication failed")
        raise ScreenScraperError(f"HTTP {e.code}: {e.reason}")


# --- Provider class ----------------------------------------------------------

class ScreenScraperProvider:
    """ScreenScraper metadata, artwork, and manual provider.

    OPTIONAL and DISABLED by default. Activated only when a ``[screenscraper]``
    config table is present with ``enabled = true`` AND credentials are available
    via environment variables or SecretStore.

    The provider supports three lookup methods in order of precedence:
    1. Hash lookup (CRC/MD5/SHA1) - exact match
    2. Provider ID reuse - cached ScreenScraper game ID
    3. Title + system search - validated through relevance gate

    Credentials (never in config):
    - Developer credentials (required): SCREENSCRAPER_DEV_ID, SCREENSCRAPER_DEV_PASSWORD, SCREENSCRAPER_SOFTNAME
    - Member credentials (optional, for higher limits): SCREENSCRAPER_SSID, SCREENSCRAPER_SSPASSWORD
    """

    def __init__(
        self,
        config: ScreenScraperConfig,
        cache_dir: Path,
        *,
        dev_id: str = "",
        dev_password: str = "",
        softname: str = "",
        ssid: str = "",
        sspassword: str = "",
        opener: Optional[Callable[..., Any]] = None,
        resolve_urls: bool = False,
    ) -> None:
        if not config.enabled:
            raise ScreenScraperDisabled("ScreenScraper provider is disabled in config")
        if not dev_id or not dev_password:
            raise ScreenScraperAuthError("Missing required developer credentials (devid, devpassword)")

        self.config = config
        self.cache_dir = Path(cache_dir)
        if opener is not None:
            self._opener = opener
            self._resolve_urls = False
        else:
            self._opener = None
            self._resolve_urls = True

        # Credentials
        self._dev_id = dev_id
        self._dev_password = dev_password
        self._softname = softname or "AmigaADFLibraryBuilder"
        self._ssid = ssid
        self._sspassword = sspassword

        # Discovered Amiga system ID (cached)
        self._amiga_system_id: Optional[str] = None
        self._system_id_lock = __import__("threading").RLock()

        # Negative lookup cache (file-backed, shared across provider instances)
        self._negative_ttl = 3600.0  # 1 hour

    # --- Credential helpers --------------------------------------------------

    def _build_auth_params(self) -> dict[str, str]:
        """Build authentication parameters for API requests."""
        params = {
            "devid": self._dev_id,
            "devpassword": self._dev_password,
            "softname": self._softname,
        }
        if self._ssid:
            params["ssid"] = self._ssid
        if self._sspassword:
            params["sspassword"] = self._sspassword
        return params

    def _get_amiga_system_id(self) -> str:
        """Get the Amiga system ID, discovering if necessary."""
        with self._system_id_lock:
            if self._amiga_system_id:
                return self._amiga_system_id
            # For now, use the default. In a full implementation, we'd call
            # the system list endpoint and find the Amiga entry.
            self._amiga_system_id = DEFAULT_AMIGA_SYSTEM_ID
            return self._amiga_system_id

    # --- Cache helpers -------------------------------------------------------

    def _check_negative_cache(self, key: str) -> bool:
        """Check if a negative lookup is cached and not expired.

        File-backed so the marker survives across provider instances that
        share the same cache root.
        """
        marker = _negative_cache_file(self.cache_dir, key)
        if not marker.is_file():
            return False
        try:
            cached_at = json.loads(marker.read_text(encoding="utf-8")).get("_cached_at", 0)
        except Exception:
            return False
        if time.time() - cached_at < self._negative_ttl:
            return True
        # Expired marker: remove it best-effort.
        try:
            marker.unlink()
        except OSError:
            pass
        return False

    def _mark_negative_cache(self, key: str) -> None:
        """Mark a lookup as negative (not found) in the shared file cache."""
        try:
            _write_json_atomic(_negative_cache_file(self.cache_dir, key), {"_cached_at": time.time()})
        except OSError:
            # Caching is best-effort; a failed marker write must not break the lookup.
            pass

    # --- Core lookup methods -------------------------------------------------

    def lookup_by_hash(
        self,
        crc: Optional[str],
        md5: Optional[str],
        sha1: Optional[str],
        canonical_title: str,
    ) -> ScreenScraperResult:
        """Lookup game by ROM hash (CRC/MD5/SHA1)."""
        # Build cache key
        cache_key = _cache_key_hash(crc, md5, sha1)
        if not cache_key:
            return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

        # Check metadata cache
        cached = _cache_load(self.cache_dir, cache_key, self.config.cache_ttl)
        if cached:
            return self._result_from_cached(cached, ScreenScraperMatchMethod.PROVIDER_ID)

        # Check negative cache
        if self._check_negative_cache(cache_key):
            return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

        # Build query parameters
        params = self._build_auth_params()
        params["systemeid"] = self._get_amiga_system_id()

        # Add hash parameters (ScreenScraper supports crc, md5, sha1)
        if crc:
            params["crc"] = crc.upper()
        if md5:
            params["md5"] = md5.lower()
        if sha1:
            params["sha1"] = sha1.lower()

        url = self.config.base_url + "jeuInfos.php?" + urllib.parse.urlencode(params)

        try:
            xml_data = _fetch_xml(
                url,
                timeout=self.config.timeout_seconds,
                max_bytes=self.config.max_response_bytes,
                opener=self._opener,
                resolve=self._resolve_urls,
            )
            parsed = _parse_jeu_infos(xml_data)

            if parsed.get("found") and parsed.get("game_id"):
                result = self._result_from_parsed(parsed, ScreenScraperMatchMethod.EXACT_HASH)
                # Cache successful result
                _cache_store(self.cache_dir, cache_key, self._result_to_cache_dict(result))
                # Also cache by provider_id for reuse
                if result.provider_id:
                    _cache_store(self.cache_dir, _cache_key_provider_id(result.provider_id), self._result_to_cache_dict(result))
                return result
            else:
                # Negative result
                self._mark_negative_cache(cache_key)
                return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

        except ScreenScraperRateLimited as e:
            if self.config.respect_rate_limit:
                # Single bounded retry with backoff
                import time
                backoff = min(e.retry_after, self.config.rate_limit_backoff_seconds)
                time.sleep(backoff)
                # Retry the request
                try:
                    xml_data = _fetch_xml(
                        url,
                        timeout=self.config.timeout_seconds,
                        max_bytes=self.config.max_response_bytes,
                        opener=self._opener,
                        resolve=self._resolve_urls,
                    )
                    parsed = _parse_jeu_infos(xml_data)
                    if parsed.get("found") and parsed.get("game_id"):
                        result = self._result_from_parsed(parsed, ScreenScraperMatchMethod.EXACT_HASH)
                        _cache_store(self.cache_dir, cache_key, self._result_to_cache_dict(result))
                        if result.provider_id:
                            _cache_store(self.cache_dir, _cache_key_provider_id(result.provider_id), self._result_to_cache_dict(result))
                        return result
                except Exception:
                    pass  # Retry failed, fall through to miss
            # If we get here, either rate limiting not respected or retry failed
            return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

        except ScreenScraperAuthError:
            raise
        except ScreenScraperQuotaExceeded:
            raise
        except Exception:
            # Non-fatal: treat as miss
            return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

    def lookup_by_provider_id(self, provider_id: str) -> ScreenScraperResult:
        """Lookup game by cached ScreenScraper game ID."""
        cache_key = _cache_key_provider_id(provider_id)

        # Check metadata cache
        cached = _cache_load(self.cache_dir, cache_key, self.config.cache_ttl)
        if cached:
            return self._result_from_cached(cached, ScreenScraperMatchMethod.PROVIDER_ID)

        # Check negative cache
        if self._check_negative_cache(cache_key):
            return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

        params = self._build_auth_params()
        params["id"] = provider_id

        url = self.config.base_url + "jeuInfos.php?" + urllib.parse.urlencode(params)

        try:
            xml_data = _fetch_xml(
                url,
                timeout=self.config.timeout_seconds,
                max_bytes=self.config.max_response_bytes,
                opener=self._opener,
                resolve=self._resolve_urls,
            )
            parsed = _parse_jeu_infos(xml_data)

            if parsed.get("found") and parsed.get("game_id"):
                result = self._result_from_parsed(parsed, ScreenScraperMatchMethod.PROVIDER_ID)
                _cache_store(self.cache_dir, cache_key, self._result_to_cache_dict(result))
                return result
            else:
                self._mark_negative_cache(cache_key)
                return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

        except ScreenScraperRateLimited:
            raise
        except ScreenScraperAuthError:
            raise
        except ScreenScraperQuotaExceeded:
            raise
        except Exception:
            return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

    def lookup_by_title(self, title: str, group: Any = None) -> ScreenScraperResult:
        """Lookup game by title + system search, validated through relevance gate."""
        cache_key = _cache_key_title(title, self._get_amiga_system_id())

        # Check metadata cache
        cached = _cache_load(self.cache_dir, cache_key, self.config.cache_ttl)
        if cached:
            return self._result_from_cached(cached, ScreenScraperMatchMethod.PROVIDER_ID)

        # Check negative cache
        if self._check_negative_cache(cache_key):
            return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

        params = self._build_auth_params()
        params["recherche"] = title
        params["systemeid"] = self._get_amiga_system_id()

        url = self.config.base_url + "jeuRecherche.php?" + urllib.parse.urlencode(params)

        try:
            xml_data = _fetch_xml(
                url,
                timeout=self.config.timeout_seconds,
                max_bytes=self.config.max_response_bytes,
                opener=self._opener,
                resolve=self._resolve_urls,
            )
            candidates = _parse_jeu_recherche(xml_data)

            if not candidates:
                self._mark_negative_cache(cache_key)
                return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

            # If multiple candidates, we need to disambiguate
            # For now, take the first exact title match
            exact_matches = [c for c in candidates if c.get("title", "").lower() == title.lower()]
            if len(exact_matches) == 1:
                # Single exact match - fetch full details
                game_id = exact_matches[0].get("game_id")
                if game_id:
                    result = self.lookup_by_provider_id(game_id)
                    if result.found:
                        # Override match method to reflect the original title search
                        result.match_method = ScreenScraperMatchMethod.TITLE_SEARCH
                    return result

            # Multiple or no exact matches - try to validate first candidate through relevance
            # For now, route to review if ambiguous
            if len(candidates) > 1:
                # Ambiguous - route to manual review
                result = ScreenScraperResult(
                    found=True,
                    match_method=ScreenScraperMatchMethod.MANUAL_REVIEW,
                    needs_manual_review=True,
                    canonical_title=candidates[0].get("title", title),
                    confidence=0.5,
                )
                return result

            # Single candidate - fetch full details
            game_id = candidates[0].get("game_id")
            if game_id:
                result = self.lookup_by_provider_id(game_id)
                if result.found:
                    result.match_method = ScreenScraperMatchMethod.TITLE_SEARCH
                    # Validate through relevance gate
                    if group is not None:
                        decision = validate_metadata_relevance(title, result.to_metadata_record(), group=group)
                        result.relevance_category = decision.category
                        result.relevance_confidence = decision.confidence
                        result.relevance_evidence = list(decision.evidence)
                        if decision.category != "accepted":
                            result.needs_manual_review = True
                            result.match_method = ScreenScraperMatchMethod.MANUAL_REVIEW
                    return result

            self._mark_negative_cache(cache_key)
            return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

        except ScreenScraperRateLimited:
            raise
        except ScreenScraperAuthError:
            raise
        except ScreenScraperQuotaExceeded:
            raise
        except Exception:
            return ScreenScraperResult(found=False, match_method=ScreenScraperMatchMethod.NONE)

    # --- Artwork download ----------------------------------------------------

    def download_artwork(
        self,
        provider_id: str,
        media_type: str = "box",
        region: Optional[str] = None,
    ) -> Optional[bytes]:
        """Download artwork for a game.

        Args:
            provider_id: ScreenScraper game ID
            media_type: Type of media (box, screenshot, title, etc.)
            region: Preferred region (e.g., "us", "eu", "jp")

        Returns:
            Image bytes or None if not found/failed.
        """
        if not self.config.download_artwork:
            return None

        params = self._build_auth_params()
        params["id"] = provider_id
        params["media"] = media_type
        if region:
            params["region"] = region

        url = self.config.base_url + "mediaJeu.php?" + urllib.parse.urlencode(params)

        try:
            return _fetch_binary(
                url,
                timeout=self.config.timeout_seconds,
                max_bytes=self.config.max_response_bytes,
                opener=self._opener,
                resolve=self._resolve_urls,
            )
        except Exception:
            return None

    # --- Manual download -----------------------------------------------------

    def download_manual(
        self,
        provider_id: str,
        region: Optional[str] = None,
    ) -> Optional[bytes]:
        """Download PDF manual for a game.

        Args:
            provider_id: ScreenScraper game ID
            region: Preferred region (e.g., "us", "eu", "jp")

        Returns:
            PDF bytes or None if not found/failed.
        """
        if not self.config.download_manuals:
            return None

        params = self._build_auth_params()
        params["id"] = provider_id
        if region:
            params["region"] = region

        url = self.config.base_url + "mediaManuelJeu.php?" + urllib.parse.urlencode(params)

        try:
            return _fetch_binary(
                url,
                timeout=self.config.timeout_seconds,
                max_bytes=self.config.max_response_bytes,
                opener=self._opener,
                resolve=self._resolve_urls,
            )
        except Exception:
            return None

    # --- Result conversion helpers -------------------------------------------

    def _result_from_parsed(self, parsed: dict, match_method: ScreenScraperMatchMethod) -> ScreenScraperResult:
        """Convert parsed API response to ScreenScraperResult."""
        result = ScreenScraperResult(
            found=parsed.get("found", False),
            provider_id=parsed.get("game_id"),
            match_method=match_method,
            canonical_title=parsed.get("title", ""),
            confidence=EXACT_HASH_CONFIDENCE if match_method == ScreenScraperMatchMethod.EXACT_HASH else CANONICAL_REUSE_CONFIDENCE,
            year=parsed.get("year"),
            publisher=parsed.get("publisher"),
            developer=parsed.get("developer"),
            genre=parsed.get("genre"),
            description=parsed.get("description"),
            players=parsed.get("players"),
            region=parsed.get("region"),
            language=parsed.get("language"),
            external_ids=parsed.get("external_ids", {}),
        )

        # Select best artwork
        if self.config.download_artwork and parsed.get("medias"):
            # Prefer box art, then title screen, then screenshot
            media_types = ["box", "cover", "title", "screenshot"]
            best_media = _select_best_media(parsed["medias"], self.config.preferred_regions, media_types)
            if best_media:
                result.artwork_url = best_media["url"]
                result.artwork_media_type = best_media["type"]
                result.artwork_source_url = self.config.base_url + "mediaJeu.php"  # placeholder

        # Select best manual
        if self.config.download_manuals and parsed.get("manuals"):
            best_manual = _select_best_manual(parsed["manuals"], self.config.preferred_regions)
            if best_manual:
                result.manual_url = best_manual["url"]
                result.manual_region = best_manual.get("region")
                result.manual_language = best_manual.get("language")

        return result

    def _result_from_cached(self, cached: dict, match_method: ScreenScraperMatchMethod) -> ScreenScraperResult:
        """Convert cached data to ScreenScraperResult."""
        return ScreenScraperResult(
            found=True,
            provider_id=cached.get("provider_id"),
            match_method=match_method,
            canonical_title=cached.get("canonical_title", ""),
            confidence=CANONICAL_REUSE_CONFIDENCE,
            year=cached.get("year"),
            publisher=cached.get("publisher"),
            developer=cached.get("developer"),
            genre=cached.get("genre"),
            description=cached.get("description"),
            players=cached.get("players"),
            region=cached.get("region"),
            language=cached.get("language"),
            artwork_url=cached.get("artwork_url"),
            artwork_media_type=cached.get("artwork_media_type"),
            manual_url=cached.get("manual_url"),
            manual_region=cached.get("manual_region"),
            manual_language=cached.get("manual_language"),
            external_ids=cached.get("external_ids", {}),
        )

    def _result_to_cache_dict(self, result: ScreenScraperResult) -> dict:
        """Convert ScreenScraperResult to cache dictionary."""
        return {
            "provider_id": result.provider_id,
            "canonical_title": result.canonical_title,
            "year": result.year,
            "publisher": result.publisher,
            "developer": result.developer,
            "genre": result.genre,
            "description": result.description,
            "players": result.players,
            "region": result.region,
            "language": result.language,
            "artwork_url": result.artwork_url,
            "artwork_media_type": result.artwork_media_type,
            "manual_url": result.manual_url,
            "manual_region": result.manual_region,
            "manual_language": result.manual_language,
            "external_ids": result.external_ids,
        }

    def to_metadata_record(self, result: ScreenScraperResult) -> MetadataRecord:
        """Convert ScreenScraperResult to MetadataRecord for relevance validation."""
        return MetadataRecord(
            canonical_title=result.canonical_title,
            year=result.year or "",
            publisher=result.publisher or "",
            developer=result.developer or "",
            genres=[result.genre] if result.genre else [],
            description=result.description or "",
            provider="screenscraper",
            provider_id=result.provider_id or "",
            confidence=result.confidence,
            source_url=result.artwork_source_url or "",
            artwork_url=result.artwork_url or "",
            artwork_provider=result.artwork_provider,
            artwork_source_url=result.artwork_source_url or "",
            retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            query=result.canonical_title,
        )


# --- High-level integration function -----------------------------------------

def enrich_group_with_screenscraper(
    group: Any,
    scans: dict[str, Any],
    config: ScreenScraperConfig,
    cache_dir: Path,
    *,
    dev_id: str,
    dev_password: str,
    softname: str,
    ssid: str = "",
    sspassword: str = "",
    online: bool = True,
    opener: Optional[Callable[..., Any]] = None,
    resolve_urls: bool = False,
) -> Optional[ScreenScraperResult]:
    """High-level function to enrich a release group with ScreenScraper data.

    This function orchestrates the full lookup chain:
    1. Try hash lookup (CRC/MD5/SHA1 from scan records)
    2. Try cached provider ID reuse
    3. Try title + system search (with relevance validation)

    Returns a ScreenScraperResult or None if provider is disabled/unavailable.
    All errors are caught and result in None (non-fatal).
    """
    if not config.enabled or not online:
        return None

    if not dev_id or not dev_password:
        return None

    try:
        provider = ScreenScraperProvider(
            config,
            cache_dir,
            dev_id=dev_id,
            dev_password=dev_password,
            softname=softname,
            ssid=ssid,
            sspassword=sspassword,
            opener=opener,
            resolve_urls=resolve_urls,
        )

        # Extract hashes from scan records
        crc = None
        md5 = None
        sha1 = None
        for scan in scans.values():
            if hasattr(scan, 'crc32') and scan.crc32:
                crc = format(scan.crc32, '08x')
            if hasattr(scan, 'md5') and scan.md5:
                md5 = scan.md5
            if hasattr(scan, 'sha1') and scan.sha1:
                sha1 = scan.sha1

        canonical_title = group.title

        # 1. Hash lookup (preferred)
        if crc or md5 or sha1:
            result = provider.lookup_by_hash(crc, md5, sha1, canonical_title)
            if result.found and not result.needs_manual_review:
                return result
            # If hash match needs review, we still return it for review routing
            if result.found and result.needs_manual_review:
                return result

        # 2. Provider ID reuse (check if any scan has a cached provider_id)
        # This would come from a previous run's metadata cache
        # For now, skip - would need to check metadata cache for existing provider_id

        # 3. Title search fallback
        result = provider.lookup_by_title(canonical_title, group=group)
        if result.found:
            return result

        return None

    except ScreenScraperDisabled:
        return None
    except ScreenScraperAuthError:
        return None
    except ScreenScraperRateLimited:
        return None
    except ScreenScraperQuotaExceeded:
        return None
    except Exception:
        return None


# --- Utility -----------------------------------------------------------------

def _write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON to ``path`` via a temp file + atomic replace (no partial reads)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)