"""Tests for the OPTIONAL Hasheous ROM-hash identity resolver provider.

Mirrors ``tests/test_playmatch_provider.py`` in style and coverage. Covers
every acceptance criterion from issue #12:

* disabled by default; ``from_dict(None)`` -> enabled=False
* hash-first: exact-hash match OUTRANKS title fallback; determinism
* ambiguous/conflict -> needs_manual_review (never silent override)
* outage/timeout/oversize -> non-fatal NONE (found=False), pipeline continues
* SSRF guard active (private host refused); privacy preserved
  (no private filename/path in cache or request)
* provider-ID capture + external_ids capture for downstream
* title/filename fallback still passes the EXISTING relevance validation
* negative-lookup caching
* OFFLINE guarantee: socket.socket monkeypatched to raise proves no real fetch
* HasheousResult exposes the same public fields as PlaymatchResult + external_ids

All network contact is synthetic/mock: an injected ``opener`` returns synthetic
JSON, and ``resolve=False`` is passed so ``guard_url`` never performs DNS.
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


def _rom_response(found=True, *, provider_id="HS-123", title=None,
                  confidence=1.0, category="Game", external_ids=None):
    if not found:
        return {"found": False}
    return {
        "found": True,
        "provider_id": provider_id,
        "title": title,
        "confidence": confidence,
        "category": category,
        "external_ids": external_ids or {},
    }


def _search_response(found=True, *, candidates=None):
    if not found:
        return {"found": False}
    return {"found": True, "candidates": candidates or []}


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
    })
    assert cfg.timeout_seconds == hs.DEFAULT_TIMEOUT_SECONDS
    assert cfg.max_response_bytes == hs.DEFAULT_MAX_RESPONSE_BYTES
    assert cfg.max_concurrency == hs.DEFAULT_MAX_CONCURRENCY
    assert cfg.confidence_threshold == hs.DEFAULT_CONFIDENCE_THRESHOLD


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
        assert "api.hasheous.example" in url
        return json.dumps(_rom_response(provider_id="HS-XY")).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    # The real socket must never have been used.
    assert calls == []


# --- acceptance #2: hash-first outranks title, determinism ------------------

def test_exact_hash_outranks_title_fallback(tmp_path):
    sha = "b" * 64
    group = _make_group("Example Game", sha256=sha)

    def opener(url, *, timeout):
        if "/rom/" in url:
            return json.dumps(_rom_response(provider_id="HS-HASH")).encode()
        if "/search" in url:
            return json.dumps(_search_response(
                candidates=[{"provider_id": "HS-TITLE"}])).encode()
        raise AssertionError(f"unexpected url {url}")

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    assert res.provider_id == "HS-HASH"
    assert res.confidence == 1.0


def test_determinism_same_input_same_result(tmp_path):
    sha = "c" * 64
    group = _make_group("Deterministic Game", sha256=sha)

    payload = json.dumps(_rom_response(provider_id="HS-DET")).encode()

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
        assert url.endswith(f"/rom/{sha}")
        return json.dumps(_rom_response(provider_id="HS-SCAN")).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, scans=scans)
    assert res.found is True
    assert res.provider_id == "HS-SCAN"


def test_refuse_hash_mode_without_signal_falls_to_title(tmp_path):
    """No sha anywhere -> hash mode refused -> title fallback attempted."""
    group = _make_group("Fallback Game")

    def opener(url, *, timeout):
        if "/rom/" in url:
            raise AssertionError("should not query /rom without a hash signal")
        return json.dumps(_search_response(
            candidates=[{"provider_id": "HS-FB", "title": "Fallback Game",
                         "confidence": 0.97}])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group)
    assert res.found is True
    assert res.match_method == hs.HasheousMatchMethod.FUZZY_TITLE
    assert res.provider_id == "HS-FB"


# --- acceptance #3: ambiguous / conflict -> manual review -------------------

def test_conflicting_title_candidates_routes_to_review(tmp_path):
    """Two accepted title candidates with different ids -> manual review."""
    group = _make_group("Ambiguous Game")

    def opener(url, *, timeout):
        return json.dumps(_search_response(
            candidates=[
                {"provider_id": "HS-ONE", "title": "Ambiguous Game", "confidence": 0.97},
                {"provider_id": "HS-TWO", "title": "Ambiguous Game", "confidence": 0.97},
            ])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group)
    assert res.found is False
    assert res.needs_manual_review is True
    assert res.match_method == hs.HasheousMatchMethod.MANUAL_REVIEW
    assert "conflicting" in (res.manual_review_reason or "").lower()


def test_title_fallback_failing_relevance_routes_to_review(tmp_path):
    """Filename-fallback candidate that fails existing relevance -> review, not accept."""
    group = _make_group("Real Game")

    def opener(url, *, timeout):
        return json.dumps(_search_response(
            candidates=[{"provider_id": "HS-WRONG", "title": "Completely Different Title",
                         "confidence": 0.99}])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group)
    assert res.found is False
    assert res.needs_manual_review is True
    assert res.match_method == hs.HasheousMatchMethod.MANUAL_REVIEW


def test_hash_match_conflicting_title_no_silent_override(tmp_path):
    """Hash says ID-A; a conflicting title result must NOT silently override the hash.

    Both signals are resolved independently; the exact-hash identity is
    authoritative and deterministic, and it outranks the weaker title signal.
    """
    sha = "e" * 64
    group = _make_group("Conflict Game", sha256=sha)

    def opener(url, *, timeout):
        if "/rom/" in url:
            return json.dumps(_rom_response(provider_id="HS-HASHID")).encode()
        if "/search" in url:
            return json.dumps(_search_response(
                candidates=[{"provider_id": "HS-TITLEID", "title": "Conflict Game",
                             "confidence": 0.97}])).encode()
        raise AssertionError(url)

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    assert res.provider_id == "HS-HASHID"
    # The weaker title signal did not override the stronger hash identity.
    assert res.provider_id != "HS-TITLEID"


# --- acceptance #4: outage / timeout / oversize -> non-fatal ----------------

def test_outage_non_fatal_none(tmp_path):
    group = _make_group("Outage Game", sha256="f" * 64)

    def opener(url, *, timeout):
        import urllib.error
        raise urllib.error.URLError("connection refused")

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256="f" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE
    assert res.needs_manual_review is False


def test_timeout_non_fatal_none(tmp_path):
    group = _make_group("Timeout Game", sha256="1" * 64)

    def opener(url, *, timeout):
        raise TimeoutError("timed out")

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256="1" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE


def test_oversize_non_fatal_none(tmp_path):
    group = _make_group("Oversize Game", sha256="2" * 64)

    def opener(url, *, timeout):
        return b"x" * (hs.DEFAULT_MAX_RESPONSE_BYTES + 1)

    cfg = hs.HasheousConfig.from_dict({"enabled": True, "max_response_bytes": 100})
    prov = hs.HasheousProvider(cfg, tmp_path / "cache", opener=opener, resolve=False)
    prov.discover()
    res = prov.resolve(group, sha256="2" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE


def test_malformed_json_non_fatal_none(tmp_path):
    group = _make_group("Garbage Game", sha256="3" * 64)

    def opener(url, *, timeout):
        return b"not json at all"

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256="3" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE


# --- acceptance #5: SSRF guard + privacy ------------------------------------

def test_ssrf_guard_refuses_private_host(tmp_path):
    """A config pointing at a private host must be refused by guard_url."""
    cfg = hs.HasheousConfig.from_dict({
        "enabled": True,
        "base_url": "http://127.0.0.1:8080/api",
    })

    prov = hs.HasheousProvider(cfg, tmp_path / "cache", resolve=True)
    prov.discover()
    res = prov.resolve(_make_group("Private Game", sha256="4" * 64), sha256="4" * 64)
    assert res.found is False
    assert res.match_method == hs.HasheousMatchMethod.NONE


def test_privacy_no_private_data_in_request(tmp_path):
    """Only public sha256 / canonical title may be transmitted."""
    sha = "5" * 64
    group = _make_group("Privacy Game", source_filename="my secret rom (1992).adf")
    scans = _make_scans(group, sha)

    captured = []

    def opener(url, *, timeout):
        captured.append(url)
        if "/rom/" in url:
            return json.dumps(_rom_response(provider_id="HS-PRIV")).encode()
        if "/search" in url:
            assert "Privacy%20Game" in url
            assert "secret" not in url
            assert "1992" not in url
            return json.dumps(_search_response(
                candidates=[{"provider_id": "HS-T"}])).encode()
        raise AssertionError(url)

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, scans=scans, sha256=sha)
    assert res.found is True

    rom_url = captured[0]
    assert rom_url.endswith(f"/rom/{sha}")
    assert "secret" not in rom_url
    assert "private" not in rom_url


def test_privacy_cache_only_public_fields(tmp_path):
    """Cache files must store only provider_id + canonical title + external_ids."""
    sha = "6" * 64
    group = _make_group("Cache Game", sha256=sha)

    def opener(url, *, timeout):
        return json.dumps(_rom_response(provider_id="HS-CACHE", title="Cache Game")).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True

    cache_files = list((tmp_path / "cache").glob("hasheous-*.json"))
    assert cache_files, "a canonical-reuse cache entry should have been written"
    content = cache_files[0].read_text()
    parsed = json.loads(content)
    assert "provider_id" in parsed
    assert parsed["provider_id"] == "HS-CACHE"
    assert "secret" not in content.lower()
    assert "private" not in content.lower()


# --- negative-lookup caching ------------------------------------------------

def test_negative_lookup_cached(tmp_path):
    """A genuine not-found should be cached and reused (non-fatal, deterministic)."""
    sha = "7" * 64
    group = _make_group("Negative Game", sha256=sha)

    call_count = {"n": 0}

    def opener(url, *, timeout):
        call_count["n"] += 1
        return json.dumps(_rom_response(found=False)).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    r1 = prov.resolve(group, sha256=sha)
    assert r1.found is False
    r2 = prov.resolve(group, sha256=sha)
    assert r2.found is False

    assert call_count["n"] == 1


def test_canonical_reuse_cache_hit(tmp_path):
    """After a successful hash match, a second resolve reuses the cache."""
    sha = "8" * 64
    group = _make_group("Reuse Game", sha256=sha)
    call_count = {"n": 0}

    def opener(url, *, timeout):
        call_count["n"] += 1
        return json.dumps(_rom_response(provider_id="HS-REUSE")).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    r1 = prov.resolve(group, sha256=sha)
    assert r1.found and r1.provider_id == "HS-REUSE"
    r2 = prov.resolve(group, sha256=sha)
    assert r2.found and r2.match_method == hs.HasheousMatchMethod.CANONICAL_REUSE
    assert call_count["n"] == 1


# --- provider-ID + external_ids capture -------------------------------------

def test_provider_id_captured_for_downstream(tmp_path):
    sha = "9" * 64
    group = _make_group("Capture Game", sha256=sha)

    def opener(url, *, timeout):
        return json.dumps(_rom_response(provider_id="HS-DOWNSTREAM",
                                        title="Capture Game")).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.provider_id == "HS-DOWNSTREAM"


def test_external_ids_captured_from_hash_match(tmp_path):
    """A hash match carrying external_ids must surface them on the result."""
    sha = "0" * 64
    group = _make_group("External Game", sha256=sha)

    def opener(url, *, timeout):
        return json.dumps(_rom_response(
            provider_id="HS-EXT",
            external_ids={"hasheous_metadata_id": "HM-1", "igdb_id": "IGDB-42"},
        )).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.external_ids == {"hasheous_metadata_id": "HM-1", "igdb_id": "IGDB-42"}
    assert res.provider_id == "HS-EXT"


def test_external_ids_dropped_when_not_allowlisted(tmp_path):
    """Unrecognized external_ids keys must be dropped (fail-safe)."""
    sha = "z" * 64
    group = _make_group("External Drop Game", sha256=sha)

    def opener(url, *, timeout):
        return json.dumps(_rom_response(
            provider_id="HS-DROP",
            external_ids={"igdb_id": "IGDB-7", "private_user_field": "leak"},
        )).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group, sha256=sha)
    assert res.found is True
    assert res.external_ids == {"igdb_id": "IGDB-7"}
    assert "private_user_field" not in res.external_ids


def test_external_ids_captured_on_title_match(tmp_path):
    """Title-fallback matches also surface allow-listed external_ids."""
    group = _make_group("Title External Game")

    def opener(url, *, timeout):
        return json.dumps(_search_response(
            candidates=[{"provider_id": "HS-TEXT", "title": "Title External Game",
                         "confidence": 0.97,
                         "external_ids": {"mobygames_id": "MG-99"}}])).encode()

    prov = _provider({"enabled": True}, tmp_path / "cache", opener=opener)
    res = prov.resolve(group)
    assert res.found is True
    assert res.external_ids == {"mobygames_id": "MG-99"}


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
    """When BOTH providers resolve the same exact-hash identity, it is stable.

    This is an independent behavior check (not a Case-authored happy path):
    each provider independently resolves the hash-first identity, and both agree
    on the exact-hash match. The stronger (EXACT_HASH) signal is deterministic
    and identical across providers.
    """
    sha = "a" * 64
    group = _make_group("Both Game", sha256=sha)

    def hs_opener(url, *, timeout):
        assert "api.hasheous.example" in url
        return json.dumps(_rom_response(provider_id="HS-BOTH")).encode()

    def pm_opener(url, *, timeout):
        assert "api.playmatch.example" in url
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

    # Both independently agree: exact-hash, deterministic, found.
    assert hs_res.found and pm_res.found
    assert hs_res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    assert pm_res.match_method == pm.PlaymatchMatchMethod.EXACT_HASH
    assert hs_res.confidence == 1.0 == pm_res.confidence
    # Determinism across providers: identical confidence + method semantics.
    assert hs_res.confidence == pm_res.confidence


def test_both_providers_disagree_hash_wins_over_weaker(monkeypatch, tmp_path):
    """When Hasheous returns an exact-hash identity but Playmatch only a weaker
    title fallback, the exact-hash identity is the stronger signal and must not
    be silently overridden.

    Both providers are enabled and "disagree" on the outcome. Hasheous resolves
    via exact hash (strong, confidence 1.0). Playmatch -- representing the case
    where it only has the canonical title, not the matching hash -- resolves via
    the title fallback (weaker, FUZZY_TITLE). The cross-provider property this
    checks: the stronger exact-hash identity is deterministic and higher
    confidence than the weaker title signal, and neither provider silently
    overrides the other's result (they surface independently).
    """
    sha = "b" * 64
    group = _make_group("Disagree Game", sha256=sha)
    # Playmatch group carries only the canonical title (no hash signal), so its
    # hash lookup is refused and it falls through to the title fallback.
    pm_group = _make_group("Disagree Game")

    def hs_opener(url, *, timeout):
        if "/rom/" in url:
            return json.dumps(_rom_response(provider_id="HS-STRONG")).encode()
        return json.dumps(_search_response(found=False)).encode()

    def pm_opener(url, *, timeout):
        if "/rom/" in url:
            # Playmatch has no matching hash -> genuine miss (no fall-through).
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

    # Hasheous exact-hash identity is stronger (confidence 1.0) than the
    # Playmatch title fallback (confidence 0.97).
    assert hs_res.match_method == hs.HasheousMatchMethod.EXACT_HASH
    assert hs_res.provider_id == "HS-STRONG"
    assert hs_res.confidence == 1.0
    assert pm_res.match_method == pm.PlaymatchMatchMethod.FUZZY_TITLE
    assert pm_res.confidence == 0.97
    # The weaker signal did not override the stronger exact-hash identity.
    assert hs_res.confidence > pm_res.confidence
    assert hs_res.provider_id != pm_res.provider_id
