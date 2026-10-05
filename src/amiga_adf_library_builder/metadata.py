"""Online metadata and artwork discovery with persistent provenance-aware cache.

Curated Amiga records remain authoritative. Missing artwork can be discovered
from operator-approved Amiga database pages (OpenRetro, Lychesis,
OpenRetro, Lychesis) by reading standard OpenGraph/Twitter/JSON-LD image
metadata. Wikipedia and RAWG remain optional fallback metadata providers.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import ipaddress
import socket
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional

from .manual_approvals import validate_source_url
from .title_norm import canonical_title
from .utils import now_iso as utc_now
from .wikipedia_client import (
    DEFAULT_POLICY,
    WikipediaGate,
    WikipediaPolicy,
    get_global_gate,
    reset_global_gate,
)
from .wikipedia_query import (
    QueryVariant,
    build_query_variants,
    candidate_is_same_subject,
)

USER_AGENT = f"AmigaADFLibraryBuilder/{__import__('amiga_adf_library_builder._version', fromlist=['__version__']).__version__} (+preservation metadata client)"
_logger = logging.getLogger(__name__)

_ALLOWED_ARTWORK_PAGE_HOSTS = {
    "www.openretro.org", "openretro.org", "amiga.lychesis.net",
    "www.mobygames.com", "mobygames.com", "images.mobygames.com",
    # Wikipedia: the pageimages API returns null for many game pages, so
    # artwork discovery falls back to fetching the page HTML and reading
    # og:image / link rel=image_src. Fetching the rendered article page is
    # the only way to obtain artwork for those games. (GH-192 Task A)
    "en.wikipedia.org", "wikipedia.org", "en.m.wikipedia.org",
}


def _roman_numerals_to_arabic(text: str) -> str:
    """Convert roman numeral tokens in ``text`` to arabic digits.

    ``"Hacker II: The Doomsday Papers"`` → ``"Hacker 2: The Doomsday Papers"``.
    Only converts valid roman numerals; non-roman tokens pass through.
    """
    _roman_vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

    def _arabic_to_roman(n: int) -> str:
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

    def _to_arabic(tok: str) -> str:
        if not tok or not re.fullmatch(r"[IVXLCDM]+", tok.upper()):
            return tok
        total = 0
        prev = 0
        try:
            for ch in reversed(tok.upper()):
                v = _roman_vals[ch]
                total += -v if v < prev else v
                prev = v
        except KeyError:
            return tok
        # Reject non-standard forms by round-tripping.
        if _arabic_to_roman(total) != tok.upper():
            return tok
        return str(total)

    return re.sub(
        r"\b([IVXLCDM]+)\b",
        lambda m: _to_arabic(m.group(1)),
        text,
        flags=re.IGNORECASE,
    )


class UnsafeUrlError(ValueError):
    """Raised when an outbound fetch URL targets a private/loopback/link-local address."""


class ProviderRequestError(urllib.error.URLError):
    """Raised when an outbound provider request fails, with full diagnostics.

    (GH-192 production FAILURE 3) A bare ``request_error`` classification told
    the operator nothing: not the URL, not the status, not whether the cause
    was a rate limit, a timeout, or a dead socket. This exception carries
    those fields so ``_try_provider`` can log them verbatim.

    ``category`` is one of ``rate_limited``, ``timeout``, ``network``,
    ``http_error``, or ``url_unsafe`` and is the deterministic reason tag.
    ``retry_after`` carries the server-supplied Retry-After seconds when the
    server provides one.

    Subclasses :class:`urllib.error.URLError` deliberately: every existing
    ``except urllib.error.URLError`` handler in the provider chain keeps
    working unchanged, and callers that only checked "was this a transport
    error" continue to see one. Callers that want the diagnosis read
    ``status`` / ``category`` / ``url``.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        status: int | None = None,
        category: str = "network",
        exception_type: str = "",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status = status
        self.category = category
        self.exception_type = exception_type
        self.retry_after = retry_after


def _http_status_of(exc: BaseException) -> Optional[int]:
    """Return the HTTP status carried by ``exc``, or None."""
    for _attr in ("status", "code"):
        _value = getattr(exc, _attr, None)
        if isinstance(_value, int):
            return _value
    return None


def classify_request_error(exc: BaseException, *, url: str = "") -> ProviderRequestError:
    """Wrap a transport-level exception in a diagnosable ProviderRequestError.

    Deterministic precedence: rate limit (429/503 with Retry-After) >
    explicit HTTP status > timeout > DNS/socket > generic network. The
    original exception class and message are preserved verbatim.
    """
    status = _http_status_of(exc)
    category = "network"
    retry_after: Optional[float] = None
    if status == 429:
        category = "rate_limited"
    elif status is not None:
        category = "http_error"
    elif isinstance(exc, (socket.timeout, TimeoutError)):
        category = "timeout"
    elif isinstance(exc, socket.gaierror):
        category = "network"
    headers = getattr(exc, "headers", None)
    if headers is not None:
        try:
            _ra = headers.get("Retry-After") if hasattr(headers, "get") else None
            if _ra is not None:
                retry_after = float(_ra)
        except (TypeError, ValueError):
            retry_after = None
    if status == 503 and retry_after is not None:
        category = "rate_limited"
    return ProviderRequestError(
        f"{type(exc).__name__}: {exc}",
        url=url,
        status=status,
        category=category,
        exception_type=type(exc).__name__,
        retry_after=retry_after,
    )


# Blocks outbound fetches to hosts that resolve to non-public address space.
# Covers loopback, link-local, RFC1918 and IPv6 ULA/private/loopback. The guard
# never performs a real network request itself; DNS resolution is only consulted
# when a real fetch will actually be attempted (opener is None), so offline
# callers that inject a fake opener are never touched.
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("10.0.0.0/8"),         # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),      # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC1918
    ipaddress.ip_network("169.254.0.0/16"),     # link-local
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),           # IPv6 ULA
    ipaddress.ip_network("fd00::/8"),           # IPv6 unique local (subset of ULA)
)


