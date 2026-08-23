"""Synthetic tests for the optional RetroKit / Archive.org MANUAL provider (GH-10).

Hard constraints (mirrors tests/test_screenscraper_provider.py and
tests/test_rtfm.py):

* **No live network.** Every HTTP call goes through an injected fake ``opener``
  that returns canned bytes or raises a canned urllib error; the real urllib
  opener is never used, and no DNS resolution occurs (``resolve=False``).
* **No maintainer private data.** Every index row, URL, and artifact payload is
  a synthetic fixture.
* **Disabled by default.** ``RetroKitConfig.from_dict(None)`` and ``{}`` must be
  disabled, and constructing the provider while disabled raises
  ``RetroKitDisabled``.
* **Non-fatal degradation.** A miss, outage, 404, oversized artifact, or rate
  limit must degrade to a normal miss (never raise out of ``resolve_manual`` /
  ``acquire`` / ``load_index``) so the base offline RTFM build is unchanged.
* **Provenance privacy.** The provenance record must not leak the local cache
  directory or any absolute host path.
* **RTFM integration.** ``to_rtfm_sources`` surfaces only clean acquired PDFs as
  ``manuals``-category ``RtfmSource`` entries, and ``build_rtfm_all`` unions
  ``extra_sources`` with the locally discovered sources.

Run in isolation:
    python -m pytest tests/test_retrokit_provider.py -v
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from pathlib import Path

import pytest

from amiga_adf_library_builder import rtfm as rc
from amiga_adf_library_builder import retrokit as rk
from amiga_adf_library_builder.metadata import UnsafeUrlError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _Group:
    """Minimal group: the provider only reads ``title`` (and optionally
    ``release_key``). ``release_basename`` falls back to the title, so the
    identity set is deterministic and network/DNS-free."""

    def __init__(self, title: str, release_key: str | None = None) -> None:
        self.title = title
        self.release_key = release_key or title.lower()
        self.quarantine_reason = None


class _Resp(io.BytesIO):
    """BytesIO standing in for a urllib response object."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _opener(routes: dict, log: list | None = None):
    """Fake opener: ``routes`` maps a URL substring -> bytes payload (or an
    Exception instance to raise). First matching substring wins; no match is a
    404. Records every fetched URL to ``log``."""

    def opener(request, timeout=None):
        url = request.full_url
        if log is not None:
            log.append(url)
        for prefix, payload in routes.items():
            if prefix in url:
                if isinstance(payload, Exception):
                    raise payload
                return _Resp(payload)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))

    return opener


def _flaky_index_opener(tsv: bytes, retry_after: str = "1"):
    """Index fetch: first call returns 429 (with Retry-After), then succeeds."""
    state = {"calls": 0}
    log: list = []

    def opener(request, timeout=None):
        url = request.full_url
        log.append(url)
        state["calls"] += 1
        if state["calls"] == 1 and "sources.tsv" in url:
            raise urllib.error.HTTPError(
                url, 429, "Rate Limited", {"Retry-After": retry_after}, io.BytesIO(b"")
            )
        return _Resp(tsv)

    opener.log = log
    opener.state = state
    return opener


def _cfg(**kw) -> rk.RetroKitConfig:
    base = {"enabled": True}
    base.update(kw)
    return rk.RetroKitConfig.from_dict(base)


INDEX_ONE = (
    "Star Voyager\ten\thttps://archive.org/download/retrokit-manuals/"
    "amiga/star-voyager.pdf\n"
).encode()

MANUAL_PDF = b"%PDF-1.4 fake manual body\n"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_config_none_disabled():
    cfg = rk.RetroKitConfig.from_dict(None)
    assert cfg.enabled is False
    assert cfg.item_id == rk.DEFAULT_ITEM_ID
    assert cfg.system == rk.DEFAULT_SYSTEM


def test_config_empty_disabled():
    assert rk.RetroKitConfig.from_dict({}).enabled is False


def test_config_enabled_passthrough():
    cfg = rk.RetroKitConfig.from_dict({"enabled": True})
    assert cfg.enabled is True


def test_config_preferred_languages_list_normalization():
    cfg = rk.RetroKitConfig.from_dict(
        {"enabled": True, "preferred_languages": ["english", "german"]}
    )
    assert cfg.preferred_languages == ("en", "de")


