"""Optional Hasheous ROM-hash identity resolver + provider-ID capture (bridge).

This is an OPTIONAL provider. It is DISABLED by default and only activates when
an explicit ``[hasheous]`` TOML table is present AND ``enabled = true``.

=== ACCESS LIMITATION (documented, not invented) ===

Per issue #12's governance, the live Hasheous lookup is platform-scoped and
requires a self-hosted / Hasheous-compatible endpoint. This bundled provider is
therefore CONFIG-DRIVEN and DISABLED by default; the default ``base_url`` is a
placeholder example host (``https://api.hasheous.example/v1``), NOT a live
endpoint. The supported subset (hash lookup + title lookup) requires NO live
API key -- if a future operation needs a credential, that is a separate
operator gate. The public repo's tests are SYNTHETIC / MOCK ONLY (see
``tests/test_hasheous_provider.py`` and ``tests/test_hasheous_real_fetch.py``);
no live network is touched by the test suite.

=== Design posture (mirrors playmatch.py exactly) ===

This module mirrors ``src/amiga_adf_library_builder/playmatch.py`` in structure
and safety posture. It is an OPTIONAL, DISABLED-by-default provider that reuses
the EXISTING hash-first identity layer established by issue #11 (Playmatch). It
does NOT create a parallel identity system -- ``HasheousResult`` mirrors
``PlaymatchResult`` and ``HasheousProvider`` is invoked alongside
``PlaymatchProvider``.

* **OFFLINE-capable at import.** The module imports nothing that touches the
  network at import time. The only stdlib pieces used for a real fetch are
  ``urllib.request`` + ``socket`` + ``ipaddress`` (via ``metadata.guard_url``),
  and they are reached ONLY when a real fetch happens. Tests inject a fake
  ``opener`` and pass ``resolve=False``, so no DNS/socket is ever exercised
  offline.
* **Hash-first identity.** A precomputed public ``sha256`` (computed elsewhere,
  never recomputed here) is the PRIMARY lookup signal. An exact-hash match
  OUTRANKS any title/filename fallback. When Hasheous returns a provider-ID /
  external correlation for the matched hash, it is captured in
  ``result.provider_id`` / ``result.external_ids`` for downstream providers.
* **Title/filename fallback (only when no hash match).** May use ONLY the
  *canonical group title* -- never a private ROM filename, local path, or
  collection detail. It MUST still pass the EXISTING deterministic relevance
  gate (:func:`metadata.validate_metadata_relevance`); a filename-derived
  candidate that fails relevance is routed to manual review, never silently
  accepted.
* **Fail-safe on ambiguity.** Two disagreeing candidates, or a hash match that
  conflicts with a title match, ALWAYS routes to manual review. We NEVER
  silently override.
* **Non-fatal outages.** Provider outage / timeout / oversize response MUST NOT
  raise into the pipeline. We catch, return ``found=False`` (or
  ``needs_manual_review``), and continue.
* **SSRF guard.** Every outbound fetch runs :func:`metadata.guard_url`
  (``resolve=True`` only on a real fetch) before any bytes move. Private/
  loopback/link-local/RFC1918 hosts are refused.
* **Bounds.** ``timeout_seconds``, ``max_response_bytes``, and
  ``max_concurrency`` are all bounded; a response that exceeds
  ``max_response_bytes`` is short-circuited and treated as a non-fatal miss.
* **Privacy.** Only the public ``sha256`` (one-way, assumed public by the issue)
  and the canonical title may be sent to Hasheous. The cache stores ONLY
  ``provider_id`` + canonical title + ``external_ids`` (public correlations) +
  a negative-lookup marker, keyed by the public signal. No private filename,
  path, or private hash is ever written to a cache or transmitted.

The synthetic request contract (documented here; a real server may differ):

* ``GET {base_url}/rom/{sha256}`` -> ``{"found": true, "provider_id": "<id>",
  "external_ids": {...}, "category": "<optional>", "title": "<canonical>",
  "confidence": <0..1>}`` or ``{"found": false}``.
* ``GET {base_url}/search?title={canonical_title}`` -> ``{"found": true,
  "candidates": [{"provider_id", "title", "confidence", "category",
  "external_ids": {...}}...]}`` or ``{"found": false}``.

The ``base_url`` is overridable in config for a self-hosted Hasheous-compatible
endpoint. The default is a placeholder example host.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from . import metadata as _metadata
from .metadata import UnsafeUrlError, validate_metadata_relevance


# --- Bounds / defaults -------------------------------------------------------

#: Public default endpoint. Overridable in config for self-host; the issue
#: assumes the hash (not the filename/path) is the only transmitted identity.
DEFAULT_BASE_URL = "https://api.hasheous.example/v1"

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

#: Confidence assigned to an exact-hash match (deterministic, highest).
EXACT_HASH_CONFIDENCE = 1.0
#: Confidence assigned when reusing a previously-stored canonical mapping.
CANONICAL_REUSE_CONFIDENCE = 0.95
#: Floor below which a title-fallback match is never auto-accepted.
TITLE_FALLBACK_MIN_CONFIDENCE = 0.90


# --- External-id allowlist ---------------------------------------------------

#: Normalized external correlations we capture for downstream metadata/manual
#: providers. Anything else in a Hasheous ``external_ids`` payload is dropped
#: (fail-safe: we never persist an unrecognized, potentially private field).
_EXTERNAL_ID_KEYS = (
    "hasheous_metadata_id",
    "igdb_id",
    "mobygames_id",
    "spotlight_id",
    "thegamesdb_id",
    "tmdb_id",
    "gameplay_id",
)


# --- Match method ------------------------------------------------------------

class HasheousMatchMethod(str, Enum):
    """How a Hasheous identity was resolved for a release group."""

    EXACT_HASH = "exact_hash"
    PROVIDER_ID = "provider_id"
    CANONICAL_REUSE = "canonical_reuse"
    FUZZY_TITLE = "fuzzy_title"
    MANUAL_REVIEW = "manual_review"
    NONE = "none"


class HasheousError(Exception):
    """Base error for Hasheous provider failures."""


class HasheousDisabled(HasheousError):
    """Raised when the provider is used while disabled in config."""


# --- Configuration -----------------------------------------------------------

@dataclass(frozen=True)
class HasheousConfig:
    """Typed view of the ``[hasheous]`` TOML table."""

    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    # Negative-lookup / canonical-reuse cache TTL in seconds. <= 0 disables
    # reuse (each run re-resolves). The cache only stores public signals.
    cache_ttl: float = 0.0

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "HasheousConfig":
        """Build a config from the raw ``[hasheous]`` table (or None).

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
            cache_ttl = float(raw.get("cache_ttl", 0.0))
        except (TypeError, ValueError):
            cache_ttl = 0.0
        if cache_ttl < 0:
            cache_ttl = 0.0

        return cls(
            enabled=enabled,
            base_url=base_url,
            timeout_seconds=timeout,
            max_response_bytes=max_bytes,
            max_concurrency=max_conc,
            confidence_threshold=conf,
            cache_ttl=cache_ttl,
        )


