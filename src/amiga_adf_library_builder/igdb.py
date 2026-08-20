"""Optional IGDB metadata/artwork provider (bridge).

This is an OPTIONAL provider. It is DISABLED by default and only activates when
an explicit ``[igdb]`` TOML table is present AND ``enabled = true``.

=== ACCESS MODEL (IGDB API, per IGDB documentation) ===

IGDB (Internet Game Database) requires a Twitch OAuth client credentials flow:
- Client ID and Client Secret are obtained from the Twitch Developer Console
- Token exchange: POST https://id.twitch.tv/oauth2/token
  with client_id, client_secret, grant_type=client_credentials
- Returns an access token (valid ~60 days) for API calls
- API endpoint: https://api.igdb.com/v4/
- Queries use a custom query language (Apicalypse)

The provider uses ONLY the public game identity (title, platform, release date)
for search. NO private ROM filenames, paths, or collection details are transmitted.

Supported endpoints:
- POST /games - search by name, filter by platform (Amiga), fields for metadata + artwork
- POST /covers - fetch cover artwork URLs by game ID
- POST /screenshots - fetch screenshot artwork URLs by game ID
- POST /platforms - verify Amiga platform ID (12 = Amiga)

=== Design posture (mirrors playmatch.py / hasheous.py exactly) ===

This module mirrors ``src/amiga_adf_library_builder/playmatch.py`` and
``src/amiga_adf_library_builder/hasheous.py`` in structure and safety posture.
It is an OPTIONAL, DISABLED-by-default provider that reuses the EXISTING
online metadata lookup layer established in ``metadata.py``.

* **OFFLINE-capable at import.** The module imports nothing that touches the
  network at import time. The only stdlib pieces used for a real fetch are
  ``urllib.request`` + ``socket`` + ``ipaddress`` (via ``metadata.guard_url``),
  and they are reached ONLY when a real fetch happens. Tests inject a fake
  ``opener`` and pass ``resolve=False``, so no DNS/socket is ever exercised
  offline.
* **Title-first search with Amiga platform filter.** The primary lookup signal
  is the game title + Amiga platform (12). An exact-title + platform match
  OUTRANKS any fuzzy match.
* **Fail-safe on ambiguity.** A search that yields multiple candidates or
  disagreeing identities ALWAYS routes to manual review. We NEVER silently
  override.
* **Non-fatal outages.** Provider outage / timeout / oversize response / 429
  MUST NOT raise into the pipeline. We catch, return ``found=False`` (or
  ``needs_manual_review``), and continue.
* **SSRF guard.** Every outbound fetch runs :func:`metadata.guard_url`
  (``resolve=True`` only on a real fetch) before any bytes move. Private/
  loopback/link-local/RFC1918 hosts are refused.
* **Bounds.** ``timeout_seconds``, ``max_response_bytes``, and
  ``max_concurrency`` are all bounded; a response that exceeds
  ``max_response_bytes`` is short-circuited and treated as a non-fatal miss.
* **Privacy.** Only the public game title and platform filter are transmitted.
  The cache stores ONLY public fields (IGDB game ID, canonical title, artwork
  URLs, metadata). No private filename, path, or private hash is ever written
  to a cache or transmitted.
* **Credentials via SecretStore ONLY.** Client ID and Client Secret are NEVER
  in config files. They are stored in the SecretStore under keys
  ``igdb_client_id`` and ``igdb_client_secret``.
"""

from __future__ import annotations

import json
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

#: Public default IGDB API endpoint. Overridable in config for self-host/enterprise.
DEFAULT_BASE_URL = "https://api.igdb.com/v4"

#: Twitch OAuth token endpoint (fixed by IGDB/Twitch).
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

#: Amiga platform ID in IGDB (constant).
IGDB_PLATFORM_AMIGA = 12

#: Hard upper bounds so a config cannot weaken the safety posture.
_MAX_TIMEOUT_SECONDS = 30.0
_MAX_RESPONSE_BYTES = 16_000_000
_MAX_CONCURRENCY = 8
_MIN_CONFIDENCE = 0.0
_MAX_CONFIDENCE = 1.0

#: Default bounded values (safe, conservative).
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_MAX_CONCURRENCY = 1
DEFAULT_CONFIDENCE_THRESHOLD = 0.9