def test_config_preferred_languages_string_comma():
    cfg = rk.RetroKitConfig.from_dict(
        {"enabled": True, "preferred_languages": "en, de , fr"}
    )
    assert cfg.preferred_languages == ("en", "de", "fr")


def test_config_preferred_languages_default():
    cfg = rk.RetroKitConfig.from_dict({"enabled": True})
    assert cfg.preferred_languages == ("en",)


def test_config_timeout_clamped_to_default_when_out_of_range():
    cfg = rk.RetroKitConfig.from_dict({"enabled": True, "timeout_seconds": 9999})
    assert cfg.timeout_seconds == rk.DEFAULT_TIMEOUT_SECONDS
    cfg_neg = rk.RetroKitConfig.from_dict({"enabled": True, "timeout_seconds": -1})
    assert cfg_neg.timeout_seconds == rk.DEFAULT_TIMEOUT_SECONDS


def test_config_negative_ttl_falls_back_to_default():
    cfg = rk.RetroKitConfig.from_dict(
        {"enabled": True, "index_ttl_seconds": -5, "negative_ttl_seconds": -5}
    )
    assert cfg.index_ttl_seconds == rk.DEFAULT_INDEX_TTL_SECONDS
    assert cfg.negative_ttl_seconds == rk.DEFAULT_NEGATIVE_TTL_SECONDS


def test_config_max_manual_bytes_clamped_to_cap():
    cfg = rk.RetroKitConfig.from_dict(
        {"enabled": True, "max_manual_bytes": rk.MAX_MANUAL_BYTES + 1}
    )
    assert cfg.max_manual_bytes == rk.MAX_MANUAL_BYTES


def test_config_item_id_and_system_sanitized():
    cfg = rk.RetroKitConfig.from_dict(
        {"enabled": True, "item_id": "  /MyItem/  ", "system": " AMIGA "}
    )
    assert cfg.item_id == "MyItem"
    assert cfg.system == "amiga"


def test_config_backoff_clamped():
    cfg = rk.RetroKitConfig.from_dict(
        {"enabled": True, "rate_limit_backoff_seconds": 999}
    )
    assert cfg.rate_limit_backoff_seconds == rk.DEFAULT_RATE_LIMIT_BACKOFF_SECONDS


# ---------------------------------------------------------------------------
# Index parsing
# ---------------------------------------------------------------------------


def test_parse_index_well_formed_rows():
    tsv = "Star Voyager\ten\thttps://x/a.pdf\nOther Game\ten,de\thttps://x/b.pdf\n"
    rows = rk.parse_index(tsv)
    assert [r["title"] for r in rows] == ["Star Voyager", "Other Game"]
    assert rows[1]["langs"] == "en,de"
    assert rows[1]["url"] == "https://x/b.pdf"


def test_parse_index_skips_malformed_rows():
    tsv = (
        "Too Many Cols\ta\tb\thttps://x\n"  # 4 cols
        "Only Two\ta\n"  # 2 cols
        "   \thttps://x\n"  # whitespace+tab stripped -> 1 col
        "\thttps://x\thttps://y\n"  # leading tab stripped -> 2 cols
        "Good\ten\thttps://x/good.pdf\n"  # valid
    )
    rows = rk.parse_index(tsv)
    assert [r["title"] for r in rows] == ["Good"]


def test_parse_index_keeps_empty_langs_row():
    # A row with a valid title/url but empty langs is kept (degrades to
    # "unknown" language later); only structurally malformed rows are dropped.
    rows = rk.parse_index("NoLangs\t\thttps://x/nolangs.pdf\n")
    assert len(rows) == 1
    assert rows[0]["title"] == "NoLangs"
    assert rows[0]["langs"] == ""


def test_parse_index_handles_crlf_and_blank_lines():
    tsv = "A\ten\thttps://x/a.pdf\r\n\r\nB\tde\thttps://x/b.pdf\r\n"
    rows = rk.parse_index(tsv)
    assert [r["title"] for r in rows] == ["A", "B"]


def test_parse_index_empty_string():
    assert rk.parse_index("") == []
    assert rk.parse_index("   \n\t\n") == []


# ---------------------------------------------------------------------------
# Provider: disabled
# ---------------------------------------------------------------------------


