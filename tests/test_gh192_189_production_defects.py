"""GH-192/#189 production defect regression tests (v0.2.37 base).

Four concrete production defects verified against origin/main @ 23cf199:

  D1  Hall of Light Anubis challenge pages (HTML-entity encoded apostrophe)
      were NOT detected as bot challenges, so a blocked provider was reported
      as a silent ``no_result`` instead of ``bot_challenge``.
  D2  ``_try_provider`` failure evidence named only an outcome class, with no
      HTTP status, exception type, or message -- production runs could not say
      WHY a provider failed.
  D3  ``wikipedia_lookup`` never attempted HTML artwork discovery, so artwork
      enrichment was permanently dead for Wikipedia matches (pageimages API
      returns null for most game pages).
  D4  Applying an online lookup candidate leaked the provider's raw display
      title (with Wikipedia disambiguator) into the library Title column.

These tests are deterministic: no live network access is required. Live
provider behaviour remains blocked by external anti-bot protection; the
runtime path is covered by the classification/diagnostic assertions here.
"""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from amiga_adf_library_builder import metadata as metadata_module
from amiga_adf_library_builder.metadata import (
    MetadataRecord,
    ProviderRequestError,
    _BotChallengeError,
    _text_get,
    lookup_metadata,
    wikipedia_lookup,
)
from amiga_adf_library_builder.paths import resolve_config
from amiga_adf_library_builder.title_norm import _strip_parenthetical_disambiguators


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str, status: int = 200):
        super().__init__(body)
        self._url = url
        self._status = status

    def geturl(self) -> str:
        return self._url

    @property
    def status(self) -> int:
        return self._status

    @property
    def headers(self):
        return None


# ============================================================================
# D1 - bot-challenge detection is entity-tolerant and broadened
# ============================================================================

# Verbatim body shape captured from the live Hall of Light response
# (2026-09-27, tracked in
# /archive01/dumbo/software-engineer/amiga-adf-library-builder/
# 2026-09-27-gh192-real-provider-proof/hol_search.txt).
ANUBIS_ENTITY_BODY = (
    b"<html><head><title>Making sure you&#39;re not a bot!</title></head>"
    b"<body><script id=\"anubis_challenge\" type=\"application/json\">"
    b"{\"rules\":{\"algorithm\":\"fast\",\"difficulty\":2}}</script></body></html>"
)


class TestBotChallengeDetectionD1:
    def test_anubis_html_entity_page_is_detected(self):
        """The live Anubis page encodes the apostrophe as ``&#39;``.

        A literal-string marker check silently missed it, so HOL reported
        ``no_result`` instead of ``bot_challenge``.
        """
        def _opener(request, timeout=0):
            return _FakeResponse(ANUBIS_ENTITY_BODY, request.full_url, status=200)

        with pytest.raises(_BotChallengeError) as excinfo:
            _text_get("https://amiga.abime.net/games/list/?gamename=Hacker+II",
                      opener=_opener)
        assert "bot_challenge" in str(excinfo.value)
        assert excinfo.value.status == 200

    def test_anubis_challenge_script_marker_detected(self):
        """``anubis_challenge`` script tag alone is a sufficient signal."""
        body = b"<html><body><script id=\"anubis_challenge\" type=\"application/json\">{}</script></body></html>"

        def _opener(request, timeout=0):
            return _FakeResponse(body, request.full_url, status=200)

        with pytest.raises(_BotChallengeError):
            _text_get("https://amiga.abime.net/games/list/?gamename=Test", opener=_opener)

    def test_cloudflare_403_body_detected(self):
        """Cloudflare 403 body is classified as bot_challenge, not a bare HTTPError."""
        body = b"<html><head><title>Just a moment...</title></head></html>"

        def _http_error():
            err = urllib.error.HTTPError(
                "https://www.lemonamiga.com/game/vroom", 403, "Forbidden", None, None)
            err.read = MagicMock(return_value=body)
            return err

        def _opener(request, timeout=0):
            raise _http_error()

        with pytest.raises(_BotChallengeError) as excinfo:
            _text_get("https://www.lemonamiga.com/game/vroom", opener=_opener)
        assert excinfo.value.status == 403

    def test_plain_403_without_challenge_markers_still_raises_http_error(self):
        """A genuine 403 with no challenge body remains a request error.

        Guards against over-broad classification that would hide real
        access-control failures behind a bot label.

        (GH-192 production FAILURE 3) A plain 403 is now raised as a
        ``ProviderRequestError`` carrying status=403 and category=http_error,
        rather than a bare ``urllib.error.HTTPError``. The rejection evidence
        (no exception detail, no failure sample) is unchanged.
        """
        def _opener(request, timeout=0):
            err = urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, None)
            err.read = MagicMock(return_value=b"<html><body>nope</body></html>")
            raise err

        with pytest.raises(ProviderRequestError) as excinfo:
            _text_get("https://example.invalid/x", opener=_opener)
        assert excinfo.value.status == 403
        assert excinfo.value.category == "http_error"
        assert excinfo.value.url == "https://example.invalid/x"

    def test_normal_page_is_not_flagged(self):
        """Ordinary provider HTML must never be misread as a challenge."""
        body = b"<html><body><a href='/games/view/hacker-2'>Hacker II</a></body></html>"

        def _opener(request, timeout=0):
            return _FakeResponse(body, request.full_url)

        text, _url = _text_get("https://amiga.abime.net/games/list/?gamename=Hacker+II",
                               opener=_opener)
        assert "Hacker II" in text