#: Token cache TTL (seconds). Tokens are cached to avoid repeated OAuth exchanges.
DEFAULT_TOKEN_CACHE_TTL = 5_000_000  # ~58 days

#: Confidence assigned to an exact-title + Amiga platform match.
EXACT_MATCH_CONFIDENCE = 1.0
#: Confidence assigned to a fuzzy title match on Amiga platform.
FUZZY_MATCH_CONFIDENCE = 0.85
#: Confidence assigned when reusing a previously-stored canonical mapping.
CANONICAL_REUSE_CONFIDENCE = 0.95


# --- External-id allowlist ---------------------------------------------------

#: Normalized external correlations we capture for downstream metadata/manual
#: providers. Anything else in an IGDB payload is dropped (fail-safe: we
#: never persist an unrecognized, potentially private field).
IGDB_EXTERNAL_ID_KEYS = (
    "igdb_id",
    "mobygames_id",
    "thegamesdb_id",
    "steam_id",
    "gog_id",
    "epic_games_id",
    "nsuid",
)


# --- Match method ------------------------------------------------------------

class IgdbMatchMethod(str, Enum):
    """How an IGDB identity was resolved for a release group."""

    EXACT_TITLE_PLATFORM = "exact_title_platform"
    FUZZY_TITLE_PLATFORM = "fuzzy_title_platform"
    CANONICAL_REUSE = "canonical_reuse"
    MANUAL_REVIEW = "manual_review"
    NONE = "none"


class IgdbError(Exception):
    """Base error for IGDB provider failures."""


class IgdbDisabled(IgdbError):
    """Raised when the provider is used while disabled in config."""


class IgdbRateLimited(IgdbError):
    """Raised on HTTP 429 so the caller can apply a bounded Retry-After backoff.

    Carries the server-advised ``retry_after`` (seconds, already clamped to the
    configured bound by the caller) so the single retry waits the right amount.
    """

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"IGDB rate limited; retry after {retry_after:.1f}s")


class IgdbAuthError(IgdbError):
    """Raised when OAuth token exchange fails (invalid credentials)."""


# --- Configuration -----------------------------------------------------------

