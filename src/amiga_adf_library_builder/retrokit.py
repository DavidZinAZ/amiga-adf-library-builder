"""
RetroKit / Archive.org optional manual provider (GitHub issue #10).

This module adds RetroKit's curated manual archive (published on Archive.org)
as an OPTIONAL, manual-focused online source of original manuals for Amiga
games. It is DISABLED by default and never required: the base application
remains fully functional with no Archive.org availability and no network.

SCOPE (manual discovery + acquisition only)
-------------------------------------------
* NOT a metadata or artwork provider. Game identity and descriptive metadata
  come from the existing providers (local, Playmatch, Hasheous, IGDB,
  ScreenScraper). This module only locates and downloads a manual PDF.
* Retrieved PDFs are preserved UNCHANGED in a managed cache and are fed into
  the existing Issue #5 PDF/scanned-document extraction layer via the RTFM
  pipeline. This module deliberately contains NO second PDF/OCR implementation
  -- it hands a cached PDF file to the existing extraction path.

ARCHIVE.ORG STRUCTURE (documented / verified)
---------------------------------------------
The canonical item is ``retrokit-manuals``. It is organized per SYSTEM as a
small number of large bundles plus a stable per-system index::

    retrokit-manuals/<system>/<system>-sources.tsv     (index)
    retrokit-manuals/<system>/<system>-original.zip    (large, ~17 GB Amiga)
    retrokit-manuals/<system>/<system>-compressed.zip  (large, ~7 GB Amiga)

The ``-sources.tsv`` index is the discovery mechanism. Each row has the form::

    <title> TAB <langs> TAB <url>

where ``<langs>`` is a comma-separated language list (e.g. ``en`` or
``en-gb,de,fr,it``) and ``<url>`` is a DIRECT manual artifact (an Archive.org
PDF, a third-party PDF, or occasionally another format). Because the index
points at per-game direct URLs, "retrieve only the selected manual artifact"
means: match the title in the index, choose the best-language row, and download
that ONE URL -- NOT extracting a single member from the multi-gigabyte bundle.

MATCHING
--------
Deterministic and conservative, reusing the RTFM core's title normalizers so
this provider's notion of "the same game" is exactly the RTFM core's:
  * tier ``exact``     -- normalized full-title identity (auto-eligible);
  * tier ``canonical`` -- canonical-reuse base (release tags stripped) (auto-eligible);
  * tier ``plausible`` -- single unambiguous minor-spelling fix (NOT accepted;
    routed to review).
A group with multiple DISTINCT manual URLs in the chosen language is AMBIGUOUS
and is routed to review rather than silently accepted.

ARCHIVE.ORG RETRIEVAL
---------------------
* Uses the documented item/index/download URL scheme (no rendered-HTML scraping).
* Bounded request size + timeout; SSRF-guarded (``metadata.guard_url``).
* Rate limits (HTTP 429) -> one bounded backoff retry, then a normal miss.
* A missing item/file, malformed row, or transient outage is a NON-FATAL miss.
* The downloaded PDF is stored unmutated; a SHA-256 is recorded and the file is
  reused on repeat runs (idempotent). Negative lookups are cached for a bounded
  period so the same title is not re-resolved on every run.

PROVENANCE (privacy)
--------------------
Result / cache metadata identify the source WITHOUT absolute host paths:
  provider = "retrokit", transport = "archive.org", item id, system, the
  RetroKit source title (never the ROM filename), language, variant
  (``original`` for an archive.org artifact, ``optimized`` for a third-party
  mirror), match method, confidence, and SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .metadata import guard_url  # noqa: F401  (re-exported for tests)
from .local_media import _sha256_file  # pure helper (re-exported for tests)

# --- Constants ---------------------------------------------------------------

#: Canonical RetroKit item on Archive.org.
DEFAULT_ITEM_ID = "retrokit-manuals"
#: Default system (this library is Amiga-focused).
DEFAULT_SYSTEM = "amiga"

#: Outbound identity for Archive.org requests.
USER_AGENT = "AmigaADFLibraryBuilder/1.0"

#: Hard cap for the per-system index file (bytes). The real Amiga index is ~290 KB;
#: this cap is generous but bounded so a malformed response cannot OOM the builder.
MAX_INDEX_BYTES = 8 * 1024 * 1024
#: Hard cap for a single downloaded manual (bytes). Kept at the RTFM per-source
#: guard (``rtfm.MAX_SOURCE_BYTES``) so a downloaded PDF is always usable by the
#: extraction path and never silently skipped for being oversized.
MAX_MANUAL_BYTES = 8 * 1024 * 1024

#: Absolute timeout bound (seconds). Config values are clamped into (0, bound].
_MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_TIMEOUT_SECONDS = 30.0
#: Index reuse TTL (seconds). The index changes rarely; 7 days is a safe default.
DEFAULT_INDEX_TTL_SECONDS = 7 * 24 * 3600.0
#: Negative-lookup TTL (seconds). A title with no manual is re-checked after 24h.
DEFAULT_NEGATIVE_TTL_SECONDS = 24 * 3600.0
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 5.0

#: Language normalization table. RetroKit uses ISO-ish tokens (``en``/``en-gb``/
#: ``de``/``fr``/``it``...). We normalize a small set of known aliases so that a
#: configured ``preferred_languages = ["english"]`` still matches ``en`` rows.
_LANG_NORMALIZE: dict[str, str] = {
    "en": "en", "eng": "en", "english": "en",
    "de": "de", "ger": "de", "german": "de",
    "fr": "fr", "fra": "fr", "french": "fr",
    "it": "it", "ita": "it", "italian": "it",
    "es": "es", "spa": "es", "spanish": "es",
    "nl": "nl", "nld": "nl", "dutch": "nl",
    "pl": "pl", "pol": "pl", "polish": "pl",
    "pt": "pt", "por": "pt", "portuguese": "pt",
    "ja": "ja", "jpn": "ja", "japanese": "ja",
}


# --- Errors ------------------------------------------------------------------


class RetroKitError(Exception):
    """Base error for RetroKit provider failures (all are non-fatal to the run)."""


class RetroKitDisabled(RetroKitError):
    """Raised when the provider is used while disabled in config."""


class RetroKitRateLimited(RetroKitError):
    """Archive.org returned 429; carries the server-advised retry_after."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Archive.org rate limited; retry after {retry_after:.1f}s")


