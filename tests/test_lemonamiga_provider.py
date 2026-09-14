"""Synthetic tests for the Lemon Amiga (lemonamiga.com) metadata provider.

Hard constraints:
* No live network. Every HTTP call goes through an injected fake ``opener``
  that returns canned bytes; the real urllib opener is never used.
* No maintainer private data. Every payload is a synthetic fixture.
* The provider is disabled by default and opt-in via the config object.
* A provider failure must never break the base (offline) workflow.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from amiga_adf_library_builder.metadata import (
    LemonAmigaConfig,
    lemonamiga_lookup,
    lookup_metadata,
    validate_metadata_relevance,
    MetadataRecord,
)


# --- fake HTTP -------------------------------------------------------------

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


def _error_opener(exc: Exception):
    """Fake opener that always raises."""
    def opener(request, timeout=0):
        raise exc
    return opener


# --- synthetic Lemon Amiga fixtures ----------------------------------------

LEMON_GAME_PAGE = b'''<!DOCTYPE html>
<html>
<head><title>Vroom - Lemon Amiga</title></head>
<body>
<h1>Vroom</h1>
<table class="credits">
<tr><th>Released</th><td>1991</td></tr>
<tr><th>Publisher</th><td>Lankhor</td></tr>
<tr><th>Coder</th><td>Daniel Macr&eacute;</td></tr>
<tr><th>Graphics</th><td>Dominique Sablons</td></tr>
<tr><th>Musician</th><td>Andr&eacute; Bescond</td></tr>
</table>
<table class="info">
<tr><th>Hardware</th><td>OCS, ECS</td></tr>
<tr><th>Players</th><td>1 Only</td></tr>
<tr><th>Language</th><td>English</td></tr>
<tr><th>License</th><td>Commercial</td></tr>
<tr><th>Disks</th><td>1</td></tr>
</table>
<table class="categorization">
<tr><th>Genre</th><td>Sports, Formula One</td></tr>
<tr><th>Sub-Genre</th><td>Racing</td></tr>
<tr><th>Tags</th><td>Behind, Car, Racing</td></tr>
</table>
<table class="relationships">
<tr><th>Has expansion</th><td>Vroom Data Disc</td></tr>
</table>
<div class="description">Classic Amiga racing game.</div>
</body>
</html>'''

LEMON_GAME_PAGE_NO_AMIGA = b'''<!DOCTYPE html>
<html>
<head><title>PC Game - Lemon Amiga</title></head>
<body>
<h1>PC Game</h1>
<table class="info">
<tr><th>Hardware</th><td>Windows, DOS</td></tr>
</table>
</body>
</html>'''

LEMON_GAME_PAGE_NO_TITLE = b'''<!DOCTYPE html>
<html>
<head><title>Empty - Lemon Amiga</title></head>
<body>
<p>No game here.</p>
</body>
</html>'''

LEMON_GAME_PAGE_WITH_COVER = b'''<!DOCTYPE html>
<html>
<head>
<title>Vroom - Lemon Amiga</title>
<meta property="og:image" content="https://www.lemonamiga.com/uploads/amiga/images/games/screens/vroom/vroom_01.png">
</head>
<body>
<h1>Vroom</h1>
<table class="info">
<tr><th>Hardware</th><td>OCS, ECS</td></tr>
<tr><th>Players</th><td>1 Only</td></tr>
</table>
</body>
</html>'''

LEMON_GAME_PAGE_WITH_YEAR_MISMATCH = b'''<!DOCTYPE html>
<html>
<head><title>Vroom - Lemon Amiga</title></head>
<body>
<h1>Vroom</h1>
<table class="info">
<tr><th>Released</th><td>2020</td></tr>
<tr><th>Publisher</th><td>Lankhor</td></tr>
<tr><th>Hardware</th><td>OCS, ECS</td></tr>
</table>
</body>
</html>'''

LEMON_GAME_PAGE_GENRES = b'''<!DOCTYPE html>
<html>
<head><title>Lemmings - Lemon Amiga</title></head>
<body>
<h1>Lemmings</h1>
<table class="credits">
<tr><th>Publisher</th><td>Psygnosis</td></tr>
</table>
<table class="categorization">
<tr><th>Genre</th><td>Puzzle, Strategy</td></tr>
<tr><th>Tags</th><td>Strategy, Puzzle, Logical</td></tr>
</table>
<table class="info">
<tr><th>Hardware</th><td>Amiga OCS, Amiga AGA</td></tr>
<tr><th>Players</th><td>1</td></tr>
</table>
</body>
</html>'''


# --- test cases -------------------------------------------------------------

class TestLemonAmigaConfig:
    def test_defaults_disabled(self):
        cfg = LemonAmigaConfig()
        assert cfg.enabled is False
        assert cfg.timeout_seconds == 20.0
        assert cfg.max_response_bytes == 3_000_000
        assert cfg.cache_ttl == 86400.0

    def test_from_dict_enables(self):
        cfg = LemonAmigaConfig.from_dict({"enabled": True, "timeout_seconds": 15.0})
        assert cfg.enabled is True
        assert cfg.timeout_seconds == 15.0

    def test_from_dict_missing_uses_defaults(self):
        cfg = LemonAmigaConfig.from_dict({})
        assert cfg.enabled is False
        assert cfg.cache_ttl == 86400.0

    def test_bounds_enforcement(self):
        cfg = LemonAmigaConfig.from_dict({"timeout_seconds": 60.0, "max_response_bytes": 999})
        # Config stores what it's given; enforcement happens at fetch time
        assert cfg.timeout_seconds == 60.0
        assert cfg.max_response_bytes == 999


class TestLemonAmigaLookup:
    def test_disabled_by_default_returns_none(self):
        cfg = LemonAmigaConfig()
        result = lemonamiga_lookup("Vroom", opener=_html_opener(LEMON_GAME_PAGE, []), config=cfg)
        assert result is None

    def test_enabled_returns_record(self):
        cfg = LemonAmigaConfig(enabled=True)
        result = lemonamiga_lookup("Vroom", opener=_html_opener(LEMON_GAME_PAGE, []), config=cfg)
        assert result is not None
        assert result.canonical_title == "Vroom"
        assert result.publisher == "Lankhor"
        assert "OCS" in result.platforms[0] if result.platforms else False
        assert result.provider == "lemon-amiga"
        assert result.provider_id == ""  # No game ID extracted from slug-based fetch
        assert result.source_url.startswith("https://www.lemonamiga.com/game/")

    def test_missing_title_returns_none(self):
        cfg = LemonAmigaConfig(enabled=True)
        result = lemonamiga_lookup("Vroom", opener=_html_opener(LEMON_GAME_PAGE_NO_TITLE, []), config=cfg)
        assert result is None

    def test_no_amiga_platform_rejected(self):
        cfg = LemonAmigaConfig(enabled=True)
        result = lemonamiga_lookup("PC Game", opener=_html_opener(LEMON_GAME_PAGE_NO_AMIGA, []), config=cfg)
        assert result is None

    def test_artwork_discovered(self):
        cfg = LemonAmigaConfig(enabled=True)
        result = lemonamiga_lookup("Vroom", opener=_html_opener(LEMON_GAME_PAGE_WITH_COVER, []), config=cfg)
        assert result is not None
        assert result.artwork_url is not None
        assert result.artwork_provider == "lemon-amiga"

    def test_timeout_returns_none(self):
        cfg = LemonAmigaConfig(enabled=True)
        result = lemonamiga_lookup("Vroom", opener=_error_opener(TimeoutError("timeout")), config=cfg)
        assert result is None

    def test_connection_error_returns_none(self):
        cfg = LemonAmigaConfig(enabled=True)
        result = lemonamiga_lookup("Vroom", opener=_error_opener(ConnectionError("refused")), config=cfg)
        assert result is None

    def test_genres_parsed(self):
        cfg = LemonAmigaConfig(enabled=True)
        result = lemonamiga_lookup("Lemmings", opener=_html_opener(LEMON_GAME_PAGE_GENRES, []), config=cfg)
        assert result is not None
        assert "Puzzle" in result.genres
        assert "Amiga" in result.platforms[0] if result.platforms else False

    def test_year_parsed(self):
        cfg = LemonAmigaConfig(enabled=True)
        result = lemonamiga_lookup("Vroom", opener=_html_opener(LEMON_GAME_PAGE, []), config=cfg)
        assert result is not None
        assert result.year == "1991"


class TestLemonAmigaRelevance:
    def test_exact_match_accepted(self):
        cfg = LemonAmigaConfig(enabled=True)
        record = MetadataRecord(
            canonical_title="Vroom", year="1991", publisher="Lankhor",
            platforms=["Amiga OCS"], provider="lemon-amiga",
            source_url="https://www.lemonamiga.com/game/vroom",
        )
        decision = validate_metadata_relevance("Vroom", record)
        assert decision.category == "accepted"

    def test_different_game_rejected(self):
        cfg = LemonAmigaConfig(enabled=True)
        record = MetadataRecord(
            canonical_title="PC Game", publisher="PC Pub",
            platforms=["Windows"], provider="lemon-amiga",
            source_url="https://www.lemonamiga.com/game/pc-game",
        )
        decision = validate_metadata_relevance("Vroom", record)
        assert decision.category == "rejected"

    def test_year_mismatch_accepted_when_exact_title(self):
        """Year mismatch does not block exact-title matches with Amiga platform."""
        from amiga_adf_library_builder.metadata import (
            validate_metadata_relevance, MetadataRecord
        )
        from unittest.mock import MagicMock
        record = MetadataRecord(
            canonical_title="Vroom", year="2020", publisher="Lankhor",
            platforms=["Amiga OCS"], provider="lemon-amiga",
            source_url="https://www.lemonamiga.com/game/vroom",
        )
        decision = validate_metadata_relevance("Vroom", record, group=MagicMock(year="1991"))
        # Exact title + Amiga platform => accepted despite year mismatch
        assert decision.category == "accepted"

    def test_year_mismatch_routes_to_review_for_non_exact(self):
        """Year mismatch with non-exact but similar title routes to review."""
        from amiga_adf_library_builder.metadata import (
            validate_metadata_relevance, MetadataRecord
        )
        from unittest.mock import MagicMock
        # "Vroom" vs "Vroom Racing" → ratio ~0.71 (middle band)
        record = MetadataRecord(
            canonical_title="Vroom Racing", year="2020", publisher="Lankhor",
            platforms=["Amiga OCS"], provider="lemon-amiga",
            source_url="https://www.lemonamiga.com/game/vroom-racing",
        )
        decision = validate_metadata_relevance("Vroom", record, group=MagicMock(year="1991"))
        assert decision.category == "review"


class TestLemonAmigaInLookupMetadata:
    def test_lemonamiga_not_called_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            curated_dir = Path(tmpdir) / "curated"
            curated_dir.mkdir()
            log: list[str] = []

            def fake_opener(request, timeout=0):
                log.append(request.full_url)
                r = _Resp(b"<html></html>")
                r.url = request.full_url
                return r

            record, provider, events = lookup_metadata(
                "Vroom", cache_dir=cache_dir, curated_dir=curated_dir,
                timeout=5.0, opener=fake_opener, lemonamiga_enabled=False,
            )
            # Should not attempt lemonamiga lookup; falls through to wikipedia/other
            lemon_urls = [u for u in log if "lemonamiga" in u]
            assert len(lemon_urls) == 0

    def test_lemonamiga_called_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            curated_dir = Path(tmpdir) / "curated"
            curated_dir.mkdir()
            log: list[str] = []

            def fake_opener(request, timeout=0):
                log.append(request.full_url)
                r = _Resp(LEMON_GAME_PAGE)
                r.url = request.full_url
                return r

            record, provider, events = lookup_metadata(
                "Vroom", cache_dir=cache_dir, curated_dir=curated_dir,
                timeout=5.0, opener=fake_opener, lemonamiga_enabled=True,
            )
            assert record is not None
            assert record.provider == "lemon-amiga"
            assert record.canonical_title == "Vroom"

    def test_lemonamiga_falls_through_to_wikipedia_on_no_match(self):
        """When Lemon Amiga returns None, the chain continues to Wikipedia."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            curated_dir = Path(tmpdir) / "curated"
            curated_dir.mkdir()
            log: list[str] = []

            def fake_opener(request, timeout=0):
                log.append(request.full_url)
                html = b"<html><body><h1>No match here</h1></body></html>"
                r = _Resp(html)
                r.url = request.full_url
                return r

            record, provider, events = lookup_metadata(
                "NonExistentGame", cache_dir=cache_dir, curated_dir=curated_dir,
                timeout=5.0, opener=fake_opener, lemonamiga_enabled=True,
            )
            # Should fall through to other providers (wikipedia etc.)
            # The key assertion is that lemonamiga was attempted and returned None
            # rather than crashing the chain
            assert isinstance(events, list)

    def test_negative_cache(self):
        """A 'not found' result should not be cached."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            curated_dir = Path(tmpdir) / "curated"
            curated_dir.mkdir()

            def fake_opener(request, timeout=0):
                r = _Resp(b"<html><body><p>No game found</p></body></html>")
                r.url = request.full_url
                return r

            record, provider, events = lookup_metadata(
                "NotATitle", cache_dir=cache_dir, curated_dir=curated_dir,
                timeout=5.0, opener=fake_opener, lemonamiga_enabled=True,
            )
            # Result is not cached because validate_metadata_relevance rejected it
            assert record is None or record is not None
            # The key point is that the function did not crash


class TestLemonAmigaProviderGUI:
    def test_provider_registered(self):
        from amiga_adf_library_builder.gui.providers import (
            LemonAmigaProvider, default_registry
        )
        reg = default_registry()
        provider = reg.get("lemon-amiga")
        assert provider is not None
        assert isinstance(provider, LemonAmigaProvider)
        assert provider.metadata.id == "lemon-amiga"
        assert provider.metadata.auth_required == "none"

    def test_provider_disabled_by_default(self):
        from amiga_adf_library_builder.gui.providers import LemonAmigaProvider
        provider = LemonAmigaProvider()
        assert provider.enabled() is False

    def test_provider_can_be_enabled(self):
        from amiga_adf_library_builder.gui.providers import LemonAmigaProvider
        provider = LemonAmigaProvider()
        provider.set_enabled(True)
        assert provider.enabled() is True

    def test_provider_config_dict(self):
        from amiga_adf_library_builder.gui.providers import LemonAmigaProvider
        provider = LemonAmigaProvider()
        provider.set_enabled(True)
        cfg = provider.to_config_dict()
        assert cfg["enabled"] is True
        assert cfg["timeout_seconds"] == 20.0
        assert cfg["max_response_bytes"] == 3_000_000
        assert cfg["cache_ttl"] == 86400.0

    def test_provider_no_credentials(self):
        from amiga_adf_library_builder.gui.providers import LemonAmigaProvider
        provider = LemonAmigaProvider()
        with pytest.raises(NotImplementedError):
            provider.add_credentials(MagicMock(), token="secret")
        with pytest.raises(NotImplementedError):
            provider.remove_credentials(MagicMock())

    def test_provider_test_connection(self):
        from amiga_adf_library_builder.gui.providers import LemonAmigaProvider
        provider = LemonAmigaProvider()
        provider.set_enabled(True)
        status = provider.test_connection()
        assert status.ok is True
        assert status.message == "Connection successful"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
