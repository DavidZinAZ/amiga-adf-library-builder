"""Optional RetroAchievements metadata/artwork provider (GH-13).

This is an OPTIONAL provider. It is DISABLED by default and only activates when
an explicit ``[retroachievements]`` TOML table is present AND ``enabled = true``
AND a valid API key is supplied via the ``RETROACHIEVEMENTS_API_KEY``
environment variable (or the SecretStore). The key is NEVER stored in a config
file.

=== ACCESS MODEL (RetroAchievements Web API) ===

RetroAchievements (RA) exposes a small public Web API:

- ``/API/API_GetConsoleIDs.php``
    Returns the list of consoles (systems) RA supports. Each entry carries an
    ``ID`` and a ``Name``. The Amiga console ID is resolved AT RUNTIME from this
    list (never hardcoded). If RA has no Amiga console the provider degrades to
    a clean miss (cached negatively) and never re-fetches on every run.

- ``/API/API_GetGameList.php?i=<consoleId>&h=1&y=<api_key>``
    Returns a TOP-LEVEL JSON LIST of games for the given console. With ``h=1``
    each game object includes a ``Hashes`` array of public ROM MD5 hashes. The
    list is cached under the managed metadata cache dir with a bounded TTL.

The provider uses ONLY the public ROM MD5 hash (computed locally from the group's
first non-special disk) as the PRIMARY lookup signal. An exact MD5 match
OUTRANKS any title fallback. No private ROM filenames, paths, or collection
details are transmitted.

=== Design posture (mirrors igdb.py / screenscraper.py exactly) ===

* **OFFLINE-capable at import.** Nothing touches the network at import time.
  Real fetches use stdlib ``urllib.request`` guarded by
  :func:`metadata.guard_url` and are reached ONLY during a real fetch. Tests
  inject a fake ``opener`` and pass ``online=False`` / a warm cache, so no
  DNS/socket is ever exercised in the suite.
* **Hash-first identity (MD5).** The group's first non-special disk is hashed
  locally (read-only) to an MD5; an exact match against a game's public ``Hashes``
  array is the only auto-accept path (confidence 1.0). There is deliberately no
  fuzzy/ title fallback: a non-matching hash is a clean miss, never a guess.
* **Non-fatal outages.** Provider outage / timeout / oversize response / 429 /
  bad key MUST NOT raise into the pipeline. We catch and return ``found=False``.
* **SSRF guard.** Every outbound fetch runs :func:`metadata.guard_url`
  (``resolve=True`` only on a real fetch) before any bytes move. Private/
  loopback/link-local/RFC1918 hosts are refused. Redirects are NOT followed.
* **Bounds.** ``timeout_seconds`` and ``max_response_bytes`` are bounded; a
  response that exceeds ``max_response_bytes`` is short-circuited and treated
  as a non-fatal miss.
* **Privacy.** Only the public ROM MD5 and the RA API key are transmitted. The
  cache stores ONLY public fields (game id, title, icon, source URL). No private
  filename, path, or local detail is ever written to a cache or transmitted.
* **Credentials via environment / SecretStore ONLY.** The API key is NEVER in a
  config file. It is read from ``RETROACHIEVEMENTS_API_KEY`` by the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from . import metadata as _metadata
from .metadata import UnsafeUrlError


# --- Bounds / defaults -------------------------------------------------------

#: Public default RetroAchievements origin. Overridable in config for
#: self-host / enterprise-compatible instances.
DEFAULT_BASE_URL = "https://retroachievements.org"

#: Hard upper bounds so a config cannot weaken the safety posture.
_MAX_TIMEOUT_SECONDS = 30.0
_MAX_RESPONSE_BYTES = 16_000_000

#: Default bounded values (safe, conservative).
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000

#: Game-list cache TTL (seconds). <= 0 disables reuse.
DEFAULT_CACHE_TTL = 86400.0

#: Negative-result / console-missing cache TTL (seconds). <= 0 disables reuse.
DEFAULT_NEGATIVE_TTL = 86400.0

#: The RA console (system) we resolve at runtime. Not a hardcoded ID.
DEFAULT_CONSOLE_NAME = "amiga"

#: Environment variable that carries the RA API key (never in config).
API_KEY_ENV = "RETROACHIEVEMENTS_API_KEY"

#: Confidence assigned to an exact MD5 hash match.
EXACT_HASH_CONFIDENCE = 1.0


# --- Match method ------------------------------------------------------------

class RaMatchMethod(str, Enum):
    """How a RetroAchievements identity was resolved for a release group."""

    EXACT_HASH = "exact_hash"        # exact MD5 match against a game's Hashes
    CONSOLE_MISSING = "console_missing"  # RA has no matching console (clean miss)
    NO_HASH = "no_hash"              # no usable disk / MD5 could not be computed
    NONE = "none"                    # fetched list but no matching hash


class RaError(Exception):
    """Base error for RetroAchievements provider failures."""


class RaDisabled(RaError):
    """Raised when the provider is used while disabled in config."""


class RaAuthError(RaError):
    """Raised when the RA API key is rejected (401/403)."""


class RaRateLimited(RaError):
    """Raised on HTTP 429 so the caller can apply a bounded Retry-After backoff."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"RetroAchievements rate limited; retry after {retry_after:.1f}s")