# --- Configuration -----------------------------------------------------------


@dataclass(frozen=True)
class RetroKitConfig:
    """Typed view of the ``[retrokit_manuals]`` TOML table.

    ``from_dict(None)`` -> disabled. Every numeric field is bounded to the
    module's hard limits; out-of-range or malformed values fall back to the safe
    default rather than weakening posture.
    """

    enabled: bool = False
    item_id: str = DEFAULT_ITEM_ID
    system: str = DEFAULT_SYSTEM
    preferred_languages: tuple[str, ...] = ("en",)
    #: Prefer the original (archive.org) artifact where the index offers one.
    prefer_original: bool = True
    max_manual_bytes: int = MAX_MANUAL_BYTES
    index_ttl_seconds: float = DEFAULT_INDEX_TTL_SECONDS
    negative_ttl_seconds: float = DEFAULT_NEGATIVE_TTL_SECONDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    #: Respect a 429 with a single bounded backoff retry (vs. an immediate miss).
    respect_rate_limit: bool = True
    rate_limit_backoff_seconds: float = DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
    #: Unused for M1; the managed cache lives under the pipeline's metadata
    #: cache dir. Kept so the example config shape in issue #10 is accepted.
    cache_dir: str = ""

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "RetroKitConfig":
        if not data:
            return cls(enabled=False)
        raw = data or {}

        enabled = bool(raw.get("enabled", False))

        item_id = raw.get("item_id") or DEFAULT_ITEM_ID
        if not isinstance(item_id, str) or not item_id.strip():
            item_id = DEFAULT_ITEM_ID
        item_id = item_id.strip().strip("/")

        system = raw.get("system") or DEFAULT_SYSTEM
        if not isinstance(system, str) or not system.strip():
            system = DEFAULT_SYSTEM
        system = system.strip().lower()

        pref = raw.get("preferred_languages")
        if isinstance(pref, list):
            pref = tuple(_norm_lang(str(x)) for x in pref if str(x).strip())
        elif isinstance(pref, str):
            pref = tuple(_norm_lang(x) for x in pref.split(",") if x.strip())
        else:
            pref = ("en",)
        if not pref:
            pref = ("en",)

        prefer_original = bool(raw.get("prefer_original", True))

        try:
            max_bytes = int(raw.get("max_manual_bytes", MAX_MANUAL_BYTES))
        except (TypeError, ValueError):
            max_bytes = MAX_MANUAL_BYTES
        if max_bytes <= 0 or max_bytes > MAX_MANUAL_BYTES:
            max_bytes = MAX_MANUAL_BYTES

        try:
            index_ttl = float(raw.get("index_ttl_seconds", DEFAULT_INDEX_TTL_SECONDS))
        except (TypeError, ValueError):
            index_ttl = DEFAULT_INDEX_TTL_SECONDS
        if index_ttl < 0:
            index_ttl = DEFAULT_INDEX_TTL_SECONDS

        try:
            neg_ttl = float(raw.get("negative_ttl_seconds", DEFAULT_NEGATIVE_TTL_SECONDS))
        except (TypeError, ValueError):
            neg_ttl = DEFAULT_NEGATIVE_TTL_SECONDS
        if neg_ttl < 0:
            neg_ttl = DEFAULT_NEGATIVE_TTL_SECONDS

        try:
            timeout = float(raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_SECONDS
        if timeout <= 0 or timeout > _MAX_TIMEOUT_SECONDS:
            timeout = DEFAULT_TIMEOUT_SECONDS

        respect = bool(raw.get("respect_rate_limit", True))
        try:
            backoff = float(raw.get("rate_limit_backoff_seconds", DEFAULT_RATE_LIMIT_BACKOFF_SECONDS))
        except (TypeError, ValueError):
            backoff = DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
        if backoff < 0 or backoff > 60.0:
            backoff = DEFAULT_RATE_LIMIT_BACKOFF_SECONDS

        cache_dir = raw.get("cache_dir") or ""
        if not isinstance(cache_dir, str):
            cache_dir = ""

        return cls(
            enabled=enabled,
            item_id=item_id,
            system=system,
            preferred_languages=pref,
            prefer_original=prefer_original,
            max_manual_bytes=max_bytes,
            index_ttl_seconds=index_ttl,
            negative_ttl_seconds=neg_ttl,
            timeout_seconds=timeout,
            respect_rate_limit=respect,
            rate_limit_backoff_seconds=backoff,
            cache_dir=cache_dir,
        )


# --- Result ------------------------------------------------------------------


@dataclass
class RetroKitResult:
    """Outcome of resolving (and optionally acquiring) one group's manual."""

    # Discovery / matching
    matched: bool = False
    ambiguous: bool = False
    routed_for_review: bool = False
    review_reason: Optional[str] = None
    match_method: str = "none"
    match_confidence: float = 0.0
    match_evidence: list[str] = field(default_factory=list)

    # Source identity (never the ROM filename)
    item_id: str = ""
    system: str = DEFAULT_SYSTEM
    source_title: str = ""
    language: Optional[str] = None
    url: Optional[str] = None
    variant: Optional[str] = None  # "original" | "optimized"

    # Acquisition
    local_path: Optional[Path] = None
    sha256: Optional[str] = None
    size: int = 0
    error: Optional[str] = None

    def provenance(self) -> dict[str, Any]:
        """Public-safe provenance record (no absolute host paths)."""
        return {
            "provider": "retrokit",
            "transport": "archive.org",
            "item_id": self.item_id,
            "system": self.system,
            "source_title": self.source_title,
            "language": self.language,
            "url": self.url,
            "variant": self.variant,
            "match_method": self.match_method,
            "match_confidence": self.match_confidence,
            "match_evidence": list(self.match_evidence),
            "sha256": self.sha256,
            "size": self.size,
            "matched": self.matched,
            "ambiguous": self.ambiguous,
        }


# --- HTTP fetch helpers (opener-injectable, SSRF-guarded) --------------------


def _fetch_text(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    opener: Optional[Callable[..., Any]] = None,
    resolve: bool = True,
) -> bytes:
    """Fetch a text/index resource with SSRF guard and a bounded read."""
    guard_url(url, resolve=resolve)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    _opener = opener or urllib.request.urlopen
    try:
        with _opener(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise RetroKitError(f"response exceeds {max_bytes} bytes")
            return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = _parse_retry_after(e.headers.get("Retry-After", "1"))
            raise RetroKitRateLimited(retry_after)
        # 403/404/other -> a normal miss (missing item or file).
        raise RetroKitError(f"HTTP {e.code}: {getattr(e, 'reason', '')}")
    except urllib.error.URLError as e:
        # Transient network failure -> non-fatal miss.
        raise RetroKitError(f"network: {getattr(e, 'reason', e)}")


def _fetch_binary(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    opener: Optional[Callable[..., Any]] = None,
    resolve: bool = True,
) -> bytes:
    """Fetch a manual artifact (binary) with SSRF guard and a bounded read."""
    guard_url(url, resolve=resolve)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    _opener = opener or urllib.request.urlopen
    try:
        with _opener(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise RetroKitError(f"response exceeds {max_bytes} bytes")
            return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = _parse_retry_after(e.headers.get("Retry-After", "1"))
            raise RetroKitRateLimited(retry_after)
        raise RetroKitError(f"HTTP {e.code}: {getattr(e, 'reason', '')}")
    except urllib.error.URLError as e:
        raise RetroKitError(f"network: {getattr(e, 'reason', e)}")


def _parse_retry_after(value: str) -> float:
    try:
        return max(0.0, min(float(value), 60.0))
    except (TypeError, ValueError):
        return 1.0


# --- Index parsing -----------------------------------------------------------


def parse_index(text: str) -> list[dict[str, str]]:
    """Parse the RetroKit TSV index into rows.

    Each well-formed row is ``<title> TAB <langs> TAB <url>``. Malformed rows
    (wrong column count, empty title/url) are SKIPPED, never raised, so a
    partially malformed index degrades to fewer candidates rather than a crash.
    """
    rows: list[dict[str, str]] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        title = parts[0].strip()
        langs = parts[1].strip()
        url = parts[2].strip()
        if not title or not url:
            continue
        rows.append({"title": title, "langs": langs, "url": url})
    return rows


def _norm_lang(token: str) -> str:
    t = token.strip().lower()
    return _LANG_NORMALIZE.get(t, t)


def _row_languages(langs: str) -> list[str]:
    return [_norm_lang(x) for x in (langs or "").split(",") if x.strip()]


def _is_archive_org(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        return host == "archive.org" or host.endswith(".archive.org")
    except Exception:
        return False


# --- Language + matching -----------------------------------------------------


def _choose_language(available: list[str], preferred: tuple[str, ...]) -> Optional[str]:
    """Deterministically pick a language: first preferred present, else first
    available (sorted for stability). ``available`` is already normalized."""
    for p in preferred:
        if p in available:
            return p
    if not available:
        return None
    return sorted(available)[0]


def _match_tier(identity: str, source_title: str) -> tuple[bool, str]:
    """Conservative title match against the RTFM core's notion of identity.

    Returns ``(matched, tier)`` with tier in ``exact`` / ``canonical`` /
    ``plausible`` / ``none``. Reuses the RTFM normalizers so this provider's
    "same game" decision is exactly the RTFM core's.
    """
    a = (identity or "").strip()
    b = (source_title or "").strip()
    if not a or not b:
        return (False, "none")
    if a.lower() == b.lower():
        return (True, "exact")
    # Lazy import keeps the core import graph clean and avoids a cycle.
    from .rtfm import (
        _normalize_title_tokens,
        _strip_release_tags,
        _single_minor_spelling_fix,
    )

    na = _normalize_title_tokens(a)
    nb = _normalize_title_tokens(b)
    if na and na == nb:
        return (True, "exact")
    base_a = _normalize_title_tokens(_strip_release_tags(a))
    base_b = _normalize_title_tokens(_strip_release_tags(b))
    if base_a and base_a == base_b:
        return (True, "canonical")
    fix = _single_minor_spelling_fix(na, nb)
    if fix is not None:
        return (True, "plausible")
    return (False, "none")


def _group_identities(group) -> list[str]:
    """Identity forms used to match a group against the index (title + basename)."""
    forms: list[str] = []
    t = (getattr(group, "title", None) or "").strip()
    if t:
        forms.append(t)
    try:
        from .naming import release_basename

        bn = (release_basename(group) or "").strip()
    except Exception:
        bn = ""
    if bn and bn not in forms:
        forms.append(bn)
    return forms


def _group_basename(group) -> str:
    """The canonical export name reused by the RTFM core (for output naming)."""
    try:
        from .rtfm import _group_identity

        return _group_identity(group)
    except Exception:
        return (getattr(group, "title", None) or "Unknown").strip() or "Unknown"


# --- Filesystem helpers (atomic, per-module) ---------------------------------


def _atomic_write(path: Path, data: bytes) -> None:
    """Write bytes atomically (temp file + replace); no partial reads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _write_json_atomic(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


# --- Provider ----------------------------------------------------------------


class RetroKitProvider:
    """Optional RetroKit / Archive.org manual provider.

    OPTIONAL and DISABLED by default. Activated only when a
    ``[retrokit_manuals]`` config table is present with ``enabled = true``.
    No credentials are required (Archive.org is public); nothing sensitive is
    ever sent. A managed cache dir is required for the index + PDF + metadata.
    """

    def __init__(
        self,
        config: RetroKitConfig,
        cache_dir: Path,
        *,
        opener: Optional[Callable[..., Any]] = None,
        resolve_urls: Optional[bool] = None,
    ) -> None:
        if not config.enabled:
            raise RetroKitDisabled("RetroKit provider is disabled in config")
        self.config = config
        self.cache_dir = Path(cache_dir)
        self._opener = opener
        # When a custom opener is injected (tests), do NOT resolve hostnames
        # (no live DNS in the sandbox); mirror screenscraper's behavior.
        if resolve_urls is None:
            resolve_urls = opener is None
        self._resolve = bool(resolve_urls)

    # --- Cache locations -----------------------------------------------------

    def _root(self) -> Path:
        return self.cache_dir / "retrokit"

    def _index_file(self) -> Path:
        return self._root() / f"{self.config.system}.index.tsv"

    def _index_meta_file(self) -> Path:
        return self._root() / f"{self.config.system}.index.meta.json"

    def _manual_dir(self) -> Path:
        return self._root() / "manuals" / self.config.system

    def _negative_file(self, key: str) -> Path:
        safe = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self._root() / f"neg_{safe}.json"

    # --- Index ----------------------------------------------------------------

    def _index_url(self) -> str:
        return (
            f"https://archive.org/download/{self.config.item_id}/"
            f"{self.config.system}/{self.config.system}-sources.tsv"
        )

    def load_index(self, *, refresh: bool = False) -> list[dict[str, str]]:
        """Load the per-system TSV index from cache (TTL) or fetch it.

        Graceful: a fetch failure with no usable cache returns ``[]`` (a
        non-fatal miss), so a remote outage never fails the builder.
        """
        idx_file = self._index_file()
        meta_file = self._index_meta_file()
        if not refresh and idx_file.is_file():
            age = _index_age(meta_file)
            if age <= self.config.index_ttl_seconds:
                try:
                    text = idx_file.read_text(encoding="utf-8")
                    rows = parse_index(text)
                    if rows:
                        return rows
                except Exception:
                    pass  # corrupt cache -> refetch

        data: Optional[bytes]
        try:
            data = _fetch_text(
                self._index_url(),
                timeout=self.config.timeout_seconds,
                max_bytes=MAX_INDEX_BYTES,
                opener=self._opener,
                resolve=self._resolve,
            )
        except RetroKitRateLimited as e:
            # Contract: one bounded backoff retry, then a normal miss. A
            # rate limit must NEVER propagate out of load_index (that would
            # abort manual resolution for the whole run, unlike _download).
            data = None
            if self.config.respect_rate_limit and self.config.rate_limit_backoff_seconds > 0:
                time.sleep(min(e.retry_after, self.config.rate_limit_backoff_seconds))
                try:
                    data = _fetch_text(
                        self._index_url(),
                        timeout=self.config.timeout_seconds,
                        max_bytes=MAX_INDEX_BYTES,
                        opener=self._opener,
                        resolve=self._resolve,
                    )
                except Exception:
                    data = None
        except RetroKitError:
            data = None
        if data is None:
            # Outage / rate-limited / missing index -> non-fatal miss
            # (return what we can from a stale cache, else []).
            try:
                if idx_file.is_file():
                    return parse_index(idx_file.read_text(encoding="utf-8"))
            except Exception:
                pass
            return []

        text = data.decode("utf-8", errors="replace")
        rows = parse_index(text)
        if rows:
            try:
                _atomic_write(idx_file, data)
                _write_json_atomic(
                    meta_file,
                    {
                        "_cached_at": time.time(),
                        "url": self._index_url(),
                        "item_id": self.config.item_id,
                        "system": self.config.system,
                        "rows": len(rows),
                    },
                )
            except OSError:
                pass
        return rows

    # --- Negative cache ------------------------------------------------------

    def _neg_key(self, title: str) -> str:
        return f"neg:{self.config.system}:{title.strip().lower()}"

    def _check_negative(self, title: str) -> bool:
        f = self._negative_file(self._neg_key(title))
        if not f.is_file():
            return False
        data = _read_json(f) or {}
        cached_at = data.get("_cached_at", 0)
        if time.time() - cached_at > self.config.negative_ttl_seconds:
            return False
        return True

    def _mark_negative(self, title: str) -> None:
        try:
            _write_json_atomic(
                self._negative_file(self._neg_key(title)),
                {"_cached_at": time.time(), "system": self.config.system},
            )
        except OSError:
            pass

    # --- Resolution ----------------------------------------------------------

    def resolve_manual(self, group) -> RetroKitResult:
        """Discover + conservatively resolve the best manual candidate.

        Does NOT download. A clean single candidate -> ``matched`` (not
        ambiguous). Multiple distinct URLs for the chosen language, or only
        low-confidence (plausible) candidates -> ``routed_for_review``.
        No index / no match -> a normal miss (``matched=False``).
        """
        title = (getattr(group, "title", None) or "").strip()
        result = RetroKitResult(item_id=self.config.item_id, system=self.config.system)
        if not title:
            result.error = "empty group title"
            return result

        # Bounded negative lookup: if we recently found no manual for this
        # title, skip the (otherwise expensive) index pass entirely.
        if self._check_negative(title):
            result.error = "negative lookup cached"
            return result

        index = self.load_index()
        if not index:
            result.error = "index unavailable (offline or missing)"
            return result

        identities = _group_identities(group)
        # Tier of every auto-eligible candidate row (exact/canonical) +
        # track plausible-only for review routing.
        matched_rows: list[tuple[str, dict, str]] = []  # (tier, row, identity)
        plausible_titles: list[str] = []
        for row in index:
            hit_tier: Optional[str] = None
            hit_ident: Optional[str] = None
            for ident in identities:
                m, tier = _match_tier(ident, row["title"])
                if m:
                    if tier in ("exact", "canonical"):
                        hit_tier, hit_ident = tier, ident
                        break
                    if tier == "plausible":
                        hit_tier, hit_ident = tier, ident  # keep scanning for a stronger hit
            if hit_tier is None:
                continue
            if hit_tier in ("exact", "canonical"):
                matched_rows.append((hit_tier, row, hit_ident))
            else:
                plausible_titles.append(row["title"])

        if not matched_rows:
            if plausible_titles:
                result.matched = True
                result.ambiguous = True
                result.routed_for_review = True
                result.review_reason = (
                    "only low-confidence RetroKit manual candidates; "
                    "routed for review"
                )
                result.match_method = "retrokit:plausible"
                result.match_confidence = 0.92
                result.match_evidence = [
                    f"retrokit:plausible:{t}" for t in plausible_titles[:3]
                ]
                return result
            self._mark_negative(title)
            result.error = "no matching RetroKit manual"
            return result

        # Group the auto-eligible candidates by (normalized) language.
        by_lang: dict[str, list[tuple[dict, str]]] = {}
        for tier, row, ident in matched_rows:
            langs = _row_languages(row["langs"]) or ["unknown"]
            for lg in langs:
                by_lang.setdefault(lg, []).append((row, tier))

        available = list(by_lang.keys())
        chosen_lang = _choose_language(available, self.config.preferred_languages)
        if chosen_lang is None:
            chosen_lang = sorted(available)[0]

        entries = by_lang.get(chosen_lang, [])
        # Dedupe by URL (keep the first occurrence / its tier).
        by_url: dict[str, tuple[dict, str]] = {}
        for row, tier in entries:
            by_url.setdefault(row["url"], (row, tier))

        if len(by_url) > 1:
            # Variant preference (deterministic, issue #10 section 4): when the
            # operator prefers the original artifact (``prefer_original``) and
            # exactly ONE archive.org original is present, choose it; the other
            # distinct URLs are third-party/optimized mirrors and are suppressed
            # (the choice is recorded in evidence). With no original -- or with
            # multiple originals -- the candidates are genuinely distinct and the
            # group is routed for review rather than silently accepted.
            originals = [
                url for url in by_url if _is_archive_org(url)
            ]
            if self.config.prefer_original and len(originals) == 1:
                chosen_url = originals[0]
                row, tier = by_url[chosen_url]
                result.matched = True
                result.source_title = row["title"]
                result.url = chosen_url
                result.language = chosen_lang
                result.variant = "original"
                result.match_method = f"retrokit:{tier}:prefer_original"
                result.match_confidence = 1.0 if tier == "exact" else 0.95
                result.match_evidence = [
                    f"retrokit:prefer_original:{chosen_url}",
                    f"retrokit:variants_suppressed:{len(by_url) - 1}",
                ]
                return result
            result.matched = True
            result.ambiguous = True
            result.routed_for_review = True
            result.language = chosen_lang
            result.review_reason = (
                f"multiple distinct RetroKit manual URLs for language "
                f"'{chosen_lang}'; routed for review"
            )
            result.match_method = "retrokit:ambiguous"
            result.match_confidence = 0.0
            result.match_evidence = [f"retrokit:url:{u}" for u in list(by_url)[:5]]
            return result

        row, tier = next(iter(by_url.values()))
        result.matched = True
        result.source_title = row["title"]
        result.url = row["url"]
        result.language = chosen_lang
        result.variant = "original" if _is_archive_org(row["url"]) else "optimized"
        result.match_method = f"retrokit:{tier}"
        result.match_confidence = 1.0 if tier == "exact" else 0.95
        result.match_evidence = [f"retrokit:{tier}:{row['title']}"]
        return result

    # --- Acquisition ---------------------------------------------------------

    def _manual_key(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]

    def acquire(self, result: RetroKitResult) -> RetroKitResult:
        """Download the resolved manual to the managed cache (UNMUTATED).

        Idempotent: a cached file whose SHA-256 matches its metadata is reused
        (no re-download). A download failure is a normal miss (``error`` set,
        ``local_path`` stays ``None``); it never raises.
        """
        if not result.matched or result.ambiguous or not result.url:
            return result
        url = result.url
        key = self._manual_key(url)
        out_file = self._manual_dir() / f"{key}.pdf"
        meta_file = out_file.with_suffix(".meta.json")

        # Cache hit (valid metadata) -> reuse, no network.
        if out_file.is_file():
            meta = _read_json(meta_file) or {}
            recorded = meta.get("sha256")
            if recorded:
                try:
                    if recorded == _sha256_file(out_file):
                        result.local_path = out_file
                        result.sha256 = recorded
                        result.size = out_file.stat().st_size
                        result.variant = meta.get("variant") or result.variant
                        return result
                except OSError:
                    pass

        data = self._download(url)
        if data is None:
            result.error = result.error or "download failed"
            return result
        if len(data) > self.config.max_manual_bytes:
            result.error = f"manual exceeds {self.config.max_manual_bytes} bytes"
            return result

        _atomic_write(out_file, data)
        sha = hashlib.sha256(data).hexdigest()
        _write_json_atomic(
            meta_file,
            {
                "provider": "retrokit",
                "transport": "archive.org",
                "url": url,
                "item_id": self.config.item_id,
                "system": self.config.system,
                "source_title": result.source_title,
                "language": result.language,
                "variant": result.variant or "original",
                "match_method": result.match_method,
                "match_confidence": result.match_confidence,
                "sha256": sha,
                "size": len(data),
                "cached_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        result.local_path = out_file
        result.sha256 = sha
        result.size = len(data)
        result.variant = result.variant or "original"
        return result

    def _download(self, url: str) -> Optional[bytes]:
        """Bounded single-artifact download with one rate-limit backoff."""
        try:
            return _fetch_binary(
                url,
                timeout=self.config.timeout_seconds,
                max_bytes=self.config.max_manual_bytes,
                opener=self._opener,
                resolve=self._resolve,
            )
        except RetroKitRateLimited as e:
            if self.config.respect_rate_limit and self.config.rate_limit_backoff_seconds > 0:
                time.sleep(min(e.retry_after, self.config.rate_limit_backoff_seconds))
                try:
                    return _fetch_binary(
                        url,
                        timeout=self.config.timeout_seconds,
                        max_bytes=self.config.max_manual_bytes,
                        opener=self._opener,
                        resolve=self._resolve,
                    )
                except Exception:
                    return None
            return None
        except Exception:
            # Any fetch failure (missing file, outage, malformed) is a miss.
            return None

    # --- Convenience ---------------------------------------------------------

    def resolve_and_acquire(self, group) -> RetroKitResult:
        """Resolve then (only if clean) acquire. Never raises for normal misses."""
        result = self.resolve_manual(group)
        if result.matched and not result.ambiguous:
            result = self.acquire(result)
        return result


def _index_age(meta_file: Path) -> float:
    data = _read_json(meta_file) or {}
    try:
        return max(0.0, time.time() - float(data.get("_cached_at", 0)))
    except (TypeError, ValueError):
        return float("inf")


# --- RTFM integration helper -------------------------------------------------


def to_rtfm_sources(results: list[RetroKitResult]) -> list:
    """Convert clean, acquired RetroKit results into RTFM ``RtfmSource`` entries.

    Each cached PDF is surfaced as a ``manuals``-category source whose ``stem``
    is set so the RTFM core's ``score_source_match`` auto-accepts it (exact
    identity) and whose ``_canonical_game_key`` collides with any higher-fidelity
    local source (so the existing dedupe suppresses duplicates). The PDF itself
    is read by the existing Issue #5 extraction layer -- no second PDF stack.
    """
    from .rtfm import CATEGORY_MANUALS, RtfmSource

    out: list = []
    seen_paths: set[str] = set()
    for res in results:
        if not (res.matched and not res.ambiguous and res.local_path is not None):
            continue
        p = Path(res.local_path)
        try:
            if not p.is_file() or p.stat().st_size == 0:
                continue
        except OSError:
            continue
        key = str(p)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        # stem is the source title (the RetroKit canonical title), which the RTFM
        # core matches against the group title/basename for auto-accept.
        out.append(
            RtfmSource(
                path=p,
                root=p.parent,
                category=CATEGORY_MANUALS,
                stem=res.source_title or p.stem,
            )
        )
    return out


def has_local_match(discovered_sources: list, group) -> bool:
    """True when a discovered local source already covers this group.

    Used to AVOID downloading a manual the operator already has locally (the
    RTFM dedupe would suppress it anyway; this just skips the network cost).
    """
    try:
        from .rtfm import RtfmSource, _canonical_game_key, CATEGORY_MANUALS
    except Exception:
        return False
    bn = _group_basename(group)
    try:
        dummy = RtfmSource(
            path=Path(bn + ".pdf"), root=Path("."),
            category=CATEGORY_MANUALS, stem=bn,
        )
        gkey = _canonical_game_key(dummy)
    except Exception:
        return False
    for s in discovered_sources:
        try:
            if _canonical_game_key(s) == gkey:
                return True
        except Exception:
            continue
    return False
