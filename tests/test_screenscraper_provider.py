"""Synthetic tests for the optional ScreenScraper metadata/artwork/manual provider (GH-8).

Hard constraints (mirrors tests/test_mobygames_provider.py and
tests/test_igdb_provider.py):

* **No live network.** Every HTTP call goes through an injected fake ``opener``
  that returns canned bytes; the real urllib opener is never used.
* **No real API key.** Keys are synthetic strings set in a controlled env
  scope (``monkeypatch``) and asserted to appear only as URL query values.
* **No maintainer private data.** Every payload is a synthetic fixture.
* The provider must be a silent no-op when disabled or keyless, and a provider
  failure must never break the base (offline) workflow.
"""

from __future__ import annotations

import io
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from amiga_adf_library_builder.screenscraper import (
    ScreenScraperConfig,
    ScreenScraperProvider,
    ScreenScraperMatchMethod,
    ScreenScraperDisabled,
    ScreenScraperAuthError,
    ScreenScraperRateLimited,
    ScreenScraperQuotaExceeded,
    enrich_group_with_screenscraper,
    _parse_jeu_infos,
    _parse_jeu_recherche,
    DEFAULT_AMIGA_SYSTEM_ID,
)


# --- fake HTTP ----------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return getattr(self, "url", "")


def _xml_opener(xml_payload: bytes):
    """Fake opener returning a fixed XML payload; records every fetched URL."""
    log = []

    def opener(request, timeout=0):
        log.append(request.full_url)
        resp = _Resp(xml_payload)
        resp.url = request.full_url
        return resp

    opener.log = log
    return opener


def _raise_opener(msg="synthetic provider failure"):
    log = []

    def opener(request, timeout=0):
        log.append(request.full_url)
        raise RuntimeError(msg)

    opener.log = log
    return opener


def _429_opener(retry_after: str = "1"):
    """Create an opener that returns 429 on first call, then success."""
    call_count = [0]
    log = []

    def opener(request, timeout=0):
        call_count[0] += 1
        log.append(request.full_url)
        if call_count[0] == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "Rate Limited",
                {"Retry-After": retry_after}, io.BytesIO(b"")
            )
        return _Resp(b"<?xml version='1.0'?><Data><Jeu><id>1111</id><nom>Test Game</nom></Jeu></Data>")

    opener.log = log
    return opener


def _401_opener():
    log = []

    def opener(request, timeout=0):
        log.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"")
        )

    opener.log = log
    return opener


def _timeout_opener():
    log = []

    def opener(request, timeout=0):
        log.append(request.full_url)
        raise TimeoutError("Request timed out")

    opener.log = log
    return opener


def _oversized_opener(max_bytes: int):
    log = []
    oversized_data = b"x" * (max_bytes + 1000)

    def opener(request, timeout=0):
        log.append(request.full_url)
        return _Resp(oversized_data)

    opener.log = log
    return opener


def _redirect_opener():
    log = []

    def opener(request, timeout=0):
        log.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, 302, "Found",
            {"Location": "http://127.0.0.1/evil"}, io.BytesIO(b"")
        )

    opener.log = log
    return opener


def _private_url_opener():
    log = []

    def opener(request, timeout=0):
        log.append(request.full_url)
        if "127.0.0.1" in request.full_url or "localhost" in request.full_url:
            raise urllib.error.URLError("Blocked by SSRF guard")
        return _Resp(b"")

    opener.log = log
    return opener


# Need to import urllib for the HTTPError
import urllib.error


# --- synthetic ScreenScraper fixtures -----------------------------------------

