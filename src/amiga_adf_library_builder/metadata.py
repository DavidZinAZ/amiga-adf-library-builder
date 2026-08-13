"""Online metadata and artwork discovery with persistent provenance-aware cache.

Curated Amiga records remain authoritative. Missing artwork can be discovered
from operator-approved Amiga database pages (Lemon Amiga, Hall of Light,
OpenRetro, Lychesis) by reading standard OpenGraph/Twitter/JSON-LD image
metadata. Wikipedia and RAWG remain optional fallback metadata providers.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import ipaddress
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional

USER_AGENT = "AmigaADFLibraryBuilder/0.2.1 (+preservation metadata client)"
_ALLOWED_ARTWORK_PAGE_HOSTS = {
    "www.lemonamiga.com", "lemonamiga.com", "amiga.abime.net",
    "www.openretro.org", "openretro.org", "amiga.lychesis.net",
}


class UnsafeUrlError(ValueError):
    """Raised when an outbound fetch URL targets a private/loopback/link-local address."""


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    with open_fn(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _text_get(url: str, *, timeout: float = 20.0,
              opener: Optional[Callable[..., Any]] = None) -> tuple[str, str]:
    # Guard against fetching non-public address space. Only resolve DNS when a
    # real fetch will occur (no fake opener was injected).
    guard_url(url, resolve=opener is None)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    open_fn = opener or urllib.request.urlopen
    with open_fn(request, timeout=timeout) as response:
        data = response.read(3_000_001)
        if len(data) > 3_000_000:
            raise RuntimeError("artwork source page exceeds 3 MB safety limit")
        final_url = getattr(response, "geturl", lambda: url)()
        charset = "utf-8"
        headers = getattr(response, "headers", None)
        if headers is not None and hasattr(headers, "get_content_charset"):
            charset = headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace"), str(final_url)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


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
_DISAMBIGUATION_PHRASES = (
    "may refer to", "can refer to", "refers to", "disambiguation", "franchise",
    "series of", "series is", "this article is about", "this page is about",
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
    target = _norm(requested_title)
    candidate = _norm(record.canonical_title or requested_title)
    ratio = SequenceMatcher(None, target, candidate).ratio() if (target or candidate) else 0.0

    # --- Strong positive: canonical identity (possibly with edition suffix) ---
    exact_identity = (target != "" and candidate != "" and (
        candidate == target
        or candidate.startswith(target + " ")
        or target.startswith(candidate + " ")
    ))

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
    if exact_identity:
        evidence.append("exact_canonical_title")
    else:
        evidence.append(f"title_similarity:{ratio:.2f}")

    # --- Build the decision from the combined evidence ---
    year_mismatch = any(e.startswith("year_mismatch:") for e in evidence)
    has_platform_negative = no_amiga_platform and not amiga_present

    if exact_identity and (amiga_present or not record.platforms) and not year_mismatch:
        return RelevanceDecision(
            category="accepted", confidence=max(0.90, ratio),
            evidence=evidence, reason="exact_match",
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
        "lemonamiga.com": "lemon-amiga", "www.lemonamiga.com": "lemon-amiga",
        "amiga.abime.net": "hall-of-light", "openretro.org": "openretro",
        "www.openretro.org": "openretro", "amiga.lychesis.net": "lychesis",
    }.get(host.lower(), host.lower())
    return image_url, provider


def wikipedia_lookup(title: str, *, timeout: float = 20.0,
                     opener: Optional[Callable[..., Any]] = None) -> Optional[MetadataRecord]:
    query = f'"{title}" Amiga video game'
    params = {
        "action": "query", "format": "json", "formatversion": "2",
        "generator": "search", "gsrsearch": query, "gsrlimit": "8",
        "prop": "extracts|pageimages|info", "exintro": "1", "explaintext": "1",
        "piprop": "original|thumbnail", "pithumbsize": "1200", "inprop": "url",
    }
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    data = _json_get(url, timeout=timeout, opener=opener)
    pages = (data.get("query") or {}).get("pages") or []
    if not pages:
        return None
    target = _norm(title)
    ranked = []
    for page in pages:
        page_title = str(page.get("title") or "")
        extract = str(page.get("extract") or "")
        score = SequenceMatcher(None, target, _norm(page_title)).ratio()
        haystack = (page_title + " " + extract[:600]).lower()
        if "video game" in haystack: score += 0.15
        if "amiga" in haystack: score += 0.20
        ranked.append((score, page))
    score, page = max(ranked, key=lambda item: item[0])
    if score < 0.45:
        return None
    original = page.get("original") or page.get("thumbnail") or {}
    art = str(original.get("source") or "")
    return MetadataRecord(
        canonical_title=str(page.get("title") or title),
        description=str(page.get("extract") or "").strip(),
        source_url=str(page.get("fullurl") or ""), artwork_url=art,
        artwork_source_url=str(page.get("fullurl") or "") if art else "",
        artwork_provider="wikipedia" if art else "", provider="wikipedia",
        provider_id=str(page.get("pageid") or ""), retrieved_at=utc_now(),
        confidence=min(score, 1.0), query=query,
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
                    opener: Optional[Callable[..., Any]] = None
                    ) -> tuple[Optional[MetadataRecord], str, list[dict]]:
    curated = load_curated(curated_dir, title)
    if curated:
        # Preserve curated identity/facts. Wikipedia may supplement only missing
        # prose/image; Amiga-specific approved pages are then tried for artwork.
        supplement: Optional[MetadataRecord] = None
        if not curated.description or not curated.artwork_url:
            try: supplement = wikipedia_lookup(title, timeout=timeout, opener=opener)
            except Exception: supplement = None
        if supplement:
            if not curated.description: curated.description = supplement.description
            if not curated.artwork_url and supplement.artwork_url:
                curated.artwork_url = supplement.artwork_url
                curated.artwork_source_url = supplement.artwork_source_url
                curated.artwork_provider = supplement.artwork_provider
                curated.provider = (curated.provider or "curated") + "+wikipedia"
        if not curated.artwork_url:
            _discover_curated_artwork(curated, title, timeout=timeout, opener=opener)
        save_cached(cache_dir, title, curated)
        return curated, curated.provider or "curated", []
    if not refresh:
        cached = load_cached(cache_dir, title)
        if cached: return cached, "cache", []
    # ONLINE candidates are validated for relevance before caching/accepting.
    # A rejected/review candidate is NEVER cached and NEVER returned; it falls
    # through to the next provider, then to offline/local. Curated and cached
    # paths above stay authoritative and skip validation.
    relevance_events: list[dict] = []
    accepted: Optional[MetadataRecord] = None

    def _try_provider(label: str,
                      lookup) -> None:
        nonlocal accepted
        candidate = None
        try:
            candidate = lookup()
        except Exception:
            candidate = None
        if candidate is None:
            return
        decision = validate_metadata_relevance(title, candidate, group=group)
        relevance_events.append({
            "provider": label,
            "canonical_title": candidate.canonical_title,
            "category": decision.category,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "evidence": list(decision.evidence),
        })
        if decision.category == "accepted":
            candidate.relevance_category = "accepted"
            candidate.relevance_confidence = decision.confidence
            candidate.relevance_evidence = list(decision.evidence)
            accepted = candidate

    rawg_key = os.environ.get("RAWG_API_KEY", "").strip()
    if rawg_key:
        _try_provider("rawg",
                      lambda: rawg_lookup(title, api_key=rawg_key, timeout=timeout, opener=opener))
    if accepted is None:
        _try_provider("wikipedia",
                      lambda: wikipedia_lookup(title, timeout=timeout, opener=opener))
    if accepted is not None:
        save_cached(cache_dir, title, accepted)
        return accepted, accepted.provider, relevance_events
    return None, "not-found", relevance_events
