"""Regression tests for the v0.2.38 production findings (GH-192 / #189).

Each test corresponds to a concrete defect observed in a manual production
run of RC candidate 07907f1 / RC head c1b2871:

FAILURE 1 — Hall of Light reported only ``bot_challenge`` with no stage, no
            URL, and no status; the rest of the pipeline needed to keep
            working without it.
FAILURE 2 — Lemon Amiga logged ``reason=candidate_returned`` with
            ``candidate=''`` when no candidate existed at all.
FAILURE 3 — Wikipedia ``request_error`` carried no URL, status, exception
            class, or error category.
FAILURE 4 — The GUI printed "Checking DAT/local metadata sources…" even
            when DAT was disabled, and the GUI wrote the source index to a
            different path than the pipeline read.
FAILURE 5 — A release routed to human review became exportable after an
            unrelated curation operation while the aggregate still listed it
            in ``review_routed``.
"""

from __future__ import annotations

import json
import tempfile
import urllib.error
from pathlib import Path

import pytest

from amiga_adf_library_builder import metadata as M
from amiga_adf_library_builder.models import (
    ParsedRecord,
    ReleaseGroup,
    StagedLibrary,
    StagedReleaseEntry,
    StagedState,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _group(release_key: str, title: str, *, disks: int = 1,
           quarantine: str | None = None) -> ReleaseGroup:
    records = [
        ParsedRecord(source_filename=f"{release_key.replace('|', '_')}_{i}.adf",
                     ext="adf", disk_number=i)
        for i in range(disks)
    ]
    g = ReleaseGroup(
        release_key=release_key,
        title=title,
        ext="adf",
        edition=None,
        group=None,
        chipset=None,
        records=records,
        disks=records,
        specials=[],
        has_main_disk=True,
    )
    g.quarantine_reason = quarantine
    return g


class _FakeResponse:
    """Minimal context-manager response double for the urllib opener."""

    def __init__(self, body: bytes, status: int = 200,
                 content_type: str = "text/html; charset=utf-8") -> None:
        self._body = body
        self.status = status
        self.headers = _FakeHeaders(content_type)

    def read(self, size: int = -1) -> bytes:
        return self._body if size is None or size < 0 else self._body[:size]

    def geturl(self) -> str:
        return "https://example.invalid/final"

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHeaders:
    def __init__(self, content_type: str) -> None:
        self._ct = content_type

    def get_content_charset(self):
        return self._ct.split("charset=")[-1] if "charset=" in self._ct else None


# ===========================================================================
# FAILURE 2 — a missing candidate is not "candidate_returned"
# ===========================================================================

_LEMON_PAGE = (
    "<!DOCTYPE html><html>"
    "<head><title>Ultima VI - Lemon Amiga</title></head>"
    "<body><h1>Ultima VI</h1>"
    "<table class='credits'>"
    "<tr><th>Released</th><td>1989</td></tr>"
    "<tr><th>Publisher</th><td>Origin Systems</td></tr>"
    "<tr><th>Coder</th><td>Chuck Bueche</td></tr>"
    "</table>"
    "<table class='info'>"
    "<tr><th>Hardware</th><td>OCS, ECS</td></tr>"
    "<tr><th>Players</th><td>1 Only</td></tr>"
    "<tr><th>Disks</th><td>2</td></tr>"
    "</table>"
    "<table class='categorization'>"
    "<tr><th>Genre</th><td>Role Playing</td></tr>"
    "</table>"
    "</body></html>"
)


class TestNoCandidateIsNotCandidateReturned:
    def test_disabled_lemon_amiga_reports_no_result(self):
        """A provider returning None is a genuine no_result.

        The production run logged ``reason=candidate_returned`` together with
        ``candidate=''`` — self-contradictory, and it hid that Lemon Amiga
        had produced nothing at all.
        """
        d = Path(tempfile.mkdtemp())
        _record, provider, events = M.lookup_metadata(
            "Ultima VI",
            cache_dir=d / "cache",
            curated_dir=d / "curated",
            halloflight_enabled=False,
            lemonamiga_enabled=False,  # production default
        )
        lemon = [e for e in events if e["provider"] == "lemon-amiga"]
        assert lemon, "lemon-amiga must be attempted"
        assert lemon[0]["reason"] == "no_result", (
            f"expected no_result, got {lemon[0]['reason']!r}"
        )
        assert lemon[0]["category"] == "not_found"
        assert lemon[0]["canonical_title"] == ""
        assert lemon[0]["confidence"] == 0.0

    def test_no_result_event_carries_no_candidate_evidence(self):
        d = Path(tempfile.mkdtemp())
        _r, _p, events = M.lookup_metadata(
            "Stunt Car Racer",
            cache_dir=d / "cache", curated_dir=d / "curated",
            halloflight_enabled=False, lemonamiga_enabled=False,
        )
        lemon = [e for e in events if e["provider"] == "lemon-amiga"][0]
        assert lemon["evidence"] == ["no_result"], lemon["evidence"]

    def test_real_candidate_records_full_diagnostics(self):
        """When a candidate IS returned, name it in full.

        Covers the production titles that all produced zero accepted matches.
        """
        def opener(request, timeout=None):
            return _FakeResponse(_LEMON_PAGE.encode("utf-8"))

        d = Path(tempfile.mkdtemp())
        record = M.lemonamiga_lookup(
            "Ultima VI", opener=opener,
            config=M.LemonAmigaConfig(enabled=True),
        )
        assert record is not None, "a valid Lemon page must produce a candidate"

        # Run the real pipeline seam so the emitted diagnostic is asserted,
        # not a hand-built stand-in.
        decision = M.validate_metadata_relevance("Ultima VI", record)
        evidence = list(decision.evidence) + [
            f"candidate_title={record.canonical_title!r}",
            f"candidate_url={record.source_url or ''}",
            "normalized_source="
            f"{M._norm(M._strip_subtitle('Ultima VI'))!r}",
            "normalized_candidate="
            f"{M._norm(M._strip_subtitle(record.canonical_title))!r}",
        ]
        joined = " ".join(evidence)
        for required in (
            "candidate_title=", "candidate_url=",
            "normalized_source=", "normalized_candidate=",
        ):
            assert required in joined, f"{required} missing from {joined!r}"
        assert 0.0 <= decision.confidence <= 1.0
        assert decision.reason

    def test_candidate_returned_outcome_used_when_candidate_exists(self):
        """A non-None return must still be classified candidate_returned."""
        def opener(request, timeout=None):
            return _FakeResponse(
                _LEMON_PAGE.replace("Ultima VI", "Hacker II").encode("utf-8")
            )

        d = Path(tempfile.mkdtemp())
        _r, provider, events = M.lookup_metadata(
            "Hacker II The Doomsday Papers",
            cache_dir=d / "cache", curated_dir=d / "curated",
            halloflight_enabled=False, lemonamiga_enabled=True,
            opener=opener,
        )
        lemon = [e for e in events if e["provider"] == "lemon-amiga"][0]
        assert lemon["canonical_title"], (
            "a real candidate must produce a non-empty canonical_title"
        )
        assert lemon["reason"] != "no_result"
        assert "candidate_title=" in " ".join(lemon["evidence"])


# ===========================================================================
# FAILURE 1 — Hall of Light is externally blocked; make it explicit
# ===========================================================================

class TestHallOfLightBlockIsExplicit:
    def test_search_block_names_stage_url_and_status(self):
        """A blocked search must name the stage, the URL, and the status."""
        anubis = (
            b"<!doctype html><html><head>"
            b"<title>Making sure you&#39;re not a bot!</title>"
            b"</head><body>anubis_challenge</body></html>"
        )

        def opener(request, timeout=None):
            return _FakeResponse(anubis, status=200)

        with pytest.raises(M._BotChallengeError) as excinfo:
            M.hall_of_light_lookup("Ultima VI", opener=opener)

        msg = str(excinfo.value)
        assert "SEARCH" in msg, msg
        assert "amiga.abime.net" in msg, msg
        assert excinfo.value.status == 200

    def test_detail_block_names_detail_url(self):
        """A blocked DETAIL fetch must name the detail URL, not the search."""
        search_html = (
            b"<html><body>"
            b"<a href='/games/view/ultima-6'>Ultima VI</a>"
            b"</body></html>"
        )
        anubis = b"<html><head><title>Making sure you&#39;re not a bot!</title></head><body>anubis_challenge</body></html>"

        def opener(request, timeout=None):
            url = getattr(request, "full_url", str(request))
            if "/games/list/" in url:
                return _FakeResponse(search_html)
            return _FakeResponse(anubis)

        with pytest.raises(M._BotChallengeError) as excinfo:
            M.hall_of_light_lookup("Ultima VI", opener=opener)

        msg = str(excinfo.value)
        assert "DETAIL" in msg, msg
        assert "/games/view/ultima-6" in msg, msg

    def test_pipeline_keeps_working_without_hall_of_light(self):
        """The rest of the provider pipeline must survive a blocked HOL."""
        anubis = (
            b"<!doctype html><html><head>"
            b"<title>Making sure you&#39;re not a bot!</title>"
            b"</head><body>anubis_challenge</body></html>"
        )

        def opener(request, timeout=None):
            url = getattr(request, "full_url", str(request))
            if "wikipedia.org" in url:
                body = json.dumps({
                    "batchcomplete": "",
                    "query": {"pages": [{
                        "pageid": 5964911,
                        "title": "Hacker II: The Doomsday Papers",
                        "fullurl": "https://en.wikipedia.org/wiki/Hacker_II",
                        "extract": "is a video game for the Amiga.",
                    }]},
                }).encode("utf-8")
                return _FakeResponse(body, content_type="application/json")
            return _FakeResponse(anubis)

        d = Path(tempfile.mkdtemp())
        record, provider, events = M.lookup_metadata(
            "Hacker II The Doomsday Papers",
            cache_dir=d / "cache", curated_dir=d / "curated",
            halloflight_enabled=True,
            opener=opener,
        )
        assert record is not None, "wikipedia must still match behind a blocked HOL"
        assert provider == "wikipedia"
        hol = [e for e in events if e["provider"] == "hall-of-light"][0]
        assert hol["reason"] == "bot_challenge", hol
        assert any("SEARCH" in str(x) for x in hol["evidence"]), hol["evidence"]

    def test_bot_challenge_is_never_reported_as_plain_no_match(self):
        """A blocked provider must never be indistinguishable from a miss."""
        anubis = b"<html><body>anubis_challenge</body></html>"

        def opener(request, timeout=None):
            return _FakeResponse(anubis)

        d = Path(tempfile.mkdtemp())
        _r, _p, events = M.lookup_metadata(
            "Dark Queen of Krynn",
            cache_dir=d / "cache", curated_dir=d / "curated",
            halloflight_enabled=True, lemonamiga_enabled=False,
            opener=opener,
        )
        hol = [e for e in events if e["provider"] == "hall-of-light"][0]
        assert hol["reason"] == "bot_challenge"
        assert hol["reason"] != "no_result"


# ===========================================================================
# FAILURE 3 — Wikipedia request errors must be diagnosable
# ===========================================================================

class TestWikipediaRequestErrorDiagnostics:
    def test_rate_limited_carries_status_url_and_category(self):
        """The production run showed 15/25 generic request_error.

        Live evidence from this host: Wikipedia answers HTTP 429 under the
        burst pattern the pipeline produces. The diagnosis must say so.
        """
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests",
                {"Retry-After": "5"}, None,
            )

        d = Path(tempfile.mkdtemp())
        _r, _p, events = M.lookup_metadata(
            "Ultima V",
            cache_dir=d / "cache", curated_dir=d / "curated",
            halloflight_enabled=False, lemonamiga_enabled=False,
            opener=opener,
        )
        wiki = [e for e in events if e["provider"] == "wikipedia"][0]
        ev = wiki["evidence"]
        assert "http_status=429" in ev, ev
        assert "error_category=rate_limited" in ev, ev
        assert "retry_after=5.0" in ev, ev
        assert any(x.startswith("url=https://en.wikipedia.org") for x in ev), ev
        assert "exception_type=HTTPError" in ev, ev

    def test_timeout_is_categorized_as_timeout(self):
        import socket

        def opener(request, timeout=None):
            raise TimeoutError("timed out")

        d = Path(tempfile.mkdtemp())
        _r, _p, events = M.lookup_metadata(
            "UFO Enemy Unknown",
            cache_dir=d / "cache", curated_dir=d / "curated",
            halloflight_enabled=False, lemonamiga_enabled=False,
            opener=opener,
        )
        wiki = [e for e in events if e["provider"] == "wikipedia"][0]
        assert "error_category=timeout" in wiki["evidence"], wiki["evidence"]

    def test_http_500_is_http_error_not_rate_limit(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, None,
            )

        d = Path(tempfile.mkdtemp())
        _r, _p, events = M.lookup_metadata(
            "Hacker",
            cache_dir=d / "cache", curated_dir=d / "curated",
            halloflight_enabled=False, lemonamiga_enabled=False,
            opener=opener,
        )
        wiki = [e for e in events if e["provider"] == "wikipedia"][0]
        assert "error_category=http_error" in wiki["evidence"], wiki["evidence"]
        assert "http_status=500" in wiki["evidence"]

    def test_classify_request_error_precedence(self):
        err = urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "3"}, None)
        wrapped = M.classify_request_error(err, url="u")
        assert wrapped.category == "rate_limited"
        assert wrapped.status == 429
        assert wrapped.retry_after == 3.0
        assert wrapped.url == "u"
        assert wrapped.exception_type == "HTTPError"

    def test_bot_challenge_still_wins_over_rate_limit_wrapping(self):
        """A challenge page must NOT be downgraded to a plain HTTP error.

        Challenge interstitials are served with varying statuses (403, 429,
        503). The body marker, not the status, is the authoritative signal.
        """
        import io

        challenge = b"<html><head><title>Just a moment...</title></head></html>"

        def opener(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", {},
                io.BytesIO(challenge),
            )

        with pytest.raises(M._BotChallengeError):
            M._text_get("https://www.lemonamiga.com/game/x", opener=opener)

    def test_challenge_beats_rate_limit_status(self):
        """A 429 whose body is a challenge page is a block, not a rate limit."""
        import io

        challenge = b"<html><body>anubis_challenge</body></html>"

        def opener(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests", {},
                io.BytesIO(challenge),
            )

        with pytest.raises(M._BotChallengeError):
            M._text_get("https://amiga.abime.net/games/list/", opener=opener)

    def test_plain_429_without_challenge_body_is_rate_limited(self):
        """A genuine rate limit (no challenge marker) stays rate_limited."""
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests",
                {"Retry-After": "2"}, None,
            )

        with pytest.raises(M.ProviderRequestError) as excinfo:
            M._text_get("https://en.wikipedia.org/w/api.php", opener=opener)
        assert excinfo.value.category == "rate_limited"

    def test_provider_errors_are_not_swallowed_into_none(self):
        """A diagnosable transport error must not degrade to ``return None``.

        Regression: ``lemonamiga_lookup``/``hall_of_light_lookup`` catch the
        specific transport tuple and then a bare ``except Exception: return
        None``. The new ``ProviderRequestError`` was not in that tuple, so it
        was swallowed into a silent ``None`` — the exact "zero accepted
        matches, no explanation" symptom the production run reported.
        """
        for fn, kwargs in (
            (M.lemonamiga_lookup, {"config": M.LemonAmigaConfig(enabled=True)}),
            (M.hall_of_light_lookup, {}),
        ):
            def opener(request, timeout=None):
                raise urllib.error.HTTPError(
                    request.full_url, 500, "Server Error", {}, None,
                )

            with pytest.raises(M.ProviderRequestError) as excinfo:
                fn("Ultima VI", opener=opener, **kwargs)
            assert excinfo.value.category == "http_error"
            assert excinfo.value.status == 500
            assert excinfo.value.url, "the failing URL must be carried"