# --- Result ------------------------------------------------------------------

@dataclass
class HasheousResult:
    """Outcome of resolving Hasheous identity for one release group.

    Mirrors :class:`PlaymatchResult` public fields exactly, plus
    ``external_ids`` (normalized public correlations captured for downstream
    metadata/manual providers). All fields are deterministic given the same
    inputs.
    """

    group_title: Optional[str]
    group_release_key: str
    found: bool = False
    category: Optional[str] = None
    match_method: HasheousMatchMethod = HasheousMatchMethod.NONE
    confidence: float = 0.0
    needs_manual_review: bool = False
    manual_review_reason: Optional[str] = None
    provider_id: Optional[str] = None
    provenance: Optional[dict] = None
    candidates_evaluated: list = field(default_factory=list)
    # Normalized public external correlations (e.g. {"hasheous_metadata_id":
    # "...", "igdb_id": "..."}). Empty unless a supported correlation was
    # returned. Never holds private data.
    external_ids: dict = field(default_factory=dict)

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
        }


@dataclass
class _Candidate:
    """One synthetic Hasheous match candidate (internal)."""

    provider_id: str
    title: str
    confidence: float
    category: Optional[str] = None
    external_ids: dict = field(default_factory=dict)


# --- Cache (privacy-bounded) -------------------------------------------------