def _jeu_infos_xml(
    game_id=1111,
    title="Star Voyage",
    year="1987",
    publisher="Publisher Inc",
    developer="Developer Ltd",
    genre="Action",
    description="Classic action-adventure title.",
    players="1",
    region="us",
    language="en",
    medias=None,
    manuals=None,
    external_ids=None,
    error=None,
):
    """Generate a synthetic jeuInfos.php XML response."""
    if error:
        return f"""<?xml version="1.0" encoding="utf-8"?>
<Error>{error}</Error>""".encode()

    medias_xml = ""
    if medias:
        for m in medias:
            medias_xml += f'<media type="{m["type"]}" region="{m.get("region", "")}">{m["url"]}</media>'

    manuals_xml = ""
    if manuals:
        for m in manuals:
            manuals_xml += f'<manuel region="{m.get("region", "")}" langue="{m.get("language", "")}">{m["url"]}</manuel>'

    ext_xml = ""
    if external_ids:
        for k, v in external_ids.items():
            if k == "mobygames_id":
                ext_xml += f'<mobyid>{v}</mobyid>'
            elif k == "thegamesdb_id":
                ext_xml += f'<thegamesdb_id>{v}</thegamesdb_id>'
            elif k == "steam_id":
                ext_xml += f'<steam_id>{v}</steam_id>'
            elif k == "gog_id":
                ext_xml += f'<gog_id>{v}</gog_id>'
            elif k == "epic_games_id":
                ext_xml += f'<epic_id>{v}</epic_id>'

    return f"""<?xml version="1.0" encoding="utf-8"?>
<Data>
<Jeu>
<id>{game_id}</id>
<nom>{title}</nom>
<annee>{year}</annee>
<editeur>{publisher}</editeur>
<developpeur>{developer}</developpeur>
<genre>{genre}</genre>
<synopsis>{description}</synopsis>
<joueurs>{players}</joueurs>
<region>{region}</region>
<langue>{language}</langue>
{medias_xml}
{manuals_xml}
{ext_xml}
</Jeu>
</Data>""".encode()