# ============================================================================
# D2 - provider failure diagnostics explain WHY
# ============================================================================


def _run_lookup(monkeypatch, tmp_path, patched_name, raising_lookup, **kwargs):
    monkeypatch.setattr(metadata_module, patched_name, raising_lookup)
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    monkeypatch.delenv("MOBYGAMES_API_KEY", raising=False)
    library_root = tmp_path / "library"
    for d in ("data", "catalog", "original"):
        (library_root / d).mkdir(parents=True, exist_ok=True)
    resolve_config(library_root=str(library_root))
    _record, _provider, events = lookup_metadata(
        title="Hacker II: The Doomsday Papers",
        cache_dir=tmp_path / "cache",
        curated_dir=tmp_path / "curated",
        group=None,
        opener=None,
        **kwargs,
    )
    return events


class TestFailureDiagnosticsD2:
    def test_bot_challenge_evidence_includes_status_and_marker(self, monkeypatch, tmp_path):
        """A blocked provider reports status + marker, not just an outcome name."""
        def _raising_lookup(title=None, **kwargs):
            raise _BotChallengeError(
                "bot_challenge: provider anti-bot page detected for "
                "https://amiga.abime.net/games/list/?gamename=X "
                "(marker='anubis_challenge')",
                status=200,
            )

        events = _run_lookup(monkeypatch, tmp_path, "hall_of_light_lookup",
                             _raising_lookup, halloflight_enabled=True)
        hol = [e for e in events if e["provider"] == "hall-of-light"]
        assert len(hol) == 1
        assert hol[0]["reason"] == "bot_challenge"
        evidence = " ".join(hol[0]["evidence"])
        assert "exception_type=_BotChallengeError" in evidence
        assert "http_status=200" in evidence
        assert "anubis_challenge" in evidence

    def test_http_error_evidence_includes_status_code(self, monkeypatch, tmp_path):
        """HTTP 403 from Lemon Amiga must surface the numeric status code."""
        def _raising_lookup(title=None, **kwargs):
            raise urllib.error.HTTPError(
                "https://www.lemonamiga.com/game/x", 403, "Forbidden", None, None)

        events = _run_lookup(monkeypatch, tmp_path, "lemonamiga_lookup",
                             _raising_lookup, lemonamiga_enabled=True)
        lemon = [e for e in events if e["provider"] == "lemon-amiga"]
        assert len(lemon) == 1
        assert lemon[0]["reason"] == "request_error"
        evidence = " ".join(lemon[0]["evidence"])
        assert "http_status=403" in evidence
        assert "HTTPError" in evidence

    def test_network_error_evidence_includes_exception_message(self, monkeypatch, tmp_path):
        """DNS/connection failures must name the underlying exception message."""
        def _raising_lookup(title=None, **kwargs):
            raise urllib.error.URLError("Name or service not known")

        events = _run_lookup(monkeypatch, tmp_path, "hall_of_light_lookup",
                             _raising_lookup, halloflight_enabled=True)
        hol = [e for e in events if e["provider"] == "hall-of-light"]
        evidence = " ".join(hol[0]["evidence"])
        assert hol[0]["reason"] == "request_error"
        assert "URLError" in evidence
        assert "Name or service not known" in evidence

    def test_genuine_no_match_has_no_failure_detail(self, monkeypatch, tmp_path):
        """A provider that simply found nothing is not an error.

        Guards against the diagnostics change inventing failure evidence
        for healthy no-match results.

        (GH-192 production FAILURE 2) A provider returning ``None`` is a
        genuine ``no_result`` — it must NOT be labelled ``candidate_returned``
        with an empty candidate title.
        """
        def _none_lookup(title=None, **kwargs):
            return None

        events = _run_lookup(monkeypatch, tmp_path, "hall_of_light_lookup",
                             _none_lookup, halloflight_enabled=True)
        hol = [e for e in events if e["provider"] == "hall-of-light"]
        assert hol[0]["reason"] == "no_result"
        assert hol[0]["evidence"] == ["no_result"]
        assert hol[0]["canonical_title"] == ""