def guard_url(url: str, *, resolve: bool = False) -> None:
    """Reject URLs that would fetch from non-public address space.

    Raises :class:`UnsafeUrlError` (a subclass of ``ValueError``) for:

      * non-http(s) schemes;
      * a missing or unparseable host;
      * a host *literal* that is loopback, link-local, RFC1918, or IPv6
        ULA/private/loopback (including IPv4-mapped IPv6 such as
        ``::ffff:127.0.0.1``);
      * (when ``resolve`` is ``True``) a *hostname* whose resolved address set
        contains any non-public address. **Every** address returned by
        ``socket.getaddrinfo`` is inspected; if *any* is loopback, link-local,
        RFC1918, IPv6 ULA/private, IPv4-mapped private, or otherwise within the
        prohibited ranges, the URL is rejected (closes the multi-address bypass
        where the first address is public but a later one is private).

    ``resolve`` MUST only be ``True`` when a real network fetch is about to occur
    (i.e. no fake ``opener`` was injected). Offline callers inject a fake opener
    and pass ``resolve=False``, so no DNS lookup is ever performed for them.

    This is defense-in-depth for a preservation tool: the only way to reach a
    private target is via a crafted curated/online metadata record or an
    approved-page redirect to an internal host. It does not replace the
    artwork-page host allow-list, which still applies first.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise UnsafeUrlError(f"could not parse fetch URL: {url!r} ({exc})") from exc
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise UnsafeUrlError(f"refusing to fetch non-http(s) URL: {url!r}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeUrlError(f"fetch URL has no host: {url!r}")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal: it is a hostname. Only resolve when a real fetch
        # will happen.
        if resolve:
            try:
                infos = socket.getaddrinfo(host, None)
            except Exception as exc:
                raise UnsafeUrlError(f"could not resolve fetch host {host!r}: {exc}") from exc
            if not infos:
                raise UnsafeUrlError(f"fetch host {host!r} resolved to no addresses")
            # Inspect EVERY address. A hostname may resolve to multiple records
            # (dual-stack, round-robin, or an attacker-supplied private alias).
            # Reject the URL if ANY resolved address is non-public.
            for info in infos:
                sockaddr = info[4]
                ip_text = sockaddr[0]
                addr = ipaddress.ip_address(ip_text)
                if addr.version == 6 and getattr(addr, "ipv4_mapped", None) is not None:
                    addr = addr.ipv4_mapped
                for net in _PRIVATE_NETWORKS:
                    if addr in net:
                        raise UnsafeUrlError(
                            f"refusing to fetch URL that targets non-public address space "
                            f"({addr} in {net}, resolved from {host!r}): {url!r}"
                        )
            # All resolved addresses are public.
            return
        else:
            return
    if addr.version == 6 and getattr(addr, "ipv4_mapped", None) is not None:
        # IPv4-mapped IPv6 address: judge by the embedded IPv4 value.
        addr = addr.ipv4_mapped
    for net in _PRIVATE_NETWORKS:
        if addr in net:
            raise UnsafeUrlError(
                f"refusing to fetch URL that targets non-public address space "
                f"({addr} in {net}): {url!r}"
            )


@dataclass
class MetadataRecord:
    canonical_title: str
    description: str = ""
    year: str = ""
    developer: str = ""
    publisher: str = ""
    genres: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    source_url: str = ""
    artwork_url: str = ""
    artwork_page_urls: list[str] = field(default_factory=list)
    artwork_source_url: str = ""
    artwork_provider: str = ""
    provider: str = ""
    provider_id: str = ""
    retrieved_at: str = ""
    confidence: float = 0.0
    query: str = ""
    relevance_category: str = ""      # accepted | rejected | review (online candidates)
    relevance_confidence: float = 0.0
    relevance_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MetadataRecord":
        data = dict(value)
        return cls(
            canonical_title=str(data.get("canonical_title") or data.get("title") or "Unknown"),
            description=str(data.get("description") or ""), year=str(data.get("year") or ""),
            developer=str(data.get("developer") or ""), publisher=str(data.get("publisher") or ""),
            genres=list(data.get("genres") or []), platforms=list(data.get("platforms") or []),
            source_url=str(data.get("source_url") or ""), artwork_url=str(data.get("artwork_url") or ""),
            artwork_page_urls=list(data.get("artwork_page_urls") or []),
            artwork_source_url=str(data.get("artwork_source_url") or ""),
            artwork_provider=str(data.get("artwork_provider") or ""),
            provider=str(data.get("provider") or ""), provider_id=str(data.get("provider_id") or ""),
            retrieved_at=str(data.get("retrieved_at") or ""), confidence=float(data.get("confidence") or 0.0),
            query=str(data.get("query") or ""),
            relevance_category=str(data.get("relevance_category") or ""),
            relevance_confidence=float(data.get("relevance_confidence") or 0.0),
            relevance_evidence=list(data.get("relevance_evidence") or []),
        )


def cache_key(title: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return key or "unknown"


def load_cached(cache_dir: Path, title: str) -> Optional[MetadataRecord]:
    path = Path(cache_dir) / f"{cache_key(title)}.json"
    if not path.is_file():
        return None
    return MetadataRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_cached(cache_dir: Path, title: str, record: MetadataRecord) -> Path:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key(title)}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_curated(curated_dir: Path, title: str) -> Optional[MetadataRecord]:
    path = Path(curated_dir) / f"{cache_key(title)}.json"
    if not path.is_file():
        return None
    record = MetadataRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
    record.provider = record.provider or "curated"
    record.retrieved_at = record.retrieved_at or utc_now()
    record.confidence = max(record.confidence, 1.0)
    return record


def _json_get(url: str, *, timeout: float = 20.0, headers: Optional[dict[str, str]] = None,
              opener: Optional[Callable[..., Any]] = None) -> dict[str, Any]:
    # Guard against fetching non-public address space. Only resolve DNS when a
    # real fetch will occur (no fake opener was injected).
    guard_url(url, resolve=opener is None)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    open_fn = opener or urllib.request.urlopen
    # (GH-192 prod FAILURE 3) Transport failures are wrapped so the caller
    # sees the URL, HTTP status, and a deterministic category (rate_limited /
    # timeout / network / http_error) instead of a bare request_error.
    try:
        with open_fn(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, socket.gaierror,
            socket.timeout, TimeoutError) as exc:
        raise classify_request_error(exc, url=url) from exc


def _text_get(url: str, *, timeout: float = 20.0,
              opener: Optional[Callable[..., Any]] = None) -> tuple[str, str]:
    # Guard against fetching non-public address space. Only resolve DNS when a
    # real fetch will occur (no fake opener was injected).
    guard_url(url, resolve=opener is None)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=timeout) as response:
            data = response.read(3_000_001)
            if len(data) > 3_000_000:
                raise RuntimeError("artwork source page exceeds 3 MB safety limit")
            final_url = getattr(response, "geturl", lambda: url)()
            charset = "utf-8"
            headers = getattr(response, "headers", None)
            if headers is not None and hasattr(headers, "get_content_charset"):
                charset = headers.get_content_charset() or "utf-8"
            text = data.decode(charset, errors="replace")
            return text, str(final_url)
    except urllib.error.HTTPError as _http_exc:
        # (GH-192 prod FAILURE 1/3) Read the error body on EVERY HTTP status,
        # not just 403: the status alone is not a reliable signal.
        try:
            _http_exc.read(3_000_001)
        except Exception:
            pass
        raise classify_request_error(_http_exc, url=url) from _http_exc
    except (urllib.error.URLError, socket.gaierror, socket.timeout, TimeoutError) as exc:
        raise classify_request_error(exc, url=url) from exc

_ARTICLES_SET = frozenset({"the", "a", "an"})


def _strip_subtitle(title: str) -> str:
    """Strip an explicit dash subtitle without discarding title punctuation.

    Colons are not reliable subtitle markers: many game titles use them as
    internal punctuation (for example, a title followed by a named episode).
    Canonical normalization handles punctuation-only differences, so only
    spaced dash separators are treated as subtitle boundaries here.
    """
    m = re.search(r"\s+(?:—|‑|–)\s+", title)
    if m and m.start() > 2:
        title = title[:m.start()].strip()
    words = title.split()
    if len(words) >= 2 and words[0].casefold() in _ARTICLES_SET:
        title = " ".join(words[1:])
    return title


def _norm(value: str) -> str:
    """Produce a generic title key tolerant of articles, punctuation and versions."""
    # Remove article tokens before canonical_title so leading and trailing
    # article variants converge; canonical_title handles Unicode, punctuation,
    # parenthetical qualifiers, and Roman/Arabic version-number equivalence.
    without_articles = re.sub(r"(?i)\b(?:the|a|an)\b", " ", value)
    canonical = canonical_title(without_articles)
    if canonical:
        return canonical.casefold()
    return re.sub(r"[^a-z0-9]", "", without_articles.casefold())


# Threshold band for the deterministic relevance validator below.
_RELEVANCE_ACCEPT_RATIO = 0.90    # >= this -> accept (identity-equivalent title)
_RELEVANCE_REJECT_RATIO = 0.60    # <  this -> reject (clearly different subject)
# Middle band (0.60 <= ratio < 0.90) routes to review unless a strong
# person/disambiguation signal is present (then reject).

# Phrasing that marks a Wikipedia/encyclopedia page as a biography rather than a game.
# Deliberately STRONG/non-generic: common game-description phrasing such as
# "is a"/"developed by" is excluded so legitimate game pages are not mis-flagged.
_PERSON_PHRASES = (
    "composer", "musician", "biography", "born", "novelist", "wrote",
    "the son of", "the daughter of", "singer", "filmmaker", "painter",
)
# Phrasing that marks a generic series/franchise/disambiguation page.
# Restricted to true disambiguation markers only (GH-164 RC1).
# Removed generic franchise phrasing ("franchise", "series of", "series is")
# which false-fired on legitimate game descriptions (e.g. "in the series of").
# "this article is about" / "this page is about" fire only when the topic
# differs from the requested title (checked in validate_metadata_relevance).
_DISAMBIGUATION_PHRASES = (
    "may refer to", "can refer to", "refers to", "disambiguation page",
)


@dataclass
class RelevanceDecision:
    """Deterministic verdict on whether an online metadata candidate is about
    the requested game (no AI/LLM, no randomness)."""

    category: str                       # accepted | rejected | review
    confidence: float                   # deterministic 0.0..1.0
    evidence: list[str]                 # human-readable deterministic reasons
    reason: str = ""                    # short machine category

    def __post_init__(self) -> None:
        self.confidence = round(float(self.confidence), 4)


def validate_metadata_relevance(requested_title: str, record: "MetadataRecord",
                                *, group=None) -> "RelevanceDecision":
    """Decide whether ``record`` is actually about ``requested_title``.

    Deterministic: identical inputs always yield an identical
    :class:`RelevanceDecision`. Used ONLY for ONLINE-derived candidates before
    they are cached/accepted. Curated and cached records are authoritative and
    never pass through this function.

    Signals (combined; none required in isolation):
      * normalized canonical-title identity/ratio vs the requested title;
      * Amiga platform presence (strong accept signal);
      * release-year mismatch (when the group supplies a year);
      * publisher/developer mismatch (weak negative);
      * biography phrasing -> rejected (reason ``person_page``);
      * series/disambiguation phrasing -> rejected/review (reason
        ``series_disambiguation``);
      * a near-miss but different game -> rejected (reason ``different_game``).
    """
    evidence: list[str] = []
    # Strip subtitles from both requested title and candidate before
    # normalization so that "Bard's Tale III" matches
    # "The Bard's Tale III: Thief of Fate" deterministically.
    # This is generic — subtitles are not title-specific.
    target = _norm(_strip_subtitle(requested_title))
    candidate_raw = _strip_subtitle(record.canonical_title or requested_title)
    candidate = _norm(candidate_raw)
    ratio = SequenceMatcher(None, target, candidate).ratio() if (target or candidate) else 0.0

    # (GH-192 Defect 1) Colon-only variant handling.
    # A colon in a title like "UFO: Enemy Unknown" is title formatting,
    # not a subtitle separator. _strip_subtitle strips after the colon,
    # which makes "UFO: Enemy Unknown" → "UFO" and loses the subtitle.
    # Also compare against a colon-normalized version where colons are
    # removed before normalization, so "UFO: Enemy Unknown" → "UFO Enemy Unknown"
    # and both the colon and no-colon variants normalize identically.
    _requested_raw = requested_title
    _candidate_raw = record.canonical_title or requested_title
    _target_colon = _norm(_requested_raw.replace(":", "").replace(" ", ""))
    _candidate_colon = _norm(_candidate_raw.replace(":", "").replace(" ", ""))
    _colon_ratio = SequenceMatcher(None, _target_colon, _candidate_colon).ratio() if (_target_colon or _candidate_colon) else 0.0
    _colon_identity = (_target_colon != "" and _candidate_colon != "" and (
        _candidate_colon == _target_colon
        or _candidate_colon.startswith(_target_colon + " ")
        or _target_colon.startswith(_candidate_colon + " ")
    ))
    if _colon_identity:
        evidence.append("colon_normalized_match")
    elif _colon_ratio > ratio:
        evidence.append(f"colon_normalized_similarity:{_colon_ratio:.2f}")

    # Track subtitle evidence for explainable diagnostics.
    # A colon in a title is title formatting, not a subtitle separator,
    # so colon-only variants should not be flagged as subtitle_stripped.
    _has_colon = ":" in requested_title or ":" in (record.canonical_title or "")
    subtitle_stripped = (candidate_raw != (record.canonical_title or requested_title)) and not _has_colon
    if subtitle_stripped:
        evidence.append("subtitle_stripped")

    # --- Strong positive: canonical identity (possibly with edition suffix) ---
    # (GH-192 Defect 1) Also accept colon-normalized identity:
    # "UFO: Enemy Unknown" and "UFO Enemy Unknown" normalize to the
    # same alphanumeric form when colons are removed.
    exact_identity = (target != "" and candidate != "" and (
        candidate == target
        or candidate.startswith(target + " ")
        or target.startswith(candidate + " ")
    )) or _colon_identity

    # --- Person / biography signal ---
    hay_text = (record.canonical_title + " " + (record.description or "")).lower()
    no_amiga_platform = record.platforms and not any(
        "amiga" in (p or "").lower() for p in record.platforms
    )
    is_person = (no_amiga_platform and any(phrase in hay_text for phrase in _PERSON_PHRASES)) or (
        not record.platforms and any(phrase in hay_text for phrase in _PERSON_PHRASES)
    )
    if is_person:
        evidence.append("entity_type_person")
        return RelevanceDecision(
            category="rejected", confidence=0.10,
            evidence=evidence, reason="person_page",
        )

    # --- Series / disambiguation signal ---
    is_disambiguation = any(phrase in hay_text for phrase in _DISAMBIGUATION_PHRASES)
    if is_disambiguation:
        evidence.append("disambiguation_page")
        # A generic franchise page for a specific-game query is rejected (falls
        # through to offline/local, never cached). If the normalized title is
        # itself identical to the game, route to review instead of hard-reject.
        if exact_identity:
            return RelevanceDecision(
                category="review", confidence=0.55,
                evidence=evidence, reason="series_disambiguation",
            )
        return RelevanceDecision(
            category="rejected", confidence=0.30,
            evidence=evidence, reason="series_disambiguation",
        )

    # --- Platform evidence ---
    amiga_present = any("amiga" in (p or "").lower() for p in record.platforms)
    if amiga_present:
        evidence.append("platform_amiga_match")

    # --- Year mismatch (only when the requested group supplies a year) ---
    requested_year = ""
    if group is not None:
        requested_year = (getattr(group, "year", "") or "") or ""
    record_year = (record.year or "").strip()
    if requested_year and record_year and requested_year != record_year:
        evidence.append(f"year_mismatch:{requested_year}!={record_year}")

    # --- Title similarity / identity ---
    # Use the better of the stripped and colon-normalized ratios.
    _effective_ratio = max(ratio, _colon_ratio)
    if exact_identity:
        evidence.append("exact_canonical_title")
    else:
        evidence.append(f"title_similarity:{_effective_ratio:.2f}")

    # --- Build the decision from the combined evidence ---
    year_mismatch = any(e.startswith("year_mismatch:") for e in evidence)
    has_platform_negative = no_amiga_platform and not amiga_present

    if exact_identity and (amiga_present or not record.platforms) and not year_mismatch:
        return RelevanceDecision(
            category="accepted", confidence=max(0.90, _effective_ratio),
            evidence=evidence, reason="exact_match",
        )

    # --- Different-game lookalike guard (issue #7) ---
    # Also check colon-normalized extension.
    extension = ""
    _colon_extension = ""
    if target and candidate:
        if candidate.startswith(target):
            extension = candidate[len(target):]
        elif target.startswith(candidate):
            extension = target[len(candidate):]
    if _target_colon and _candidate_colon:
        if _candidate_colon.startswith(_target_colon):
            _colon_extension = _candidate_colon[len(_target_colon):]
        elif _target_colon.startswith(_candidate_colon):
            _colon_extension = _target_colon[len(_candidate_colon):]
    if (not exact_identity and (extension or _colon_extension)
            and (ratio >= _RELEVANCE_ACCEPT_RATIO or extension[0].isdigit()
                 or (_colon_extension and (_colon_extension[0].isdigit())))):
        evidence.append("different_game_substring")
        return RelevanceDecision(
            category="rejected", confidence=min(0.45, _effective_ratio),
            evidence=evidence, reason="different_game",
        )

    if ratio >= _RELEVANCE_ACCEPT_RATIO and (amiga_present or not record.platforms):
        return RelevanceDecision(
            category="accepted", confidence=max(0.90, ratio),
            evidence=evidence, reason="high_title_similarity",
        )

    if ratio < _RELEVANCE_REJECT_RATIO or (ratio < 0.8 and has_platform_negative):
        reason = "different_game" if not has_platform_negative else "low_title_similarity"
        return RelevanceDecision(
            category="rejected", confidence=min(0.45, ratio),
            evidence=evidence, reason=reason,
        )

    # Middle band: ambiguous near-miss. Route to review (fall through to
    # offline/local; never cached, never returned as accepted).
    return RelevanceDecision(
        category="review", confidence=ratio,
        evidence=evidence, reason="ambiguous_midband",
    )


class _ImagePageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[tuple[int, str, str]] = []
        self._json_ld = False
        self._json_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            value = a.get("content", "")
            if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and value:
                self.candidates.append((100, value, key))
        elif tag.lower() == "link":
            if "image_src" in a.get("rel", "").lower() and a.get("href"):
                self.candidates.append((90, a["href"], "link:image_src"))
        elif tag.lower() == "img" and a.get("src"):
            text = " ".join([a.get("alt", ""), a.get("title", ""), a.get("class", ""), a.get("id", "")])
            self.candidates.append((20, a["src"], text))
        elif tag.lower() == "script" and "ld+json" in a.get("type", "").lower():
            self._json_ld = True
            self._json_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._json_ld:
            raw = "".join(self._json_parts).strip()
            self._json_ld = False
            try:
                value = json.loads(raw)
            except Exception:
                return
            for url in _json_ld_images(value):
                self.candidates.append((80, url, "json-ld:image"))

    def handle_data(self, data: str) -> None:
        if self._json_ld:
            self._json_parts.append(data)


def _json_ld_images(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        image = value.get("image")
        if isinstance(image, str):
            found.append(image)
        elif isinstance(image, dict) and isinstance(image.get("url"), str):
            found.append(image["url"])
        elif isinstance(image, list):
            for item in image:
                if isinstance(item, str): found.append(item)
                elif isinstance(item, dict) and isinstance(item.get("url"), str): found.append(item["url"])
        for child in value.values():
            if isinstance(child, (dict, list)):
                found.extend(_json_ld_images(child))
    elif isinstance(value, list):
        for item in value:
            found.extend(_json_ld_images(item))
    return found


def _candidate_score(base: int, url: str, context: str, title: str) -> int:
    hay = (url + " " + context).lower()
    score = base
    positive = ("cover", "box", "front", "title", "game", "scan", "screenshot")
    negative = ("logo", "icon", "avatar", "flag", "button", "score", "rating", "smiley", "theme", "banner", "pixel.gif")
    score += sum(15 for word in positive if word in hay)
    score -= sum(35 for word in negative if word in hay)
    tokens = [t for t in re.findall(r"[a-z0-9]+", title.lower()) if len(t) >= 3]
    score += sum(5 for token in tokens if token in hay)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https", ""}:
        score -= 200
    if Path(parsed.path).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        score += 10
    return score


def discover_artwork_from_page(page_url: str, title: str, *, timeout: float = 20.0,
                               opener: Optional[Callable[..., Any]] = None) -> Optional[tuple[str, str]]:
    """Return the best image URL and provider label from an approved Amiga page."""
    host = urllib.parse.urlparse(page_url).hostname or ""
    if host.lower() not in _ALLOWED_ARTWORK_PAGE_HOSTS:
        raise ValueError(f"unapproved artwork page host: {host}")
    html, final_url = _text_get(page_url, timeout=timeout, opener=opener)
    parser = _ImagePageParser()
    parser.feed(html)
    ranked: list[tuple[int, str, str]] = []
    for base, raw_url, context in parser.candidates:
        absolute = urllib.parse.urljoin(final_url, raw_url.strip())
        ranked.append((_candidate_score(base, absolute, context, title), absolute, context))
    if not ranked:
        return None
    score, image_url, _ = max(ranked, key=lambda x: x[0])
    if score < 25:
        return None
    provider = {
        "openretro.org": "openretro",
        "www.openretro.org": "openretro", "amiga.lychesis.net": "lychesis",
        "en.wikipedia.org": "wikipedia", "wikipedia.org": "wikipedia",
        "en.m.wikipedia.org": "wikipedia",
    }.get(host.lower(), host.lower())
    return image_url, provider


_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"


def _wikipedia_api_params(variant: "QueryVariant") -> dict[str, str]:
    """Build the deterministic API parameter set for one query form.

    ``bare_quoted`` deliberately omits the ``Amiga video game`` context terms:
    measured live on 2026-10-04, those terms EXCLUDE the correct article for
    titles whose page does not use them ("Oil Barons" ranked the real article
    7th without the context terms and returned only generic list pages with
    them).
    """
    if variant.label == "bare_quoted":
        search = f'"{variant.search}"'
    else:
        search = f'"{variant.search}" Amiga video game'
    return {
        "action": "query", "format": "json", "formatversion": "2",
        "generator": "search",
        "gsrsearch": search,
        "gsrlimit": "8",
        "prop": "extracts|pageimages|info", "exintro": "1", "explaintext": "1",
        "piprop": "original|thumbnail", "pithumbsize": "1200", "inprop": "url",
    }


def _wikipedia_fetch(url: str, *, timeout: float,
                     opener: Optional[Callable[..., Any]],
                     gate: "WikipediaGate",
                     diagnostics: list[dict],
                     attempt_label: str,
                     allow_retry: bool = True) -> dict[str, Any]:
    """Issue ONE Wikipedia API request with pacing and bounded retry.

    Retry is bounded by ``gate.policy.max_retries``; there is no unbounded
    loop. ``allow_retry=False`` forces a single attempt, used for query forms
    after the first 429 so a rate-limited run does not multiply its request
    count by the number of forms. A 429 opens a gate cooldown rather than
    immediately re-requesting. Every decision is appended to ``diagnostics``.
    """
    attempt = 0
    while True:
        gate.begin_request()
        gate.note_request()
        try:
            data = _json_get(url, timeout=timeout, opener=opener)
        except ProviderRequestError as exc:
            rate_limited = exc.category == "rate_limited" or exc.status == 429
            entry = {
                "attempt": attempt,
                "query_variant": attempt_label,
                "url": url,
                "http_status": exc.status,
                "rate_limited": rate_limited,
                "retry_after": exc.retry_after,
                "error_category": exc.category,
                "gate_decision": gate.last_decision,
            }
            if not rate_limited:
                entry["decision"] = "give_up_non_rate_limited"
                diagnostics.append(entry)
                raise
            # Every 429 is counted, even when no retry follows, so the
            # run-level rate-limit budget can stop further requests.
            gate.note_rate_limited(retry_after=exc.retry_after)
            if (not allow_retry or not gate.policy.retries_enabled
                    or attempt >= gate.policy.max_retries):
                entry["decision"] = "give_up_retry_budget_exhausted"
                entry["rate_limit_events"] = gate.rate_limited_events
                diagnostics.append(entry)
                raise
            wait = gate.cooldown_remaining()
            entry["decision"] = "backoff"
            entry["backoff_seconds"] = round(wait, 3)
            diagnostics.append(entry)
            gate.note_retry()
            attempt += 1
            continue
        diagnostics.append({
            "attempt": attempt,
            "query_variant": attempt_label,
            "url": url,
            "http_status": 200,
            "rate_limited": False,
            "retry_after": None,
            "gate_decision": gate.last_decision,
            "decision": "ok",
        })
        return data


def _rank_wikipedia_pages(title: str, pages: list[dict]) -> list[tuple[float, dict]]:
    """Rank candidate pages for ``title`` with the conservative subject test."""
    ranked: list[tuple[float, dict]] = []
    for page in pages:
        page_title = str(page.get("title") or "")
        if not page_title:
            continue
        extract = str(page.get("extract") or "")
        if not candidate_is_same_subject(title, page_title, extract=extract):
            continue
        haystack = (page_title + " " + extract[:600]).lower()
        if "amiga" in haystack:
            score = 0.95
        elif "video game" in haystack:
            score = 0.85
        else:
            score = 0.70
        ranked.append((score, page))
    return ranked


def wikipedia_lookup(title: str, *, timeout: float = 20.0,
                     opener: Optional[Callable[..., Any]] = None,
                     gate: "Optional[WikipediaGate]" = None,
                     policy: "Optional[WikipediaPolicy]" = None,
                     diagnostics: Optional[list[dict]] = None,
                     artwork: bool = True) -> Optional[MetadataRecord]:
    """Look up ``title`` on Wikipedia with pacing, fallbacks and diagnostics.

    Behaviour changes vs the previous single-query implementation:

    - requests are paced and rate-limit aware (``gate``/``policy``);
    - several deterministic query forms are tried, cheapest and most likely
      first, stopping at the first acceptable subject;
    - a candidate is accepted only when
      :func:`candidate_is_same_subject` confirms it is the requested work;
    - every request, wait, retry and rejection is recorded in ``diagnostics``.

    ``opener`` injection still bypasses pacing entirely (the gate only counts
    real requests), so offline tests never wait.
    """
    gate = gate or get_global_gate(policy)
    trail: list[dict] = diagnostics if diagnostics is not None else []
    # Once this lookup has seen a 429, later query forms must NOT retry.
    # Retrying each of N forms would multiply the request count by
    # ``max_retries + 1`` against an endpoint that has already told us it is
    # rate limiting this client. Later forms still get ONE attempt each, so a
    # differently-cached form can still succeed.
    rate_limited_seen = False
    if opener is not None:
        # A caller-injected opener is a test/offline path: never sleep, but
        # keep the SAME gate object so its counters, budget and rate-limit
        # state stay observable to the caller that passed it in.
        gate.without_sleeping()

    variants = build_query_variants(title)
    if not variants:
        return None

    best_page: Optional[dict] = None
    best_score = 0.0
    used_variant: Optional[QueryVariant] = None
    #: First transport failure, re-raised once every query form is exhausted.
    #: Returning None instead would discard the status/category/URL that
    #: production needs to tell a rate limit from a timeout from a dead socket,
    #: and would reintroduce exactly the opaque ``no_result`` diagnosis that
    #: GH-192 round 2 was opened to fix.
    first_error: Optional[ProviderRequestError] = None
    #: True when some form was answered and simply held no acceptable match.
    #: A real "no such article" answer must NOT be masked by an error from a
    #: different form.
    answered_any = False

    for index, variant in enumerate(variants):
        if not gate.request_allowed():
            trail.append({
                "query_variant": variant.label,
                "decision": "budget_exhausted",
                "rate_limited": False,
                "gate_decision": "budget_exhausted",
            })
            break
        params = _wikipedia_api_params(variant)
        url = _WIKIPEDIA_API + "?" + urllib.parse.urlencode(params)
        try:
            data = _wikipedia_fetch(url, timeout=timeout, opener=opener,
                                    gate=gate, diagnostics=trail,
                                    attempt_label=variant.label,
                                    allow_retry=not rate_limited_seen)
        except ProviderRequestError as exc:
            # A rate-limited form must not abort the whole lookup: another
            # query form may still be served. The failure is retained and
            # re-raised only if EVERY form failed (see below).
            if first_error is None:
                first_error = exc
            if exc.category == "rate_limited" or exc.status == 429:
                rate_limited_seen = True
            if gate.rate_limited_exhausted():
                break
            continue
        pages = (data.get("query") or {}).get("pages") or []
        answered_any = True
        ranked = _rank_wikipedia_pages(title, pages)
        if ranked:
            score, page = max(ranked, key=lambda item: item[0])
            trail.append({
                "query_variant": variant.label,
                "decision": "candidate_accepted",
                "candidate_title": str(page.get("title") or ""),
                "candidate_url": str(page.get("fullurl") or ""),
                "confidence": score,
                "variant_index": index,
            })
            best_page, best_score, used_variant = page, score, variant
            break
        trail.append({
            "query_variant": variant.label,
            "decision": "no_acceptable_candidate",
            "candidate_count": len(pages),
        })
        if gate.rate_limited_exhausted():
            break

    if best_page is None:
        # Re-raise the transport failure when NO form produced a usable
        # answer, so the caller sees the real diagnosis (429 / timeout /
        # http_error with URL and Retry-After) instead of a bare None. When at
        # least one form was answered, the genuine "no acceptable article"
        # verdict wins and is returned as None.
        if first_error is not None and not answered_any:
            raise first_error
        return None

    query_text = (_wikipedia_api_params(used_variant)["gsrsearch"]
                if used_variant else title)
    original = best_page.get("original") or best_page.get("thumbnail") or {}
    art = str(original.get("source") or "")
    page_url = str(best_page.get("fullurl") or "")
    art_provider = "wikipedia" if art else ""
    if artwork and not art and page_url:
        # The pageimages API returns null for many game pages even when the
        # page itself carries artwork (og:image / link rel=image_src). Fall
        # back to HTML-based discovery so artwork enrichment is not silently
        # dead for Wikipedia matches. (GH-192 Task A)
        try:
            gate.begin_request()
            gate.note_request()
            art_found = discover_artwork_from_page(
                page_url, str(best_page.get("title") or title),
                timeout=timeout, opener=opener,
            )
            trail.append({
                "query_variant": "artwork_page",
                "url": page_url,
                "decision": "artwork_found" if art_found else "artwork_none",
                "gate_decision": gate.last_decision,
            })
        except Exception:
            art_found = None
            trail.append({
                "query_variant": "artwork_page",
                "url": page_url,
                "decision": "artwork_error",
            })
        if art_found:
            art, art_provider = art_found
    return MetadataRecord(
        canonical_title=str(best_page.get("title") or title),
        description=str(best_page.get("extract") or "").strip(),
        source_url=page_url, artwork_url=art,
        artwork_source_url=page_url if art else "",
        artwork_provider=art_provider, provider="wikipedia",
        provider_id=str(best_page.get("pageid") or ""), retrieved_at=utc_now(),
        confidence=min(best_score, 1.0), query=query_text,
    )


def rawg_lookup(title: str, *, api_key: str, timeout: float = 20.0,
                opener: Optional[Callable[..., Any]] = None) -> Optional[MetadataRecord]:
    params = {"key": api_key, "search": title, "search_precise": "true", "page_size": "10"}
    data = _json_get("https://api.rawg.io/api/games?" + urllib.parse.urlencode(params), timeout=timeout, opener=opener)
    results = data.get("results") or []
    if not results: return None
    target = _norm(title)
    game = max(results, key=lambda g: SequenceMatcher(None, target, _norm(str(g.get("name") or ""))).ratio())
    game_id = game.get("id")
    detail = _json_get(f"https://api.rawg.io/api/games/{game_id}?key={urllib.parse.quote(api_key)}", timeout=timeout, opener=opener)
    platforms = [str((p.get("platform") or {}).get("name") or "") for p in detail.get("platforms") or []]
    if platforms and not any("amiga" in p.lower() for p in platforms): return None
    released = str(detail.get("released") or "")
    art = str(detail.get("background_image") or "")
    return MetadataRecord(
        canonical_title=str(detail.get("name") or title),
        description=re.sub(r"<[^>]+>", "", str(detail.get("description") or "")).strip(),
        year=released[:4] if released else "",
        developer=", ".join(str(x.get("name") or "") for x in detail.get("developers") or []),
        publisher=", ".join(str(x.get("name") or "") for x in detail.get("publishers") or []),
        genres=[str(x.get("name") or "") for x in detail.get("genres") or [] if x.get("name")],
        platforms=[p for p in platforms if p], source_url=str(detail.get("website") or f"https://rawg.io/games/{detail.get('slug','')}"),
        artwork_url=art, artwork_source_url=str(detail.get("website") or "") if art else "",
        artwork_provider="rawg" if art else "", provider="rawg", provider_id=str(game_id),
        retrieved_at=utc_now(), confidence=0.9, query=title,
    )


def mobygames_lookup(title: str, *, api_key: str, timeout: float = 20.0,
                     opener: Optional[Callable[..., Any]] = None) -> Optional[MetadataRecord]:
    """Search MobyGames for a title and return a MetadataRecord if an Amiga
    release of a clearly-matching game exists.

    Uses the ``/games`` endpoint in ``normal`` format, which returns per game a
    ``platforms`` array (``platform_name`` / ``first_release_date``), a
    ``sample_cover`` (with its own ``platforms`` list), a ``sample_screenshots``
    array, ``genres``, ``moby_url`` and ``description`` -- all of which we need.
    This is a single verified-shape request: no second detail fetch, no reading
    of fields the response does not carry.

    The ``platform`` query parameter requires an integer platform ID, so we do
    NOT pass it. Instead we search by ``title`` (case-insensitive substring) and
    filter client-side to games released on an Amiga platform, then rank by
    title similarity. Matches below a confidence floor are rejected rather than
    silently chosen (mirrors the Wikipedia relevance floor).

    The API key is passed as the ``api_key`` query argument (URL-encoded). All
    HTTP goes through :func:`_json_get` with an injectable ``opener`` for tests.
    Every artwork URL we emit is validated against the ratified
    :func:`validate_source_url` guard before it is returned; a URL that fails
    validation is dropped (no artwork) rather than returned.
    """
    # Title-only search. The `platform` param is an integer ID and is
    # intentionally omitted -- we filter to Amiga client-side below.
    params = {"api_key": api_key, "title": title, "format": "normal", "limit": "25"}
    data = _json_get(
        "https://api.mobygames.com/v1/games?" + urllib.parse.urlencode(params),
        timeout=timeout, opener=opener,
    )
    games = [g for g in (data.get("games") or []) if isinstance(g, dict)]
    if not games:
        return None
    target = _norm(title)

    # Keep only games released on at least one Amiga platform.
    amiga_games: list[tuple[float, dict, list[str]]] = []
    for game in games:
        platforms = [
            str(p.get("platform_name") or "")
            for p in (game.get("platforms") or []) if isinstance(p, dict)
        ]
        amiga_platforms = [p for p in platforms if "amiga" in p.lower()]
        if not amiga_platforms:
            continue
        ratio = SequenceMatcher(None, target, _norm(str(game.get("title") or ""))).ratio()
        amiga_games.append((ratio, game, amiga_platforms))
    if not amiga_games:
        return None

    # Deterministic best match: highest title similarity, ties broken by the
    # lower game_id (stable, no dict-order dependence).
    amiga_games.sort(key=lambda item: (-item[0], int(item[1].get("game_id") or 0)))
    best_ratio, game, amiga_platforms = amiga_games[0]
    if best_ratio < 0.60:
        # Ambiguous or unrelated title -- reject rather than silently choose.
        return None
    game_id = game.get("game_id")
    if not game_id:
        return None

    # Year: earliest Amiga first_release_date (string "YYYY", "YYYY-MM" or
    # "YYYY-MM-DD"); take the 4-char year prefix.
    dates = []
    for p in (game.get("platforms") or []):
        if not isinstance(p, dict):
            continue
        name = str(p.get("platform_name") or "")
        if "amiga" in name.lower():
            d = str(p.get("first_release_date") or "")
            if d:
                dates.append(d)
    dates.sort()
    year = dates[0][:4] if dates else ""

    # Artwork: prefer a sample_cover that actually covers an Amiga platform
    # (or an unspecified platform), else the first sample screenshot. Every
    # candidate is validated by the ratified guard before it may be emitted.
    artwork_url = ""
    cover = game.get("sample_cover") or {}
    cover_platforms = {str(x).lower() for x in (cover.get("platforms") or [])}
    amiga_names = {n.lower() for n in amiga_platforms}
    cover_image = str(cover.get("image") or "")
    cover_is_amiga = bool(cover_image) and (not cover_platforms or bool(cover_platforms & amiga_names))
    if cover_is_amiga:
        ok, _reason = validate_source_url(cover_image)
        if ok:
            artwork_url = cover_image
    if not artwork_url:
        for shot in (game.get("sample_screenshots") or []):
            img = str((shot or {}).get("image") or "")
            if img:
                ok, _reason = validate_source_url(img)
                if ok:
                    artwork_url = img
                    break

    source_url = str(game.get("moby_url") or f"https://www.mobygames.com/game/{game_id}")
    ok_src, _ = validate_source_url(source_url)
    if not ok_src:
        source_url = ""
    return MetadataRecord(
        canonical_title=str(game.get("title") or title),
        description=re.sub(r"<[^>]+>", "", str(game.get("description") or "")).strip(),
        year=year,
        genres=[str(g.get("genre_name") or "") for g in (game.get("genres") or []) if isinstance(g, dict) and g.get("genre_name")],
        platforms=amiga_platforms,
        source_url=source_url,
        artwork_url=artwork_url,
        artwork_source_url=source_url if artwork_url else "",
        artwork_provider="mobygames" if artwork_url else "",
        provider="mobygames",
        provider_id=str(game_id),
        retrieved_at=utc_now(),
        confidence=min(round(best_ratio, 4), 1.0),
        query=title,
    )
def _discover_curated_artwork(record: MetadataRecord, title: str, *, timeout: float,
                              opener: Optional[Callable[..., Any]]) -> None:
    pages = list(dict.fromkeys(record.artwork_page_urls))
    # A curated source URL on an approved Amiga host can also act as an artwork page.
    if record.source_url and (urllib.parse.urlparse(record.source_url).hostname or "").lower() in _ALLOWED_ARTWORK_PAGE_HOSTS:
        pages.append(record.source_url)
    for page_url in pages:
        try:
            found = discover_artwork_from_page(page_url, title, timeout=timeout, opener=opener)
        except Exception:
            found = None
        if found:
            record.artwork_url, record.artwork_provider = found
            record.artwork_source_url = page_url
            suffix = "+" + record.artwork_provider + "-artwork"
            if suffix not in record.provider:
                record.provider = (record.provider or "curated") + suffix
            return


def lookup_metadata(title: str, *, cache_dir: Path, curated_dir: Path,
                    refresh: bool = False, timeout: float = 20.0,
                    group: Any = None,
                    opener: Optional[Callable[..., Any]] = None,
                    mobygames_enabled: bool = False,
                    mobygames_api_key_env: str = "MOBYGAMES_API_KEY",
                    wikipedia_enabled: bool = True,
                    wikipedia_gate: "Optional[WikipediaGate]" = None,
                    activity: Optional[Callable[[str], None]] = None
                    ) -> tuple[Optional[MetadataRecord], str, list[dict]]:
    """Resolve metadata for ``title`` using the shared precedence chain.

    Precedence (deterministic, highest first): curated -> cache -> keyed online
    providers (RAWG, then MobyGames) -> Wikipedia (unkeyed). A keyed provider
    is only attempted when BOTH its config flag is enabled AND its API key is
    present in the environment; otherwise it is a no-op and the chain simply
    proceeds to the next provider. MobyGames is DISABLED BY DEFAULT
    (``mobygames_enabled=False``), so the base app is unchanged unless an
    operator opts in via the ``[mobygames]`` config table. Wikipedia is
    ENABLED BY DEFAULT (``wikipedia_enabled=True``) and is the primary
    supported online provider.

    Requests are paced by the shared gate, which carries the operator's
    configured Wikipedia policy (delay, retry count, Retry-After handling) --
    see :mod:`amiga_adf_library_builder.wikipedia_config`.
    """
    curated = load_curated(curated_dir, title)
    # The gate must exist before the curated branch: a curated record with
    # missing prose/artwork still supplements from Wikipedia, and that request
    # must be paced like every other one.
    gate = wikipedia_gate or get_global_gate()
    if curated:
        # Preserve curated identity/facts. Wikipedia may supplement only missing
        # prose/image; Amiga-specific approved pages are then tried for artwork.
        supplement: Optional[MetadataRecord] = None
        _wiki_art: Optional[tuple[str, str, str]] = None
        if not curated.description or not curated.artwork_url:
            try:
                supplement = wikipedia_lookup(title, timeout=timeout,
                                             opener=opener, gate=gate)
            except Exception: supplement = None
        if supplement:
            if not curated.description: curated.description = supplement.description
            if not curated.artwork_url and supplement.artwork_url:
                # Hold the Wikipedia artwork as a fallback only. An Amiga-specific
                # curated artwork page is a better source of box art than the
                # generic encyclopedia image, so it is tried first below.
                _wiki_art = (
                    supplement.artwork_url, supplement.artwork_source_url,
                    supplement.artwork_provider,
                )
        if not curated.artwork_url:
            _discover_curated_artwork(curated, title, timeout=timeout, opener=opener)
            # If the Amiga-specific pages yielded nothing, fall back to the
            # Wikipedia image rather than leaving artwork empty.
            if not curated.artwork_url and _wiki_art:
                curated.artwork_url, curated.artwork_source_url, curated.artwork_provider = _wiki_art
            if curated.artwork_url and "+wikipedia" not in (curated.provider or ""):
                curated.provider = (curated.provider or "curated") + "+wikipedia"
        save_cached(cache_dir, title, curated)
        return curated, curated.provider or "curated", []
    # ONLINE candidates are validated for relevance before caching/accepting.
    # A rejected/review candidate is NEVER cached and NEVER returned; it falls
    # through to the next provider, then to offline/local. Curated and cached
    # paths above stay authoritative and skip validation.
    relevance_events: list[dict] = []
    if not refresh:
        cached = load_cached(cache_dir, title)
        if cached:
            # (GH-192 online-usability pass) An explicit cache hit is a
            # first-class diagnostic. Previously a reused cache entry was
            # indistinguishable from a fresh online match, so "why did this
            # title not query the network?" was unanswerable from the run log.
            relevance_events.append({
                "provider": "cache",
                "canonical_title": cached.canonical_title,
                "category": "accepted",
                "confidence": cached.confidence,
                "reason": "cache_hit",
                "evidence": [
                    "cache_hit",
                    f"cache_source={cached.provider or 'unknown'}",
                    f"retrieved_at={cached.retrieved_at}",
                    "network_requests_avoided=True",
                ],
            })
            return cached, "cache", relevance_events
    accepted: Optional[MetadataRecord] = None

    # Live activity hook for on-screen diagnostics. Matches the pattern used
    # elsewhere in enrich_group(): an optional callable wrapped in try/except so
    # logging failures never break metadata resolution.
    def _log(msg: str) -> None:
        if activity is None:
            return
        try:
            activity(str(msg))
        except Exception:
            pass

    def _try_provider(label: str,
                      lookup) -> None:
        nonlocal accepted
        _log(f"Querying {label}…")
        candidate = None
        outcome = "no_result"
        failure_detail: dict[str, Any] = {}
        try:
            candidate = lookup()
            # (GH-192 prod FAILURE 2) Only a NON-None return is a candidate.
            # A provider that returns None (disabled, no page, no Amiga
            # platform, below similarity floor) is a genuine no_result.
            # Classifying that as ``candidate_returned`` produced the
            # self-contradictory diagnostic
            # ``outcome=no_match reason=candidate_returned candidate=''``
            # and made a real "no candidate" look like a rejected candidate.
            outcome = "candidate_returned" if candidate is not None else "no_result"
        except Exception as exc:
            # Classify the exception type for diagnostics using proper
            # isinstance checks rather than string-containment heuristics.
            if isinstance(exc, ProviderRequestError):
                # (GH-192 prod FAILURE 1/3) The wrapper already carries the
                # deterministic category. Keep the stable outcome name
                # ``request_error`` so downstream reason taxonomy is
                # unchanged, and carry the category as evidence.
                outcome = "request_error"
                failure_detail = {
                    "exception_type": exc.exception_type or type(exc).__name__,
                    "exception_message": str(exc)[:400],
                    "http_status": exc.status,
                    "url": exc.url,
                    "error_category": exc.category,
                    "retry_after": exc.retry_after,
                }
            elif isinstance(exc, (urllib.error.URLError, urllib.error.HTTPError)):
                outcome = "request_error"
            elif isinstance(exc, json.JSONDecodeError):
                outcome = "parse_error"
            elif isinstance(exc, (socket.gaierror, socket.timeout, TimeoutError)):
                outcome = "request_error"
            elif "auth" in type(exc).__name__.lower() or "credentials" in str(exc).lower():
                outcome = "auth_error"
            else:
                outcome = "parse_error"  # genuine unexpected parse/internal error
            # Record the concrete failure detail so a production run says
            # WHY a provider failed instead of only naming an outcome class.
            # (GH-192: "when a provider fails, the app must make it obvious why")
            if not failure_detail:
                _status = _http_status_of(exc)
                failure_detail = {
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:400],
                    "http_status": _status,
                }
            candidate = None
        if candidate is None:
            evidence = [outcome]
            for _key in (
                "exception_type", "exception_message", "http_status",
                "url", "error_category", "retry_after",
            ):
                _value = failure_detail.get(_key)
                if _value not in (None, ""):
                    evidence.append(f"{_key}={_value}")
            relevance_events.append({
                "provider": label,
                "canonical_title": "",
                "category": "not_found",
                "confidence": 0.0,
                "reason": outcome,
                "evidence": evidence,
            })
            return
        decision = validate_metadata_relevance(title, candidate, group=group)
        # (GH-192 prod FAILURE 2) When a candidate WAS returned, the run
        # diagnostic must name it: candidate title, candidate URL/provider id,
        # normalized source vs candidate title, confidence, and the exact
        # rejection reason. Without these, "zero accepted matches" is
        # unactionable.
        candidate_evidence = list(decision.evidence)
        candidate_evidence.append(f"candidate_title={candidate.canonical_title!r}")
        candidate_evidence.append(f"candidate_url={candidate.source_url or ''}")
        candidate_evidence.append(f"candidate_provider_id={candidate.provider_id or ''}")
        candidate_evidence.append(
            f"normalized_source={_norm(_strip_subtitle(title))!r}"
        )
        candidate_evidence.append(
            f"normalized_candidate={_norm(_strip_subtitle(candidate.canonical_title or ''))!r}"
        )
        relevance_events.append({
            "provider": label,
            "canonical_title": candidate.canonical_title,
            "category": decision.category,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "evidence": candidate_evidence,
        })
        if decision.category == "accepted":
            _log(f"{label}: accepted.")
            candidate.relevance_category = "accepted"
            candidate.relevance_confidence = decision.confidence
            candidate.relevance_evidence = list(decision.evidence)
            accepted = candidate

    rawg_key = os.environ.get("RAWG_API_KEY", "").strip()
    if rawg_key:
        _try_provider("rawg",
                      lambda: rawg_lookup(title, api_key=rawg_key, timeout=timeout, opener=opener))
    if accepted is None:
        mobygames_key = os.environ.get(mobygames_api_key_env, "").strip() if mobygames_enabled else ""
        if mobygames_key:
            _try_provider("mobygames",
                          lambda: mobygames_lookup(title, api_key=mobygames_key, timeout=timeout, opener=opener))
    if accepted is None and wikipedia_enabled:
        wiki_diagnostics: list[dict] = []
        _try_provider("wikipedia",
                      lambda: wikipedia_lookup(
                          title, timeout=timeout, opener=opener,
                          gate=gate, diagnostics=wiki_diagnostics))
        # (GH-192 online-usability pass) Surface the per-request decision trail:
        # which query form was sent, the URL, HTTP status, rate-limit flag,
        # Retry-After, the backoff/pause decision, candidate title, confidence,
        # and the accept/reject reason. Without this the operator sees only a
        # single opaque "not-found" for a rate-limited run.
        for entry in wiki_diagnostics:
            detail = (
                f"query={entry.get('query_variant', '')!r} "
                f"decision={entry.get('decision', '')} "
                f"status={entry.get('http_status', '')} "
                f"rate_limited={entry.get('rate_limited', False)} "
                f"retry_after={entry.get('retry_after', '')} "
                f"gate={entry.get('gate_decision', '')} "
                f"url={entry.get('url', '')}"
            )
            for _key in ("backoff_seconds", "candidate_title", "candidate_url",
                         "confidence", "candidate_count", "attempt",
                         "error_category"):
                _value = entry.get(_key)
                if _value not in (None, ""):
                    detail += f" {_key}={_value}"
            relevance_events.append({
                "provider": "wikipedia-request",
                "canonical_title": str(entry.get("candidate_title") or ""),
                "category": "not_found",
                "confidence": float(entry.get("confidence") or 0.0),
                "reason": "wikipedia_" + str(entry.get("decision") or "step"),
                "evidence": [detail],
            })
    if accepted is not None:
        save_cached(cache_dir, title, accepted)
        return accepted, accepted.provider, relevance_events
    return None, "not-found", relevance_events