# --- Configuration -----------------------------------------------------------

@dataclass(frozen=True)
class RaConfig:
    """Typed view of the ``[retroachievements]`` TOML table."""

    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    cache_ttl: float = DEFAULT_CACHE_TTL
    negative_ttl: float = DEFAULT_NEGATIVE_TTL
    console_name: str = DEFAULT_CONSOLE_NAME
    respect_rate_limit: bool = True
    rate_limit_backoff_seconds: float = 1.0
    confidence_threshold: float = EXACT_HASH_CONFIDENCE

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "RaConfig":
        """Build a config from the raw ``[retroachievements]`` table (or None).

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
        base_url = base_url.rstrip("/")

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
            cache_ttl = float(raw.get("cache_ttl", DEFAULT_CACHE_TTL))
        except (TypeError, ValueError):
            cache_ttl = DEFAULT_CACHE_TTL
        if cache_ttl < 0:
            cache_ttl = 0.0

        try:
            negative_ttl = float(raw.get("negative_ttl", DEFAULT_NEGATIVE_TTL))
        except (TypeError, ValueError):
            negative_ttl = DEFAULT_NEGATIVE_TTL
        if negative_ttl < 0:
            negative_ttl = 0.0

        console_name = (raw.get("console_name") or DEFAULT_CONSOLE_NAME)
        if not isinstance(console_name, str) or not console_name.strip():
            console_name = DEFAULT_CONSOLE_NAME

        respect = bool(raw.get("respect_rate_limit", True))

        try:
            backoff = float(raw.get("rate_limit_backoff_seconds", 1.0))
        except (TypeError, ValueError):
            backoff = 1.0
        if backoff < 0 or backoff > 5.0:
            backoff = 1.0

        try:
            conf = float(raw.get("confidence_threshold", EXACT_HASH_CONFIDENCE))
        except (TypeError, ValueError):
            conf = EXACT_HASH_CONFIDENCE
        if conf < 0.0 or conf > 1.0:
            conf = EXACT_HASH_CONFIDENCE

        return cls(
            enabled=enabled,
            base_url=base_url,
            timeout_seconds=timeout,
            max_response_bytes=max_bytes,
            cache_ttl=cache_ttl,
            negative_ttl=negative_ttl,
            console_name=console_name.strip().lower(),
            respect_rate_limit=respect,
            rate_limit_backoff_seconds=backoff,
            confidence_threshold=conf,
        )


# --- Result ------------------------------------------------------------------

@dataclass
class RaResult:
    """Outcome of resolving RetroAchievements identity for one release group.

    Mirrors :class:`~amiga_adf_library_builder.igdb.IgdbResult` public fields.
    All fields are deterministic given the same inputs.
    """

    group_title: Optional[str]
    group_release_key: str
    found: bool = False
    category: Optional[str] = None
    match_method: RaMatchMethod = RaMatchMethod.NONE
    confidence: float = 0.0
    needs_manual_review: bool = False
    manual_review_reason: Optional[str] = None
    provider_id: Optional[str] = None  # RA game ID
    provenance: Optional[dict] = None
    candidates_evaluated: list = field(default_factory=list)
    # Normalized public external correlations (e.g. {"retroachievements_id": "..."}).
    external_ids: dict = field(default_factory=dict)
    # Artwork URLs from RetroAchievements (game icon).
    artwork_urls: list = field(default_factory=list)
    artwork_provider: str = ""
    # Full metadata record fields (see enrich merge).
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "group_title": self.group_title,
            "group_release_key": self.group_release_key,
            "found": self.found,
            "category": self.category,
            "match_method": self.match_method.value,
            "confidence": self.confidence,
            "needs_manual_review": self.needs_manual_review,
            "manual_review_reason": self.manual_review_reason,
            "provider_id": self.provider_id,
            "provenance": self.provenance,
            "candidates_evaluated": list(self.candidates_evaluated),
            "external_ids": dict(self.external_ids),
            "artwork_urls": list(self.artwork_urls),
            "artwork_provider": self.artwork_provider,
            "metadata": self.metadata,
        }


# --- Cache (privacy-bounded) -------------------------------------------------

def _cache_file(cache_dir: Path, key: str) -> Path:
    # Key is always a public signal (console id / console marker). We never
    # persist filenames/paths/private hashes as cache keys or values beyond the
    # explicitly-allowed public fields.
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in key)
    return Path(cache_dir) / f"ra-{safe}.json"


def _cache_store(cache_dir: Path, key: str, entry: dict) -> None:
    """Write a privacy-bounded cache entry (best-effort)."""
    try:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = _cache_file(cache_dir, key)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        # Caching is best-effort; a cache failure must never break the run.
        return


def _cache_load(cache_dir: Path, key: str, ttl: float) -> Optional[dict]:
    try:
        path = _cache_file(cache_dir, key)
        if not path.is_file():
            return None
        if ttl > 0 and (time.time() - path.stat().st_mtime) > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- Fetch plumbing (stdlib only) --------------------------------------------

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects.

    A 3xx response is surfaced rather than silently re-fetched, so a rogue 30x
    to a cloud-metadata / loopback / RFC1918 host is NEVER fetched on the
    second hop.
    """

    def http_error_302(self, req, fp, code, msg, headers):
        return self._refuse(req, fp, code, msg, headers)

    def http_error_303(self, req, fp, code, msg, headers):
        return self._refuse(req, fp, code, msg, headers)

    def http_error_307(self, req, fp, code, msg, headers):
        return self._refuse(req, fp, code, msg, headers)

    def http_error_308(self, req, fp, code, msg, headers):
        return self._refuse(req, fp, code, msg, headers)

    def _refuse(self, req, fp, code, msg, headers):
        return fp