# ============================================================================
# D3 - Wikipedia artwork fallback
# ============================================================================


WIKI_API_NO_IMAGE = {
    "query": {
        "pages": [{
            "pageid": 2667046,
            "title": "Hacker (video game)",
            "fullurl": "https://en.wikipedia.org/wiki/Hacker_(video_game)",
            "extract": "Hacker is a 1985 video game for the Amiga and Atari ST.",
            "original": None,
            "thumbnail": None,
        }]
    }
}

WIKI_PAGE_HTML = (
    b"<html><head>"
    b'<meta property="og:image" content="https://upload.wikimedia.org/wikipedia/en/1/1f/Hacker_box.jpg">'
    b"</head><body></body></html>"
)


class TestWikipediaArtworkFallbackD3:
    def test_api_image_used_when_present(self, monkeypatch):
        api = {"query": {"pages": [dict(WIKI_API_NO_IMAGE["query"]["pages"][0],
                                         original={"source": "https://upload.wikimedia.org/api.jpg"})]}}

        def _json_get(url, **kwargs):
            return api

        def _discover(page_url, title, **kwargs):
            raise AssertionError("must not fetch HTML when API supplied artwork")

        monkeypatch.setattr(metadata_module, "_json_get", _json_get)
        monkeypatch.setattr(metadata_module, "discover_artwork_from_page", _discover)
        rec = wikipedia_lookup("Hacker")
        assert rec is not None
        assert rec.artwork_url == "https://upload.wikimedia.org/api.jpg"
        assert rec.artwork_provider == "wikipedia"

    def test_html_fallback_used_when_api_image_missing(self, monkeypatch):
        def _json_get(url, **kwargs):
            return WIKI_API_NO_IMAGE

        seen: list[str] = []

        def _discover(page_url, title, **kwargs):
            seen.append(page_url)
            return "https://upload.wikimedia.org/wikipedia/en/1/1f/Hacker_box.jpg", "wikipedia"

        monkeypatch.setattr(metadata_module, "_json_get", _json_get)
        monkeypatch.setattr(metadata_module, "discover_artwork_from_page", _discover)
        rec = wikipedia_lookup("Hacker")
        assert rec is not None
        assert seen == ["https://en.wikipedia.org/wiki/Hacker_(video_game)"]
        assert rec.artwork_url.endswith("Hacker_box.jpg")
        assert rec.artwork_source_url == "https://en.wikipedia.org/wiki/Hacker_(video_game)"

    def test_wikipedia_is_an_approved_artwork_page_host(self):
        assert "en.wikipedia.org" in metadata_module._ALLOWED_ARTWORK_PAGE_HOSTS

    def test_artwork_discovery_failure_does_not_kill_the_metadata_match(
        self, monkeypatch
    ):
        """A failed artwork fetch must not discard accepted Wikipedia metadata."""
        def _json_get(url, **kwargs):
            return WIKI_API_NO_IMAGE

        def _discover(page_url, title, **kwargs):
            raise RuntimeError("artwork page fetch failed")

        monkeypatch.setattr(metadata_module, "_json_get", _json_get)
        monkeypatch.setattr(metadata_module, "discover_artwork_from_page", _discover)
        rec = wikipedia_lookup("Hacker")
        assert rec is not None
        assert rec.provider == "wikipedia"
        assert rec.artwork_url == ""
        assert rec.artwork_provider == ""

    def test_bot_challenge_during_artwork_fallback_propagates(self, monkeypatch):
        """Bot blocking on the artwork page must stay diagnosable."""
        def _json_get(url, **kwargs):
            return WIKI_API_NO_IMAGE

        def _discover(page_url, title, **kwargs):
            raise _BotChallengeError("bot_challenge: anti-bot page", status=403)

        monkeypatch.setattr(metadata_module, "_json_get", _json_get)
        monkeypatch.setattr(metadata_module, "discover_artwork_from_page", _discover)
        with pytest.raises(_BotChallengeError):
            wikipedia_lookup("Hacker")

    def test_end_to_end_html_parse_from_wikipedia_page(self, monkeypatch):
        """Fixture-level proof: real og:image markup is parsed for Wikipedia."""
        def _json_get(url, **kwargs):
            return WIKI_API_NO_IMAGE

        def _opener(request, timeout=0):
            return _FakeResponse(WIKI_PAGE_HTML, request.full_url)

        monkeypatch.setattr(metadata_module, "_json_get", _json_get)
        rec = wikipedia_lookup("Hacker", opener=_opener)
        assert rec is not None
        assert rec.artwork_url == (
            "https://upload.wikimedia.org/wikipedia/en/1/1f/Hacker_box.jpg")
        assert rec.artwork_provider == "wikipedia"