def _cache_file(cache_dir: Path, key: str) -> Path:
    # Key is always a public signal (sha256 hex or normalized title). We never
    # persist filenames/paths/private hashes as cache keys or values beyond the
    # explicitly-allowed public fields.
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in key)
    return Path(cache_dir) / f"hasheous-{safe}.json"


def _cache_store(cache_dir: Path, key: str, entry: dict) -> None:
    """Write a privacy-bounded cache entry (provider_id + title + external_ids)."""
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


# --- external_id normalization ----------------------------------------------

def _normalize_external_ids(raw: Optional[dict]) -> dict:
    """Return only the allow-listed public correlations from a payload.

    Anything outside ``_EXTERNAL_ID_KEYS`` is dropped (fail-safe: we never
    persist an unrecognized, potentially private field). Values are coerced to
    ``str`` so the cache stays JSON-safe and bounded.
    """
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key in _EXTERNAL_ID_KEYS:
        val = raw.get(key)
        if val is None or val == "":
            continue
        out[key] = str(val)
    return out


# --- Fetch plumbing (stdlib only) --------------------------------------------

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects.

    A 3xx response is surfaced to the caller (``http_error_302`` returns the
    redirect response object) rather than being silently re-fetched. The
    caller inspects ``response.geturl()`` / status to decide; for Hasheous we
    treat any redirect as a non-fatal miss so a rogue 30x to a cloud-metadata /
    loopback / RFC1918 host is NEVER fetched on the second hop. This keeps the
    central ``metadata.guard_url`` SSRF guard authoritative for every byte
    actually fetched.
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
        # Surface the redirect response WITHOUT following it. Returning the
        # current response object means no second hop is ever opened, so a 30x
        # to a cloud-metadata / loopback / RFC1918 host is never fetched. The
        # caller (``_json_get``) treats the non-200 as a non-fatal miss. We
        # deliberately do NOT call the base redirect-following routine.
        return fp


def _build_no_redirect_opener(*, timeout: float) -> urllib.request.OpenerDirector:
    """Build a hardened opener with explicit, minimal handlers.

    * No ``HTTPRedirectHandler`` (the default chain auto-follows 3xx): we
      supply :class:`_NoRedirectHandler` which surfaces redirects instead.
    * ``ProxyHandler({})`` clears any ambient ``HTTP_PROXY`` / ``HTTPS_PROXY``
      so the request never leaves through an unapproved egress path.
    * Standard error handling / response processing only.
    """
    opener = urllib.request.OpenerDirector()
    opener.add_handler(_NoRedirectHandler())
    opener.add_handler(urllib.request.ProxyHandler({}))
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(urllib.request.HTTPSHandler())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    return opener


def _default_opener(url: str, *, timeout: float,
                    max_bytes: int = _MAX_RESPONSE_BYTES) -> bytes:
    """Real network opener. Guarded by callers (guard_url + size bound).

    Uses a hardened :class:`OpenerDirector` that does NOT follow redirects and
    does NOT honor ambient proxy environment variables. The caller
    (``_bounded_read`` + ``_json_get``) enforces the SSRF guard and size bound.
    The response is streamed and the ``max_bytes`` cap is enforced *before* the
    whole body buffers (DoS defense).
    """
    opener = _build_no_redirect_opener(timeout=timeout)
    req = urllib.request.Request(url, headers={"User-Agent": "amiga-adf-builder/hasheous"})
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310 (guarded)
        # We never follow redirects (SSRF pivot defense). A 3xx is surfaced
        # here, not opened on a second hop, so a redirect to a cloud-metadata /
        # loopback / RFC1918 host is NEVER fetched. Any non-success status is a
        # non-fatal miss for the pipeline.
        if resp.status is not None and resp.status >= 300:
            raise HasheousError(
                f"Hasheous endpoint returned non-success status {resp.status} "
                f"(redirects are not followed)"
            )
        data = _stream_read(resp, max_bytes=max_bytes)
    return data