def _build_no_redirect_opener(*, timeout: float) -> urllib.request.OpenerDirector:
    """Build a hardened opener with explicit, minimal handlers."""
    opener = urllib.request.OpenerDirector()
    opener.add_handler(_NoRedirectHandler())
    opener.add_handler(urllib.request.ProxyHandler({}))
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(urllib.request.HTTPSHandler())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    return opener


def _retry_after_seconds(headers) -> Optional[float]:
    """Parse a Retry-After header (delta-seconds) into a float, if present."""
    if headers is None:
        return None
    raw = headers.get("Retry-After")
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val < 0:
        return None
    return val


def _stream_read(resp, *, max_bytes: int) -> bytes:
    """Read ``resp`` in bounded chunks, aborting before the body buffers."""
    chunks: list[bytes] = []
    total = 0
    chunk_size = min(64 * 1024, max(max_bytes, 1))
    while True:
        chunk = resp.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _default_opener(url: str, *, timeout: float,
                    max_bytes: int = _MAX_RESPONSE_BYTES) -> bytes:
    """Real network opener. Guarded by callers (guard_url + size bound)."""
    opener = _build_no_redirect_opener(timeout=timeout)
    req = urllib.request.Request(url, headers={"User-Agent": "amiga-adf-builder/retroachievements"})
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        code = getattr(exc, "code", None)
        if code == 429:
            raise RaRateLimited(_retry_after_seconds(getattr(exc, "headers", None)) or 0.0)
        if code in (401, 403):
            raise RaAuthError("RetroAchievements API key rejected")
        raise
    status = getattr(resp, "status", None)
    if status is not None and status >= 300:
        raise RaError(
            f"RetroAchievements endpoint returned non-success status {status} "
            f"(redirects are not followed)"
        )
    return _stream_read(resp, max_bytes=max_bytes)


