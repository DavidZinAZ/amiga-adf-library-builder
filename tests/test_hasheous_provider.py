"""Tests for the OPTIONAL Hasheous ROM-hash identity resolver provider.

Mirrors ``tests/test_playmatch_provider.py`` in style and coverage. Covers
every acceptance criterion from issue #12 against the REAL Hasheous contract:

* disabled by default; ``from_dict(None)`` -> enabled=False
* hash-first: exact-hash match OUTRANKS everything; determinism
* ambiguous/conflict -> needs_manual_review (never silent override)
* outage/timeout/oversize/429 -> non-fatal NONE (found=False), pipeline continues
* SSRF guard active (private host refused); privacy preserved
  (no private filename/path in cache or request)
* provider-ID capture + external_ids capture for downstream
* negative-lookup caching (empty HashLookup -> not found, public-signal only)
* OFFLINE guarantee: socket.socket monkeypatched to raise proves no real fetch
* HasheousResult exposes the same public fields as PlaymatchResult + external_ids
* ONLY the documented lookup method is used (GET /Lookup/ByHash/sha256/{sha256});
  there is NO REST title endpoint in the real API, so a group with no hash
  signal deterministically misses (no private data transmitted).

All network contact is synthetic/mock: an injected ``opener`` returns synthetic
JSON shaped like the real ``Classes.HashLookup`` response, and ``resolve=False``
is passed so ``guard_url`` never performs DNS.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from amiga_adf_library_builder import hasheous as hs
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup, ScanRecord
from amiga_adf_library_builder import playmatch as pm
from amiga_adf_library_builder.paths import load_hasheous_config


# --- helpers ----------------------------------------------------------------

def _make_group(title: str, *, release_key=None, sha256=None, source_filename=None):
    fn = source_filename or f"{title}.adf"
    rec = ParsedRecord(
        source_filename=fn,
        ext="adf",
        title=title,
    )
    group = ReleaseGroup(
        release_key=(release_key or title.lower()),
        title=title,
        edition=None,
        group=None,
        chipset=None,
        language=None,
        version=None,
        alt_marker=None,
        ext="adf",
        records=[rec],
        disks=[rec],
    )
    if sha256 is not None:
        group.sha256 = sha256  # type: ignore[attr-defined]
    return group


def _make_scans(group: ReleaseGroup, sha256: str) -> dict:
    rec = group.records[0]
    scan = ScanRecord(
        path=Path(f"/private/original/{rec.source_filename}"),
        filename=rec.source_filename,
        size=1234,
        sha256=sha256,
        scanned_at="2026-01-01T00:00:00+00:00",
    )
    return {rec.source_filename: scan}


def _provider(config: dict, cache: Path, *, opener=None):
    cfg = hs.HasheousConfig.from_dict(config)
    assert cfg.enabled is True
    prov = hs.HasheousProvider(cfg, cache, opener=opener, resolve=False)
    prov.discover()
    return prov


def _hash_lookup(*, id="HS-1", name=None, platform="Amiga", publisher=None,
                 metadata_matches=None, **extra):
    """Build a synthetic Classes.HashLookup response (real contract shape)."""
    return {
        "id": id,
        "name": name,
        "platform": platform,
        "publisher": publisher,
        "signatures": {},
        "metadataMatches": metadata_matches or [],
        **extra,
    }


# --- acceptance #1: disabled by default -------------------------------------

def test_disabled_by_default_from_none():
    cfg = hs.HasheousConfig.from_dict(None)
    assert cfg.enabled is False
    assert cfg.base_url == hs.DEFAULT_BASE_URL


def test_disabled_by_default_from_empty():
    cfg = hs.HasheousConfig.from_dict({})
    assert cfg.enabled is False


def test_disabled_provider_raises_on_construct():
    cfg = hs.HasheousConfig.from_dict({"enabled": False})
    with pytest.raises(hs.HasheousDisabled):
        hs.HasheousProvider(cfg, Path("/tmp/cache"))


def test_config_bounding_rejects_weakened_values():
    cfg = hs.HasheousConfig.from_dict({
        "enabled": True,
        "timeout_seconds": 9999,
        "max_response_bytes": 10**12,
        "max_concurrency": 1000,
        "confidence_threshold": 5.0,
        "rate_limit_backoff_seconds": 9999,
    })
    assert cfg.timeout_seconds == hs.DEFAULT_TIMEOUT_SECONDS
    assert cfg.max_response_bytes == hs.DEFAULT_MAX_RESPONSE_BYTES
    assert cfg.max_concurrency == hs.DEFAULT_MAX_CONCURRENCY
    assert cfg.confidence_threshold == hs.DEFAULT_CONFIDENCE_THRESHOLD
    assert cfg.rate_limit_backoff_seconds <= hs._MAX_429_BACKOFF_SECONDS


def test_result_mirrors_playmatch_public_fields():
    """HasheousResult must expose the same public fields as PlaymatchResult + extra."""
    grp = _make_group("Field Game", sha256="a" * 64)
    r = hs.HasheousResult(
        group_title=grp.title, group_release_key=grp.release_key,
    )
    # Public field parity with PlaymatchResult (excluding the extra external_ids).
    pm_r = pm.PlaymatchResult(
        group_title=grp.title, group_release_key=grp.release_key,
    )
    pm_keys = set(pm_r.to_dict().keys())
    hs_keys = set(r.to_dict().keys())
    assert pm_keys.issubset(hs_keys), hs_keys - pm_keys
    assert "external_ids" in hs_keys


# --- offline guarantee ------------------------------------------------------

def test_offline_no_real_socket(monkeypatch, tmp_path):
    """Block socket.socket; injected opener still resolves (opener is the only path)."""

    calls = []

    def _blocked_socket(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("network blocked in test")

    monkeypatch.setattr(socket, "socket", _blocked_socket)

    sha = "a" * 64
    group = _make_group("Example Game", sha256=sha)

    def opener(url, *, timeout):
        assert "/Lookup/ByHash/sha256/" in url
        return json.dumps(_hash_lookup(id="HS-XY", name="Example Game",
                                        metadata_matches=[{"source": "NoIntro",
                                                           "gameId": "NI-XY"}])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    # The real socket must never have been used.
    assert calls == []


# --- acceptance #2: hash-first outranks everything, determinism -------------

def test_exact_hash_match_resolves(tmp_path):
    sha = "b" * 64
    group = _make_group("Example Game", sha256=sha)

    def opener(url, *, timeout):
        assert url.endswith(f"/Lookup/ByHash/sha256/{sha}")
        return json.dumps(_hash_lookup(
            id="HS-HASH", name="Example Game",
            metadata_matches=[{"source": "NoIntro", "gameId": "NI-HASH"}])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    assert res.provider_id == "NI-HASH"
    assert res.confidence == 1.0


def test_determinism_same_input_same_result(tmp_path):
    sha = "c" * 64
    group = _make_group("Deterministic Game", sha256=sha)

    payload = json.dumps(_hash_lookup(id="HS-DET",
                                      metadata_matches=[{"source": "NoIntro",
                                                         "gameId": "NI-DET"}])).encode()

    def opener(url, *, timeout):
        return payload

    prov1 = _provider({"enabled": True}, tmp_path / "cache1", opener=opener)
    prov2 = _provider({"enabled": True}, tmp_path / "cache2", opener=opener)
    r1 = prov1.resolve(group, sha256=sha)
    r2 = prov2.resolve(group, sha256=sha)
    assert r1.to_dict() == r2.to_dict()


def test_hash_signal_from_scans_reuses_scanner_sha(tmp_path):
    """Provider must reuse the sha from scans (already computed), not refuse."""
    sha = "d" * 64
    group = _make_group("Scan Game", source_filename="scan game.adf")
    scans = _make_scans(group, sha)

    def opener(url, *, timeout):
        assert url.endswith(f"/Lookup/ByHash/sha256/{sha}")
        return json.dumps(_hash_lookup(id="HS-SCAN",
                                       metadata_matches=[{"source": "NoIntro",
                                                          "gameId": "NI-SCAN"}])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, scans=scans)
    assert res.found is True
    assert res.provider_id == "NI-SCAN"


def test_no_hash_signal_deterministic_miss(tmp_path):
    """No sha anywhere -> cannot query Hasheous -> deterministic NONE miss.

    The real Hasheous API has NO REST title endpoint, so the canonical title is
    never transmitted as a substitute (privacy: only the public sha256 is sent).
    """
    group = _make_group("Fallback Game")

    sent_urls = []

    def opener(url, *, timeout):
        sent_urls.append(url)
        raise AssertionError("should not be called without a hash signal")

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE
    assert res.needs_manual_review is False
    assert sent_urls == []


# --- acceptance #3: ambiguous / conflict -> manual review -------------------

def test_hash_match_without_provider_id_routes_to_review(tmp_path):
    """A HashLookup that names the game but yields no provider id is ambiguous
    (recognized-but-identity-less) -> manual review, never a silent miss."""
    sha = "e" * 64
    group = _make_group("Conflict Game", sha256=sha)

    def opener(url, *, timeout):
        # Recognized HashLookup (has a name) but no usable id / metadataMatch:
        # ambiguous -> fail-safe manual review, not a negative cache.
        return json.dumps(_hash_lookup(id="", name="Conflict Game",
                                        metadata_matches=[])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is False
    assert res.needs_manual_review is True
    assert res.match_method == hs.HasheousMatchMethod.MANUAL_REVIEW


def test_canonical_reuse_returns_cached_identity(tmp_path):
    """Canonical reuse returns the stored mapping; cross-provider disagreement
    is handled by enrich_group, not by re-detecting intra-provider conflict."""
    sha = "f" * 64
    group = _make_group("Ambiguous Game", sha256=sha)

    def opener(url, *, timeout):
        return json.dumps(_hash_lookup(id="HS-A",
                                       metadata_matches=[{"source": "NoIntro",
                                                          "gameId": "NI-A"}])).encode()

    prov = _provider({"enabled": True, "cache_ttl": 1000.0},
                     tmp_path / "cache", opener=opener)
    r1 = prov.resolve(group, sha256=sha)
    assert r1.found and r1.provider_id == "NI-A"
    # Re-resolve reuses the cache quietly (single fetch).
    r2 = prov.resolve(group, sha256=sha)
    assert r2.found
    assert r2.match_method == hs.HasheousMatchMethod.CANONICAL_REUSE
    assert r2.provider_id == "NI-A"


# --- acceptance #4: outage / timeout / oversize / 429 -> non-fatal ---------

def test_outage_non_fatal_none(tmp_path):
    group = _make_group("Outage Game", sha256="1" * 64)

    def opener(url, *, timeout):
        import urllib.error
        raise urllib.error.URLError("connection refused")

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256="1" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE
    assert res.needs_manual_review is False


def test_timeout_non_fatal_none(tmp_path):
    group = _make_group("Timeout Game", sha256="2" * 64)

    def opener(url, *, timeout):
        raise TimeoutError("timed out")

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256="2" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE


def test_oversize_non_fatal_none(tmp_path):
    group = _make_group("Oversize Game", sha256="3" * 64)

    def opener(url, *, timeout):
        return b"x" * (hs.DEFAULT_MAX_RESPONSE_BYTES + 1)

    cfg = hs.HasheousConfig.from_dict({"enabled": True, "max_response_bytes": 100})
    prov = hs.HasheousProvider(cfg, tmp_path / "cache", opener=opener, resolve=False)
    prov.discover()
    res = prov.resolve(group, sha256="3" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE


def test_malformed_json_non_fatal_none(tmp_path):
    group = _make_group("Garbage Game", sha256="4" * 64)

    def opener(url, *, timeout):
        return b"not json at all"

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256="4" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE


def test_429_rate_limited_non_fatal_none(tmp_path):
    """A 429 (HasheousRateLimited) from the opener must back off once, then
    miss without raising into the pipeline."""
    group = _make_group("Limited Game", sha256="5" * 64)

    calls = {"n": 0}

    def opener(url, *, timeout):
        calls["n"] += 1
        # First attempt is rate-limited; second attempt (after the bounded
        # backoff) is also limited -> non-fatal miss.
        raise hs.HasheousRateLimited(0.0)

    cfg = hs.HasheousConfig.from_dict({"enabled": True, "respect_rate_limit": True})
    prov = hs.HasheousProvider(cfg, tmp_path / "cache", opener=opener, resolve=False)
    prov.discover()
    res = prov.resolve(group, sha256="5" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE
    # Exactly one retry after the backoff (two total attempts).
    assert calls["n"] == 2


def test_429_with_rate_limit_disabled_misses_immediately(tmp_path):
    """When respect_rate_limit is off, a 429 is a non-fatal miss (no backoff)."""
    group = _make_group("NoBackoff Game", sha256="6" * 64)

    calls = {"n": 0}

    def opener(url, *, timeout):
        calls["n"] += 1
        raise hs.HasheousRateLimited(0.0)

    cfg = hs.HasheousConfig.from_dict({"enabled": True, "respect_rate_limit": False})
    prov = hs.HasheousProvider(cfg, tmp_path / "cache", opener=opener, resolve=False)
    prov.discover()
    res = prov.resolve(group, sha256="6" * 64)
    assert res.found is False
    assert calls["n"] == 1


# --- acceptance #5: SSRF guard + privacy ------------------------------------

def test_ssrf_guard_refuses_private_host(tmp_path):
    """A config pointing at a private host must be refused by guard_url."""
    cfg = hs.HasheousConfig.from_dict({
        "enabled": True,
        "base_url": "http://127.0.0.1:8080/v1",
    })

    prov = hs.HasheousProvider(cfg, tmp_path / "cache", resolve=True)
    prov.discover()
    res = prov.resolve(_make_group("Private Game", sha256="6" * 64), sha256="6" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE


def test_privacy_no_private_data_in_request(tmp_path):
    """Only the public sha256 is transmitted; private filename/path is never sent."""
    sha = "7" * 64
    group = _make_group("Privacy Game", source_filename="my secret rom (1992).adf")
    scans = _make_scans(group, sha)

    captured = []

    def opener(url, *, timeout):
        captured.append(url)
        return json.dumps(_hash_lookup(id="HS-PRIV", name="Privacy Game",
                                        metadata_matches=[{"source": "NoIntro",
                                                           "gameId": "NI-PRIV"}])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, scans=scans, sha256=sha)
    assert res.found is True

    url = captured[0]
    assert url.endswith(f"/Lookup/ByHash/sha256/{sha}")
    assert "secret" not in url
    assert "private" not in url
    assert "1992" not in url


def test_privacy_cache_only_public_fields(tmp_path):
    """Cache files must store only provider_id + canonical title + external_ids."""
    sha = "8" * 64
    group = _make_group("Cache Game", sha256=sha)

    def opener(url, *, timeout):
        return json.dumps(_hash_lookup(id="HS-CACHE", name="Cache Game",
                                       metadata_matches=[{"source": "NoIntro",
                                                          "gameId": "NI-CACHE"}])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True

    cache_files = list((tmp_path / "cache").glob("hasheous-*.json"))
    assert cache_files, "a canonical-reuse cache entry should have been written"
    content = cache_files[0].read_text()
    parsed = json.loads(content)
    assert "provider_id" in parsed
    assert parsed["provider_id"] == "NI-CACHE"
    assert "secret" not in content.lower()
    assert "private" not in content.lower()


# --- negative-lookup caching -----------------------------------------------

def test_negative_lookup_cached(tmp_path):
    """A genuine not-found (empty HashLookup) should be cached and reused."""
    sha = "9" * 64
    group = _make_group("Negative Game", sha256=sha)

    call_count = {"n": 0}

    def opener(url, *, timeout):
        call_count["n"] += 1
        return json.dumps(_hash_lookup(id="", name="",
                                        metadata_matches=[])).encode()

    prov = _provider({"enabled": True, "cache_ttl": 1000.0}, tmp_path / "cache", opener=opener)
    r1 = prov.resolve(group, sha256=sha)
    assert r1.found is False
    r2 = prov.resolve(group, sha256=sha)
    assert r2.found is False

    assert call_count["n"] == 1


def test_canonical_reuse_cache_hit(tmp_path):
    """After a successful hash match, a second resolve reuses the cache."""
    sha = "0" * 64
    group = _make_group("Reuse Game", sha256=sha)
    call_count = {"n": 0}

    def opener(url, *, timeout):
        call_count["n"] += 1
        return json.dumps(_hash_lookup(id="HS-REUSE", name="Reuse Game",
                                       metadata_matches=[{"source": "NoIntro",
                                                          "gameId": "NI-REUSE"}])).encode()

    prov = _provider({"enabled": True, "cache_ttl": 1000.0}, tmp_path / "cache", opener=opener)
    r1 = prov.resolve(group, sha256=sha)
    assert r1.found and r1.provider_id == "NI-REUSE"
    r2 = prov.resolve(group, sha256=sha)
    assert r2.found and r2.match_method == hs.HasheousMatchMethod.CANONICAL_REUSE
    assert call_count["n"] == 1


# --- provider-ID + external_ids capture -------------------------------------

def test_provider_id_captured_for_downstream(tmp_path):
    sha = "a" * 64
    group = _make_group("Capture Game", sha256=sha)

    def opener(url, *, timeout):
        return json.dumps(_hash_lookup(id="HS-DOWNSTREAM", name="Capture Game",
                                       metadata_matches=[{"source": "NoIntro",
                                                          "gameId": "NI-DOWNSTREAM"}])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.provider_id == "NI-DOWNSTREAM"


def test_external_ids_captured_from_hash_match(tmp_path):
    """A hash match carrying allow-listed external_ids must surface them."""
    sha = "b" * 64
    group = _make_group("External Game", sha256=sha)

    def opener(url, *, timeout):
        return json.dumps(_hash_lookup(
            id="HS-EXT", name="External Game",
            metadata_matches=[{"source": "NoIntro", "gameId": "NI-EXT"}],
            igdb_id="IGDB-42", hasheous_metadata_id="HM-1",
        )).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    # The allow-listed igdb_id is captured; hasheous_metadata_id already set
    # from the metadataMatch gameId (first wins).
    assert res.external_ids.get("igdb_id") == "IGDB-42"
    assert res.provider_id == "NI-EXT"


def test_external_ids_dropped_when_not_allowlisted(tmp_path):
    """Unrecognized external_ids keys must be dropped (fail-safe)."""
    sha = "c" * 64
    group = _make_group("External Drop Game", sha256=sha)

    def opener(url, *, timeout):
        return json.dumps(_hash_lookup(
            id="HS-DROP", name="External Drop Game",
            metadata_matches=[{"source": "NoIntro", "gameId": "NI-DROP"}],
            igdb_id="IGDB-7", private_user_field="leak",
        )).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.external_ids.get("igdb_id") == "IGDB-7"
    assert "private_user_field" not in res.external_ids


# --- paths integration -----------------------------------------------------

def test_load_hasheous_config_no_table(tmp_path):
    """A config without a [hasheous] table returns {} (disabled)."""
    p = tmp_path / "cfg.toml"
    p.write_text('[other]\nenabled = true\n')
    assert load_hasheous_config(str(p)) == {}


def test_load_hasheous_config_reads_table(tmp_path):
    """The [hasheous] table is surfaced verbatim for from_dict to consume."""
    p = tmp_path / "cfg.toml"
    p.write_text('[hasheous]\nenabled = true\nbase_url = "https://h.example/v1"\n')
    raw = load_hasheous_config(str(p))
    assert raw.get("enabled") is True
    cfg = hs.HasheousConfig.from_dict(raw)
    assert cfg.enabled is True
    assert cfg.base_url == "https://h.example/v1"


# --- independent shim: both providers enabled + disagree -------------------

def test_both_providers_exact_hash_deterministic_stronger_wins(tmp_path):
    """When BOTH providers resolve, each independently agrees on an identity."""
    sha = "a" * 64
    group = _make_group("Both Game", sha256=sha)

    def hs_opener(url, *, timeout):
        assert "/Lookup/ByHash/sha256/" in url
        return json.dumps(_hash_lookup(id="HS-BOTH", name="Both Game",
                                       metadata_matches=[{"source": "NoIntro",
                                                          "gameId": "NI-BOTH"}])).encode()

    def pm_opener(url, *, timeout):
        assert "/rom/" in url
        return json.dumps({
            "found": True, "provider_id": "PM-BOTH", "title": "Both Game",
            "confidence": 1.0,
        }).encode()

    hs_prov = _provider({"enabled": True}, tmp_path / "hs_cache", opener=hs_opener)
    pm_cfg = pm.PlaymatchConfig.from_dict({"enabled": True})
    pm_prov = pm.PlaymatchProvider(pm_cfg, tmp_path / "pm_cache",
                                   opener=pm_opener, resolve=False)
    pm_prov.discover()

    hs_res = hs_prov.resolve(group, sha256=sha)
    pm_res = pm_prov.resolve(group, sha256=sha)

    assert hs_res.found and pm_res.found
    assert hs_res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    assert pm_res.match_method == pm.PlaymatchMatchMethod.EXACT_HASH
    assert hs_res.confidence == 1.0 == pm_res.confidence
    assert hs_res.confidence == pm_res.confidence


def test_both_providers_disagree_hash_wins_over_weaker(monkeypatch, tmp_path):
    """Hasheous exact-hash identity is strong and must not be silently overridden.

    Playmatch's shape is the legacy synthetic contract (still valid for the
    Playmatch provider). Hasheous resolves via the real ByHash route.
    """
    sha = "b" * 64
    group = _make_group("Disagree Game", sha256=sha)
    pm_group = _make_group("Disagree Game")

    def hs_opener(url, *, timeout):
        return json.dumps(_hash_lookup(id="HS-STRONG", name="Disagree Game",
                                       metadata_matches=[{"source": "NoIntro",
                                                          "gameId": "NI-STRONG"}])).encode()

    def pm_opener(url, *, timeout):
        if "/rom/" in url:
            return json.dumps({"found": False}).encode()
        return json.dumps(_search_response(
            candidates=[{"provider_id": "PM-WEAK", "title": "Disagree Game",
                         "confidence": 0.97}])).encode()

    hs_prov = _provider({"enabled": True}, tmp_path / "hs_cache2", opener=hs_opener)
    pm_cfg = pm.PlaymatchConfig.from_dict({"enabled": True})
    pm_prov = pm.PlaymatchProvider(pm_cfg, tmp_path / "pm_cache2",
                                   opener=pm_opener, resolve=False)
    pm_prov.discover()

    hs_res = hs_prov.resolve(group, sha256=sha)
    pm_res = pm_prov.resolve(pm_group)

    assert hs_res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    assert hs_res.provider_id == "NI-STRONG"
    assert hs_res.confidence == 1.0
    assert pm_res.match_method == pm.PlaymatchMatchMethod.FUZZY_TITLE
    assert pm_res.confidence == 0.97
    assert hs_res.confidence > pm_res.confidence
    assert hs_res.provider_id != pm_res.provider_id


def _search_response(found=True, *, candidates=None):
    """Playmatch synthetic shape (used only for the cross-provider shim)."""
    if not found:
        return {"found": False}
    return {"found": True, "candidates": candidates or []}