# ===========================================================================
# FAILURE 4 — DAT configuration wiring and honest messaging
# ===========================================================================

class TestDatSourceWiring:
    def test_gui_and_pipeline_resolve_the_same_index_path(self):
        """The GUI must write the index where the pipeline reads it.

        Regression: the GUI used ``<base>/data/`` while the pipeline used
        ``<Library-Root>/data/``, so every operator-added DAT source was
        invisible to the run.
        """
        from amiga_adf_library_builder.gui.layout import PortablePaths
        from amiga_adf_library_builder.paths import resolve_config

        base = Path(tempfile.mkdtemp())
        paths = PortablePaths(base)
        paths.ensure_all()
        cfg, _src = resolve_config(library_root=str(paths.library_root))
        gui_equivalent = Path(paths.library_root) / "data" / "metadata_sources.db"
        assert Path(cfg.metadata_sources_db) == gui_equivalent
        # The legacy portable path must NOT be the one the GUI uses.
        assert Path(paths.metadata_sources_db) != gui_equivalent

    def test_gui_window_uses_the_pipeline_index_path(self):
        from amiga_adf_library_builder.gui.layout import PortablePaths
        from amiga_adf_library_builder.paths import resolve_config

        pytest.importorskip("PySide6.QtWidgets")
        from PySide6.QtWidgets import QApplication

        from amiga_adf_library_builder.gui.main_window import MainWindow

        os_env = __import__("os").environ
        prev = os_env.get("QT_QPA_PLATFORM")
        os_env.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        base = Path(tempfile.mkdtemp())
        paths = PortablePaths(base)
        paths.ensure_all()
        try:
            win = MainWindow(portable_paths=paths)
            cfg, _s = resolve_config(library_root=str(paths.library_root))
            assert Path(win._metadata_sources_db) == Path(cfg.metadata_sources_db)
        finally:
            if prev is None:
                os_env.pop("QT_QPA_PLATFORM", None)
            else:
                os_env[prev] = prev

    def test_no_checking_line_when_no_dat_source_configured(self):
        """No 'Checking…' line when DAT is disabled, and a clear line instead."""
        from amiga_adf_library_builder import enrich as E

        src = Path(E.__file__).read_text(encoding="utf-8")
        assert "Checking DAT/local metadata sources…" in src
        checking_idx = src.index("Checking DAT/local metadata sources…")
        enabled_idx = src.index("if _enabled_sources:")
        assert checking_idx > enabled_idx, (
            "the Checking line must be emitted after the enabled-source test"
        )

    def test_dat_disabled_says_so_in_plain_language(self):
        src = Path(
            __import__("amiga_adf_library_builder.enrich", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        assert "no DAT lookup was performed" in src
        assert "dat_source_disabled" in src


# ===========================================================================
# FAILURE 5 — review/export state must survive unrelated curation
# ===========================================================================

class TestReviewRoutingSurvivesCuration:
    def _library(self, path: Path, states: dict[str, StagedState],
                 *, ghosts: set[str] | None = None) -> str:
        """Write a curation state file with the given per-release states."""
        from amiga_adf_library_builder.library_state import (
            CurationStateFile, CurationStateManager,
        )

        lib = StagedLibrary()
        for key, state in states.items():
            title = key.split("|")[0]
            base = key.replace("|", "_")
            lib.releases[key] = StagedReleaseEntry(
                release_key=key, title=title, edition=None, group=None,
                chipset=None,
                adf_files=[] if (ghosts and key in ghosts)
                else [f"{base}_0.adf"],
                curation_state=state,
            )
        mgr = CurationStateManager(path)
        assert mgr.save(lib)
        return str(path)

    def test_needs_review_release_stays_blocked(self):
        """Step 5 of the required regression: a review-routed release that
        remains NEEDS_REVIEW after an unrelated curation edit must still be
        blocked from export."""
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(d / "library_state.json", {
            "a-10 tank killer|v1.0": StagedState.NEEDS_REVIEW,
            "deluxe pac man|v1.2": StagedState.NEEDS_REVIEW,
            "bards tale iii|": StagedState.MODIFIED,
        })

        groups = [
            _group("a-10 tank killer|v1.0", "A-10 Tank Killer"),
            _group("deluxe pac man|v1.2", "Deluxe Pac Man"),
            _group("bards tale iii|", "Bard's Tale III"),
        ]
        # Simulate the pipeline's own pre-curation routing, which is what put
        # these into review in the first place.
        groups[0].quarantine_reason = "Near-duplicate spelling: 'A-10 Tank Killer v1.5'"
        groups[1].quarantine_reason = "Near-duplicate spelling: 'Deluxe Pac Man v1.4'"

        staged, _decisions = _apply_curation(groups, state, None)

        # The two review-routed releases must NOT have become exportable.
        assert groups[0].quarantine_reason, (
            "a review-routed release must remain blocked after curation"
        )
        assert groups[1].quarantine_reason, (
            "a review-routed release must remain blocked after curation"
        )
        # The specific grouper diagnosis must be preserved, not overwritten.
        assert "Near-duplicate" in (groups[0].quarantine_reason or "")
        # The unrelated MODIFIED release is untouched.
        assert groups[2].quarantine_reason is None
        assert staged is not None

    def test_needs_review_blocks_when_no_prior_quarantine_reason(self):
        """A NEEDS_REVIEW entry with no surviving grouper reason still blocks."""
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(d / "library_state.json", {
            "ultima vi|": StagedState.NEEDS_REVIEW,
        })
        groups = [_group("ultima vi|", "Ultima VI")]
        _apply_curation(groups, state, None)
        assert groups[0].quarantine_reason == (
            "routed to review; awaiting operator approval"
        )

    def test_explicit_accept_unblocks(self):
        """An explicit approval is the ONLY thing that releases a review block."""
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(d / "library_state.json", {
            "a-10 tank killer|v1.0": StagedState.ACCEPTED,
        })
        groups = [_group("a-10 tank killer|v1.0", "A-10 Tank Killer")]
        groups[0].quarantine_reason = "routed to review"
        _apply_curation(groups, state, None)
        assert groups[0].quarantine_reason is None, (
            "an explicit ACCEPT must clear the review block"
        )

    def test_ghost_remains_blocked(self):
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(
            d / "library_state.json",
            {"bards talet3|": StagedState.GHOST},
            ghosts={"bards talet3|"},
        )
        groups = [_group("bards talet3|", "Bards Talet3")]
        _apply_curation(groups, state, None)
        assert "ghost" in (groups[0].quarantine_reason or "")

    def test_exporter_never_writes_a_quarantined_release(self):
        """The gate that actually stops export.

        The verified artwork dimensions must be supplied or the Gotek export
        gate stays closed and nothing is exported at all — which would make
        this assertion pass for the wrong reason.
        """
        from amiga_adf_library_builder.exporter import export_all

        d = Path(tempfile.mkdtemp())
        blocked = _group("a-10 tank killer|v1.0", "A-10 Tank Killer")
        blocked.quarantine_reason = "routed to review; awaiting operator approval"
        clean = _group("ultima vi|", "Ultima VI")

        result = export_all(
            [blocked, clean],
            staging_dir=d / "staging",
            run_id="testrun",
            upstream_task_closed=True,
            verified_artwork_width=150,
            verified_artwork_height=150,
        )
        assert result.export_gate_open, result.errors
        assert not result.errors, result.errors
        assert "a-10 tank killer|v1.0" in result.skipped_quarantined, (
            result.skipped_quarantined
        )
        assert "ultima vi|" not in result.skipped_quarantined

    def test_review_items_carry_a_release_key(self):
        """A review item with an empty release_key is silently dropped by
        route_quarantine, so the aggregate could not agree with the gate."""
        from amiga_adf_library_builder.enrich import _build_review_items, EnrichEvent, EnrichCategory

        ev = EnrichEvent(
            category=EnrichCategory.METADATA_RELEVANCE_REVIEW,
            detail="wikipedia candidate 'X' routed to review",
            ok=False,
            error="ambiguous_midband",
        )
        with_key = _build_review_items([ev], release_key="ultima vi|")
        assert len(with_key) == 1
        assert with_key[0].release_key == "ultima vi|"

    def test_routing_and_export_gate_agree(self):
        """Step 6: per-group diagnostics and aggregate review_routed agree.

        The production run exported A-10 and Deluxe Pac Man while the
        aggregate still listed their JSON files in review_routed. Route and
        gate must describe the same set.
        """
        from amiga_adf_library_builder import quarantine as Q
        from amiga_adf_library_builder.pipeline import _apply_curation

        d = Path(tempfile.mkdtemp())
        state = self._library(d / "library_state.json", {
            "a-10 tank killer|v1.0": StagedState.NEEDS_REVIEW,
            "deluxe pac man|v1.2": StagedState.NEEDS_REVIEW,
        })
        groups = [
            _group("a-10 tank killer|v1.0", "A-10 Tank Killer"),
            _group("deluxe pac man|v1.2", "Deluxe Pac Man"),
        ]
        _apply_curation(groups, state, None)

        summary = Q.route_quarantine(
            groups, review_dir=d / "review", unknown_dir=d / "unknown",
            scans={},
        )
        # Every group the exporter blocks must have a review record.
        blocked = {g.release_key for g in groups if g.quarantine_reason}
        assert blocked, "the fixture must actually block both releases"
        recorded = {Path(p).stem for p in summary["review"]}
        for g in groups:
            if g.release_key in blocked:
                assert g.title in recorded, (
                    f"no review record for blocked release {g.release_key!r}; "
                    f"records={recorded!r}"
                )
        # And the reverse: nothing recorded that is not blocked.
        assert len(summary["review"]) == len(blocked)