# --- URL construction --------------------------------------------------------

def _console_url(base_url: str) -> str:
    return f"{base_url}/API/API_GetConsoleIDs.php"


def _game_list_url(base_url: str, console_id: int, api_key: str) -> str:
    params = urllib.parse.urlencode({"i": console_id, "h": 1, "y": api_key})
    return f"{base_url}/API/API_GetGameList.php?{params}"


# --- Local hashing / disk selection ------------------------------------------

def _md5_of_file(path: Path, *, chunk_size: int = 1024 * 1024) -> Optional[str]:
    """Compute the MD5 of a disk file, read-only. Returns None on any failure."""
    try:
        digest = hashlib.md5()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _first_disk_path(group, scans) -> Optional[Path]:
    """Return the filesystem path of the group's FIRST non-special disk.

    ``scans`` may be either form the pipeline passes:
      * a list/tuple of :class:`ScanRecord` (``run_pipeline`` form), or
      * a dict mapping filename -> :class:`ScanRecord` (``enrich_group``'s
        ``scan_map``).
    We match the first disk's ``source_filename`` to a scan and return its
    on-disk path. Returns None when no usable disk or no matching scan is
    available (a clean miss, never a guess).
    """
    disks = getattr(group, "disks", None) or []
    if not disks:
        records = getattr(group, "records", None) or []
        disks = [r for r in records if not getattr(r, "special_disk", False)]
    if not disks:
        return None
    first = disks[0]
    fname = getattr(first, "source_filename", None)
    if not fname or scans is None:
        return None
    if isinstance(scans, dict):
        scan = scans.get(fname)
    else:
        scan = next((s for s in scans if getattr(s, "filename", None) == fname), None)
    if scan is None:
        return None
    path = getattr(scan, "path", None)
    if path is None:
        return None
    return Path(path)


# --- Provider ----------------------------------------------------------------