# ============================================================================
# D4 - provider display title must not leak into the library title
# ============================================================================


@pytest.fixture(scope="module")
def _qt_app():
    """Offscreen QApplication for the real GUI apply-path test."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestProviderTitleStrippingD4:
    @pytest.mark.parametrize("raw,expected", [
        ("Hacker (video game)", "Hacker"),
        ("Rocket Ranger (Amiga)", "Rocket Ranger"),
        ("Neuromancer (computer game)", "Neuromancer"),
        ("Bard's Tale III (1986)", "Bard's Tale III"),
        ("Ultima VI: Quest of the Avatar", "Ultima VI: Quest of the Avatar"),
        ("", ""),
    ])
    def test_strip_disambiguators(self, raw, expected):
        assert _strip_parenthetical_disambiguators(raw) == expected

    def test_lookup_candidate_title_is_cleaned_on_apply(self, _qt_app):
        """Applying a provider candidate must not write the disambiguated title.

        Drives the real ``PreviewWidget._apply_lookup_candidate`` path with a
        real ``StagedReleaseEntry`` -- no mocking of the apply logic itself.
        """
        from amiga_adf_library_builder.gui.preview_widget import PreviewWidget
        from amiga_adf_library_builder.models import StagedReleaseEntry, StagedState

        widget = PreviewWidget()

        for title in ("Bard's Tale III", "Ultima V",
                      "Hacker II: The Doomsday Papers"):
            entry = StagedReleaseEntry(
                release_key=f"{title}-key", title=title,
                edition=None, group=None, chipset=None,
            )
            widget._apply_lookup_candidate(
                entry, "online",
                {
                    "status": "found",
                    "kind": "online",
                    "title": f"{title} (video game)",
                    "provider": "wikipedia",
                    "confidence": 0.93,
                },
            )
            assert entry.title == title, (
                f"provider disambiguator leaked into library title: "
                f"{entry.title!r} != {title!r}"
            )
            assert entry.metadata_source == "wikipedia"
            assert entry.curation_state is StagedState.MODIFIED
        widget.close()