def test_disabled_provider_raises(tmp_path):
    with pytest.raises(rk.RetroKitDisabled):
        rk.RetroKitProvider(rk.RetroKitConfig.from_dict(None), tmp_path)


# ---------------------------------------------------------------------------
# Provider: resolution + acquisition (happy path)
# ---------------------------------------------------------------------------


def test_exact_match_acquires_and_caches(tmp_path):
    log: list = []
    opener = _opener(
        {"sources.tsv": INDEX_ONE, "star-voyager.pdf": MANUAL_PDF}, log=log
    )
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    res = provider.resolve_and_acquire(_Group("Star Voyager"))

    assert res.matched is True
    assert res.ambiguous is False
    assert res.routed_for_review is False
    assert res.source_title == "Star Voyager"
    assert res.language == "en"
    assert res.variant == "original"
    assert res.match_method.startswith("retrokit:exact")
    assert res.match_confidence == 1.0
    # Acquired: file written unmutated, sha + size recorded.
    assert res.local_path is not None and res.local_path.is_file()
    assert res.local_path.read_bytes() == MANUAL_PDF
    assert res.sha256 == hashlib.sha256(MANUAL_PDF).hexdigest()
    assert res.size == len(MANUAL_PDF)
    # Index fetched exactly once, manual fetched exactly once.
    assert sum("sources.tsv" in u for u in log) == 1
    assert sum("star-voyager.pdf" in u for u in log) == 1


def test_idempotent_second_run_reuses_cache_no_network(tmp_path):
    log: list = []
    opener = _opener(
        {"sources.tsv": INDEX_ONE, "star-voyager.pdf": MANUAL_PDF}, log=log
    )
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    first = provider.resolve_and_acquire(_Group("Star Voyager"))
    assert first.local_path is not None

    log.clear()
    second = provider.resolve_and_acquire(_Group("Star Voyager"))
    assert second.matched and second.local_path is not None
    # No index refetch (within TTL) and no manual redownload (cache hit).
    assert log == []
    assert second.local_path == first.local_path


def test_index_cache_reused_within_ttl(tmp_path):
    log: list = []
    opener = _opener(
        {"sources.tsv": INDEX_ONE, "star-voyager.pdf": MANUAL_PDF}, log=log
    )
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    provider.resolve_and_acquire(_Group("Star Voyager"))
    # Force a second resolution from a fresh provider over the same cache dir:
    # the index must come from cache (no index fetch), only the manual fetched.
    log.clear()
    provider2 = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    res = provider2.resolve_manual(_Group("Star Voyager"))
    assert res.matched
    assert sum("sources.tsv" in u for u in log) == 0


# ---------------------------------------------------------------------------
# Provider: misses (all non-fatal)
# ---------------------------------------------------------------------------


def test_no_index_miss_is_non_fatal(tmp_path):
    log: list = []
    # Index returns 404 -> load_index returns [] -> normal miss.
    opener = _opener({"sources.tsv": urllib.error.HTTPError(
        "x", 404, "Not Found", {}, io.BytesIO(b""))}, log=log)
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    res = provider.resolve_manual(_Group("Star Voyager"))
    assert res.matched is False
    assert res.error == "index unavailable (offline or missing)"


def test_no_matching_title_miss_and_negative_cache(tmp_path, monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(rk.time, "sleep", lambda s: sleeps.append(s))
    log: list = []
    # Index has a DIFFERENT title -> no match -> negative lookup cached.
    other_index = b"Unrelated Game\ten\thttps://archive.org/download/x/other.pdf\n"
    opener = _opener({"sources.tsv": other_index}, log=log)
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)

    res = provider.resolve_manual(_Group("Star Voyager"))
    assert res.matched is False
    assert res.error == "no matching RetroKit manual"

    # A negative-lookup marker must exist on disk.
    neg_files = list((tmp_path / "retrokit").glob("neg_*.json"))
    assert len(neg_files) == 1

    # Second resolve short-circuits on the negative cache (no index refetch).
    log.clear()
    res2 = provider.resolve_manual(_Group("Star Voyager"))
    assert res2.matched is False
    assert res2.error == "negative lookup cached"
    assert sum("sources.tsv" in u for u in log) == 0


