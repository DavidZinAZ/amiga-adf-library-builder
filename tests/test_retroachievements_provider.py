"""Unit tests for the optional RetroAchievements provider (GH-13).

Offline, synthetic, NO live network. A fake opener returns canned bytes keyed
by the URL, exactly like ``tests/test_igdb_provider.py``. The provider is
hash-first: exact MD5 of the group's first non-special disk against the RA
game list's public ``Hashes`` array. An exact match is auto-accepted (conf 1.0);
a non-matching hash is a clean miss (never a fuzzy guess).

Coverage:
  * config parsing (enabled/disabled, numeric bounds, console name)
  * construction gating (disabled / missing API key -> RaDisabled)
  * exact MD5 hit -> found=True, conf 1.0, EXACT_HASH, provider_id, metadata
  * non-matching hash -> found=False, NONE (clean miss, no review)
  * no disk / unreadable disk -> NO_HASH clean miss
  * offline, no warm cache -> CONSOLE_MISSING, no network, no crash
  * online, console list missing the target console -> CONSOLE_MISSING
  * online, game list fetch fails -> NONE clean miss, no crash
  * cache: a warm cache serves the game list offline (no opener call)
  * ``_icon_url`` joins site-relative icons to the origin; absolute pass through
  * ``_first_disk_path`` accepts both list and dict scan forms
  * the pipeline loader: ``load_retroachievements_config`` mirrors siblings
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from amiga_adf_library_builder import retroachievements as ra
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup, ScanRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONSOLE_JSON = json.dumps(
    [{"ID": 99, "Name": "amiga"}, {"ID": 1, "Name": "atari 2600"}]
).encode("utf-8")

GAME_HASH = "d41d8cd98f00b204e9800998ecf8427e"  # md5 of empty file
OTHER_HASH = "00000000000000000000000000000001"

GAME_LIST_HIT = json.dumps(
    [
        {"ID": 7, "Title": "Test Game", "ImageIcon": "/Badge/7.png",
         "Hashes": [GAME_HASH, OTHER_HASH]},
        {"ID": 8, "Title": "Other Game", "Hashes": []},
    ]
).encode("utf-8")

GAME_LIST_NO_MATCH = json.dumps(
    [
        {"ID": 7, "Title": "Test Game", "ImageIcon": "/Badge/7.png",
         "Hashes": [OTHER_HASH]},
    ]
).encode("utf-8")


def _cfg(**over) -> ra.RaConfig:
    base = {"enabled": True, "cache_ttl": 3600, "negative_ttl": 3600}
    base.update(over)
    return ra.RaConfig.from_dict(base)


def _provider(cache_dir, *, opener, **cfg_over):
    cfg = _cfg(**cfg_over)
    return ra.RetroAchievementsProvider(cfg, cache_dir, "test-key", opener=opener)


def _scan(path, filename, size=0):
    return ScanRecord(path=path, filename=filename, size=size,
                      sha256="", scanned_at="2026-01-01T00:00:00Z")


def _group(title, release_key, disks, ext="adf"):
    return ReleaseGroup(
        release_key=release_key, title=title, edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext=ext,
        disks=disks,
    )


def _make_group_and_scan(tmp_path: Path, *, content: bytes = b"",
                         disk_name: str = "disk1.adf"):
    """Create a real group + on-disk scan file and return (group, scan)."""
    disk = tmp_path / disk_name
    disk.write_bytes(content)
    scan = _scan(disk, disk_name, len(content))
    record = ParsedRecord(source_filename=disk_name, ext="adf")
    group = _group("Test Game", "test-game", [record])
    return group, scan


def _opener_by_url(responses: dict, *, fail=False):
    """Return an opener returning canned bytes per URL substring, else fail."""
    def opener(url, *, timeout):  # noqa: ARG001 - signature parity with default
        if fail:
            raise RuntimeError("forced outage")
        for key, val in responses.items():
            if key in url:
                return val
        raise RuntimeError(f"unexpected url {url}")
    return opener


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_config_disabled_by_default():
    assert ra.RaConfig.from_dict(None).enabled is False
    assert ra.RaConfig.from_dict({}).enabled is False


def test_config_enabled_round_trip():
    cfg = ra.RaConfig.from_dict({"enabled": True, "console_name": "Amiga 500"})
    assert cfg.enabled is True
    assert cfg.console_name == "amiga 500"
    assert cfg.base_url == ra.DEFAULT_BASE_URL


def test_config_numeric_bounds():
    cfg = ra.RaConfig.from_dict({
        "enabled": True,
        "timeout_seconds": "999999",
        "max_response_bytes": -1,
        "cache_ttl": -5,
        "negative_ttl": -5,
        "rate_limit_backoff_seconds": 99,
        "confidence_threshold": 3.0,
    })
    assert 0 < cfg.timeout_seconds <= ra._MAX_TIMEOUT_SECONDS
    assert 0 < cfg.max_response_bytes <= ra._MAX_RESPONSE_BYTES
    assert cfg.cache_ttl == 0.0
    assert cfg.negative_ttl == 0.0
    assert cfg.rate_limit_backoff_seconds == 1.0
    assert cfg.confidence_threshold == 1.0


# ---------------------------------------------------------------------------
# Construction gating
# ---------------------------------------------------------------------------

def test_construction_disabled_raises(tmp_path):
    with pytest.raises(ra.RaDisabled):
        ra.RetroAchievementsProvider(_cfg(enabled=False), tmp_path, "key")


def test_construction_missing_key_raises(tmp_path):
    with pytest.raises(ra.RaDisabled):
        ra.RetroAchievementsProvider(_cfg(), tmp_path, "")
    with pytest.raises(ra.RaDisabled):
        ra.RetroAchievementsProvider(_cfg(), tmp_path, "   ")


def test_construction_enabled_ok(tmp_path):
    p = _provider(tmp_path, opener=_opener_by_url({}))
    assert p.discover() == 1


# ---------------------------------------------------------------------------
# Resolve: exact MD5 hit
# ---------------------------------------------------------------------------

def test_resolve_exact_md5_hit(tmp_path):
    group, scan = _make_group_and_scan(tmp_path)
    md5 = hashlib.md5(b"").hexdigest()
    assert md5 == GAME_HASH
    opener = _opener_by_url({
        "API_GetConsoleIDs": CONSOLE_JSON,
        "API_GetGameList": GAME_LIST_HIT,
    })
    p = _provider(tmp_path, opener=opener)
    result = p.resolve(group, scans={scan.filename: scan}, online=True)

    assert result.found is True
    assert result.match_method is ra.RaMatchMethod.EXACT_HASH
    assert result.confidence == 1.0
    assert result.provider_id == "7"
    assert result.needs_manual_review is False
    assert result.metadata is not None
    assert result.metadata["canonical_title"] == "Test Game"
    assert result.metadata["provider_id"] == "7"
    assert result.metadata["provider"] == "retroachievements"
    assert result.metadata["artwork_urls"] == [
        f"{ra.DEFAULT_BASE_URL}/Badge/7.png"
    ]
    assert result.external_ids == {"retroachievements_id": "7"}


def test_resolve_hit_case_insensitive_hash(tmp_path):
    group, scan = _make_group_and_scan(tmp_path)
    game_list = json.dumps(
        [{"ID": 7, "Title": "Test Game", "Hashes": [GAME_HASH.upper()]}]
    ).encode("utf-8")
    opener = _opener_by_url({
        "API_GetConsoleIDs": CONSOLE_JSON,
        "API_GetGameList": game_list,
    })
    p = _provider(tmp_path, opener=opener)
    result = p.resolve(group, scans=[scan], online=True)  # list form
    assert result.found is True
    assert result.match_method is ra.RaMatchMethod.EXACT_HASH


# ---------------------------------------------------------------------------
# Resolve: clean misses (never a fuzzy guess, never review)
# ---------------------------------------------------------------------------

def test_resolve_non_matching_hash_is_clean_miss(tmp_path):
    group, scan = _make_group_and_scan(tmp_path)
    opener = _opener_by_url({
        "API_GetConsoleIDs": CONSOLE_JSON,
        "API_GetGameList": GAME_LIST_NO_MATCH,
    })
    p = _provider(tmp_path, opener=opener)
    result = p.resolve(group, scans={scan.filename: scan}, online=True)
    assert result.found is False
    assert result.match_method is ra.RaMatchMethod.NONE
    assert result.needs_manual_review is False
    assert result.confidence == 0.0


def test_resolve_no_disk_is_no_hash(tmp_path):
    group = _group("No Disk", "no-disk", [])
    opener = _opener_by_url({"API_GetConsoleIDs": CONSOLE_JSON})
    p = _provider(tmp_path, opener=opener)
    result = p.resolve(group, scans={}, online=True)
    assert result.found is False
    assert result.match_method is ra.RaMatchMethod.NO_HASH
    assert result.needs_manual_review is False


def test_resolve_unreadable_disk_is_no_hash(tmp_path):
    group, scan = _make_group_and_scan(tmp_path)
    scan.path = tmp_path / "does-not-exist.adf"  # point at a missing file
    opener = _opener_by_url({"API_GetConsoleIDs": CONSOLE_JSON})
    p = _provider(tmp_path, opener=opener)
    result = p.resolve(group, scans={scan.filename: scan}, online=True)
    assert result.found is False
    assert result.match_method is ra.RaMatchMethod.NO_HASH


# ---------------------------------------------------------------------------
# Offline / outage: no network, clean miss, no crash
# ---------------------------------------------------------------------------

def test_resolve_offline_no_warm_cache_is_console_missing(tmp_path):
    group, scan = _make_group_and_scan(tmp_path)
    called = {"n": 0}

    def opener(url, *, timeout):  # any call proves a network attempt
        called["n"] += 1
        raise AssertionError("offline resolve must not touch the network")

    p = _provider(tmp_path, opener=opener)
    result = p.resolve(group, scans={scan.filename: scan}, online=False)
    assert result.found is False
    assert result.match_method is ra.RaMatchMethod.CONSOLE_MISSING
    assert result.needs_manual_review is False
    assert called["n"] == 0


def test_resolve_online_console_not_in_list(tmp_path):
    group, scan = _make_group_and_scan(tmp_path)
    opener = _opener_by_url({
        "API_GetConsoleIDs": json.dumps([{"ID": 1, "Name": "atari 2600"}]).encode(),
    })
    p = _provider(tmp_path, opener=opener)
    result = p.resolve(group, scans={scan.filename: scan}, online=True)
    assert result.found is False
    assert result.match_method is ra.RaMatchMethod.CONSOLE_MISSING


def test_resolve_online_game_list_outage_is_none(tmp_path):
    group, scan = _make_group_and_scan(tmp_path)
    opener = _opener_by_url(
        {"API_GetConsoleIDs": CONSOLE_JSON}, fail=True
    )

    def mixed(url, *, timeout):
        if "API_GetConsoleIDs" in url:
            return CONSOLE_JSON
        raise RuntimeError("game list outage")

    p = _provider(tmp_path, opener=mixed)
    result = p.resolve(group, scans={scan.filename: scan}, online=True)
    assert result.found is False
    assert result.match_method is ra.RaMatchMethod.NONE
    assert result.needs_manual_review is False


# ---------------------------------------------------------------------------
# Cache: a warm cache serves the game list offline (no opener call)
# ---------------------------------------------------------------------------

def test_cache_warm_serves_offline(tmp_path):
    # 1) Warm the cache online (opener served).
    group, scan = _make_group_and_scan(tmp_path)
    opener = _opener_by_url({
        "API_GetConsoleIDs": CONSOLE_JSON,
        "API_GetGameList": GAME_LIST_HIT,
    })
    p = _provider(tmp_path, opener=opener)
    assert p.resolve(group, scans={scan.filename: scan}, online=True).found is True

    # 2) Offline resolve with an opener that MUST NOT be called.
    def no_network(url, *, timeout):
        raise AssertionError("warm cache must be served offline, no network")

    p2 = _provider(tmp_path, opener=no_network)
    result = p2.resolve(group, scans={scan.filename: scan}, online=False)
    assert result.found is True
    assert result.match_method is ra.RaMatchMethod.EXACT_HASH
    assert result.provider_id == "7"


# ---------------------------------------------------------------------------
# _icon_url
# ---------------------------------------------------------------------------

def test_icon_url_joins_and_passthrough():
    p = _provider(Path("/tmp/ra-never"), opener=_opener_by_url({}))
    base = p.config.base_url
    assert p._icon_url("/Badge/7.png") == f"{base}/Badge/7.png"
    assert p._icon_url("Badge/7.png") == f"{base}/Badge/7.png"
    assert p._icon_url("https://cdn.example/x.png") == "https://cdn.example/x.png"
    assert p._icon_url("") == ""


# ---------------------------------------------------------------------------
# _first_disk_path: list and dict scan forms
# ---------------------------------------------------------------------------

def test_first_disk_path_list_and_dict(tmp_path):
    group, scan = _make_group_and_scan(tmp_path)
    assert ra._first_disk_path(group, [scan]) == scan.path
    assert ra._first_disk_path(group, {scan.filename: scan}) == scan.path
    # No matching scan -> None (clean miss, no guess).
    assert ra._first_disk_path(group, [ScanRecord(
        path=tmp_path / "other.adf", filename="other.adf", size=0,
        sha256="", scanned_at="2026-01-01T00:00:00Z")]) is None
    assert ra._first_disk_path(group, {}) is None
    assert ra._first_disk_path(group, None) is None


# ---------------------------------------------------------------------------
# Pipeline loader parity with siblings
# ---------------------------------------------------------------------------

def test_loader_missing_explicit_path_raises_like_siblings():
    from amiga_adf_library_builder import paths as paths_mod
    for name in ("load_screenscraper_config", "load_igdb_config",
                 "load_retroachievements_config"):
        fn = getattr(paths_mod, name)
        with pytest.raises(Exception) as ei:
            fn("/nonexistent/config.toml")
        assert type(ei.value).__name__ == "PathConfigError"


def test_loader_present_returns_table(tmp_path):
    from amiga_adf_library_builder import paths as paths_mod
    cfg = tmp_path / "cfg.toml"
    cfg.write_text("[retroachievements]\nenabled = true\nconsole_name = \"Amiga 500\"\n")
    got = paths_mod.load_retroachievements_config(str(cfg))
    assert got == {"enabled": True, "console_name": "Amiga 500"}
    rc = ra.RaConfig.from_dict(got)
    assert rc.enabled is True and rc.console_name == "amiga 500"