def _stream_read(resp, *, max_bytes: int) -> bytes:
    """Read ``resp`` in bounded chunks, aborting before the body buffers.

    Mirrors ``metadata._text_get``'s capped read but enforces the bound on the
    accumulated length: the instant accumulated bytes would exceed
    ``max_bytes``, raise :class:`HasheousError` so a multi-GB response cannot
    exhaust process memory before the check runs.
    """
    chunks: list[bytes] = []
    total = 0
    # Cap each read so the per-call allocation stays bounded even if the
    # caller passes a very large max_bytes.
    chunk_size = min(64 * 1024, max(max_bytes, 1))
    while True:
        try:
            chunk = resp.read(chunk_size)
        except (OSError, urllib.error.URLError) as exc:
            raise HasheousError(f"Hasheous response read failed: {exc}") from exc
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HasheousError(
                f"Hasheous response exceeded max_response_bytes "
                f"({total} > {max_bytes})"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _bounded_read(opener: Callable[..., bytes], url: str, *, timeout: float,
                  max_bytes: int) -> bytes:
    """Fetch ``url`` via ``opener`` and enforce the response-size bound.

    ``opener`` has the signature ``opener(url, *, timeout) -> bytes``. Real
    fetches also require ``guard_url(url, resolve=True)`` to have been called
    first. A response larger than ``max_bytes`` raises :class:`HasheousError`
    so the caller can treat it as a non-fatal miss.

    The real opener (:func:`_default_opener`) already streams with a hard cap
    so a multi-GB body cannot buffer before the bound is enforced. Injected
    byte-openers (tests) return a prebuilt blob and are size-checked here as a
    defense-in-depth backstop.
    """
    data = opener(url, timeout=timeout)
    if len(data) > max_bytes:
        raise HasheousError(
            f"Hasheous response exceeded max_response_bytes ({len(data)} > {max_bytes})"
        )
    return data


def _json_get(opener: Callable[..., bytes], url: str, *, timeout: float,
              max_bytes: int, resolve: bool) -> Optional[dict]:
    """Fetch + parse a JSON response, with SSRF guard and size bound.

    Returns ``None`` on any non-fatal failure (outage/timeout/oversize/malformed).
    Raises :class:`HasheousError` ONLY for programmer errors (a malformed URL
    constructed internally) -- provider outages are swallowed to ``None``.
    """
    # SSRF guard: refuse private/loopback/link-local/RFC1918. ``resolve`` is
    # True only on a real fetch; injected test openers pass resolve=False. A
    # refusal means the URL is unsafe -> treat as a non-fatal miss (no request
    # is ever sent, so nothing private can leak).
    try:
        _metadata.guard_url(url, resolve=resolve)
    except UnsafeUrlError:
        return None

    try:
        raw = _bounded_read(opener, url, timeout=timeout, max_bytes=max_bytes)
    except urllib.error.URLError:
        return None  # outage / unreachable -> non-fatal miss
    except TimeoutError:
        return None  # timeout -> non-fatal miss
    except HasheousError:
        return None  # oversize -> non-fatal miss
    except Exception:
        # Any other fetch failure is non-fatal; never raise into the pipeline.
        return None

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _build_rom_url(base_url: str, sha256: str) -> str:
    return f"{base_url}/rom/{sha256}"


def _build_search_url(base_url: str, title: str) -> str:
    q = urllib.parse.urlencode({"title": title})
    return f"{base_url}/search?{q}"


# --- Provider ----------------------------------------------------------------

class HasheousProvider:
    """Optional Hasheous ROM-hash identity resolver + provider-ID capture.

    Usage::

        cfg = load_hasheous_config(config_path)
        h_cfg = HasheousConfig.from_dict(cfg)
        if h_cfg.enabled:
            provider = HasheousProvider(h_cfg, cache_dir)
            provider.discover()                       # config sanity
            result = provider.resolve(group, scans=scan_map, sha256=hash)
    """

    def __init__(
        self,
        config: HasheousConfig,
        cache_dir: Path,
        *,
        opener: Optional[Callable[..., bytes]] = None,
        resolve: bool = True,
    ) -> None:
        if not config.enabled:
            raise HasheousDisabled("hasheous provider is disabled in config")
        self.config = config
        self.cache_dir = Path(cache_dir)
        if opener is not None:
            # Injected (test) opener: no DNS/socket touched; resolve stays False.
            self._opener = opener
            self._resolve = False
        else:
            # Real fetch path. Wrap _default_opener so the configured response
            # size bound is enforced *while streaming* (DoS defense): the cap is
            # applied to the actual socket read, not only to the injected
            # max_bytes backstop in _bounded_read. resolve=True so guard_url
            # performs DNS validation on the original URL.
            self._opener = lambda url, *, timeout: _default_opener(
                url, timeout=timeout, max_bytes=self.config.max_response_bytes
            )
            self._resolve = True
        self._discovered = False

    # -- discovery (config sanity) -------------------------------------------

    def discover(self) -> int:
        """Validate config sanity. Returns the bounded concurrency (>=1)."""
        if not self.config.enabled:
            raise HasheousDisabled("hasheous provider is disabled in config")
        # Ensure the cache dir exists (best-effort, non-fatal).
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._discovered = True
        return max(1, int(self.config.max_concurrency))

    # -- resolve -------------------------------------------------------------

    def resolve(self, group, *, scans: Optional[dict] = None,
                sha256: Optional[str] = None) -> HasheousResult:
        """Resolve Hasheous identity for ``group``.

        Hash-first: a precomputed public ``sha256`` outranks any title fallback.
        The hash signal is taken from (in order): an explicit ``sha256`` arg, a
        matching :class:`ScanRecord` for the group's first record, or
        ``group.sha256`` (if a pipeline attached one). If none is available we
        refuse hash mode and fall through to the title fallback.

        Outage/timeout/oversize are non-fatal: we return ``found=False`` (or
        ``needs_manual_review``) and never raise into the caller. Ambiguous or
        conflicting identities route to manual review.
        """
        if not self.config.enabled:
            raise HasheousDisabled("hasheous provider is disabled in config")
        if not self._discovered:
            self.discover()

        title = (getattr(group, "title", None) or None)
        release_key = (getattr(group, "release_key", "") or "")

        result = HasheousResult(
            group_title=title,
            group_release_key=release_key,
        )

        # ---- Hash signal acquisition (reuse, never recompute) --------------
        hash_signal = self._acquire_hash(group, scans=scans, sha256=sha256)

        # ---- Phase 1: exact-hash lookup (OUTRANKS everything) --------------
        if hash_signal:
            hash_result = self._resolve_by_hash(hash_signal, title=title,
                                                release_key=release_key)
            if hash_result is not None:
                return hash_result

        # ---- Phase 2: title/filename fallback (only if no hash match) ------
        if title:
            title_result = self._resolve_by_title(title, group=group,
                                                  release_key=release_key)
            if title_result is not None:
                return title_result

        # Nothing found.
        result.match_method = HasheousMatchMethod.NONE
        result.confidence = 0.0
        return result

    # -- hash signal acquisition --------------------------------------------

    def _acquire_hash(self, group, *, scans: Optional[dict],
                      sha256: Optional[str]) -> Optional[str]:
        """Return a public sha256 to use as the PRIMARY lookup signal.

        Source order (reuse, never recompute):
          1. explicit ``sha256`` argument,
          2. ``scans[group.records[0].source_filename].sha256`` (already hashed
             elsewhere),
          3. ``group.sha256`` if a pipeline attached one,
          4. otherwise refuse hash mode (return None -> title fallback).
        """
        if sha256:
            return sha256
        rec = None
        records = getattr(group, "records", None) or []
        if records:
            rec = records[0]
        if rec is not None:
            fn = getattr(rec, "source_filename", None)
            if fn and scans:
                scan = scans.get(fn)
                if scan is not None:
                    s = getattr(scan, "sha256", None)
                    if s:
                        return s
        g_hash = getattr(group, "sha256", None)
        if g_hash:
            return g_hash
        return None

    # -- hash resolution -----------------------------------------------------

    def _resolve_by_hash(self, sha256: str, *, title: Optional[str],
                         release_key: str) -> Optional[HasheousResult]:
        """Exact-hash lookup. Returns a result, or None to fall through."""
        # Privacy-bounded cache key: the public sha256 only.
        cache_key = f"hash:{sha256}"
        cached = _cache_load(self.cache_dir, cache_key, self.config.cache_ttl)

        if cached is not None:
            if cached.get("negative"):
                # Negative-lookup cache: previously unresolved hash.
                return HasheousResult(
                    group_title=title,
                    group_release_key=release_key,
                    found=False,
                    match_method=HasheousMatchMethod.NONE,
                    confidence=0.0,
                    candidates_evaluated=[{"kind": "hash_negative_cache",
                                           "sha256": sha256[:8] + "..."}],
                )
            pid = cached.get("provider_id")
            ctitle = cached.get("title")
            if pid:
                # Canonical reuse of a previously-confirmed mapping.
                return self._exact_hash_result(
                    sha256=sha256, provider_id=pid, title=ctitle or title,
                    category=cached.get("category"),
                    external_ids=cached.get("external_ids") or {},
                    confidence=CANONICAL_REUSE_CONFIDENCE,
                    method=HasheousMatchMethod.CANONICAL_REUSE,
                    release_key=release_key,
                    provenance={"kind": "cache_reuse", "key": "hash"},
                )

        url = _build_rom_url(self.config.base_url, sha256)
        payload = _json_get(
            self._opener, url,
            timeout=self.config.timeout_seconds,
            max_bytes=self.config.max_response_bytes,
            resolve=self._resolve,
        )

        if payload is None:
            # Non-fatal outage/timeout/oversize/malformed -> miss (cache negative
            # only on a genuine "not found", not on outage; we treat None as
            # transient and do NOT poison the negative cache).
            return HasheousResult(
                group_title=title,
                group_release_key=release_key,
                found=False,
                match_method=HasheousMatchMethod.NONE,
                confidence=0.0,
                candidates_evaluated=[{"kind": "hash", "sha256": sha256[:8] + "...",
                                       "outcome": "no_response"}],
            )

        if not payload.get("found"):
            # Genuine negative lookup -> cache it (public signal only).
            _cache_store(self.cache_dir, cache_key,
                         {"negative": True, "sha256": sha256})
            return HasheousResult(
                group_title=title,
                group_release_key=release_key,
                found=False,
                match_method=HasheousMatchMethod.NONE,
                confidence=0.0,
                candidates_evaluated=[{"kind": "hash", "sha256": sha256[:8] + "...",
                                       "outcome": "not_found"}],
            )

        provider_id = (payload.get("provider_id") or "").strip()
        ctitle = payload.get("title") or title
        category = payload.get("category")
        confidence = payload.get("confidence", EXACT_HASH_CONFIDENCE)
        external_ids = _normalize_external_ids(payload.get("external_ids"))
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = EXACT_HASH_CONFIDENCE
        confidence = max(0.0, min(1.0, confidence))

        if not provider_id:
            # Found but no provider-id correlation: ambiguous -> manual review.
            return HasheousResult(
                group_title=title,
                group_release_key=release_key,
                found=False,
                needs_manual_review=True,
                manual_review_reason="hasheous hash match returned no provider_id",
                match_method=HasheousMatchMethod.MANUAL_REVIEW,
                confidence=confidence,
                external_ids=external_ids,
                candidates_evaluated=[{"kind": "hash", "sha256": sha256[:8] + "...",
                                       "outcome": "found_no_provider_id"}],
            )

        # Cache the confirmed public mapping (provider_id + canonical title +
        # public external_ids).
        _cache_store(self.cache_dir, cache_key,
                     {"provider_id": provider_id, "title": ctitle,
                      "category": category, "sha256": sha256,
                      "external_ids": external_ids})

        return self._exact_hash_result(
            sha256=sha256, provider_id=provider_id, title=ctitle,
            category=category, external_ids=external_ids,
            confidence=EXACT_HASH_CONFIDENCE,
            method=HasheousMatchMethod.EXACT_HASH, release_key=release_key,
            provenance={"kind": "hash_match", "sha256": sha256[:8] + "..."},
        )

    def _exact_hash_result(self, *, sha256: str, provider_id: str, title: Optional[str],
                           category: Optional[str], external_ids: dict,
                           confidence: float,
                           method: HasheousMatchMethod, release_key: str,
                           provenance: dict) -> HasheousResult:
        return HasheousResult(
            group_title=title,
            group_release_key=release_key,
            found=True,
            category=category,
            match_method=method,
            confidence=confidence,
            provider_id=provider_id,
            external_ids=external_ids,
            provenance=provenance,
            candidates_evaluated=[{"kind": "hash", "sha256": sha256[:8] + "...",
                                   "provider_id": provider_id,
                                   "method": method.value}],
        )

    # -- title fallback ------------------------------------------------------

    def _resolve_by_title(self, title: str, *, group, release_key: str
                          ) -> Optional[HasheousResult]:
        """Title/filename fallback. Uses ONLY the canonical group title.

        Never transmits private filenames/paths. Reuses the EXISTING
        deterministic relevance gate (``validate_metadata_relevance``). A
        candidate that fails relevance is routed to manual review -- never
        silently accepted. Conflicting/disagreeing candidates -> manual review.
        """
        # Privacy-bounded cache key: normalized canonical title only.
        norm_title = _norm(title)
        cache_key = f"title:{norm_title}"
        cached = _cache_load(self.cache_dir, cache_key, self.config.cache_ttl)
        if cached is not None and cached.get("provider_id"):
            return HasheousResult(
                group_title=title,
                group_release_key=release_key,
                found=True,
                category=cached.get("category"),
                match_method=HasheousMatchMethod.CANONICAL_REUSE,
                confidence=CANONICAL_REUSE_CONFIDENCE,
                provider_id=cached.get("provider_id"),
                external_ids=cached.get("external_ids") or {},
                provenance={"kind": "cache_reuse", "key": "title"},
                candidates_evaluated=[{"kind": "title_cache", "title": norm_title}],
            )

        url = _build_search_url(self.config.base_url, title)
        payload = _json_get(
            self._opener, url,
            timeout=self.config.timeout_seconds,
            max_bytes=self.config.max_response_bytes,
            resolve=self._resolve,
        )

        if payload is None:
            return HasheousResult(
                group_title=title,
                group_release_key=release_key,
                found=False,
                match_method=HasheousMatchMethod.NONE,
                confidence=0.0,
                candidates_evaluated=[{"kind": "title", "title": norm_title,
                                       "outcome": "no_response"}],
            )

        if not payload.get("found"):
            return HasheousResult(
                group_title=title,
                group_release_key=release_key,
                found=False,
                match_method=HasheousMatchMethod.NONE,
                confidence=0.0,
                candidates_evaluated=[{"kind": "title", "title": norm_title,
                                       "outcome": "not_found"}],
            )

        candidates_raw = payload.get("candidates") or []
        candidates: list[_Candidate] = []
        for c in candidates_raw:
            if not isinstance(c, dict):
                continue
            cid = (c.get("provider_id") or "").strip()
            ctitle = (c.get("title") or "").strip()
            ext = _normalize_external_ids(c.get("external_ids"))
            try:
                cconf = float(c.get("confidence", 0.0))
            except (TypeError, ValueError):
                cconf = 0.0
            if cid and ctitle:
                candidates.append(_Candidate(
                    provider_id=cid, title=ctitle,
                    confidence=max(0.0, min(1.0, cconf)),
                    category=c.get("category"),
                    external_ids=ext,
                ))

        evaluated = []
        for c in candidates:
            # Reuse the EXISTING deterministic relevance gate. We synthesize a
            # MetadataRecord carrying only the PUBLIC canonical title (never a
            # private filename), so the relevance function judges identity on
            # the public signal alone.
            rec = _metadata.MetadataRecord(canonical_title=c.title)
            decision = validate_metadata_relevance(title, rec, group=group)
            evaluated.append({
                "kind": "title",
                "provider_id": c.provider_id,
                "title": c.title,
                "confidence": c.confidence,
                "relevance_category": decision.category,
                "relevance_reason": decision.reason,
                "external_ids": c.external_ids,
            })

        # No usable candidates.
        if not candidates:
            return HasheousResult(
                group_title=title,
                group_release_key=release_key,
                found=False,
                match_method=HasheousMatchMethod.NONE,
                confidence=0.0,
                candidates_evaluated=evaluated,
            )

        # Conflict detection: disagreeing provider_ids for the requested title,
        # or a candidate that fails the relevance gate -> manual review.
        accepted = [
            c for c, e in zip(candidates, evaluated)
            if e["relevance_category"] == "accepted"
            and c.confidence >= TITLE_FALLBACK_MIN_CONFIDENCE
        ]
        if len(accepted) == 0:
            # No relevance-accepted candidate. If any candidate existed at all,
            # route to manual review rather than silently dropping.
            if candidates:
                return HasheousResult(
                    group_title=title,
                    group_release_key=release_key,
                    found=False,
                    needs_manual_review=True,
                    manual_review_reason=(
                        "title fallback candidates failed relevance validation"
                    ),
                    match_method=HasheousMatchMethod.MANUAL_REVIEW,
                    confidence=max((c.confidence for c in candidates), default=0.0),
                    candidates_evaluated=evaluated,
                )
            return HasheousResult(
                group_title=title,
                group_release_key=release_key,
                found=False,
                match_method=HasheousMatchMethod.NONE,
                confidence=0.0,
                candidates_evaluated=evaluated,
            )

        # Multiple disagreeing accepted identities -> ambiguous -> manual review.
        distinct_ids = {c.provider_id for c in accepted}
        if len(distinct_ids) > 1:
            return HasheousResult(
                group_title=title,
                group_release_key=release_key,
                found=False,
                needs_manual_review=True,
                manual_review_reason="conflicting Hasheous identities for same title",
                match_method=HasheousMatchMethod.MANUAL_REVIEW,
                confidence=max((c.confidence for c in accepted), default=0.0),
                candidates_evaluated=evaluated,
            )

        best = accepted[0]
        _cache_store(self.cache_dir, cache_key,
                     {"provider_id": best.provider_id, "title": norm_title,
                      "category": best.category,
                      "external_ids": best.external_ids})
        return HasheousResult(
            group_title=title,
            group_release_key=release_key,
            found=True,
            category=best.category,
            match_method=HasheousMatchMethod.FUZZY_TITLE,
            confidence=best.confidence,
            provider_id=best.provider_id,
            external_ids=best.external_ids,
            provenance={"kind": "title_match", "title": norm_title},
            candidates_evaluated=evaluated,
        )


def _norm(value: str) -> str:
    """Normalized form for privacy-bounded cache keys (lowercase alnum)."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())