def test_empty_title_miss(tmp_path):
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=_opener({}))
    res = provider.resolve_manual(_Group(""))
    assert res.matched is False
    assert res.error == "empty group title"


def test_network_outage_miss_non_fatal(tmp_path):
    log: list = []

    def opener(request, timeout=None):
        log.append(request.full_url)
        raise urllib.error.URLError("synthetic network down")

    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    res = provider.resolve_manual(_Group("Star Voyager"))
    assert res.matched is False


# ---------------------------------------------------------------------------
# Provider: language + variant selection
# ---------------------------------------------------------------------------


def test_prefer_original_selects_archive_org_artifact(tmp_path):
    index = (
        "Star Voyager\ten\thttps://archive.org/download/retrokit-manuals/"
        "amiga/star-voyager.pdf\n"
        "Star Voyager\ten\thttps://retro-commodore.eu/star-voyager.pdf\n"
    ).encode()
    log: list = []
    opener = _opener(
        {
            "sources.tsv": index,
            "star-voyager.pdf": MANUAL_PDF,
            "retro-commodore.eu": MANUAL_PDF,
        },
        log=log,
    )
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    res = provider.resolve_manual(_Group("Star Voyager"))
    assert res.matched and not res.ambiguous
    assert res.variant == "original"
    assert res.url.endswith("archive.org/download/retrokit-manuals/amiga/star-voyager.pdf")
    assert "prefer_original" in res.match_method


def test_two_distinct_urls_no_original_preference_is_ambiguous(tmp_path):
    index = (
        "Star Voyager\ten\thttps://retro-commodore.eu/a.pdf\n"
        "Star Voyager\ten\thttps://bombjack.org/b.pdf\n"
    ).encode()
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=_opener({"sources.tsv": index}))
    res = provider.resolve_manual(_Group("Star Voyager"))
    assert res.matched is True
    assert res.ambiguous is True
    assert res.routed_for_review is True
    assert res.match_method == "retrokit:ambiguous"
    # Ambiguous results must NOT be acquired (no download attempted).
    assert res.local_path is None


def test_prefer_original_off_still_ambiguous(tmp_path):
    index = (
        "Star Voyager\ten\thttps://archive.org/download/x/a.pdf\n"
        "Star Voyager\ten\thttps://retro-commodore.eu/b.pdf\n"
    ).encode()
    provider = rk.RetroKitProvider(
        _cfg(prefer_original=False), tmp_path, opener=_opener({"sources.tsv": index})
    )
    res = provider.resolve_manual(_Group("Star Voyager"))
    assert res.ambiguous is True and res.routed_for_review is True


def test_language_preference_selects_matching_row(tmp_path):
    # Two rows for the same title in different languages; preferred is de.
    index = (
        "Star Voyager\ten\thttps://archive.org/download/x/en.pdf\n"
        "Star Voyager\tde\thttps://archive.org/download/x/de.pdf\n"
    ).encode()
    provider = rk.RetroKitProvider(
        _cfg(preferred_languages=["de"]), tmp_path,
        opener=_opener({"sources.tsv": index}),
    )
    res = provider.resolve_manual(_Group("Star Voyager"))
    assert res.language == "de"
    assert res.url.endswith("/de.pdf")


# ---------------------------------------------------------------------------
# Provider: rate limit handling
# ---------------------------------------------------------------------------