class RetroAchievementsProvider:
    """Optional RetroAchievements metadata/artwork provider.

    Usage::

        cfg = load_retroachievements_config(config_path)
        ra_cfg = RaConfig.from_dict(cfg)
        if ra_cfg.enabled and api_key:
            provider = RetroAchievementsProvider(ra_cfg, cache_dir, api_key)
            provider.discover()
            result = provider.resolve(group, scans=scans, online=online)
    """

    def __init__(
        self,
        config: RaConfig,
        cache_dir: Path,
        api_key: str,
        *,
        opener: Optional[Callable[..., bytes]] = None,
    ) -> None:
        if not config.enabled:
            raise RaDisabled("retroachievements provider is disabled in config")
        if not api_key or not api_key.strip():
            raise RaDisabled("retroachievements provider requires an API key")
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.api_key = api_key.strip()
        if opener is not None:
            self._opener = opener
            self._resolve = False
        else:
            self._opener = lambda url, *, timeout: _default_opener(
                url, timeout=timeout, max_bytes=self.config.max_response_bytes
            )
            self._resolve = True
        self._discovered = False
        self._lock = threading.RLock()

    def discover(self) -> int:
        """Validate config sanity. Returns the bounded concurrency (>=1)."""
        if not self.config.enabled:
            raise RaDisabled("retroachievements provider is disabled in config")
        if not self.api_key:
            raise RaDisabled("retroachievements provider requires an API key")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._discovered = True
        return 1

    # -- fetch ---------------------------------------------------------------

    def _fetch(self, url: str) -> Optional[bytes]:
        """Fetch ``url`` through the (possibly injected) opener, guarded + bounded.

        Returns raw bytes, or None on any failure (outage, guard, oversize, 429
        that does not recover). Never raises into the caller.
        """
        try:
            _metadata.guard_url(url, resolve=self._resolve)
        except UnsafeUrlError:
            return None
        raw: Optional[bytes] = None
        for attempt in (1, 2):
            try:
                raw = self._opener(url, timeout=self.config.timeout_seconds)
                break
            except RaRateLimited as exc:
                if attempt == 1 and self.config.respect_rate_limit:
                    time.sleep(min(exc.retry_after or self.config.rate_limit_backoff_seconds, 5.0))
                    continue
                return None
            except Exception:
                return None
        if raw is None:
            return None
        if len(raw) > self.config.max_response_bytes:
            return None
        return raw

    def _parse_json_list(self, raw: bytes) -> Optional[list]:
        """Parse a response into a list, tolerating a common wrapper object."""
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("Data", "Consoles", "consoles", "systems", "Games", "games"):
                inner = data.get(key)
                if isinstance(inner, list):
                    return inner
        return None

    # -- console resolution --------------------------------------------------

    def _ensure_console_id(self, online: bool) -> Optional[int]:
        """Resolve the RA console (system) ID at runtime, cached.

        Returns the console ID, or None when RA has no matching console, when we
        are offline with no warm cache, or when the console list cannot be
        fetched. A positive resolution and a definitive "missing" are both
        cached (negatively) to avoid re-fetching on every run.
        """
        cached = _cache_load(self.cache_dir, "console", self.config.negative_ttl)
        if cached is not None:
            if cached.get("missing"):
                return None
            cid = cached.get("console_id")
            if isinstance(cid, int):
                return cid
        if not online:
            return None
        raw = self._fetch(_console_url(self.config.base_url))
        if raw is None:
            # Outage/timeout: do NOT cache negative (retry next time).
            return None
        consoles = self._parse_json_list(raw)
        if consoles is None:
            return None
        target = self.config.console_name.lower()
        for console in consoles:
            if not isinstance(console, dict):
                continue
            name = str(
                console.get("Name")
                or console.get("FriendlyName")
                or console.get("DisplayName")
                or ""
            ).strip().lower()
            if name == target:
                cid = console.get("ID")
                if isinstance(cid, int):
                    _cache_store(self.cache_dir, "console", {"console_id": cid})
                    return cid
        # Definitively no matching console -> negative cache.
        _cache_store(self.cache_dir, "console", {"missing": True})
        return None

    # -- game list ------------------------------------------------------------

    def _fetch_game_list(self, console_id: int, online: bool) -> Optional[list]:
        """Fetch (or load from cache) the RA game list for a console."""
        key = f"games:{console_id}"
        cached = _cache_load(self.cache_dir, key, self.config.cache_ttl)
        if cached is not None and isinstance(cached.get("games"), list):
            return cached["games"]
        if not online:
            return None
        url = _game_list_url(self.config.base_url, console_id, self.api_key)
        raw = self._fetch(url)
        if raw is None:
            return None
        games = self._parse_json_list(raw)
        if games is None:
            return None
        _cache_store(self.cache_dir, key, {"games": games})
        return games

    # -- icon / metadata ------------------------------------------------------

    def _icon_url(self, icon: str) -> str:
        """Join a site-relative RA icon to the configured origin."""
        icon = (icon or "").strip()
        if not icon:
            return ""
        if icon.startswith("http://") or icon.startswith("https://"):
            return icon
        base = self.config.base_url.rstrip("/")
        if not icon.startswith("/"):
            icon = "/" + icon
        return f"{base}{icon}"

    def _hit(self, base: RaResult, game: dict, console_id: int) -> RaResult:
        game_id = game.get("ID")
        game_title = game.get("Title") or base.group_title or "Unknown"
        icon = game.get("ImageIcon") or game.get("Icon") or ""
        icon_url = self._icon_url(icon)
        base_url = self.config.base_url
        source_url = f"{base_url}/game/{game_id}" if game_id is not None else ""
        artwork_urls = [icon_url] if icon_url else []
        metadata = {
            "canonical_title": game_title,
            "source_url": source_url,
            "provider_id": str(game_id) if game_id is not None else "",
            "provider": "retroachievements",
            "retrieved_at": _metadata.utc_now(),
            "artwork_urls": artwork_urls,
            "artwork_provider": "retroachievements" if artwork_urls else "",
        }
        return RaResult(
            group_title=base.group_title,
            group_release_key=base.group_release_key,
            found=True,
            category="retroachievements",
            match_method=RaMatchMethod.EXACT_HASH,
            confidence=EXACT_HASH_CONFIDENCE,
            provider_id=str(game_id) if game_id is not None else None,
            external_ids={"retroachievements_id": str(game_id)} if game_id is not None else {},
            artwork_urls=artwork_urls,
            artwork_provider="retroachievements" if artwork_urls else "",
            metadata=metadata,
            provenance={"kind": "exact_md5", "console_id": console_id},
            candidates_evaluated=[{"kind": "exact_md5", "matched": True}],
        )

    # -- resolve --------------------------------------------------------------

    def resolve(self, group, scans=None, online: bool = False) -> RaResult:
        """Resolve RetroAchievements identity for ``group`` via exact MD5 match.

        Outage/timeout/oversize/429/bad-key are non-fatal: we return
        ``found=False`` and never raise into the caller. Offline runs never touch
        the network (they may still use a warm cache).
        """
        if not self.config.enabled:
            raise RaDisabled("retroachievements provider is disabled in config")
        if not self._discovered:
            self.discover()

        title = (getattr(group, "title", None) or "").strip()
        release_key = (getattr(group, "release_key", "") or "")
        result = RaResult(group_title=title, group_release_key=release_key)

        # 1. Local MD5 of the first non-special disk (read-only, no network).
        path = _first_disk_path(group, scans)
        if path is None:
            result.match_method = RaMatchMethod.NO_HASH
            result.candidates_evaluated = [{"kind": "no_disk", "title": title[:32]}]
            return result
        md5 = _md5_of_file(path)
        if not md5:
            result.match_method = RaMatchMethod.NO_HASH
            result.candidates_evaluated = [{"kind": "md5_failed", "title": title[:32]}]
            return result

        # 2. Resolve the RA console ID (cached; no network when offline).
        console_id = self._ensure_console_id(online)
        if console_id is None:
            result.match_method = RaMatchMethod.CONSOLE_MISSING
            result.candidates_evaluated = [
                {"kind": "console_missing", "console": self.config.console_name}
            ]
            return result

        # 3. Fetch (or cache) the console game list (no network when offline).
        games = self._fetch_game_list(console_id, online)
        if games is None:
            result.match_method = RaMatchMethod.NONE
            result.candidates_evaluated = [{"kind": "game_list", "outcome": "no_response"}]
            return result

        # 4. Exact MD5 match against each game's public Hashes array.
        md5_lower = md5.lower()
        for game in games:
            if not isinstance(game, dict):
                continue
            hashes = game.get("Hashes")
            if not isinstance(hashes, list):
                hashes = game.get("hashes")
            if not isinstance(hashes, list):
                continue
            if any(isinstance(h, str) and h.strip().lower() == md5_lower for h in hashes):
                return self._hit(result, game, console_id)

        result.match_method = RaMatchMethod.NONE
        result.candidates_evaluated = [{"kind": "md5_search", "outcome": "not_found"}]
        return result