def _jeu_recherche_xml(results: list[dict]):
    """Generate a synthetic jeuRecherche.php XML response."""
    items = ""
    for r in results:
        items += f"""<Jeu>
<id>{r.get('game_id', '')}</id>
<nom>{r.get('title', '')}</nom>
<annee>{r.get('year', '')}</annee>
<editeur>{r.get('publisher', '')}</editeur>
</Jeu>"""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Data>
{items}
</Data>""".encode()


# --- env helpers --------------------------------------------------------------

def _env_creds(monkeypatch, dev_id="test_devid", dev_password="test_devpassword", softname="AmigaADFLibraryBuilder", ssid="", sspassword=""):
    monkeypatch.setenv("SCREENSCRAPER_DEV_ID", dev_id)
    monkeypatch.setenv("SCREENSCRAPER_DEV_PASSWORD", dev_password)
    monkeypatch.setenv("SCREENSCRAPER_SOFTNAME", softname)
    if ssid:
        monkeypatch.setenv("SCREENSCRAPER_SSID", ssid)
    if sspassword:
        monkeypatch.setenv("SCREENSCRAPER_SSPASSWORD", sspassword)


def _clear_env(monkeypatch):
    for k in ["SCREENSCRAPER_DEV_ID", "SCREENSCRAPER_DEV_PASSWORD", "SCREENSCRAPER_SOFTNAME", "SCREENSCRAPER_SSID", "SCREENSCRAPER_SSPASSWORD"]:
        monkeypatch.delenv(k, raising=False)


# --- 1. Config tests ----------------------------------------------------------

def test_screenscraper_config_from_dict_disabled():
    cfg = ScreenScraperConfig.from_dict(None)
    assert cfg.enabled is False

    cfg = ScreenScraperConfig.from_dict({})
    assert cfg.enabled is False

    cfg = ScreenScraperConfig.from_dict({"enabled": False})
    assert cfg.enabled is False


def test_screenscraper_config_from_dict_enabled():
    cfg = ScreenScraperConfig.from_dict({"enabled": True})
    assert cfg.enabled is True
    assert cfg.base_url == "https://www.screenscraper.fr/api2/"
    assert cfg.timeout_seconds == 15.0
    assert cfg.max_response_bytes == 2_000_000
    assert cfg.max_concurrency == 1
    assert cfg.confidence_threshold == 0.85
    assert cfg.preferred_regions == ("us", "eu", "wor")
    assert cfg.download_metadata is True
    assert cfg.download_artwork is True
    assert cfg.download_manuals is True
    assert cfg.cache_ttl == 86400.0
    assert cfg.respect_rate_limit is True
    assert cfg.rate_limit_backoff_seconds == 5.0


def test_screenscraper_config_bounds_enforcement():
    # timeout_seconds bounded
    cfg = ScreenScraperConfig.from_dict({"enabled": True, "timeout_seconds": 100.0})
    assert cfg.timeout_seconds == 15.0  # falls back to default

    cfg = ScreenScraperConfig.from_dict({"enabled": True, "timeout_seconds": 10.0})
    assert cfg.timeout_seconds == 10.0

    # max_response_bytes bounded
    cfg = ScreenScraperConfig.from_dict({"enabled": True, "max_response_bytes": 100_000_000})
    assert cfg.max_response_bytes == 2_000_000  # falls back

    # max_concurrency bounded
    cfg = ScreenScraperConfig.from_dict({"enabled": True, "max_concurrency": 10})
    assert cfg.max_concurrency == 1  # falls back (max is 4)

    cfg = ScreenScraperConfig.from_dict({"enabled": True, "max_concurrency": 2})
    assert cfg.max_concurrency == 2

    # confidence_threshold bounded
    cfg = ScreenScraperConfig.from_dict({"enabled": True, "confidence_threshold": 1.5})
    assert cfg.confidence_threshold == 0.85  # falls back

    cfg = ScreenScraperConfig.from_dict({"enabled": True, "confidence_threshold": 0.8})
    assert cfg.confidence_threshold == 0.8

    # preferred_regions parsing
    cfg = ScreenScraperConfig.from_dict({"enabled": True, "preferred_regions": ["us", " jp ", "EU"]})
    assert cfg.preferred_regions == ("us", "jp", "eu")

    cfg = ScreenScraperConfig.from_dict({"enabled": True, "preferred_regions": "us, jp, eu"})
    assert cfg.preferred_regions == ("us", "jp", "eu")

    # cache_ttl bounded
    cfg = ScreenScraperConfig.from_dict({"enabled": True, "cache_ttl": -1})
    assert cfg.cache_ttl == 0.0


# --- 2. XML parsing tests -----------------------------------------------------

def test_parse_jeu_infos_success():
    xml = _jeu_infos_xml(
        game_id=1111,
        title="Star Voyage",
        year="1987",
        publisher="Publisher Inc",
        developer="Developer Ltd",
        genre="Action",
        description="Classic action-adventure title.",
        players="1",
        region="us",
        language="en",
        medias=[
            {"type": "box", "url": "https://images.screenscraper.fr/box.jpg", "region": "us"},
            {"type": "screenshot", "url": "https://images.screenscraper.fr/screen.jpg", "region": "eu"},
        ],
        manuals=[
            {"url": "https://images.screenscraper.fr/manual.pdf", "region": "us", "language": "en"},
        ],
        external_ids={"mobygames_id": "12345", "thegamesdb_id": "tgdb-678"},
    )
    parsed = _parse_jeu_infos(xml)
    assert parsed["found"] is True
    assert parsed["game_id"] == "1111"
    assert parsed["title"] == "Star Voyage"
    assert parsed["year"] == "1987"
    assert parsed["publisher"] == "Publisher Inc"
    assert parsed["developer"] == "Developer Ltd"
    assert parsed["genre"] == "Action"
    assert parsed["description"] == "Classic action-adventure title."
    assert parsed["players"] == "1"
    assert parsed["region"] == "us"
    assert parsed["language"] == "en"
    assert len(parsed["medias"]) == 2
    assert parsed["medias"][0]["type"] == "box"
    assert parsed["medias"][0]["url"] == "https://images.screenscraper.fr/box.jpg"
    assert parsed["medias"][0]["region"] == "us"
    assert len(parsed["manuals"]) == 1
    assert parsed["manuals"][0]["url"] == "https://images.screenscraper.fr/manual.pdf"
    assert parsed["external_ids"]["mobygames_id"] == "12345"
    assert parsed["external_ids"]["thegamesdb_id"] == "tgdb-678"


def test_parse_jeu_infos_error():
    xml = _jeu_infos_xml(error="Invalid API key")
    parsed = _parse_jeu_infos(xml)
    assert parsed["found"] is False
    assert parsed["error"] == "Invalid API key"


def test_parse_jeu_recherche():
    xml = _jeu_recherche_xml([
        {"game_id": "1111", "title": "Star Voyage", "year": "1987", "publisher": "Publisher Inc"},
        {"game_id": "2222", "title": "Star Voyage 2", "year": "1989", "publisher": "Publisher Inc"},
    ])
    results = _parse_jeu_recherche(xml)
    assert len(results) == 2
    assert results[0]["game_id"] == "1111"
    assert results[0]["title"] == "Star Voyage"
    assert results[1]["game_id"] == "2222"
    assert results[1]["title"] == "Star Voyage 2"


# --- 3. Provider tests --------------------------------------------------------

def test_provider_disabled_raises(tmp_path):
    cfg = ScreenScraperConfig(enabled=False)
    with pytest.raises(ScreenScraperDisabled):
        ScreenScraperProvider(cfg, Path("/tmp/cache"), dev_id="x", dev_password="y")


def test_provider_missing_credentials_raises(tmp_path):
    cfg = ScreenScraperConfig(enabled=True)
    with pytest.raises(ScreenScraperAuthError):
        ScreenScraperProvider(cfg, Path("/tmp/cache"), dev_id="", dev_password="y")
    with pytest.raises(ScreenScraperAuthError):
        ScreenScraperProvider(cfg, Path("/tmp/cache"), dev_id="x", dev_password="")


def test_lookup_by_hash_exact_match(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    xml = _jeu_infos_xml(game_id=1111, title="Star Voyage")
    opener = _xml_opener(xml)

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    result = provider.lookup_by_hash("1234abcd", "abcd1234", "sha1hash", "Star Voyage")
    assert result.found is True
    assert result.provider_id == "1111"
    assert result.match_method == ScreenScraperMatchMethod.EXACT_HASH
    assert result.canonical_title == "Star Voyage"
    assert result.confidence == 1.0
    assert "crc=1234ABCD" in opener.log[0] or "crc=1234abcd" in opener.log[0]
    assert "md5=abcd1234" in opener.log[0]
    assert "sha1=sha1hash" in opener.log[0]


def test_lookup_by_hash_cached(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True, cache_ttl=86400.0)

    xml = _jeu_infos_xml(game_id=1111, title="Star Voyage")
    opener = _xml_opener(xml)

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    # First call - populates cache
    result1 = provider.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result1.found is True
    assert result1.provider_id == "1111"
    assert result1.match_method == ScreenScraperMatchMethod.EXACT_HASH
    assert len(opener.log) == 1

    # Second call - should use cache
    opener2 = _xml_opener(b"SHOULD_NOT_BE_CALLED")
    # Create new provider with the cached data but different opener
    provider2 = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener2)
    result2 = provider2.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result2.found is True
    assert result2.provider_id == "1111"
    assert result2.match_method == ScreenScraperMatchMethod.PROVIDER_ID  # cached via provider_id
    assert len(opener2.log) == 0  # No network call


def test_lookup_by_hash_not_found(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    # Generate XML with error or empty game (no game found)
    xml = _jeu_infos_xml(error="Game not found")
    opener = _xml_opener(xml)

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    result = provider.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result.found is False
    assert result.match_method == ScreenScraperMatchMethod.NONE


def test_lookup_by_provider_id(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    xml = _jeu_infos_xml(game_id=1111, title="Star Voyage")
    opener = _xml_opener(xml)

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    result = provider.lookup_by_provider_id("1111")
    assert result.found is True
    assert result.provider_id == "1111"
    assert result.match_method == ScreenScraperMatchMethod.PROVIDER_ID
    assert "id=1111" in opener.log[0]


def test_lookup_by_title_exact_match(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    # First, search returns one exact match
    search_xml = _jeu_recherche_xml([{"game_id": "1111", "title": "Star Voyage", "year": "1987"}])
    # Then, jeuInfos returns full details
    details_xml = _jeu_infos_xml(game_id=1111, title="Star Voyage")

    call_count = [0]
    def multi_opener(request, timeout=0):
        call_count[0] += 1
        if "jeuRecherche" in request.full_url:
            return _xml_opener(search_xml)(request, timeout)
        return _xml_opener(details_xml)(request, timeout)

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=multi_opener)

    result = provider.lookup_by_title("Star Voyage")
    assert result.found is True
    assert result.provider_id == "1111"
    assert result.match_method == ScreenScraperMatchMethod.TITLE_SEARCH


def test_lookup_by_title_ambiguous(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    # Search returns multiple matches - ambiguous
    search_xml = _jeu_recherche_xml([
        {"game_id": "1111", "title": "Star Voyage", "year": "1987"},
        {"game_id": "2222", "title": "Star Voyage", "year": "1990"},
    ])
    opener = _xml_opener(search_xml)

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    result = provider.lookup_by_title("Star Voyage")
    assert result.found is True
    assert result.needs_manual_review is True
    assert result.match_method == ScreenScraperMatchMethod.MANUAL_REVIEW


def test_lookup_by_title_no_results(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    search_xml = _jeu_recherche_xml([])
    opener = _xml_opener(search_xml)

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    result = provider.lookup_by_title("Unknown Game")
    assert result.found is False
    assert result.match_method == ScreenScraperMatchMethod.NONE


# --- 4. Artwork/Manual download tests -----------------------------------------

def test_download_artwork(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True, download_artwork=True)

    image_data = b"fake_image_data"
    opener = _xml_opener(image_data)

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    result = provider.download_artwork("1111", "box", "us")
    assert result == image_data
    assert "id=1111" in opener.log[0]
    assert "media=box" in opener.log[0]
    assert "region=us" in opener.log[0]


def test_download_artwork_disabled(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True, download_artwork=False)
    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword")

    result = provider.download_artwork("1111", "box", "us")
    assert result is None


def test_download_manual(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True, download_manuals=True)

    pdf_data = b"fake_pdf_data"
    opener = _xml_opener(pdf_data)

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    result = provider.download_manual("1111", "us")
    assert result == pdf_data
    assert "id=1111" in opener.log[0]
    assert "region=us" in opener.log[0]


def test_download_manual_disabled(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True, download_manuals=False)
    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword")

    result = provider.download_manual("1111", "us")
    assert result is None


# --- 5. High-level integration function tests ---------------------------------

def test_enrich_group_with_screenscraper_disabled(tmp_path):
    """Disabled provider returns None."""
    cfg = ScreenScraperConfig(enabled=False)
    result = enrich_group_with_screenscraper(
        None, {}, cfg, tmp_path,
        dev_id="test", dev_password="test", softname="test",
        online=True,
    )
    assert result is None


def test_enrich_group_with_screenscraper_offline(tmp_path):
    """Offline mode returns None."""
    cfg = ScreenScraperConfig(enabled=True)
    result = enrich_group_with_screenscraper(
        None, {}, cfg, tmp_path,
        dev_id="test", dev_password="test", softname="test",
        online=False,
    )
    assert result is None


def test_enrich_group_with_screenscraper_missing_creds(tmp_path):
    """Missing credentials returns None."""
    cfg = ScreenScraperConfig(enabled=True)
    result = enrich_group_with_screenscraper(
        None, {}, cfg, tmp_path,
        dev_id="", dev_password="test", softname="test",
        online=True,
    )
    assert result is None


def test_enrich_group_with_screenscraper_hash_match(monkeypatch, tmp_path):
    """Full hash-match flow works end-to-end."""
    _env_creds(monkeypatch)

    cfg = ScreenScraperConfig(enabled=True)
    # Mock scans with CRC32
    class MockScan:
        crc32 = 0x1234abcd
        md5 = "abcd1234"
        sha1 = "sha1hash"

    scans = {"game.adf": MockScan()}

    class MockGroup:
        title = "Star Voyage"

    xml = _jeu_infos_xml(game_id=1111, title="Star Voyage")
    opener = _xml_opener(xml)

    result = enrich_group_with_screenscraper(
        MockGroup(), scans, cfg, tmp_path,
        dev_id="test_devid", dev_password="test_devpassword",
        softname="AmigaADFLibraryBuilder",
        online=True,
        opener=opener,
        resolve_urls=False,
    )
    assert result is not None
    assert result.found is True
    assert result.provider_id == "1111"
    assert result.match_method == ScreenScraperMatchMethod.EXACT_HASH


# --- 6. Rate limiting tests ---------------------------------------------------

def test_rate_limited_single_retry(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True, respect_rate_limit=True, rate_limit_backoff_seconds=0.01)

    xml = _jeu_infos_xml(game_id=1111, title="Star Voyage")
    opener = _429_opener("1")

    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    # Should retry once and succeed
    result = provider.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result.found is True
    assert result.provider_id == "1111"
    # First call (429) + second call (success) = 2 calls
    assert len(opener.log) == 2


def test_rate_limited_respect_false_no_retry(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True, respect_rate_limit=False)

    opener = _429_opener("1")
    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    # Should NOT retry, treat as miss
    result = provider.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result.found is False
    assert len(opener.log) == 1


# --- 7. Auth error tests ------------------------------------------------------

def test_auth_error(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    opener = _401_opener()
    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    # Should raise ScreenScraperAuthError
    with pytest.raises(ScreenScraperAuthError):
        provider.lookup_by_hash("1234abcd", None, None, "Star Voyage")


# --- 8. Timeout tests ---------------------------------------------------------

def test_timeout_handled_gracefully(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    opener = _timeout_opener()
    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    # Should return miss, not raise
    result = provider.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result.found is False


# --- 9. Oversized response tests ----------------------------------------------

def test_oversized_response_handled(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True, max_response_bytes=1000)

    opener = _oversized_opener(1000)
    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    # Should return miss, not raise
    result = provider.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result.found is False


# --- 10. SSRF guard tests -----------------------------------------------------

def test_ssrf_guard_blocks_private_urls(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    opener = _private_url_opener()
    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    # Should return miss (SSRF guard blocks the request)
    result = provider.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result.found is False


# --- 11. Negative caching tests -----------------------------------------------

def test_negative_cache_prevents_repeat(monkeypatch, tmp_path):
    _env_creds(monkeypatch)
    cfg = ScreenScraperConfig(enabled=True)

    # Generate XML with error (not found)
    xml = _jeu_infos_xml(error="Game not found")
    opener = _xml_opener(xml)
    provider = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener)

    # First call - negative result
    result1 = provider.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result1.found is False
    assert len(opener.log) == 1

    # Second call - should use negative cache
    opener2 = _xml_opener(b"SHOULD_NOT_BE_CALLED")
    provider2 = ScreenScraperProvider(cfg, tmp_path, dev_id="test_devid", dev_password="test_devpassword", opener=opener2)
    result2 = provider2.lookup_by_hash("1234abcd", None, None, "Star Voyage")
    assert result2.found is False
    assert len(opener2.log) == 0  # No network call


# --- 12. to_metadata_record tests ---------------------------------------------

def test_to_metadata_record():
    from amiga_adf_library_builder.screenscraper import ScreenScraperResult, ScreenScraperProvider
    from amiga_adf_library_builder.metadata import MetadataRecord

    cfg = ScreenScraperConfig(enabled=True)
    provider = ScreenScraperProvider.__new__(ScreenScraperProvider)
    provider.config = cfg

    result = ScreenScraperResult(
        found=True,
        provider_id="1111",
        match_method=ScreenScraperMatchMethod.EXACT_HASH,
        canonical_title="Star Voyage",
        confidence=1.0,
        year="1987",
        publisher="Publisher Inc",
        developer="Developer Ltd",
        genre="Action",
        description="Classic game.",
        artwork_url="https://example.com/art.jpg",
        artwork_provider="screenscraper",
        artwork_source_url="https://example.com",
        external_ids={"mobygames_id": "12345"},
    )

    md = provider.to_metadata_record(result)
    assert isinstance(md, MetadataRecord)
    assert md.canonical_title == "Star Voyage"
    assert md.provider == "screenscraper"
    assert md.provider_id == "1111"
    assert md.confidence == 1.0
    assert md.year == "1987"
    assert md.publisher == "Publisher Inc"
    assert md.developer == "Developer Ltd"
    assert md.genres == ["Action"]
    assert md.description == "Classic game."
    assert md.artwork_url == "https://example.com/art.jpg"
    assert md.artwork_provider == "screenscraper"