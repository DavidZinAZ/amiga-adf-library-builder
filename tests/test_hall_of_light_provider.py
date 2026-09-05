"""Synthetic tests for the Hall of Light (amiga.abime.net) HTML metadata provider (GH-74).

Hard constraints:
* No live network. Every HTTP call goes through an injected fake ``opener``
  that returns canned bytes; the real urllib opener is never used.
* No maintainer private data. Every payload is a synthetic fixture.
* The provider is unkeyed (no API key required) and participates in the
  standard relevance validation pipeline.
* A provider failure must never break the base (offline) workflow.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from amiga_adf_library_builder.metadata import (
    hall_of_light_lookup,
    lookup_metadata,
    validate_metadata_relevance,
    MetadataRecord,
)


# --- fake HTTP ----------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return getattr(self, "url", "")


def _html_opener(html: bytes, log: list[str]):
    """Fake opener returning fixed HTML; records every fetched URL."""
    def opener(request, timeout=0):
        log.append(request.full_url)
        r = _Resp(html)
        r.url = request.full_url
        return r
    return opener


def _multi_opener(responses: dict[str, bytes], log: list[str]):
    """Fake opener returning different HTML per URL pattern."""
    def opener(request, timeout=0):
        log.append(request.full_url)
        for pattern, html in responses.items():
            if pattern in request.full_url:
                r = _Resp(html)
                r.url = request.full_url
                return r
        # Default: empty page
        r = _Resp(b"<html></html>")
        r.url = request.full_url
        return r
    return opener


# --- synthetic Hall of Light fixtures ------------------------------------------

HOL_SEARCH_HTML = b'''
<html>
<head><title>Hall of Light - Search Results</title></head>
<body>
<div class="search-results">
<a href="/games/view/1234/star-voyage">Star Voyage</a>
<a href="/games/view/5678/star-voyage-ii">Star Voyage II: The Return</a>
<a href="/games/view/9999/unrelated-game">Unrelated Game</a>
</div>
</body>
</html>
'''

HOL_DETAIL_STAR_VOYAGE = b'''
<html>
<head><title>Star Voyage - Hall of Light</title></head>
<body>
<h1 class="game-title">Star Voyage</h1>
<div class="game-description">
Classic action-adventure title for the Amiga.
Released in 1987 by Electronic Arts.
</div>
<dl>
<dt class="game-year">Year</dt>
<dd class="game-year">1987</dd>
<dt class="game-developer">Developer</dt>
<dd class="game-developer">EA Games</dd>
<dt class="game-publisher">Publisher</dt>
<dd class="game-publisher">Electronic Arts</dd>
<dt class="game-genre">Genre</dt>
<dd class="game-genre">Action, Adventure</dd>
<dt class="game-platform">Platform</dt>
<dd class="game-platform">Amiga, Amiga OCS</dd>
</dl>
<a href="/games/view/1234/star-voyage">View</a>
</body>
</html>
'''

HOL_DETAIL_STAR_VOYAGE_II = b'''
<html>
<head><title>Star Voyage II: The Return - Hall of Light</title></head>
<body>
<h1 class="game-title">Star Voyage II: The Return</h1>
<div class="game-description">
Sequel to the classic Star Voyage.
</div>
<dl>
<dt class="game-year">Year</dt>
<dd class="game-year">1989</dd>
<dt class="game-developer">Developer</dt>
<dd class="game-developer">EA Games</dd>
<dt class="game-publisher">Publisher</dt>
<dd class="game-publisher">Electronic Arts</dd>
<dt class="game-genre">Genre</dt>
<dd class="game-genre">Action, Adventure</dd>
<dt class="game-platform">Platform</dt>
<dd class="game-platform">Amiga, Amiga AGA</dd>
</dl>
<a href="/games/view/5678/star-voyage-ii">View</a>
</body>
</html>
'''

HOL_DETAIL_NO_AMIGA = b'''
<html>
<head><title>PC Only Game - Hall of Light</title></head>
<body>
<h1 class="game-title">PC Only Game</h1>
<div class="game-description">A game only on PC.</div>
<dl>
<dt class="game-year">Year</dt>
<dd class="game-year">1990</dd>
<dt class="game-developer">Developer</dt>
<dd class="game-developer">PC Devs</dd>
<dt class="game-publisher">Publisher</dt>
<dd class="game-publisher">PC Pub</dd>
<dt class="game-genre">Genre</dt>
<dd class="game-genre">Strategy</dd>
<dt class="game-platform">Platform</dt>
<dd class="game-platform">PC (Windows), DOS</dd>
</dl>
<a href="/games/view/1111/pc-only-game">View</a>
</body>
</html>
'''

HOL_DETAIL_WITH_COVER = b'''
<html>
<head>
<title>Star Voyage - Hall of Light</title>
<meta property="og:image" content="https://amiga.abime.net/media/covers/star-voyage-front.jpg">
</head>
<body>
<h1 class="game-title">Star Voyage</h1>
<div class="game-description">Classic action-adventure title.</div>
<dl>
<dt class="game-year">Year</dt>
<dd class="game-year">1987</dd>
<dt class="game-developer">Developer</dt>
<dd class="game-developer">EA Games</dd>
<dt class="game-publisher">Publisher</dt>
<dd class="game-publisher">Electronic Arts</dd>
<dt class="game-genre">Genre</dt>
<dd class="game-genre">Action, Adventure</dd>
<dt class="game-platform">Platform</dt>
<dd class="game-platform">Amiga, Amiga OCS</dd>
</dl>
<a href="/games/view/1234/star-voyage">View</a>
</body>
</html>
'''

HOL_SEARCH_EMPTY = b'''
<html>
<head><title>Hall of Light - Search Results</title></head>
<body>
<div class="search-results">No games found.</div>
</body>
</html>
'''

HOL_DETAIL_MINIMAL = b'''
<html>
<head><title>Minimal Game - Hall of Light</title></head>
<body>
<h1 class="game-title">Minimal Game</h1>
<div class="game-description">A minimal entry.</div>
<dl>
<dt class="game-platform">Platform</dt>
<dd class="game-platform">Amiga</dd>
</dl>
<a href="/games/view/2222/minimal-game">View</a>
</body>
</html>
'''


# --- 1. hall_of_light_lookup unit tests ---------------------------------------

def test_hall_of_light_lookup_returns_record(monkeypatch, tmp_path):
    """Basic happy path: search finds game, detail page parses metadata."""
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    log: list[str] = []
    responses = {
        "/games/search?q=Star+Voyage": HOL_SEARCH_HTML,
        "/games/view/1234/star-voyage": HOL_DETAIL_STAR_VOYAGE,
    }
    record = hall_of_light_lookup(
        "Star Voyage", opener=_multi_opener(responses, log),
    )
    assert record is not None
    assert record.provider == "hall-of-light"
    assert record.provider_id == "1234"
    assert record.canonical_title == "Star Voyage"
    assert record.year == "1987"
    assert record.developer == "EA Games"
    assert record.publisher == "Electronic Arts"
    assert "Action" in record.genres
    assert "Adventure" in record.genres
    assert any("amiga" in p.lower() for p in record.platforms)
    assert record.source_url.startswith("https://amiga.abime.net/games/view/1234/star-voyage")
    assert record.confidence > 0.5


def test_hall_of_light_lookup_ranks_by_similarity(monkeypatch, tmp_path):
    """Best match by title similarity is chosen, not first search result."""
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    log: list[str] = []
    responses = {
        "/games/search?q=Star+Voyage": HOL_SEARCH_HTML,
        "/games/view/1234/star-voyage": HOL_DETAIL_STAR_VOYAGE,
        "/games/view/5678/star-voyage-ii": HOL_DETAIL_STAR_VOYAGE_II,
    }
    record = hall_of_light_lookup(
        "Star Voyage", opener=_multi_opener(responses, log),
    )
    assert record is not None
    # Exact match "Star Voyage" should rank higher than "Star Voyage II"
    assert record.canonical_title == "Star Voyage"


def test_hall_of_light_lookup_returns_none_on_empty_search(monkeypatch):
    """Empty search results returns None."""
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    log: list[str] = []
    record = hall_of_light_lookup(
        "Nonexistent Game", opener=_html_opener(HOL_SEARCH_EMPTY, log),
    )
    assert record is None


def test_hall_of_light_lookup_skips_non_amiga_games(monkeypatch):
    """Games without Amiga platform are skipped."""
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    log: list[str] = []
    search_html = b'''
<html><body><a href="/games/view/1111/pc-only-game">PC Only Game</a></body></html>
'''
    responses = {
        "/games/search?q=PC+Only+Game": search_html,
        "/games/view/1111/pc-only-game": HOL_DETAIL_NO_AMIGA,
    }
    record = hall_of_light_lookup(
        "PC Only Game", opener=_multi_opener(responses, log),
    )
    # Should return None because the only result has no Amiga platform
    assert record is None


def test_hall_of_light_lookup_artwork_discovery(monkeypatch):
    """Artwork URL is discovered from og:image meta tag."""
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    log: list[str] = []
    responses = {
        "/games/search?q=Star+Voyage": HOL_SEARCH_HTML,
        "/games/view/1234/star-voyage": HOL_DETAIL_WITH_COVER,
    }
    record = hall_of_light_lookup(
        "Star Voyage", opener=_multi_opener(responses, log),
    )
    assert record is not None
    assert record.artwork_url == "https://amiga.abime.net/media/covers/star-voyage-front.jpg"
    assert record.artwork_provider == "hall-of-light"
    assert record.artwork_source_url.startswith("https://amiga.abime.net/games/view/1234/star-voyage")


def test_hall_of_light_lookup_handles_network_errors(monkeypatch):
    """Network errors during detail fetch are swallowed; next candidate tried."""
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    log: list[str] = []
    class FailingOpener:
        def __init__(self, fail_on: str):
            self.fail_on = fail_on
            self.call_count = 0
        def __call__(self, request, timeout=0):
            log.append(request.full_url)
            self.call_count += 1
            if self.fail_on in request.full_url:
                raise RuntimeError("synthetic network error")
            r = _Resp(HOL_DETAIL_STAR_VOYAGE)
            r.url = request.full_url
            return r

    search_html = b'''
<html><body>
<a href="/games/view/1111/failed-game">Failed Game</a>
<a href="/games/view/1234/star-voyage">Star Voyage</a>
</body></html>
'''
    opener = FailingOpener("/games/view/1111/failed-game")
    # Need to return search HTML first
    def multi_opener(request, timeout=0):
        log.append(request.full_url)
        if "/games/search" in request.full_url:
            r = _Resp(search_html)
            r.url = request.full_url
            return r
        return opener(request, timeout)

    record = hall_of_light_lookup("Star Voyage", opener=multi_opener)
    assert record is not None
    assert record.canonical_title == "Star Voyage"


def test_hall_of_light_lookup_minimal_fields(monkeypatch):
    """Record with only title and platform still returns valid record."""
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    log: list[str] = []
    search_html = b'<html><body><a href="/games/view/2222/minimal-game">Minimal Game</a></body></html>'
    responses = {
        "/games/search?q=Minimal+Game": search_html,
        "/games/view/2222/minimal-game": HOL_DETAIL_MINIMAL,
    }
    record = hall_of_light_lookup(
        "Minimal Game", opener=_multi_opener(responses, log),
    )
    assert record is not None
    assert record.canonical_title == "Minimal Game"
    assert record.provider == "hall-of-light"
    assert any("amiga" in p.lower() for p in record.platforms)
    assert record.year == ""  # Not present
    assert record.developer == ""  # Not present
    assert record.publisher == ""  # Not present


# --- 2. Integration: lookup_metadata relevance gating ---------------------------

class _WikiHeaders:
    def get_content_type(self): return "application/json"
    def get_content_charset(self): return "utf-8"

class _WikiResp(io.BytesIO):
    headers = _WikiHeaders()
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def geturl(self): return getattr(self, "url", "")


def _wiki_opener(payload: dict, log: list[str]):
    def opener(request, timeout=0):
        log.append(request.full_url)
        r = _WikiResp(json.dumps(payload).encode())
        r.url = request.full_url
        return r
    return opener


def test_hall_of_light_in_lookup_metadata_accepted(monkeypatch, tmp_path):
    """Accepted Hall of Light record is returned via lookup_metadata."""
    cache = tmp_path / "cache"
    curated = tmp_path / "curated"
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    log: list[str] = []
    responses = {
        "/games/search?q=Star+Voyage": HOL_SEARCH_HTML,
        "/games/view/1234/star-voyage": HOL_DETAIL_STAR_VOYAGE,
    }

    record, provider, events = lookup_metadata(
        "Star Voyage",
        cache_dir=cache,
        curated_dir=curated,
        opener=_multi_opener(responses, log),
    )
    assert record is not None
    assert provider == "hall-of-light"
    assert record.canonical_title == "Star Voyage"
    assert record.relevance_category == "accepted"
    assert record.relevance_confidence > 0.8
    # Relevance event recorded
    assert any(e["provider"] == "hall-of-light" and e["category"] == "accepted" for e in events)
    # Cached
    assert (cache / "star-voyage.json").exists()


def test_hall_of_light_rejected_falls_through_to_wikipedia(monkeypatch, tmp_path):
    """Rejected Hall of Light candidate falls through to Wikipedia fallback."""
    cache = tmp_path / "cache"
    curated = tmp_path / "curated"
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    log: list[str] = []
    # Hall of Light returns a different game (low similarity, but above 0.30 floor)
    hol_search = b'<html><body><a href=\"/games/view/9999/star-quest\">Star Quest</a></body></html>'
    hol_detail = b'''
<html><body>
<h1 class="game-title">Star Quest</h1>
<div class="game-description">Completely different.</div>
<dl><dt class="game-platform">Platform</dt><dd class="game-platform">Amiga</dd></dl>
<a href="/games/view/9999/star-quest">View</a>
</body></html>
'''
    responses = {
        "/games/search?q=Star+Voyage": hol_search,
        "/games/view/9999/star-quest": hol_detail,
    }

    # Wikipedia returns the correct game
    wiki_payload = {
        "query": {"pages": [{
            "pageid": 42,
            "title": "Star Voyage",
            "extract": "Star Voyage is a strategy video game released for the Amiga.",
            "fullurl": "https://en.wikipedia.org/wiki/Star_Voyage",
            "original": {"source": "https://en.wikipedia.org/wiki/Star_Voyage.jpg"},
        }]}
    }

    def multi_opener(request, timeout=0):
        log.append(request.full_url)
        if "amiga.abime.net" in request.full_url:
            for pattern, html in responses.items():
                if pattern in request.full_url:
                    r = _Resp(html)
                    r.url = request.full_url
                    return r
        return _wiki_opener(wiki_payload, log)(request, timeout)

    record, provider, events = lookup_metadata(
        "Star Voyage",
        cache_dir=cache,
        curated_dir=curated,
        opener=multi_opener,
    )
    assert record is not None
    assert record.canonical_title == "Star Voyage"
    assert provider == "wikipedia"
    # Hall of Light rejection should be recorded
    assert any(e["provider"] == "hall-of-light" and e["category"] == "rejected" for e in events)


def test_hall_of_light_provider_failure_no_crash(monkeypatch, tmp_path):
    """Provider exception does not break lookup_metadata."""
    cache = tmp_path / "cache"
    curated = tmp_path / "curated"
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)

    def failing_opener(request, timeout=0):
        log.append(request.full_url)
        raise RuntimeError("synthetic provider failure")

    log: list[str] = []
    # First Hall of Light fails, then Wikipedia succeeds
    wiki_payload = {
        "query": {"pages": [{
            "pageid": 42,
            "title": "Star Voyage",
            "extract": "Star Voyage is a strategy video game released for the Amiga.",
            "fullurl": "https://en.wikipedia.org/wiki/Star_Voyage",
            "original": {"source": "https://en.wikipedia.org/wiki/Star_Voyage.jpg"},
        }]}
    }

    def multi_opener(request, timeout=0):
        log.append(request.full_url)
        if "amiga.abime.net" in request.full_url:
            raise RuntimeError("synthetic provider failure")
        return _wiki_opener(wiki_payload, log)(request, timeout)

    record, provider, events = lookup_metadata(
        "Star Voyage",
        cache_dir=cache,
        curated_dir=curated,
        opener=multi_opener,
    )
    assert record is not None
    assert provider == "wikipedia"
    assert record.canonical_title == "Star Voyage"


# --- 3. Relevance validator integration ---------------------------------------

def test_hall_of_light_relevance_exact_match_accepted():
    """Exact title match with Amiga platform is accepted."""
    rec = MetadataRecord(
        canonical_title="Star Voyage",
        description="Classic action-adventure game.",
        platforms=["Amiga"],
        provider="hall-of-light",
    )
    decision = validate_metadata_relevance("Star Voyage", rec)
    assert decision.category == "accepted"
    assert decision.reason == "exact_match"
    assert "exact_canonical_title" in decision.evidence
    assert "platform_amiga_match" in decision.evidence


def test_hall_of_light_relevance_different_game_rejected():
    """Different game (sequel) is rejected as different_game."""
    rec = MetadataRecord(
        canonical_title="Star Voyage II",
        description="Sequel to Star Voyage.",
        platforms=["Amiga"],
        provider="hall-of-light",
    )
    decision = validate_metadata_relevance("Star Voyage", rec)
    assert decision.category == "rejected"
    assert decision.reason == "different_game"
    assert "different_game_substring" in decision.evidence


# --- 4. Cache round-trip -------------------------------------------------------

def test_hall_of_light_cache_round_trip(tmp_path):
    """Accepted Hall of Light record survives cache write -> reload."""
    from amiga_adf_library_builder.metadata import save_cached, load_cached

    cache = tmp_path / "cache"
    rec = MetadataRecord(
        canonical_title="Star Voyage",
        description="Classic action-adventure game.",
        year="1987",
        developer="EA Games",
        publisher="Electronic Arts",
        genres=["Action", "Adventure"],
        platforms=["Amiga"],
        source_url="https://amiga.abime.net/games/view/1234/star-voyage",
        provider="hall-of-light",
        provider_id="1234",
        confidence=0.95,
        query="Star Voyage",
        relevance_category="accepted",
        relevance_confidence=0.95,
        relevance_evidence=["exact_canonical_title", "platform_amiga_match"],
    )
    save_cached(cache, "Star Voyage", rec)
    loaded = load_cached(cache, "Star Voyage")
    assert loaded is not None
    assert loaded.canonical_title == "Star Voyage"
    assert loaded.provider == "hall-of-light"
    assert loaded.relevance_category == "accepted"
    assert loaded.relevance_confidence == 0.95
    assert "exact_canonical_title" in loaded.relevance_evidence


# --- 5. Parser edge cases ------------------------------------------------------

def test_hall_of_light_search_parser_extracts_links():
    """Search parser extracts all /games/view/ links."""
    from amiga_adf_library_builder.metadata import _HallOfLightSearchParser

    html = '''
<html><body>
<a href="/games/view/111/game-one">Game One</a>
<a href="/games/view/222/game-two">Game Two</a>
<a href="/other/link">Not a game</a>
<a href="/games/view/333/game-three">Game Three</a>
</body></html>
'''
    parser = _HallOfLightSearchParser()
    parser.feed(html)
    assert len(parser.game_links) == 3
    assert "https://amiga.abime.net/games/view/111/game-one" in parser.game_links
    assert "https://amiga.abime.net/games/view/222/game-two" in parser.game_links
    assert "https://amiga.abime.net/games/view/333/game-three" in parser.game_links


def test_hall_of_light_detail_parser_extracts_fields():
    """Detail parser extracts all known fields."""
    from amiga_adf_library_builder.metadata import _HallOfLightDetailParser

    html = '''
<html><body>
<h1 class="game-title">Test Game</h1>
<div class="game-description">A test game description.</div>
<dl>
<dt class="game-year">Year</dt><dd class="game-year">1992</dd>
<dt class="game-developer">Developer</dt><dd class="game-developer">Test Devs</dd>
<dt class="game-publisher">Publisher</dt><dd class="game-publisher">Test Pub</dd>
<dt class="game-genre">Genre</dt><dd class="game-genre">Action, Strategy</dd>
<dt class="game-platform">Platform</dt><dd class="game-platform">Amiga, Amiga AGA</dd>
</dl>
<a href="/games/view/444/test-game">View</a>
</body></html>
'''
    parser = _HallOfLightDetailParser()
    parser.feed(html)
    assert parser.canonical_title == "Test Game"
    assert parser.description == "A test game description."
    assert parser.year == "1992"
    assert parser.developer == "Test Devs"
    assert parser.publisher == "Test Pub"
    assert parser.genres == ["Action", "Strategy"]
    assert parser.platforms == ["Amiga", "Amiga AGA"]
    assert parser.game_id == "444"