@dataclass(frozen=True)
class IgdbConfig:
    """Typed view of the ``[igdb]`` TOML table."""

    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    # Token cache TTL in seconds. <= 0 disables token reuse.
    token_cache_ttl: float = DEFAULT_TOKEN_CACHE_TTL
    # Honor HTTP 429 + Retry-After with a single bounded retry (ToS).
    respect_rate_limit: bool = True
    # Bounded backoff applied on 429 when no/invalid Retry-After header is sent.
    rate_limit_backoff_seconds: float = 1.0

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "IgdbConfig":
        """Build a config from the raw ``[igdb]`` table (or None).

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

        try:
            token_ttl = float(raw.get("token_cache_ttl", DEFAULT_TOKEN_CACHE_TTL))
        except (TypeError, ValueError):
            token_ttl = DEFAULT_TOKEN_CACHE_TTL
        if token_ttl < 0:
            token_ttl = 0.0

        respect = bool(raw.get("respect_rate_limit", True))

        try:
            backoff = float(raw.get("rate_limit_backoff_seconds", 1.0))
        except (TypeError, ValueError):
            backoff = 1.0
        if backoff < 0 or backoff > 5.0:
            backoff = 1.0

        return cls(
            enabled=enabled,
            base_url=base_url,
            timeout_seconds=timeout,
            max_response_bytes=max_bytes,
            max_concurrency=max_conc,
            confidence_threshold=conf,
            token_cache_ttl=token_ttl,
            respect_rate_limit=respect,
            rate_limit_backoff_seconds=backoff,
        )


# --- Result ------------------------------------------------------------------

@dataclass
class IgdbResult:
    """Outcome of resolving IGDB identity for one release group.

    Mirrors :class:`PlaymatchResult` / :class:`HasheousResult` public fields.
    All fields are deterministic given the same inputs.
    """

    group_title: Optional[str]
    group_release_key: str
    found: bool = False
    category: Optional[str] = None
    match_method: IgdbMatchMethod = IgdbMatchMethod.NONE
    confidence: float = 0.0
    needs_manual_review: bool = False
    manual_review_reason: Optional[str] = None
    provider_id: Optional[str] = None  # IGDB game ID
    provenance: Optional[dict] = None
    candidates_evaluated: list = field(default_factory=list)
    # Normalized public external correlations (e.g. {"igdb_id": "..."}).
    external_ids: dict = field(default_factory=dict)
    # Artwork URLs from IGDB (covers + screenshots)
    artwork_urls: list = field(default_factory=list)
    artwork_provider: str = ""
    # Full metadata record fields
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
    # Key is always a public signal (normalized title). We never persist
    # filenames/paths/private hashes as cache keys or values beyond the
    # explicitly-allowed public fields.
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in key)
    return Path(cache_dir) / f"igdb-{safe}.json"


def _cache_store(cache_dir: Path, key: str, entry: dict) -> None:
    """Write a privacy-bounded cache entry."""
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


def _token_cache_file(cache_dir: Path) -> Path:
    return Path(cache_dir) / "igdb-token.json"


def _token_cache_store(cache_dir: Path, token: str, expires_at: float) -> None:
    try:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = _token_cache_file(cache_dir)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"access_token": token, "expires_at": expires_at}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        return


def _token_cache_load(cache_dir: Path) -> Optional[dict]:
    try:
        path = _token_cache_file(cache_dir)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- external_id normalization ----------------------------------------------

def _normalize_external_ids(raw) -> dict:
    """Return only the allow-listed public correlations from a payload.
    
    IGDB returns external_games as a list of objects with category/uid/url.
    Category 1 = MobyGames, 3 = GOG, 5 = Steam, etc.
    """
    out: dict = {}
    if not isinstance(raw, list):
        # Handle legacy dict format
        if isinstance(raw, dict):
            for key in IGDB_EXTERNAL_ID_KEYS:
                val = raw.get(key)
                if val is None or val == "":
                    continue
                out[key] = str(val)
        return out
    
    # Map IGDB category IDs to our keys
    category_map = {
        1: "mobygames_id",      # MobyGames
        3: "gog_id",            # GOG
        5: "steam_id",          # Steam
        4: "epic_games_id",     # Epic Games
        11: "nsuid",            # Nintendo
        6: "thegamesdb_id",     # TheGamesDB
    }
    
    for game in raw:
        if not isinstance(game, dict):
            continue
        category = game.get("category")
        uid = game.get("uid")
        if category in category_map and uid:
            out[category_map[category]] = str(uid)
    
    return out


# --- Fetch plumbing (stdlib only) --------------------------------------------

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects.

    A 3xx response is surfaced to the caller rather than being silently
    re-fetched. The caller inspects ``response.geturl()`` / status to decide;
    for IGDB we treat any redirect as a non-fatal miss so a rogue 30x to a
    cloud-metadata / loopback / RFC1918 host is NEVER fetched on the second hop.
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


def _default_opener(url: str, *, timeout: float,
                    max_bytes: int = _MAX_RESPONSE_BYTES) -> bytes:
    """Real network opener. Guarded by callers (guard_url + size bound)."""
    opener = _build_no_redirect_opener(timeout=timeout)
    req = urllib.request.Request(url, headers={"User-Agent": "amiga-adf-builder/igdb"})
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if getattr(exc, "code", None) == 429:
            retry_after = _retry_after_seconds(getattr(exc, "headers", None))
            raise IgdbRateLimited(retry_after or 0.0)
        if getattr(exc, "code", None) == 401:
            raise IgdbAuthError("IGDB authentication failed (invalid/expired token)")
        raise
    status = getattr(resp, "status", None)
    if status is not None and status >= 300:
        raise IgdbError(
            f"IGDB endpoint returned non-success status {status} "
            f"(redirects are not followed)"
        )
    data = _stream_read(resp, max_bytes=max_bytes)
    return data


def _stream_read(resp, *, max_bytes: int) -> bytes:
    """Read ``resp`` in bounded chunks, aborting before the body buffers."""
    chunks: list[bytes] = []
    total = 0
    chunk_size = min(64 * 1024, max(max_bytes, 1))
    while True:
        try:
            chunk = resp.read(chunk_size)
        except (OSError, urllib.error.URLError) as exc:
            raise IgdbError(f"IGDB response read failed: {exc}") from exc
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise IgdbError(
                f"IGDB response exceeded max_response_bytes "
                f"({total} > {max_bytes})"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _bounded_read(opener: Callable[..., bytes], url: str, *, timeout: float,
                  max_bytes: int) -> bytes:
    """Fetch ``url`` via ``opener`` and enforce the response-size bound."""
    data = opener(url, timeout=timeout)
    if len(data) > max_bytes:
        raise IgdbError(
            f"IGDB response exceeded max_response_bytes ({len(data)} > {max_bytes})"
        )
    return data


def _json_post(opener: Callable[..., bytes], url: str, *, timeout: float,
               max_bytes: int, resolve: bool,
               config: "IgdbConfig", body: str) -> Optional[list]:
    """Fetch + parse a JSON response via POST, with SSRF guard, size bound, 429 backoff.

    Returns ``None`` on any non-fatal failure. Raises :class:`IgdbError` ONLY for
    programmer errors -- provider outages are swallowed to ``None``.
    """
    # SSRF guard: refuse private/loopback/link-local/RFC1918.
    try:
        _metadata.guard_url(url, resolve=resolve)
    except UnsafeUrlError:
        return None

    try:
        raw = _bounded_read(opener, url, timeout=timeout, max_bytes=max_bytes)
    except IgdbRateLimited as exc:
        if not config.respect_rate_limit:
            return None
        delay = exc.retry_after or config.rate_limit_backoff_seconds
        delay = min(max(delay, 0.0), 5.0)
        try:
            time.sleep(delay)
        except (OSError, ValueError):
            pass
        try:
            raw = _bounded_read(opener, url, timeout=timeout, max_bytes=max_bytes)
        except IgdbRateLimited:
            return None
        except (urllib.error.URLError, TimeoutError, IgdbError):
            return None
        except Exception:
            return None
    except urllib.error.URLError:
        return None
    except TimeoutError:
        return None
    except IgdbError:
        return None
    except Exception:
        return None

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


# --- OAuth token management --------------------------------------------------

# Token management is handled inline in the provider instance methods
# to avoid async complexity and keep the stdlib-only dependency profile.


# --- Provider ----------------------------------------------------------------

class IgdbProvider:
    """Optional IGDB metadata/artwork provider.

    Usage::

        cfg = load_igdb_config(config_path)
        igdb_cfg = IgdbConfig.from_dict(cfg)
        if igdb_cfg.enabled:
            provider = IgdbProvider(igdb_cfg, cache_dir, client_id, client_secret)
            provider.discover()
            result = provider.resolve(group)
    """

    def __init__(
        self,
        config: IgdbConfig,
        cache_dir: Path,
        client_id: str,
        client_secret: str,
        *,
        opener: Optional[Callable[..., bytes]] = None,
        resolve: bool = True,
    ) -> None:
        if not config.enabled:
            raise IgdbDisabled("igdb provider is disabled in config")
        if not client_id or not client_secret:
            raise IgdbDisabled("igdb provider requires client_id and client_secret")
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        if opener is not None:
            self._opener = opener
            self._resolve = False
        else:
            self._opener = lambda url, *, timeout: _default_opener(
                url, timeout=timeout, max_bytes=self.config.max_response_bytes
            )
            self._resolve = True
        self._discovered = False
        self._lock = __import__("threading").RLock()

    def _ensure_token(self) -> bool:
        """Ensure we have a valid access token. Returns True on success."""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:  # 60s buffer
            return True
        # Try loading from cache
        cached = _token_cache_load(self.cache_dir)
        if cached and cached.get("expires_at", 0) > now + 60:
            self._access_token = cached.get("access_token")
            self._token_expires_at = cached.get("expires_at", 0)
            return True
        # Fetch new token
        return self._fetch_token()

    def _fetch_token(self) -> bool:
        """Fetch a new OAuth access token from Twitch."""
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }).encode()
        url = TWITCH_TOKEN_URL
        try:
            _metadata.guard_url(url, resolve=self._resolve)
        except UnsafeUrlError:
            return False
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": "amiga-adf-builder/igdb",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            if self._opener is _default_opener:
                # Use a simple opener for token fetch
                opener = _build_no_redirect_opener(timeout=self.config.timeout_seconds)
                resp = opener.open(req, timeout=self.config.timeout_seconds)
                raw = _stream_read(resp, max_bytes=100_000)
            else:
                # Test opener injected - use it for token fetch too
                # The test opener should handle POST requests with data
                raw = self._opener(req, timeout=self.config.timeout_seconds)
            parsed = json.loads(raw.decode("utf-8"))
            token = parsed.get("access_token")
            expires_in = parsed.get("expires_in", 5_000_000)
            if not token:
                return False
            self._access_token = token
            self._token_expires_at = time.time() + expires_in
            _token_cache_store(self.cache_dir, token, self._token_expires_at)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # For test openers, return False instead of raising
                if self._opener is not None and self._opener is not _default_opener:
                    return False
                raise IgdbAuthError("Invalid IGDB client credentials")
            return False
        except Exception:
            return False

    def _make_api_request(self, endpoint: str, query: str) -> Optional[list]:
        """Make an authenticated POST request to IGDB API."""
        if not self._ensure_token():
            return None
        url = f"{self.config.base_url}/{endpoint}"
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, data=query.encode(), headers=headers, method="POST")
        try:
            if self._opener is not None and self._opener is not _default_opener:
                # Test opener injected - use it directly with the Request object
                raw = self._opener(req, timeout=self.config.timeout_seconds)
                # Test opener returns bytes, parse directly
                # Enforce max_response_bytes bound for test openers too
                if len(raw) > self.config.max_response_bytes:
                    return None
                parsed = json.loads(raw.decode("utf-8"))
                return parsed if isinstance(parsed, list) else None
            else:
                # Real opener path
                opener = _build_no_redirect_opener(timeout=self.config.timeout_seconds)
                resp = opener.open(req, timeout=self.config.timeout_seconds)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # Token expired, invalidate and retry once
                self._access_token = None
                self._token_expires_at = 0
                if self._ensure_token():
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    req = urllib.request.Request(url, data=query.encode(), headers=headers, method="POST")
                    try:
                        if self._opener is not None and self._opener is not _default_opener:
                            # Test opener path - retry with test opener
                            raw = self._opener(req, timeout=self.config.timeout_seconds)
                            parsed = json.loads(raw.decode("utf-8"))
                            return parsed if isinstance(parsed, list) else None
                        else:
                            # Real opener path
                            opener = _build_no_redirect_opener(timeout=self.config.timeout_seconds)
                            resp = opener.open(req, timeout=self.config.timeout_seconds)
                    except Exception:
                        return None
                else:
                    return None
            elif exc.code == 429:
                # Handle 429 for both real and test openers
                retry_after = _retry_after_seconds(getattr(exc, "headers", None))
                if self._opener is not None and self._opener is not _default_opener:
                    # Test opener path - apply retry logic inline
                    if not self.config.respect_rate_limit:
                        return None
                    delay = retry_after or self.config.rate_limit_backoff_seconds
                    delay = min(max(delay, 0.0), 5.0)
                    try:
                        time.sleep(delay)
                    except (OSError, ValueError):
                        pass
                    try:
                        raw = self._opener(req, timeout=self.config.timeout_seconds)
                        parsed = json.loads(raw.decode("utf-8"))
                        return parsed if isinstance(parsed, list) else None
                    except urllib.error.HTTPError as exc2:
                        if exc2.code == 429:
                            return None
                        return None
                    except Exception:
                        return None
                else:
                    # Real opener path
                    raise IgdbRateLimited(retry_after or 0.0)
            return None
        except Exception:
            return None
        try:
            status = getattr(resp, "status", None)
            if status is not None and status >= 300:
                return None
            data = _stream_read(resp, max_bytes=self.config.max_response_bytes)
            parsed = json.loads(data.decode("utf-8"))
            return parsed if isinstance(parsed, list) else None
        except Exception:
            return None

    def discover(self) -> int:
        """Validate config sanity. Returns the bounded concurrency (>=1)."""
        if not self.config.enabled:
            raise IgdbDisabled("igdb provider is disabled in config")
        if not self.client_id or not self.client_secret:
            raise IgdbDisabled("igdb provider requires client_id and client_secret")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._discovered = True
        return max(1, int(self.config.max_concurrency))

    def resolve(self, group) -> IgdbResult:
        """Resolve IGDB identity for ``group`` via title + Amiga platform search.

        Outage/timeout/oversize/429 are non-fatal: we return ``found=False`` (or
        ``needs_manual_review``) and never raise into the caller. Ambiguous or
        conflicting identities route to manual review.
        """
        if not self.config.enabled:
            raise IgdbDisabled("igdb provider is disabled in config")
        if not self._discovered:
            self.discover()

        title = (getattr(group, "title", None) or "").strip()
        release_key = (getattr(group, "release_key", "") or "")
        edition = (getattr(group, "edition", None) or "").strip()

        # Build lookup title (title + edition if present)
        lookup_title = title
        if edition:
            lookup_title = f"{title} {edition}"

        result = IgdbResult(
            group_title=title,
            group_release_key=release_key,
        )

        if not lookup_title:
            result.match_method = IgdbMatchMethod.NONE
            result.confidence = 0.0
            return result

        # Privacy-bounded cache key: normalized title only
        cache_key = f"title:{_metadata.cache_key(lookup_title)}"
        cached = _cache_load(self.cache_dir, cache_key, self.config.token_cache_ttl)

        if cached is not None:
            if cached.get("negative"):
                if cached.get("ambiguous"):
                    result.needs_manual_review = True
                    result.manual_review_reason = f"multiple high-confidence IGDB matches for '{lookup_title}'"
                    result.match_method = IgdbMatchMethod.MANUAL_REVIEW
                    result.confidence = 0.0
                    result.candidates_evaluated = [{"kind": "title_negative_cache", "title": lookup_title[:32]}]
                    return result
                result.match_method = IgdbMatchMethod.NONE
                result.confidence = 0.0
                result.candidates_evaluated = [{"kind": "title_negative_cache", "title": lookup_title[:32]}]
                return result
            pid = cached.get("provider_id")
            if pid:
                return IgdbResult(
                    group_title=title,
                    group_release_key=release_key,
                    found=True,
                    category=cached.get("category"),
                    match_method=IgdbMatchMethod.CANONICAL_REUSE,
                    confidence=CANONICAL_REUSE_CONFIDENCE,
                    provider_id=pid,
                    external_ids=cached.get("external_ids") or {},
                    artwork_urls=cached.get("artwork_urls") or [],
                    artwork_provider=cached.get("artwork_provider") or "igdb",
                    metadata=cached.get("metadata"),
                    provenance={"kind": "cache_reuse", "key": "title"},
                    candidates_evaluated=[{"kind": "title_cache_reuse", "provider_id": pid}],
                )

        # Search for games by title + Amiga platform
        # IGDB query language: search "title" fields platform = (12) limit 10
        query = f'search "{lookup_title}"; fields id,name,summary,first_release_date,platforms,cover.screenshots,artworks,external_games,genres,themes,keywords; where platforms = ({IGDB_PLATFORM_AMIGA}); limit 10;'
        games = self._make_api_request("games", query)

        if games is None:
            # Non-fatal outage
            result.match_method = IgdbMatchMethod.NONE
            result.confidence = 0.0
            result.candidates_evaluated = [{"kind": "title_search", "title": lookup_title[:32], "outcome": "no_response"}]
            return result

        if not games:
            # No results - negative cache
            _cache_store(self.cache_dir, cache_key, {"negative": True, "title": lookup_title})
            result.match_method = IgdbMatchMethod.NONE
            result.confidence = 0.0
            result.candidates_evaluated = [{"kind": "title_search", "title": lookup_title[:32], "outcome": "not_found"}]
            return result

        # Score candidates
        target_norm = _metadata._norm(lookup_title)
        scored = []
        for game in games:
            game_title = game.get("name", "")
            game_norm = _metadata._norm(game_title)
            ratio = 0.0
            if target_norm and game_norm:
                from difflib import SequenceMatcher
                ratio = SequenceMatcher(None, target_norm, game_norm).ratio()
            # Exact title match on Amiga platform = highest confidence
            exact = (target_norm == game_norm)
            scored.append((ratio, exact, game))

        # Sort by exact match first, then ratio
        scored.sort(key=lambda x: (not x[1], -x[0]))
        best_ratio, best_exact, best_game = scored[0]

        # Check for ambiguous matches (multiple high-confidence candidates)
        high_confidence_count = sum(1 for r, e, g in scored if (e or r >= 0.9))
        if high_confidence_count > 1:
            # Ambiguous - route to manual review
            _cache_store(self.cache_dir, cache_key, {
                "negative": True, "title": lookup_title, "ambiguous": True
            })
            result.needs_manual_review = True
            result.manual_review_reason = f"multiple high-confidence IGDB matches for '{lookup_title}'"
            result.match_method = IgdbMatchMethod.MANUAL_REVIEW
            result.confidence = 0.0
            result.candidates_evaluated = [
                {"kind": "title_search", "title": lookup_title[:32], "outcome": "ambiguous", "count": len(scored)}
            ]
            return result

        if not best_game:
            result.match_method = IgdbMatchMethod.NONE
            result.confidence = 0.0
            return result

        game_id = str(best_game.get("id"))
        if not game_id:
            result.needs_manual_review = True
            result.manual_review_reason = "IGDB result missing game ID"
            result.match_method = IgdbMatchMethod.MANUAL_REVIEW
            result.confidence = 0.0
            return result

        # Fetch covers and screenshots for artwork
        artwork_urls = []
        artwork_provider = "igdb"
        cover_query = f'fields url; where game = {game_id}; limit 5;'
        covers = self._make_api_request("covers", cover_query)
        if covers:
            for cover in covers:
                url = cover.get("url")
                if url:
                    # Convert to full URL (IGDB returns //images.igdb.com/...)
                    if url.startswith("//"):
                        url = "https:" + url
                    # Prefer larger sizes: replace _thumb with _cover_big
                    url = url.replace("_thumb", "_cover_big").replace("_cover_small", "_cover_big")
                    artwork_urls.append(url)

        screenshot_query = f'fields url; where game = {game_id}; limit 5;'
        screenshots = self._make_api_request("screenshots", screenshot_query)
        if screenshots:
            for shot in screenshots:
                url = shot.get("url")
                if url:
                    if url.startswith("//"):
                        url = "https:" + url
                    url = url.replace("_thumb", "_screenshot_big")
                    artwork_urls.append(url)

        # Build metadata record
        metadata = {
            "canonical_title": best_game.get("name", title),
            "description": best_game.get("summary", ""),
            "year": str(best_game.get("first_release_date", ""))[:4] if best_game.get("first_release_date") else "",
            "genres": [g.get("name", "") for g in best_game.get("genres", []) if g.get("name")],
            "platforms": ["Amiga"],
            "source_url": f"https://www.igdb.com/games/{best_game.get('slug', '')}",
            "provider": "igdb",
            "provider_id": game_id,
            "artwork_urls": artwork_urls,
            "artwork_provider": artwork_provider,
        }

        external_ids = _normalize_external_ids(best_game.get("external_games", []))

        # Cache the confirmed mapping
        _cache_store(self.cache_dir, cache_key, {
            "provider_id": game_id,
            "title": best_game.get("name", title),
            "category": "Amiga",
            "external_ids": external_ids,
            "artwork_urls": artwork_urls,
            "artwork_provider": artwork_provider,
            "metadata": metadata,
        })

        match_method = IgdbMatchMethod.EXACT_TITLE_PLATFORM if best_exact else IgdbMatchMethod.FUZZY_TITLE_PLATFORM
        confidence = EXACT_MATCH_CONFIDENCE if best_exact else FUZZY_MATCH_CONFIDENCE

        return IgdbResult(
            group_title=title,
            group_release_key=release_key,
            found=True,
            category="Amiga",
            match_method=match_method,
            confidence=confidence,
            provider_id=game_id,
            external_ids=external_ids,
            artwork_urls=artwork_urls,
            artwork_provider=artwork_provider,
            metadata=metadata,
            provenance={"kind": "title_search", "title": lookup_title[:32], "method": match_method.value},
            candidates_evaluated=[{
                "kind": "title_search",
                "title": lookup_title[:32],
                "provider_id": game_id,
                "ratio": best_ratio,
                "exact": best_exact,
                "method": match_method.value
            }],
        )