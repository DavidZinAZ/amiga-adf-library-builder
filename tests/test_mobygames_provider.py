"""Synthetic tests for the optional MobyGames metadata/artwork provider (GH-1).

Hard constraints (mirrors tests/test_local_media_provider.py and
tests/test_local_media_security_adversarial.py):

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
from pathlib import Path

import pytest

from amiga_adf_library_builder import local_media as lm
from amiga_adf_library_builder.manual_approvals import validate_source_url
from amiga_adf_library_builder.metadata import lookup_metadata, mobygames_lookup


# --- fake HTTP ----------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return getattr(self, "url", "")


def _json_opener(payload, log: list[str]):
    """Fake opener returning a fixed JSON payload; records every fetched URL."""
    def opener(request, timeout=0):
        log.append(request.full_url)
        return _Resp(json.dumps(payload).encode())
    return opener


def _raise_opener(log: list[str], msg="synthetic provider failure"):
    def opener(request, timeout=0):
        log.append(request.full_url)
        raise RuntimeError(msg)
    return opener


def _wiki_opener(log: list[str]):
    """Fake opener answering the Wikipedia search API call (fallback provider).

    Echoes the requested title back (parsed from the gsrsearch query) so the
    shared relevance validator accepts the Wikipedia fallback candidate —
    matching the current main lookup_metadata contract where every provider
    (including the unkeyed Wikipedia fallback) is relevance-gated.
    """
    import re as _re
    import urllib.parse as _up

    def opener(request, timeout=0):
        log.append(request.full_url)
        qs = _up.urlparse(request.full_url).query
        params = _up.parse_qs(qs)
        raw = (params.get("gsrsearch") or [""])[0]
        # gsrsearch looks like: '"Star Voyage" Amiga video game'
        m = _re.search(r'"([^"]+)"', raw)
        title = m.group(1) if m else "Wikipedia Fallback Game"
        payload = {
            "query": {"pages": [{
                "pageid": 777,
                "title": title,
                "extract": f"{title} is a strategy video game released for the Amiga.",
                "fullurl": f"https://en.wikipedia.org/wiki/{_up.quote(title)}",
            }]}
        }
        return _json_opener(payload, log)(request, timeout)

    return opener


# --- synthetic MobyGames fixtures ----------------------------------------------

def _amiga_game(game_id=1111, title="Star Voyage", **overrides):
    game = {
        "game_id": game_id,
        "title": title,
        "moby_url": f"https://www.mobygames.com/game/{game_id}/star-voyage",
        "description": "<p>Classic <b>action-adventure</b> title.</p>",
        "platforms": [
            {"platform_name": "Amiga", "first_release_date": "1987-06-01"},
            {"platform_name": "Atari ST", "first_release_date": "1987-09-15"},
        ],
        "genres": [{"genre_name": "Action"}, {"genre_name": "Adventure"}],
        "sample_cover": {
            "image": "https://images.mobygames.com/shots/covers/star-voyage-front.jpg",
            "platforms": ["Amiga", "Atari ST"],
        },
        "sample_screenshots": [
            {"image": "https://images.mobygames.com/shots/1/star-voyage-1.jpg", "platform": "Amiga"},
        ],
    }
    game.update(overrides)
    return game


def _pc_only_game(game_id=2222, title="Star Voyage PC"):
    return {
        "game_id": game_id,
        "title": title,
        "moby_url": f"https://www.mobygames.com/game/{game_id}/star-voyage-pc",
        "description": "PC port.",
        "platforms": [{"platform_name": "PC (Windows)", "first_release_date": "1990-01-01"}],
        "genres": [],
        "sample_cover": {"image": "https://images.mobygames.com/shots/covers/pc.jpg", "platforms": ["PC"]},
        "sample_screenshots": [],
    }


MOBY_URL_PREFIX = "https://api.mobygames.com/v1/games?"


def _env_key(monkeypatch, value="synthetic-mobygames-test-key", env="MOBYGAMES_API_KEY"):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    if value is None:
        monkeypatch.delenv(env, raising=False)
    else:
        monkeypatch.setenv(env, value)


# --- 1. matching ---------------------------------------------------------------

def test_matching_populates_record_deterministically(monkeypatch, tmp_path):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.setenv("MOBYGAMES_API_KEY", "synthetic-mobygames-test-key")
    log: list[str] = []
    payload = {"games": [_pc_only_game(), _amiga_game()]}
    record = mobygames_lookup(
        "Star Voyage", api_key="synthetic-mobygames-test-key", opener=_json_opener(payload, log),
    )
    assert record is not None
    assert record.provider == "mobygames"
    assert record.provider_id == "1111"
    assert record.canonical_title == "Star Voyage"
    assert record.year == "1987"
    assert record.genres == ["Action", "Adventure"]
    assert record.platforms == ["Amiga"]  # Amiga platforms only, non-Amiga dropped
    assert record.confidence >= 0.60
    assert record.query == "Star Voyage"
    assert record.retrieved_at
    # Only the single API endpoint is ever fetched.
    assert len(log) == 1
    assert log[0].startswith(MOBY_URL_PREFIX)
    assert "api_key=synthetic-mobygames-test-key" in log[0]


def test_matching_prefers_amiga_specific_cover(monkeypatch):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    payload = {"games": [_amiga_game()]}
    record = mobygames_lookup("Star Voyage", api_key="k", opener=_json_opener(payload, []))
    assert record is not None
    assert record.artwork_url == "https://images.mobygames.com/shots/covers/star-voyage-front.jpg"
    assert record.artwork_provider == "mobygames"
    assert record.artwork_source_url == "https://www.mobygames.com/game/1111/star-voyage"


def test_non_amiga_only_result_is_filtered(monkeypatch):
    payload = {"games": [_pc_only_game()]}
    record = mobygames_lookup("Star Voyage PC", api_key="k", opener=_json_opener(payload, []))
    assert record is None


def test_ambiguous_low_similarity_match_is_rejected(monkeypatch):
    # "Zor" vs "Zorbac the Great": similarity well below the 0.60 floor ->
    # rejected rather than silently chosen.
    payload = {"games": [_amiga_game(title="Zorbac the Great", game_id=3333)]}
    record = mobygames_lookup("Zor", api_key="k", opener=_json_opener(payload, []))
    assert record is None


# --- 2. provider failure (non-fatal) -------------------------------------------

def test_mobygames_failure_falls_through_to_wikipedia(monkeypatch, tmp_path):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.setenv("MOBYGAMES_API_KEY", "synthetic-mobygames-test-key")
    log: list[str] = []

    def opener(request, timeout=0):
        log.append(request.full_url)
        if request.full_url.startswith(MOBY_URL_PREFIX):
            raise RuntimeError("synthetic provider failure")
        return _Resp(json.dumps({
            "query": {"pages": [{
                "pageid": 777, "title": "Wikipedia Fallback Game",
                "extract": "A strategy video game released for the Amiga.",
                "fullurl": "https://en.wikipedia.org/wiki/Wikipedia_Fallback_Game",
            }]}
        }).encode())

    record, provider, _ = lookup_metadata(
        "Wikipedia Fallback Game", cache_dir=tmp_path / "cache",
        curated_dir=tmp_path / "curated", opener=opener,
        mobygames_enabled=True,
    )
    assert record is not None, "provider failure must not break the base workflow"
    assert provider == "wikipedia"
    assert any(u.startswith(MOBY_URL_PREFIX) for u in log)
    assert any("wikipedia" in u for u in log)


def test_mobygames_lookup_exception_does_not_escape_lookup_metadata(monkeypatch, tmp_path):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.setenv("MOBYGAMES_API_KEY", "synthetic-mobygames-test-key")
    # Every provider fails (mobygames raises, wikipedia raises): lookup must
    # degrade to not-found, never raise.
    record, provider, _ = lookup_metadata(
        "Anything", cache_dir=tmp_path / "cache", curated_dir=tmp_path / "curated",
        opener=_raise_opener([]), mobygames_enabled=True,
    )
    assert record is None
    assert provider == "not-found"


# --- 3. caching -----------------------------------------------------------------

def test_second_lookup_hits_cache_without_network(monkeypatch, tmp_path):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.setenv("MOBYGAMES_API_KEY", "synthetic-mobygames-test-key")
    cache = tmp_path / "cache"
    curated = tmp_path / "curated"
    log: list[str] = []
    payload = {"games": [_amiga_game()]}

    first, provider1, _ = lookup_metadata(
        "Star Voyage", cache_dir=cache, curated_dir=curated,
        opener=_json_opener(payload, log), mobygames_enabled=True,
    )
    assert first is not None
    assert provider1 == "mobygames"
    assert first.provider == "mobygames"
    assert any(p.is_file() for p in cache.glob("*.json")), "record must be saved to cache"
    calls_after_first = len([u for u in log if u.startswith(MOBY_URL_PREFIX)])
    assert calls_after_first == 1

    second, provider2, _ = lookup_metadata(
        "Star Voyage", cache_dir=cache, curated_dir=curated,
        opener=_raise_opener([]), mobygames_enabled=True,  # network is now impossible
    )
    assert provider2 == "cache"
    assert second is not None
    assert second.provider == "mobygames"
    assert second.canonical_title == first.canonical_title
    assert second.source_url == first.source_url
    assert second.artwork_url == first.artwork_url
    assert len([u for u in log if u.startswith(MOBY_URL_PREFIX)]) == calls_after_first, (
        "cached lookup must not re-fetch the provider"
    )


# --- 4. provenance ---------------------------------------------------------------

def test_provenance_fields_recorded_on_record(monkeypatch):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    payload = {"games": [_amiga_game()]}
    record = mobygames_lookup("Star Voyage", api_key="k", opener=_json_opener(payload, []))
    assert record is not None
    assert record.provider == "mobygames"
    assert record.provider_id == "1111"
    assert record.source_url == "https://www.mobygames.com/game/1111/star-voyage"
    assert record.artwork_source_url == record.source_url
    assert record.artwork_provider == "mobygames"
    assert record.query == "Star Voyage"


# --- 5. security-sensitive URL handling -------------------------------------------

def test_ssrf_guard_blocks_blocked_address_ranges():
    for url in (
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/admin",
        "http://[::1]/",
        "http://[fd00::1]/",
    ):
        ok, reason = validate_source_url(url)
        assert ok is False, f"{url} must be blocked: {reason}"


def test_artwork_allowlist_only_permits_mobygames_hosts():
    ok, _ = validate_source_url("https://images.mobygames.com/shots/covers/x.jpg")
    assert ok is True
    ok, _ = validate_source_url("https://www.mobygames.com/game/1111")
    assert ok is True
    ok, _ = validate_source_url("https://cdn.mobygames.com/shots/y.jpg")
    assert ok is True, "subdomain of allowlisted mobygames.com"
    for url in (
        "https://evil.example.com/img.jpg",
        "https://mobygames.com.evil.example/img.jpg",
        "https://rawg.io/games/x",  # allowlisted for RAWG, not a mobygames host
    ):
        ok, reason = validate_source_url(url)
        assert ok is False or "rawg.io" in url, f"{url} should not pass as mobygames artwork: {reason}"


def test_non_http_schemes_refused():
    for url in ("file:///etc/passwd", "ftp://images.mobygames.com/x.jpg", "gopher://x"):
        ok, reason = validate_source_url(url)
        assert ok is False, f"{url} must be refused: {reason}"


def test_invalid_artwork_url_is_dropped_not_returned(monkeypatch):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    payload = {"games": [_amiga_game(
        sample_cover={"image": "https://evil.example.com/cover.jpg", "platforms": ["Amiga"]},
        sample_screenshots=[{"image": "https://images.mobygames.com/shots/1/ok.jpg", "platform": "Amiga"}],
    )]}
    record = mobygames_lookup("Star Voyage", api_key="k", opener=_json_opener(payload, []))
    assert record is not None
    # Bad cover dropped; allowlisted screenshot used as fallback.
    assert record.artwork_url == "https://images.mobygames.com/shots/1/ok.jpg"
    assert record.artwork_provider == "mobygames"


def test_all_invalid_artwork_yields_record_without_artwork(monkeypatch):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    payload = {"games": [_amiga_game(
        sample_cover={"image": "https://evil.example.com/cover.jpg", "platforms": ["Amiga"]},
        sample_screenshots=[],
    )]}
    record = mobygames_lookup("Star Voyage", api_key="k", opener=_json_opener(payload, []))
    assert record is not None
    assert record.artwork_url == ""
    assert record.artwork_provider == ""
    assert record.artwork_source_url == ""
    # Metadata is still usable without artwork.
    assert record.provider == "mobygames"
    assert record.provider_id == "1111"


def test_lookup_only_ever_fetches_the_api_endpoint(monkeypatch, tmp_path):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.setenv("MOBYGAMES_API_KEY", "synthetic-mobygames-test-key")
    log: list[str] = []
    payload = {"games": [_amiga_game()]}
    lookup_metadata(
        "Star Voyage", cache_dir=tmp_path / "cache", curated_dir=tmp_path / "curated",
        opener=_json_opener(payload, log), mobygames_enabled=True,
    )
    assert log, "expected at least one fetch"
    assert all(u.startswith(MOBY_URL_PREFIX) for u in log), (
        f"provider must only fetch its API endpoint, got: {log}"
    )


# --- 6. disabled / offline / config -----------------------------------------------

def test_enabled_but_keyless_is_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    _env_key(monkeypatch, value=None)
    log: list[str] = []
    record, provider, _ = lookup_metadata(
        "Star Voyage", cache_dir=tmp_path / "cache", curated_dir=tmp_path / "curated",
        opener=_wiki_opener(log), mobygames_enabled=True,
    )
    assert record is not None
    assert provider == "wikipedia"
    assert not any(u.startswith(MOBY_URL_PREFIX) for u in log), (
        "keyless provider must be a no-op"
    )


def test_keyed_but_disabled_is_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    _env_key(monkeypatch, value="synthetic-mobygames-test-key")
    log: list[str] = []
    record, provider, _ = lookup_metadata(
        "Star Voyage", cache_dir=tmp_path / "cache", curated_dir=tmp_path / "curated",
        opener=_wiki_opener(log),  # mobygames_enabled defaults to False
    )
    assert record is not None
    assert provider == "wikipedia"
    assert not any(u.startswith(MOBY_URL_PREFIX) for u in log), (
        "disabled provider must be a no-op even when a key is present"
    )


def test_default_lookup_metadata_signature_is_unchanged(monkeypatch, tmp_path):
    """Base app call path (no mobygames args) behaves exactly as before."""
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    _env_key(monkeypatch, value="synthetic-mobygames-test-key")
    log: list[str] = []
    record, provider, _ = lookup_metadata(
        "Star Voyage", cache_dir=tmp_path / "cache", curated_dir=tmp_path / "curated",
        opener=_wiki_opener(log),
    )
    assert provider == "wikipedia"
    assert not any(u.startswith(MOBY_URL_PREFIX) for u in log)


def test_custom_api_key_env_is_honored(monkeypatch, tmp_path):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    _env_key(monkeypatch, value=None)
    monkeypatch.setenv("CUSTOM_MG_KEY", "synthetic-custom-key")
    log: list[str] = []
    payload = {"games": [_amiga_game()]}
    record, provider, _ = lookup_metadata(
        "Star Voyage", cache_dir=tmp_path / "cache", curated_dir=tmp_path / "curated",
        opener=_json_opener(payload, log), mobygames_enabled=True,
        mobygames_api_key_env="CUSTOM_MG_KEY",
    )
    assert provider == "mobygames"
    assert "api_key=synthetic-custom-key" in log[0]


def test_config_table_disabled_by_default(tmp_path):
    assert lm.MobyGamesConfig.from_dict(None).enabled is False
    assert lm.MobyGamesConfig.from_dict({}).enabled is False
    assert lm.load_mobygames_config(tmp_path / "missing.toml").enabled is False
    cfg = lm.MobyGamesConfig.from_dict({"enabled": True, "api_key_env": "CUSTOM_MG_KEY",
                                        "preferred_image_types": ["cover"], "timeout": "15"})
    assert cfg.enabled is True
    assert cfg.api_key_env == "CUSTOM_MG_KEY"
    assert cfg.preferred_image_types == ("cover",)
    assert cfg.timeout == 15.0


def test_config_roundtrip_from_toml(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mobygames]\n'
        'enabled = true\n'
        'api_key_env = "MOBYGAMES_API_KEY"\n'
        'preferred_image_types = ["cover", "screenshot"]\n'
        'timeout = 25.0\n',
        encoding="utf-8",
    )
    cfg = lm.load_mobygames_config(config)
    assert cfg.enabled is True
    assert cfg.api_key_env == "MOBYGAMES_API_KEY"
    assert cfg.preferred_image_types == ("cover", "screenshot")
    assert cfg.timeout == 25.0


def test_example_config_ships_disabled(tmp_path):
    """The shipped config template must ship the provider DISABLED, keyless."""
    import amiga_adf_library_builder  # noqa: F401  (package importable)
    repo_root = Path(amiga_adf_library_builder.__file__).resolve().parent.parent.parent
    example = repo_root / "config" / "example.toml"
    if not example.is_file():
        pytest.skip("config/example.toml not present in this checkout layout")
    cfg = lm.load_mobygames_config(example)
    assert cfg.enabled is False
    assert cfg.api_key_env == "MOBYGAMES_API_KEY"
    text = example.read_text(encoding="utf-8")
    assert "MOBYGAMES_API_KEY = " not in text, "no key value may be committed in config"
