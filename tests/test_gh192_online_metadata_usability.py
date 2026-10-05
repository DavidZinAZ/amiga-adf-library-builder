"""Regression tests: polite, bounded, diagnosable Wikipedia metadata access.

GH-192/#189 online-usability pass. Production findings these tests bind:

  F1  Wikipedia answered 10 of 11 back-to-back burst requests with 200 and
      then returned HTTP 429 with ``Retry-After: 48`` — a value the old code
      parsed into diagnostics and then ignored entirely.
  F2  There was no pacing at all: one API request plus one artwork request per
      release, no delay, so a single run exhausted the burst budget.
  F3  Retry was unbounded in spirit and absent in practice: a 429 became a
      terminal ``request_error`` with no backoff and no explanation.
  F4  Cache hits were silent, so "why did this title not hit the network?"
      was unanswerable from the run log.
  F5  A single query form ``"<title>" Amiga video game`` missed real releases
      whose canonical article differs ("Hacker II The Doomsday Papers" ->
      "Hacker II: The Doomsday Papers"), reporting an unexplained
      ``no_result`` after a full round trip.
  F6  Blocked providers (Hall of Light / Lemon Amiga) were re-requested for
      every release, adding a wasted round trip per title and delaying the one
      provider that works.

All tests are deterministic and offline: the clock and the sleep function are
injected, and every network call goes through a fake opener. Hall of Light and
Lemon Amiga remain externally bot-blocked; these tests assert that the
classification and the fail-fast behaviour are correct, never that a bypass
exists.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from amiga_adf_library_builder import metadata as metadata_module
from amiga_adf_library_builder.metadata import (
    MetadataRecord,
    ProviderRequestError,
    load_cached,
    lookup_metadata,
    save_cached,
    wikipedia_lookup,
)
from amiga_adf_library_builder.wikipedia_client import (
    WikipediaGate,
    WikipediaPolicy,
    get_global_gate,
    reset_global_gate,
)
from amiga_adf_library_builder.wikipedia_query import (
    build_query_variants,
    candidate_is_same_subject,
    roman_to_int,
    subject_tokens,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_global_wikipedia_state():
    """Reset the shared pacing gate around every test.

    The pacing gate is intentionally process-wide so pacing is not defeated by
    a per-call-site reset, which means its budgets would otherwise leak between
    tests (a budget-exhausting test silently makes every later test see no
    requests at all). The blocked-provider breaker needs no reset here: it is
    caller-owned, so each ``lookup_metadata`` call without an explicit circuit
    gets a fresh one.
    """
    reset_global_gate()
    yield
    reset_global_gate()

class _FakeClock:
    """Deterministic monotonic clock advanced only by the fake sleep."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += float(seconds)


def _make_gate(policy: WikipediaPolicy | None = None, **kwargs):
    clock = _FakeClock()
    gate = WikipediaGate(policy=policy or WikipediaPolicy(),
                         clock=clock, sleep_fn=clock.sleep, **kwargs)
    return gate, clock


class _Headers:
    def __init__(self, retry_after: str | None = None) -> None:
        self._retry_after = retry_after

    def get_content_type(self):
        return "application/json"

    def get(self, name, default=None):
        if name == "Retry-After":
            return self._retry_after
        return default


class _Response(io.BytesIO):
    def __init__(self, body: bytes, url: str = "https://en.wikipedia.org/",
                 retry_after: str | None = None):
        super().__init__(body)
        self._url = url
        self.headers = _Headers(retry_after)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self._url


class _MessageHeaders(dict):
    """Minimal email.message-like header mapping for HTTPError."""

    def get_content_type(self):
        return "application/json"

    def get_content_charset(self):
        return "utf-8"


def _http_error(status: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = _MessageHeaders()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://en.wikipedia.org/w/api.php", status, "err", headers,
        io.BytesIO(b"rate limited"))


def _api_response(title: str, *, extract: str = "", pageid: int = 7,
                  artwork: str = "") -> bytes:
    page = {
        "pageid": pageid,
        "title": title,
        "extract": extract or f"{title} is an Amiga video game.",
        "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
    }
    if artwork:
        page["original"] = {"source": artwork}
    return json.dumps({"query": {"pages": [page]}}).encode()