def test_index_rate_limit_one_bounded_retry_then_success(tmp_path, monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(rk.time, "sleep", lambda s: sleeps.append(s))
    opener = _flaky_index_opener(INDEX_ONE, retry_after="3")
    provider = rk.RetroKitProvider(
        _cfg(rate_limit_backoff_seconds=5.0), tmp_path, opener=opener
    )
    res = provider.resolve_manual(_Group("Star Voyager"))
    assert res.matched is True
    # Exactly one backoff sleep, bounded by the configured backoff.
    assert sleeps == [3.0]
    # Index was fetched twice (429 then success).
    assert sum("sources.tsv" in u for u in opener.log) == 2


def test_download_rate_limit_one_bounded_retry(tmp_path, monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(rk.time, "sleep", lambda s: sleeps.append(s))
    # Manual fetch: first 429, then success. Index always succeeds.
    state = {"manual_calls": 0}
    log: list = []

    def opener(request, timeout=None):
        url = request.full_url
        log.append(url)
        if "sources.tsv" in url:
            return _Resp(INDEX_ONE)
        state["manual_calls"] += 1
        if state["manual_calls"] == 1:
            raise urllib.error.HTTPError(
                url, 429, "Rate Limited", {"Retry-After": "2"}, io.BytesIO(b"")
            )
        return _Resp(MANUAL_PDF)

    provider = rk.RetroKitProvider(
        _cfg(rate_limit_backoff_seconds=5.0), tmp_path, opener=opener
    )
    res = provider.resolve_and_acquire(_Group("Star Voyager"))
    assert res.matched and res.local_path is not None
    assert sleeps == [2.0]
    assert state["manual_calls"] == 2


def test_rate_limit_disabled_is_immediate_miss(tmp_path):
    # respect_rate_limit off -> a 429 on the manual is an immediate miss (no retry).
    state = {"manual_calls": 0}

    def opener(request, timeout=None):
        url = request.full_url
        if "sources.tsv" in url:
            return _Resp(INDEX_ONE)
        state["manual_calls"] += 1
        raise urllib.error.HTTPError(url, 429, "RL", {"Retry-After": "1"}, io.BytesIO(b""))

    provider = rk.RetroKitProvider(
        _cfg(respect_rate_limit=False), tmp_path, opener=opener
    )
    res = provider.resolve_and_acquire(_Group("Star Voyager"))
    assert res.matched is True  # resolved, but...
    assert res.local_path is None  # ...not acquired (429, no retry)
    assert state["manual_calls"] == 1


# ---------------------------------------------------------------------------
# Provider: oversized artifact
# ---------------------------------------------------------------------------


def test_oversized_manual_not_acquired(tmp_path):
    # _fetch_binary enforces the byte cap (read(max+1) -> RetroKitError),
    # which _download degrades to None -> the provider records a non-fatal
    # error and never writes an oversized artifact to disk.
    big = b"x" * 500
    provider = rk.RetroKitProvider(
        _cfg(max_manual_bytes=100),
        tmp_path,
        opener=_opener({"sources.tsv": INDEX_ONE, "star-voyager.pdf": big}),
    )
    res = provider.resolve_and_acquire(_Group("Star Voyager"))
    assert res.matched is True
    assert res.local_path is None
    assert res.error is not None
    # The oversized manual must not have been persisted anywhere in the cache.
    pdfs = [f for f in (tmp_path / "retrokit").rglob("*") if f.suffix == ".pdf"]
    assert pdfs == []


# ---------------------------------------------------------------------------
# Provider: SSRF guard (defense in depth)
# ---------------------------------------------------------------------------


def test_ssrf_guard_blocks_private_index_host(monkeypatch):
    # With no injected opener, resolve=True and the SSRF guard runs. A
    # private-literal index host must be refused before any network call.
    provider = rk.RetroKitProvider(
        _cfg(item_id="x", system="amiga"), Path("/tmp/rk-nomatch"), resolve_urls=True
    )
    with pytest.raises(UnsafeUrlError):
        rk._fetch_text(
            "http://127.0.0.1/amiga/amiga-sources.tsv",
            timeout=1.0,
            max_bytes=1024,
            resolve=True,
        )


def test_ssrf_guard_blocks_non_http_scheme():
    with pytest.raises(UnsafeUrlError):
        rk._fetch_text(
            "file:///etc/passwd", timeout=1.0, max_bytes=1024, resolve=True
        )


# ---------------------------------------------------------------------------
# Provenance privacy
# ---------------------------------------------------------------------------


def test_provenance_has_no_local_path_leak(tmp_path):
    opener = _opener({"sources.tsv": INDEX_ONE, "star-voyager.pdf": MANUAL_PDF})
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    res = provider.resolve_and_acquire(_Group("Star Voyager"))
    prov = res.provenance()
    blob = json.dumps(prov)
    # The managed cache dir and the local artifact path must not appear.
    assert str(tmp_path) not in blob
    assert str(res.local_path) not in blob
    # Core provenance identity is present and safe.
    assert prov["provider"] == "retrokit"
    assert prov["transport"] == "archive.org"
    assert prov["item_id"] == "retrokit-manuals"
    assert prov["system"] == "amiga"
    assert prov["source_title"] == "Star Voyager"
    assert prov["sha256"] == hashlib.sha256(MANUAL_PDF).hexdigest()


# ---------------------------------------------------------------------------
# RTFM integration
# ---------------------------------------------------------------------------


def test_to_rtfm_sources_only_clean_acquired(tmp_path):
    opener = _opener({"sources.tsv": INDEX_ONE, "star-voyager.pdf": MANUAL_PDF})
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    good = provider.resolve_and_acquire(_Group("Star Voyager"))
    # A miss result and an ambiguous result must be excluded.
    miss = rk.RetroKitResult()  # not matched
    amb = rk.RetroKitResult(matched=True, ambiguous=True)
    amb.local_path = good.local_path  # even with a path, ambiguity excludes it

    srcs = rk.to_rtfm_sources([good, miss, amb])
    assert len(srcs) == 1
    s = srcs[0]
    assert s.category == rc.CATEGORY_MANUALS
    assert s.stem == "Star Voyager"
    assert s.path == good.local_path


def test_to_rtfm_sources_empty_for_empty_results():
    assert rk.to_rtfm_sources([]) == []
    assert rk.to_rtfm_sources([rk.RetroKitResult()]) == []


def test_to_rtfm_sources_dedupes_duplicate_paths(tmp_path):
    opener = _opener({"sources.tsv": INDEX_ONE, "star-voyager.pdf": MANUAL_PDF})
    provider = rk.RetroKitProvider(_cfg(), tmp_path, opener=opener)
    res = provider.resolve_and_acquire(_Group("Star Voyager"))
    # Passing the same acquired result twice must yield a single source.
    srcs = rk.to_rtfm_sources([res, res])
    assert len(srcs) == 1


def test_extra_pdf_source_scores_as_auto_accept_match():
    # A RetroKit-surfaced source (stem == title) must score at or above the
    # auto-accept threshold so the RTFM core composes it without review.
    src = rc.RtfmSource(
        path=Path("/cache/retrokit/manuals/amiga/deadbeef.pdf"),
        root=Path("/cache/retrokit/manuals/amiga"),
        category=rc.CATEGORY_MANUALS,
        stem="Star Voyager",
    )
    score = rc.score_source_match(src, _Group("Star Voyager"))
    assert score.matched is True
    assert score.confidence >= rc.HIGH_CONFIDENCE


def test_build_rtfm_all_unions_extra_sources(tmp_path):
    # No local discovery roots -> discover_sources returns []; the extra
    # source (a .txt manual, so no PDF extraction dependency) must still be
    # picked up through the ``extra_sources`` union and composed into the .rtfm.
    local_root = tmp_path / "manuals"
    local_root.mkdir()
    txt_path = local_root / "Star Voyager.txt"
    txt_path.write_bytes(b"controls: button 1 fires\n")

    extra = rc.RtfmSource(
        path=txt_path, root=local_root, category=rc.CATEGORY_MANUALS,
        stem="Star Voyager",
    )
    cfg = rc.RtfmConfig(enabled=True, manuals_roots=())  # no roots discovered
    rtfm_dir = tmp_path / "rtfm_out"

    results = rc.build_rtfm_all(
        [_Group("Star Voyager")], cfg=cfg, rtfm_dir=rtfm_dir,
        extra_sources=[extra],
    )
    assert len(results) == 1
    r = results[0]
    assert r.written is True
    assert r.rtfm_path is not None and r.rtfm_path.is_file()
    body = r.rtfm_path.read_text(encoding="utf-8")
    assert "controls: button 1 fires" in body


def test_build_rtfm_all_extra_sources_default_none_unchanged(tmp_path):
    # With no extra_sources and no local roots, the offline behavior is
    # unchanged: no .rtfm is written (nothing matched) and it is routed for
    # review rather than emitting an empty file.
    cfg = rc.RtfmConfig(enabled=True, manuals_roots=())
    rtfm_dir = tmp_path / "rtfm_out"
    results = rc.build_rtfm_all([_Group("Star Voyager")], cfg=cfg, rtfm_dir=rtfm_dir)
    assert len(results) == 1
    assert results[0].written is False
    assert results[0].routed_for_review is True
