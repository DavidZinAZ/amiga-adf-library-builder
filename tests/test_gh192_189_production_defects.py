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