def _json_opener(payload_by_search: dict[str, bytes], seen: list[str]):
    """Serve fixture payloads keyed by a substring of the QUERY TEXT.

    The needle is matched against the URL-DECODED ``gsrsearch`` value. Both
    percent-encoding and ``+``-for-space must be decoded: ``unquote`` alone
    leaves ``Rocket+Ranger`` intact, so a needle of "Rocket Ranger" silently
    never matches and every request falls through to the unrelated default.
    """
    def opener(request, timeout=0):
        url = request.full_url
        seen.append(url)
        query = urllib.parse.unquote_plus(url)
        for needle, payload in payload_by_search.items():
            if needle in query:
                return _Response(payload, url)
        return _Response(_api_response("Unrelated Title"), url)
    return opener


# ---------------------------------------------------------------------------
# F1/F2/F3 - throttling, Retry-After, bounded retry
# ---------------------------------------------------------------------------

class TestThrottleAndBackoff:
    def test_minimum_interval_is_enforced_between_requests(self):
        gate, clock = _make_gate(WikipediaPolicy(min_interval_seconds=1.5,
                                                  max_retries=0))
        gate.begin_request()
        gate.note_request()
        gate.begin_request()
        # The second call must have slept the configured interval.
        assert clock.sleeps == [1.5]

    def test_first_request_does_not_sleep(self):
        gate, clock = _make_gate(WikipediaPolicy(min_interval_seconds=1.5))
        gate.begin_request()
        assert clock.sleeps == []

    def test_retry_after_is_respected_and_capped(self):
        policy = WikipediaPolicy(retry_after_cap_seconds=30.0)
        # A server demanding 48 s must be capped by policy.
        assert policy.backoff_for(1, retry_after=48.0) == 30.0
        # A modest value is honoured exactly.
        assert policy.backoff_for(1, retry_after=5.0) == 5.0
        # Absent Retry-After, exponential backoff applies.
        assert policy.backoff_for(1) == 2.0
        assert policy.backoff_for(2) == 4.0

    def test_backoff_is_capped(self):
        policy = WikipediaPolicy(backoff_cap_seconds=8.0, backoff_base_seconds=3.0)
        assert policy.backoff_for(5) == 8.0

    def test_retry_after_can_be_disabled(self):
        policy = WikipediaPolicy(respect_retry_after=False, backoff_base_seconds=2.0)
        assert policy.backoff_for(1, retry_after=48.0) == 2.0

    def test_429_opens_cooldown_instead_of_immediate_retry(self):
        gate, clock = _make_gate(WikipediaPolicy(min_interval_seconds=0.0,
                                                  retry_after_cap_seconds=30.0))
        wait = gate.note_rate_limited(retry_after=48.0)
        assert wait == 30.0
        assert gate.cooldown_remaining() == 30.0
        # The next request must observe the cooldown and wait it out.
        gate.begin_request()
        assert clock.sleeps == [30.0]

    def test_429_triggers_bounded_retry_then_gives_up(self):
        """A persistently rate-limited form retries a BOUNDED number of times.

        The contract is per-query-form tolerance: a rate-limited variant is
        abandoned and the lookup continues with the next form, so one bad form
        cannot abort the whole release. When EVERY form fails this way, the
        transport failure is RE-RAISED (not swallowed into None) so the caller
        keeps the real diagnosis.
        """
        gate, clock = _make_gate(WikipediaPolicy(
            min_interval_seconds=0.0, max_retries=2, backoff_base_seconds=2.0,
            retry_after_cap_seconds=60.0, retries_enabled=True,
            max_rate_limit_events=99))
        seen: list[str] = []

        def opener(request, timeout=0):
            seen.append(request.full_url)
            raise _http_error(429, retry_after="1")

        trail: list[dict] = []
        with pytest.raises(ProviderRequestError) as excinfo:
            wikipedia_lookup("Ultima IV", opener=opener, gate=gate,
                             diagnostics=trail)
        # The diagnosis survives: HTTP 429, category rate_limited, Retry-After.
        assert excinfo.value.status == 429
        assert excinfo.value.category == "rate_limited"
        assert excinfo.value.retry_after == 1.0

        # Only the FIRST rate-limited form retries; every later form gets a
        # single attempt. Retrying all N forms would multiply the request count
        # by ``max_retries + 1`` against an endpoint that has already said it
        # is rate limiting this client.
        n_variants = len(build_query_variants("Ultima IV"))
        assert len(seen) == 3 + (n_variants - 1)
        assert gate.retries_made == 2
        backoffs = [e for e in trail if e.get("decision") == "backoff"]
        assert len(backoffs) == 2
        assert backoffs[0]["retry_after"] == 1.0
        assert any(e.get("decision") == "give_up_retry_budget_exhausted"
                   for e in trail)
        assert all(e["rate_limited"] is True
                   for e in trail if "rate_limited" in e)

    def test_retries_can_be_disabled_for_fail_fast(self):
        gate, _ = _make_gate(WikipediaPolicy(
            min_interval_seconds=0.0, retries_enabled=False,
            max_rate_limit_events=99))
        seen: list[str] = []

        def opener(request, timeout=0):
            seen.append(request.full_url)
            raise _http_error(429, retry_after="1")

        with pytest.raises(ProviderRequestError):
            wikipedia_lookup("Ultima IV", opener=opener, gate=gate)
        # Exactly one attempt per variant: no retry at all.
        assert gate.retries_made == 0
        assert len(seen) == len(build_query_variants("Ultima IV"))

    def test_non_rate_limited_error_is_not_retried(self):
        gate, _ = _make_gate(WikipediaPolicy(
            min_interval_seconds=0.0, max_retries=3, max_rate_limit_events=99))
        seen: list[str] = []

        def opener(request, timeout=0):
            seen.append(request.full_url)
            raise urllib.error.URLError("connection refused")

        trail: list[dict] = []
        with pytest.raises(ProviderRequestError) as excinfo:
            wikipedia_lookup("Ultima IV", opener=opener, gate=gate,
                             diagnostics=trail)
        # One attempt per variant, never repeated.
        assert len(seen) == len(build_query_variants("Ultima IV"))
        assert gate.retries_made == 0
        assert excinfo.value.category in ("network", "http_error")
        assert all(e.get("decision") == "give_up_non_rate_limited"
                   for e in trail if e.get("decision", "").startswith("give_up"))

    def test_request_budget_is_enforced(self):
        gate, _ = _make_gate(WikipediaPolicy(min_interval_seconds=0.0,
                                             max_requests=2))
        seen: list[str] = []

        def opener(request, timeout=0):
            seen.append(request.full_url)
            return _Response(_api_response("Unrelated Thing"), request.full_url)

        # Enough variants to exhaust the budget of two.
        original = metadata_module.build_query_variants
        metadata_module.build_query_variants = lambda title, **kw: [
            metadata_module.QueryVariant(search=f"{title} {i}", label=f"v{i}",
                                         derived_from=title)
            for i in range(10)]
        try:
            record = wikipedia_lookup("Ultima IV", opener=opener, gate=gate)
        finally:
            metadata_module.build_query_variants = original
        assert record is None
        assert len(seen) == 2
        assert gate.budget_exhausted is True

    def test_stats_report_the_cost_of_the_run(self):
        gate, _ = _make_gate(WikipediaPolicy(min_interval_seconds=1.0))
        gate.begin_request()
        gate.note_request()
        gate.begin_request()
        stats = gate.stats()
        assert stats["requests_made"] == 1
        assert stats["waits"] == 1
        assert stats["throttled_seconds"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# F2 - request-count reduction
# ---------------------------------------------------------------------------

class TestRequestCount:
    def test_single_matching_title_costs_one_api_request(self):
        """The common case must not regress into multi-query fan-out.

        Two requests total is correct here: one API query plus one artwork page
        fetch, because the fixture supplies no API artwork. The point of the
        assertion is that only ONE API query was issued.
        """
        seen: list[str] = []
        opener = _json_opener({"Ultima IV": _api_response(
            "Ultima IV: Quest of the Avatar")}, seen)
        record = wikipedia_lookup("Ultima IV", opener=opener, artwork=False)
        assert record is not None
        assert record.canonical_title == "Ultima IV: Quest of the Avatar"
        assert len(seen) == 1

    def test_artwork_is_fetched_only_when_the_api_has_none(self):
        seen: list[str] = []
        opener = _json_opener({"Rocket Ranger": _api_response(
            "Rocket Ranger", artwork="https://upload.wikimedia.org/rr.jpg")}, seen)
        record = wikipedia_lookup("Rocket Ranger", opener=opener, artwork=True)
        assert record is not None
        assert record.artwork_url == "https://upload.wikimedia.org/rr.jpg"
        # Exactly one request: no redundant artwork page fetch, because the API
        # already supplied the image.
        assert len(seen) == 1

    def test_artwork_page_fetch_is_a_second_bounded_request(self):
        """With no API artwork the page fetch is one extra paced request."""
        seen: list[str] = []
        opener = _json_opener({"Rocket Ranger": _api_response("Rocket Ranger")},
                              seen)
        gate = WikipediaGate(policy=WikipediaPolicy(min_interval_seconds=0.0))
        gate.without_sleeping()
        wikipedia_lookup("Rocket Ranger", opener=opener, gate=gate, artwork=True)
        assert len(seen) == 2
        assert seen[1].startswith("https://en.wikipedia.org/wiki/")

    def test_artwork_discovery_can_be_skipped_entirely(self):
        """artwork=False must avoid the second request altogether."""
        seen: list[str] = []
        opener = _json_opener({"Rocket Ranger": _api_response("Rocket Ranger")},
                              seen)
        record = wikipedia_lookup("Rocket Ranger", opener=opener, artwork=False)
        assert record is not None
        assert record.artwork_url == ""
        assert len(seen) == 1

    def test_fallback_form_is_reached_without_retrying_the_first(self):
        """A title needing a second form must not re-send the first one."""
        seen: list[str] = []
        opener = _json_opener({
            "Hacker II: The Doomsday Papers": _api_response(
                "Hacker II: The Doomsday Papers"),
        }, seen)
        record = wikipedia_lookup("Hacker II The Doomsday Papers", opener=opener,
                                  artwork=False)
        assert record is not None
        assert record.canonical_title == "Hacker II: The Doomsday Papers"
        # Two API queries (verbatim miss + colon form hit), no duplicates.
        assert len(seen) == 2
        assert seen[0] != seen[1]


# ---------------------------------------------------------------------------
# F5 - query fallback forms
# ---------------------------------------------------------------------------

class TestQueryVariants:
    def test_verbatim_is_always_first(self):
        variants = build_query_variants("Hacker II The Doomsday Papers")
        assert variants[0].search == "Hacker II The Doomsday Papers"
        assert variants[0].label == "verbatim"

    def test_version_suffix_is_stripped(self):
        searches = [v.search for v in build_query_variants(
            "Ultima IV Quest of the Avatar (v1.1)")]
        assert "Ultima IV Quest of the Avatar" in searches

    def test_paren_qualifier_is_stripped(self):
        searches = [v.search for v in build_query_variants(
            "Stunt Car Racer (1989 video game)")]
        assert "Stunt Car Racer" in searches

    def test_colon_subtitle_form_is_generated(self):
        searches = [v.search for v in build_query_variants(
            "Hacker II The Doomsday Papers")]
        assert "Hacker II: The Doomsday Papers" in searches

    def test_stem_version_form_is_generated(self):
        searches = [v.search for v in build_query_variants(
            "Ultima VI The False Prophet")]
        assert "Ultima VI" in searches

    def test_article_stripped_form_is_generated(self):
        searches = [v.search for v in build_query_variants("The Dark Queen of Krynn")]
        assert "Dark Queen of Krynn" in searches

    def test_variants_are_deduplicated_and_ordered(self):
        """No two variants share the same (label, search text) pair."""
        variants = build_query_variants("Dark Queen of Krynn")
        keys = [(v.label, v.search) for v in variants]
        assert len(keys) == len(set(keys))
        # The verbatim form is always first.
        assert variants[0].label == "verbatim"

    def test_no_roman_variant_for_lowercase_words(self):
        """A lowercase word is not a Roman numeral."""
        assert roman_to_int("mix") is None
        assert roman_to_int("civil") is None
        assert roman_to_int("II") == 2
        assert roman_to_int("VI") == 6

    def test_empty_title_yields_no_variants(self):
        assert build_query_variants("   ") == []

    def test_limit_is_honoured(self):
        assert len(build_query_variants(
            "The Dark Queen of Krynn", limit=1)) == 1


# ---------------------------------------------------------------------------
# Conservative acceptance
# ---------------------------------------------------------------------------

class TestSubjectAcceptance:
    @pytest.mark.parametrize("requested,candidate", [
        ("Hacker II The Doomsday Papers", "Hacker II: The Doomsday Papers"),
        ("Ultima IV", "Ultima IV: Quest of the Avatar"),
        ("Ultima VI The False Prophet", "Ultima VI"),
        ("Neuromancer", "Neuromancer (video game)"),
        ("Rocket Ranger", "Rocket Ranger (video game)"),
        ("Oil Barons", "Oil Barons (video game)"),
        ("Stunt Car Racer", "Stunt Car Racer (1989 video game)"),
        ("The Dark Queen of Krynn", "Dark Queen of Krynn"),
        ("UFO Enemy Unknown", "UFO (video game)"),
        ("Ultima III", "Ultima III Exodus: The Escape from Durdin"),
    ])
    def test_same_subject_is_accepted(self, requested, candidate):
        assert candidate_is_same_subject(requested, candidate,
                                        extract="is an Amiga video game") is True

    @pytest.mark.parametrize("requested,candidate", [
        ("Hacker", "Hacker II: The Doomsday Papers"),
        ("Hacker II", "Hacker"),
        ("Ultima IV", "Ultima V: Warriors of Destiny"),
        ("Ultima VI", "Ultima VII"),
        ("Zarch", "Virus (video game)"),
        ("Oil Barons", "Oil Imperium"),
        ("Lemmings", "Lemmings 2"),
        ("Alien Breed 3D", "Alien Breed 3D II"),
    ])
    def test_different_subject_is_rejected(self, requested, candidate):
        assert candidate_is_same_subject(requested, candidate,
                                        extract="is an Amiga video game") is False

    def test_release_year_difference_is_rejected_when_both_sides_carry_one(self):
        """Two DIFFERENT years on both sides is always a different game."""
        assert candidate_is_same_subject(
            "Doom (1993 video game)", "Doom (2016 video game)") is False

    def test_one_sided_year_is_treated_as_a_disambiguator(self):
        """A year only the ARTICLE carries cannot identify a different game.

        The release string for an Amiga ADF usually carries no year
        ("Doom"), while the Wikipedia article always does. Rejecting on a
        one-sided year would make every such title unmatchable. This is a
        deliberate, documented acceptance of residual ambiguity: the
        downstream ``validate_metadata_relevance`` review routing remains the
        backstop when the operator wants manual confirmation.
        """
        assert candidate_is_same_subject("Doom", "Doom (1993 video game)") is True
        assert candidate_is_same_subject("Doom", "Doom (2016 video game)") is True

    def test_empty_input_is_rejected(self):
        assert candidate_is_same_subject("", "Anything") is False
        assert candidate_is_same_subject("Anything", "") is False

    def test_subject_tokens_keeps_version_separate(self):
        assert subject_tokens("Ultima IV") == ["ultima", "v4"]

    def test_extract_cannot_override_a_title_mismatch(self):
        """A matching intro sentence is not evidence of identity."""
        assert candidate_is_same_subject(
            "Hacker", "Hacker II: The Doomsday Papers",
            extract="is an Amiga video game") is False


# ---------------------------------------------------------------------------
# F5 - known Amiga titles end to end
# ---------------------------------------------------------------------------

#: Real canonical Wikipedia article title for each required known title.
KNOWN_TITLE_ARTICLES = {
    "Hacker II The Doomsday Papers": "Hacker II: The Doomsday Papers",
    "Dark Queen of Krynn": "Dark Queen of Krynn",
    "UFO Enemy Unknown": "UFO (video game)",
    "Ultima III Exodus": "Ultima III: Exodus",
    "Ultima IV Quest of the Avatar": "Ultima IV: Quest of the Avatar",
    "Ultima V Warriors of Destiny": "Ultima V: Warriors of Destiny",
    "Ultima VI The False Prophet": "Ultima VI: The False Prophet",
    "Rocket Ranger": "Rocket Ranger",
    "Stunt Car Racer": "Stunt Car Racer",
    "Neuromancer": "Neuromancer (video game)",
    "Oil Barons": "Oil Barons",
}


class TestKnownAmigaTitles:
    @pytest.mark.parametrize("requested,article", sorted(
        KNOWN_TITLE_ARTICLES.items()))
    def test_known_title_article_is_accepted(self, requested, article):
        assert candidate_is_same_subject(
            requested, article, extract="is an Amiga video game") is True

    @pytest.mark.parametrize("requested,article", sorted(
        KNOWN_TITLE_ARTICLES.items()))
    def test_known_title_resolves_via_lookup(self, requested, article):
        def opener(request, timeout=0):
            return _Response(_api_response(article), request.full_url)
        record = wikipedia_lookup(requested, opener=opener, artwork=False)
        assert record is not None, f"{requested!r} did not resolve"
        assert record.canonical_title == article
        assert record.provider == "wikipedia"
        assert record.confidence > 0.0

    def test_unrelated_article_is_not_accepted_as_a_match(self):
        def opener(request, timeout=0):
            return _Response(_api_response("Total Recall (1990 video game)"),
                             request.full_url)
        record = wikipedia_lookup("Rocket Ranger", opener=opener, artwork=False)
        assert record is None


# ---------------------------------------------------------------------------
# F6 - cache reuse and explicit refresh
# ---------------------------------------------------------------------------

class TestCacheReuse:
    def test_cache_hit_is_reported_and_avoids_the_network(self, tmp_path: Path):
        cache = tmp_path / "cache"
        curated = tmp_path / "curated"
        cache.mkdir()
        save_cached(cache, "Rocket Ranger", MetadataRecord(
            canonical_title="Rocket Ranger", description="Cached.",
            provider="wikipedia", confidence=0.9))

        def opener(request, timeout=0):  # pragma: no cover - must not be called
            raise AssertionError("cache hit must not touch the network")

        record, provider, events = lookup_metadata(
            "Rocket Ranger", cache_dir=cache, curated_dir=curated, opener=opener)
        assert provider == "cache"
        assert record is not None
        assert [ev["reason"] for ev in events] == ["cache_hit"]
        assert "network_requests_avoided=True" in events[0]["evidence"]

    def test_refresh_bypasses_the_cache(self, tmp_path: Path):
        cache = tmp_path / "cache"
        curated = tmp_path / "curated"
        cache.mkdir()
        save_cached(cache, "Rocket Ranger", MetadataRecord(
            canonical_title="Stale", description="Stale.", provider="wikipedia",
            confidence=0.5))
        seen: list[str] = []

        def opener(request, timeout=0):
            seen.append(request.full_url)
            return _Response(_api_response("Rocket Ranger"), request.full_url)

        record, provider, _events = lookup_metadata(
            "Rocket Ranger", cache_dir=cache, curated_dir=curated,
            opener=opener, refresh=True,)
        assert provider == "wikipedia"
        assert record is not None
        assert record.canonical_title == "Rocket Ranger"
        assert seen, "refresh must issue a real request"

    def test_accepted_result_is_cached_and_reused_on_second_call(self, tmp_path: Path):
        cache = tmp_path / "cache"
        curated = tmp_path / "curated"
        seen: list[str] = []

        def opener(request, timeout=0):
            seen.append(request.full_url)
            return _Response(_api_response("Rocket Ranger"), request.full_url)

        first, provider_a, _ = lookup_metadata(
            "Rocket Ranger", cache_dir=cache, curated_dir=curated,
            opener=opener,)
        assert provider_a == "wikipedia"
        count_after_first = len(seen)
        second, provider_b, events = lookup_metadata(
            "Rocket Ranger", cache_dir=cache, curated_dir=curated,
            opener=opener,)
        assert provider_b == "cache"
        assert second is not None and first is not None
        assert second.canonical_title == first.canonical_title
        # No further network traffic for the second call.
        assert len(seen) == count_after_first
        assert any(ev["reason"] == "cache_hit" for ev in events)

    def test_canonical_title_and_artwork_url_are_cached(self, tmp_path: Path):
        cache = tmp_path / "cache"
        curated = tmp_path / "curated"
        artwork = "https://upload.wikimedia.org/rr.jpg"

        def opener(request, timeout=0):
            return _Response(_api_response("Rocket Ranger", artwork=artwork),
                             request.full_url)

        lookup_metadata("Rocket Ranger", cache_dir=cache, curated_dir=curated,
                        opener=opener,)
        cached = load_cached(cache, "Rocket Ranger")
        assert cached is not None
        assert cached.canonical_title == "Rocket Ranger"
        assert cached.artwork_url == artwork
        assert cached.artwork_source_url.endswith("Rocket_Ranger")


# ---------------------------------------------------------------------------
# F6 - blocked providers must not stall the run
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# F6 - diagnostics completeness
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_request_diagnostics_carry_the_full_decision_trail(self):
        seen: list[str] = []
        opener = _json_opener({"Rocket Ranger": _api_response("Rocket Ranger")},
                              seen)
        trail: list[dict] = []
        record = wikipedia_lookup("Rocket Ranger", opener=opener,
                                  diagnostics=trail, artwork=False)
        assert record is not None
        ok_entries = [e for e in trail if e.get("decision") == "ok"]
        assert ok_entries, trail
        entry = ok_entries[0]
        assert entry["query_variant"] == "verbatim"
        assert entry["url"].startswith("https://en.wikipedia.org/w/api.php")
        assert entry["http_status"] == 200
        assert entry["rate_limited"] is False
        assert entry["retry_after"] is None

    def test_rate_limit_diagnostics_carry_status_and_retry_after(self):
        gate, _ = _make_gate(WikipediaPolicy(min_interval_seconds=0.0,
                                             retries_enabled=False))

        def opener(request, timeout=0):
            raise _http_error(429, retry_after="17")

        original = metadata_module.build_query_variants
        metadata_module.build_query_variants = lambda title, **kw: [
            metadata_module.QueryVariant(search=title, label="verbatim",
                                         derived_from=title)]
        trail: list[dict] = []
        try:
            with pytest.raises(ProviderRequestError) as excinfo:
                wikipedia_lookup("Ultima IV", opener=opener, gate=gate,
                                 diagnostics=trail)
        finally:
            metadata_module.build_query_variants = original
        assert excinfo.value.status == 429
        assert excinfo.value.retry_after == 17.0
        entry = trail[-1]
        assert entry["http_status"] == 429
        assert entry["rate_limited"] is True
        assert entry["retry_after"] == 17.0
        assert entry["decision"] == "give_up_retry_budget_exhausted"

    def test_candidate_acceptance_is_recorded(self):
        def opener(request, timeout=0):
            return _Response(_api_response("Rocket Ranger"), request.full_url)
        trail: list[dict] = []
        wikipedia_lookup("Rocket Ranger", opener=opener, diagnostics=trail,
                         artwork=False)
        accepted = [e for e in trail if e.get("decision") == "candidate_accepted"]
        assert accepted
        assert accepted[0]["candidate_title"] == "Rocket Ranger"
        assert accepted[0]["candidate_url"].endswith("Rocket_Ranger")
        assert accepted[0]["confidence"] > 0.0

    def test_rejected_candidate_records_the_no_match_reason(self):
        def opener(request, timeout=0):
            return _Response(_api_response("Total Recall (1990 video game)"),
                             request.full_url)
        trail: list[dict] = []
        record = wikipedia_lookup("Rocket Ranger", opener=opener,
                                  diagnostics=trail, artwork=False)
        assert record is None
        rejects = [e for e in trail
                   if e.get("decision") == "no_acceptable_candidate"]
        assert rejects

    def test_lookup_metadata_exposes_wikipedia_request_events(self, tmp_path: Path):
        cache = tmp_path / "cache"
        curated = tmp_path / "curated"

        def opener(request, timeout=0):
            return _Response(_api_response("Rocket Ranger"), request.full_url)

        _record, _provider, events = lookup_metadata(
            "Rocket Ranger", cache_dir=cache, curated_dir=curated,
            opener=opener,)
        request_events = [ev for ev in events
                          if ev["provider"] == "wikipedia-request"]
        assert request_events
        evidence = " ".join(request_events[0]["evidence"])
        for field in ("query=", "decision=", "status=", "rate_limited=",
                      "retry_after=", "gate=", "url="):
            assert field in evidence, evidence


# ---------------------------------------------------------------------------
# No infinite loops
# ---------------------------------------------------------------------------

class TestBoundedBehaviour:
    def test_transport_failure_is_reraised_when_no_form_answers(self):
        """A total transport failure must NOT degrade into a bare None.

        GH-192 round 2 regression caught by the full chunked suite. The
        multi-form loop initially caught the ProviderRequestError per form and
        returned None, which silently erased the status/category/Retry-After
        diagnosis that `test_gh192_prod_findings.py` requires and that
        production needs to tell a rate limit from a timeout from a dead
        socket. When no query form produced any answer, the first error is
        re-raised.
        """
        def opener(request, timeout=0):
            raise _http_error(429, retry_after="9")

        gate, _ = _make_gate(WikipediaPolicy(min_interval_seconds=0.0,
                                             retries_enabled=False,
                                             max_rate_limit_events=99))
        with pytest.raises(ProviderRequestError) as excinfo:
            wikipedia_lookup("Ultima IV", opener=opener, gate=gate)
        assert excinfo.value.status == 429
        assert excinfo.value.category == "rate_limited"
        assert excinfo.value.retry_after == 9.0

    def test_genuine_no_match_is_not_masked_by_an_error(self):
        """If any form is ANSWERED, a real no-match returns None, not an error."""
        calls: list[str] = []

        def opener(request, timeout=0):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise _http_error(429, retry_after="1")
            return _Response(_api_response("Some Unrelated Article"),
                             request.full_url)

        gate, _ = _make_gate(WikipediaPolicy(min_interval_seconds=0.0,
                                             retries_enabled=False,
                                             max_rate_limit_events=99))
        record = wikipedia_lookup("Ultima IV", opener=opener, gate=gate,
                                  artwork=False)
        # The endpoint answered; there simply is no such article.
        assert record is None

    def test_rate_limit_event_budget_stops_the_variant_loop(self):
        """A run that keeps hitting 429 must stop, not grind every variant."""
        gate, _ = _make_gate(WikipediaPolicy(
            min_interval_seconds=0.0, max_retries=0,
            max_rate_limit_events=2, retry_after_cap_seconds=0.0))
        seen: list[str] = []

        def opener(request, timeout=0):
            seen.append(request.full_url)
            raise _http_error(429, retry_after="0")

        original = metadata_module.build_query_variants
        metadata_module.build_query_variants = lambda title, **kw: [
            metadata_module.QueryVariant(search=f"{title} {i}", label=f"v{i}",
                                         derived_from=title)
            for i in range(50)]
        try:
            with pytest.raises(ProviderRequestError):
                wikipedia_lookup("Ultima IV", opener=opener, gate=gate)
        finally:
            metadata_module.build_query_variants = original
        # Two 429 events tolerated, then the loop stops regardless of the 50
        # available variants. No unbounded hammering.
        assert len(seen) == 2

    def test_global_gate_is_shared_between_calls(self):
        reset_global_gate()
        try:
            first = get_global_gate()
            second = get_global_gate()
            assert first is second
        finally:
            reset_global_gate()

    def test_passing_a_policy_replaces_the_global_gate(self):
        reset_global_gate()
        try:
            policy = WikipediaPolicy(min_interval_seconds=3.0)
            gate = get_global_gate(policy)
            assert gate.policy.min_interval_seconds == 3.0
        finally:
            reset_global_gate()
