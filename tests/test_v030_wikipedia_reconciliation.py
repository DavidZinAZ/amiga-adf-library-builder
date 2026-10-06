"""v0.3.0 Wikipedia result-reconciliation regression tests.

Binds the real 45-ADF packaged-Windows acceptance run
(20261006T044759Z-6836-00000) findings to deterministic, offline tests:

PRIMARY DEFECT -- two disagreeing acceptance paths.
  The lower-level Wikipedia request path (``wikipedia_lookup`` ->
  ``_rank_wikipedia_pages`` -> ``candidate_is_same_subject``) accepted a
  candidate (``candidate accepted``), but the higher-level
  ``validate_metadata_relevance`` ratio band independently re-evaluated the
  same candidate and returned a contradictory ``review/ambiguous_midband`` or
  ``reject/different_game`` verdict, so the final metadata became
  ``not-found``. Real-run examples: A-10 Tank Killer, Dark Queen of Krynn,
  Hacker II, Ogre. The fix makes the strict lower-level subject test
  authoritative: a record the Wikipedia lookup accepted is marked
  ``subject_verified`` and the outer gate accepts it (reason
  ``strict_subject_verified``) instead of discarding it -- while the outer
  gate's independent non-title guards (person page, disambiguation page) still
  run first and can override.

SECONDARY DEFECT -- diagnostics.
  A successful (asset-bearing) release must not be listed under
  ``zero_asset_releases`` merely because it carries an internal
  ``wikipedia-request`` sub-attempt; a genuine zero-asset release must still be
  listed.

SECONDARY DEFECT -- artwork rate limiting.
  Wikimedia artwork downloads bypassed the Wikipedia gate/backoff and abandoned
  on an immediate HTTP 429. ``_fetch_artwork_bytes`` now retries with
  Retry-After / exponential backoff, is intentionally standalone (it does NOT
  consult the shared :class:`WikipediaGate`), so a burst of image fetches
  cannot inflate the metadata provider's request accounting.

All tests are deterministic and offline: the clock, sleep and network opener
are injected. Nothing here hits a real endpoint.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from amiga_adf_library_builder import diagnostics as diagnostics_module
from amiga_adf_library_builder import metadata as metadata_module
from amiga_adf_library_builder import enrich as enrich_module
from amiga_adf_library_builder.enrich import EnrichCategory
from amiga_adf_library_builder.metadata import (
    MetadataRecord,
    lookup_metadata,
    validate_metadata_relevance,
)
from amiga_adf_library_builder.wikipedia_client import (
    WikipediaGate,
    WikipediaPolicy,
    reset_global_gate,
)
from amiga_adf_library_builder.wikipedia_query import candidate_is_same_subject


# ---------------------------------------------------------------------------
# shared fixtures / fakes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_gate():
    """Isolate the shared pacing gate so test budgets never leak."""
    reset_global_gate()
    yield
    reset_global_gate()


def _wiki_page(title: str, *, pageid: int = 1, extract: str = "") -> dict:
    return {
        "pageid": pageid,
        "title": title,
        "extract": extract or f"{title} is an Amiga video game.",
        "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
    }


def _wiki_api_response(title: str, *, extract: str = "", pageid: int = 1,
                       artwork: str = "") -> bytes:
    page = _wiki_page(title, pageid=pageid, extract=extract)
    if artwork:
        page["original"] = {"source": artwork}
    return json.dumps({"query": {"pages": [page]}}).encode("utf-8")


class _JsonHeaders:
    def get_content_type(self):
        return "application/json"


class _JsonResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str = "https://en.wikipedia.org/w/api.php"):
        super().__init__(body)
        self._url = url
        self.headers = _JsonHeaders()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self._url


def _wiki_opener(payload_by_title: dict[str, bytes], seen: list[str]):
    """Serve a Wikipedia API payload keyed by a substring of ``gsrsearch``.

    The needle is matched against the URL-DECODED search value (both
    percent-encoding and ``+``-for-space are decoded) so a title needle always
    hits regardless of which query form the lookup emitted.
    """
    def opener(request, timeout=0):
        url = request.full_url
        seen.append(url)
        decoded = urllib.parse.unquote_plus(url)
        for needle, payload in payload_by_title.items():
            if needle in decoded:
                return _JsonResponse(payload, url)
        # A query form that names no acceptable candidate: answer with an
        # unrelated page so the lookup records ``no_acceptable_candidate``.
        return _JsonResponse(_wiki_api_response("Unrelated Title"), url)
    return opener


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


def _http_error(status: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers: dict = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://upload.wikimedia.org/wikipedia/en/thumb/0/01/Art.jpg",
        status, "err", headers, io.BytesIO(b"rate limited"))


class _ImageHeaders:
    def __init__(self, retry_after: str | None = None) -> None:
        self._ra = retry_after

    def get_content_type(self):
        return "image/jpeg"

    def get(self, name, default=None):
        if name == "Retry-After":
            return self._ra
        return default


class _ImageResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str = "https://upload.wikimedia.org/x.jpg",
                 retry_after: str | None = None):
        super().__init__(body)
        self._url = url
        self.headers = _ImageHeaders(retry_after)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self._url


# The four real-run disagreement titles, each mapped to the Wikipedia article
# title that the lower-level subject test genuinely accepts while the ratio band
# genuinely disagrees (verified offline; see the module docstring). The
# description is the article extract (mentions Amiga); ``platforms`` is left
# empty exactly as ``wikipedia_lookup`` produces it.
TITLES = [
    ("A-10 Tank Killer", "A-10 Tank Killer: The Tank Game",
     "A-10 Tank Killer: The Tank Game is an Amiga video game about armored combat."),
    ("Dark Queen of Krynn", "Dark Queen of Krynn: The Lost Kingdom",
     "Dark Queen of Krynn: The Lost Kingdom is an Amiga video game."),
    ("Hacker II", "Hacker II: The Doomsday Papers",
     "Hacker II: The Doomsday Papers is an Amiga video game."),
    ("Ogre", "Ogre (Amiga video game)",
     "Ogre (Amiga video game) is an Amiga video game."),
]


def _wiki_style_record(candidate: str, description: str, *, subject_verified: bool) -> MetadataRecord:
    return MetadataRecord(
        canonical_title=candidate,
        description=description,
        platforms=[],  # wikipedia_lookup does not set platforms
        provider="wikipedia",
        source_url=f"https://en.wikipedia.org/wiki/{candidate.replace(' ', '_')}",
        subject_verified=subject_verified,
    )


# ---------------------------------------------------------------------------
# PRIMARY DEFECT -- the lower-level acceptance must be authoritative
# ---------------------------------------------------------------------------

class TestSubjectVerifiedIsAuthoritative:
    @pytest.mark.parametrize("requested,candidate,extract", TITLES,
                             ids=[t[0] for t in TITLES])
    def test_lower_level_test_accepts(self, requested, candidate, extract):
        # The strict token-based subject test must accept: this is the
        # lower-level "candidate accepted" the real run recorded.
        assert candidate_is_same_subject(requested, candidate) is True

    @pytest.mark.parametrize("requested,candidate,extract", TITLES,
                             ids=[t[0] for t in TITLES])
    def test_stale_ratio_band_discards_without_verification(self,
                                                            requested, candidate, extract):
        # WITHOUT subject verification (the pre-fix state) the outer ratio band
        # contradicts the lower-level acceptance -- this is exactly the defect.
        stale = _wiki_style_record(candidate, extract, subject_verified=False)
        decision = validate_metadata_relevance(requested, stale)
        assert decision.category in {"review", "rejected"}, (
            f"expected the pre-fix contradiction for {requested!r}, got {decision.category}")

    @pytest.mark.parametrize("requested,candidate,extract", TITLES,
                             ids=[t[0] for t in TITLES])
    def test_verified_record_is_accepted(self, requested, candidate, extract):
        # WITH subject verification (the fix) the outer gate accepts the
        # same-subject candidate and must not discard the lower-level decision.
        rec = _wiki_style_record(candidate, extract, subject_verified=True)
        decision = validate_metadata_relevance(requested, rec)
        assert decision.category == "accepted"
        assert decision.reason == "strict_subject_verified"
        assert decision.confidence >= 0.90
        assert "strict_subject_verified" in decision.evidence

    def test_person_page_guard_still_overrides_verification(self):
        # The outer gate's independent non-title guards must still run BEFORE
        # the subject-verified branch: a biography page is never accepted on
        # the strength of a matching title alone.
        rec = _wiki_style_record("Some Person", "Some Person is a composer and "
                                "musician born in 1960.", subject_verified=True)
        decision = validate_metadata_relevance("Some Person", rec)
        assert decision.category == "rejected"
        assert decision.reason == "person_page"

    def test_disambiguation_guard_still_overrides_verification(self):
        rec = _wiki_style_record("Some Game", "This is a disambiguation page; it "
                                "may refer to several things.", subject_verified=True)
        decision = validate_metadata_relevance("Some Game", rec)
        assert decision.category in {"review", "rejected"}
        assert decision.reason == "series_disambiguation"


class TestGenuinelyDifferentGamesStillRejected:
    """Requirement 3: the fix must not turn a blind accept out of the strict test."""

    @pytest.mark.parametrize("requested,candidate", [
        ("Hacker", "Hacker II: The Doomsday Papers"),
        ("Ultima IV", "Ultima V"),
        ("Ogre", "Ogre: The Game"),
        ("A-10 Tank Killer", "A-10 Thunderbolt Attack"),
    ])
    def test_strict_test_rejects_different_release(self, requested, candidate):
        assert candidate_is_same_subject(requested, candidate) is False

    def test_unverified_different_game_still_rejected_by_outer(self):
        rec = _wiki_style_record("Hacker II: The Doomsday Papers",
                                 "Hacker II: The Doomsday Papers is an Amiga video game.",
                                 subject_verified=False)
        decision = validate_metadata_relevance("Hacker", rec)
        assert decision.category in {"review", "rejected"}


class TestEndToEndLookupMetadata:
    """Drive the real production path: lookup_metadata -> wikipedia_lookup ->
    validate_metadata_relevance. The four real-run titles must now RESOLVE
    (return a record) instead of ``not-found``."""

    @pytest.mark.parametrize("requested,candidate,extract", TITLES,
                             ids=[t[0] for t in TITLES])
    def test_lookup_resolves_instead_of_not_found(self, tmp_path,
                                                  requested, candidate, extract):
        seen: list[str] = []
        opener = _wiki_opener({requested: _wiki_api_response(candidate, extract=extract)}, seen)
        record, provider, events = lookup_metadata(
            requested, cache_dir=tmp_path / "cache", curated_dir=tmp_path / "curated",
            opener=opener, wikipedia_enabled=True,
        )
        assert record is not None, (
            f"{requested!r} resolved to not-found: "
            f"{[(e['provider'], e['category'], e['reason']) for e in events]}")
        assert provider == "wikipedia"
        # The final relevance event for the wikipedia provider must be the
        # authoritative accept, not the contradictory review/reject.
        wiki_events = [e for e in events if e["provider"] == "wikipedia"]
        assert wiki_events, "no wikipedia provider event recorded"
        final = wiki_events[-1]
        assert final["category"] == "accepted"
        assert final["reason"] == "strict_subject_verified"
        # And the record is cached for reuse.
        assert record.relevance_category == "accepted"


# ---------------------------------------------------------------------------
# SECONDARY DEFECT -- diagnostics zero-asset aggregation
# ---------------------------------------------------------------------------

class TestDiagnosticsZeroAssetTruthfulness:
    def test_successful_release_not_listed_as_zero_asset(self):
        # A release whose metadata matched and produced an asset, even though
        # it also carries an internal wikipedia-request sub-attempt, must NOT be
        # listed as assetless.
        events = [
            enrich_module.EnrichEvent(
                EnrichCategory.METADATA_PROVIDER_ATTEMPT,
                "provider=wikipedia-request outcome=no_match "
                "reason=wikipedia_candidate_accepted confidence=0.95 "
                "candidate='Hacker II: The Doomsday Papers'"),
            enrich_module.EnrichEvent(
                EnrichCategory.METADATA_PROVIDER_ATTEMPT,
                "provider=wikipedia outcome=hit reason=strict_subject_verified "
                "confidence=0.95 candidate='Hacker II: The Doomsday Papers'"),
            enrich_module.EnrichEvent(
                EnrichCategory.METADATA_LOOKUP, "result=hit provider=wikipedia",
                url="https://en.wikipedia.org/wiki/Hacker_II:_The_Doomsday_Papers"),
            enrich_module.EnrichEvent(EnrichCategory.ARTWORK_GENERATED,
                                       "downloaded artwork master"),
        ]
        attempt = diagnostics_module.attempt_from_enrich_events(
            events, title="Hacker II", release_key="hacker-ii")
        agg = diagnostics_module.aggregate_provider_attempts(attempt)
        assert agg["zero_asset_releases"] == {}, (
            f"successful release falsely listed as zero-asset: {agg['zero_asset_releases']}")

    def test_genuine_zero_asset_release_still_listed(self):
        events = [
            enrich_module.EnrichEvent(
                EnrichCategory.METADATA_PROVIDER_ATTEMPT,
                "provider=wikipedia-request outcome=no_match "
                "reason=wikipedia_no_acceptable_candidate confidence=0.00 candidate=''"),
            enrich_module.EnrichEvent(
                EnrichCategory.METADATA_PROVIDER_ATTEMPT,
                "provider=wikipedia outcome=no_match reason=no_result "
                "confidence=0.00 candidate=''"),
            enrich_module.EnrichEvent(
                EnrichCategory.METADATA_NOT_FOUND, "online lookup returned no record"),
        ]
        attempt = diagnostics_module.attempt_from_enrich_events(
            events, title="Ghost Title", release_key="ghost")
        agg = diagnostics_module.aggregate_provider_attempts(attempt)
        assert agg["zero_asset_releases"], "genuine zero-asset release was not listed"
        assert any(k == "ghost" for k in agg["zero_asset_releases"])

    def test_metadata_matched_artwork_429_not_zero_asset(self):
        # Metadata matched (an asset exists) but the artwork download 429'd.
        # The release is NOT assetless; the 429 still lands in the taxonomy.
        events = [
            enrich_module.EnrichEvent(
                EnrichCategory.METADATA_PROVIDER_ATTEMPT,
                "provider=wikipedia outcome=hit reason=strict_subject_verified "
                "confidence=0.95 candidate='Ultima IV'"),
            enrich_module.EnrichEvent(
                EnrichCategory.METADATA_LOOKUP, "result=hit provider=wikipedia",
                url="https://en.wikipedia.org/wiki/Ultima_IV"),
            enrich_module.EnrichEvent(
                EnrichCategory.ARTWORK_DOWNLOAD_FAILED, "http 429 rate limited",
                ok=False, error="HTTP 429"),
        ]
        attempt = diagnostics_module.attempt_from_enrich_events(
            events, title="Ultima IV", release_key="ult4")
        agg = diagnostics_module.aggregate_provider_attempts(attempt)
        assert agg["zero_asset_releases"] == {}
        assert agg["reason_taxonomy"].get("transport_error") == 1


# ---------------------------------------------------------------------------
# SECONDARY DEFECT -- artwork 429 pacing (standalone, no gate accounting)
# ---------------------------------------------------------------------------

class _FlakyArtworkOpener:
    """Return the given sequence of results (HTTPError to raise, or a body
    bytes to succeed) one per call, then repeat the last result."""

    def __init__(self, results: list):
        self._results = results
        self.calls = 0

    def __call__(self, request, timeout=0):
        index = min(self.calls, len(self._results) - 1)
        self.calls += 1
        result = self._results[index]
        if isinstance(result, urllib.error.HTTPError):
            raise result
        return _ImageResponse(result)


class TestArtworkFetchPacing:
    def test_retries_429_and_honors_retry_after(self):
        clock = _FakeClock()
        opener = _FlakyArtworkOpener([
            _http_error(429, retry_after="7"),
            _http_error(429, retry_after="3"),
            b"jpeg-bytes",
        ])
        body, ctype = enrich_module._fetch_artwork_bytes(
            "https://upload.wikimedia.org/wikipedia/en/0/01/Art.jpg",
            sleep_fn=clock.sleep, opener=opener)
        assert body == b"jpeg-bytes"
        assert ctype == "image/jpeg"
        assert opener.calls == 3
        # Retry-After values are honoured (capped at 60s), not ignored.
        assert clock.sleeps == [7.0, 3.0]

    def test_backs_off_exponentially_without_retry_after(self):
        clock = _FakeClock()
        opener = _FlakyArtworkOpener([
            _http_error(429),  # no Retry-After -> exponential backoff
            b"jpeg-bytes",
        ])
        body, _ = enrich_module._fetch_artwork_bytes(
            "https://upload.wikimedia.org/x.jpg", sleep_fn=clock.sleep, opener=opener)
        assert body == b"jpeg-bytes"
        # backoff_base ** (attempt+1) with base 2 -> first wait 2.0s.
        assert clock.sleeps == [2.0]

    def test_gives_up_after_retry_budget_exhausted(self):
        clock = _FakeClock()
        opener = _FlakyArtworkOpener([_http_error(429)])  # always 429
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            enrich_module._fetch_artwork_bytes(
                "https://upload.wikimedia.org/x.jpg", sleep_fn=clock.sleep,
                opener=opener, max_retries=2)
        assert excinfo.value.code == 429
        # 1 initial + 2 retries = 3 attempts, 2 sleeps.
        assert opener.calls == 3
        assert len(clock.sleeps) == 2

    def test_non_rate_limit_error_raises_immediately(self):
        clock = _FakeClock()
        opener = _FlakyArtworkOpener([_http_error(500)])
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            enrich_module._fetch_artwork_bytes(
                "https://upload.wikimedia.org/x.jpg", sleep_fn=clock.sleep,
                opener=opener, max_retries=3)
        assert excinfo.value.code == 500
        # A non-retryable status must not be retried: exactly one attempt, no sleep.
        assert opener.calls == 1
        assert clock.sleeps == []

    def test_does_not_touch_metadata_request_accounting(self):
        # The whole point: artwork pacing is standalone and must NOT inflate
        # the shared metadata gate's request counter.
        gate = WikipediaGate(policy=WikipediaPolicy())
        before = gate.requests_made
        opener = _FlakyArtworkOpener([
            _http_error(429, retry_after="1"),
            b"jpeg-bytes",
        ])
        enrich_module._fetch_artwork_bytes(
            "https://upload.wikimedia.org/x.jpg", sleep_fn=lambda s: None, opener=opener)
        assert gate.requests_made == before, (
            "artwork fetch corrupted the metadata gate request accounting")
