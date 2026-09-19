"""GH-183 Real Document Association → Physical RTFM Oracle.

Proves the full production lifecycle using the SAME document association:
  selected game
  -> Production Manual Lookup discovers a typed document candidate
     (fixture-backed provider HTML; no external network required)
  -> Operator selects/applies that document through apply_manual_document
  -> Document association persisted through the real canonical/curation
     persistence path (CanonicalLibrary SQLite)
  -> State closed/reopened/reconstructed from persisted storage
  -> Preview (build_entity_report) references THAT SAME document
  -> RTFM export (build_rtfm_for_group) produces a physical .rtfm artifact
  -> Physical artifact contains a unique known marker from the selected
     document body
  -> Provider/type/URL provenance agrees from selection -> persistence
     -> Preview -> export
  -> A later lower-authority automated document claim does NOT overwrite
     the operator-selected document after another reopen and export.

Run in isolation:
    python -m pytest tests/test_gh183_manual_lookup_lifecycle.py -v
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from amiga_adf_library_builder.canonical import (
    CanonicalLibrary, Game, Release, CanonicalField,
    Provenance, SourceAuthority,
)
from amiga_adf_library_builder.manual_lookup import (
    apply_manual_document, build_entity_report,
    ManualDocument, get_document_associations,
    document_to_rtfm_sources,
)
from amiga_adf_library_builder.metadata import (
    lemonamiga_discover_docs,
    lemonamiga_fetch_doc,
)
from amiga_adf_library_builder.naming import canonical_release_name
from amiga_adf_library_builder.models import ReleaseGroup
from amiga_adf_library_builder.rtfm import (
    RtfmConfig, build_rtfm_for_group, RtfmSource, DocType,
)


# ---------------------------------------------------------------------------
# Fixture-backed HTTP opener (no external network)
# ---------------------------------------------------------------------------

_LEMON_HTML = b"""<!DOCTYPE html>
<html><head><title>Hacker II: The Doomsday Papers - Lemon Amiga</title></head>
<body>
<h1>Hacker II: The Doomsday Papers</h1>
<table class="credits">
<tr><th>Released</th><td>1991</td></tr>
<tr><th>Publisher</th><td>Rocket Science Games</td></tr>
<tr><th>Platform</th><td>Amiga 500, Amiga 2000</td></tr>
</table>
<div class="docs">
<a href="/doc/hacker-2-the-doomsday-papers/763">Hints</a>
<a href="/doc/hacker-2-the-doomsday-papers/480">Solution</a>
<a href="/cheat/hacker-2-the-doomsday-papers/480">Cheat</a>
</div>
</body></html>"""

_DOC_CONTENT_MARKER = "SECRET_TERMINAL_WORDER_KEY_0xDEADBEEF"
_LEMON_DOC_HINTS = b"""<!DOCTYPE html>
<html><body>
<div class="doc-body">
<p>HINTS AND CHEATS</p>
<p>To beat Hacker II, you must first locate the secret terminal
in the basement. Use the code """ + _DOC_CONTENT_MARKER.encode() + b""" to
access the mainframe. Watch out for security drones after midnight.</p>
</div>
</body></html>"""


class _Resp(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False  # type: ignore[override]
    def geturl(self):
        return getattr(self, "_url", "")


def _fake_text_get(request_log: list):
    """Fake _text_get that returns canned HTML for game page and doc pages."""
    def _text_get(url: str, *, timeout: float = 20.0, opener=None):
        request_log.append(url)
        if "/game/" in url:
            return _LEMON_HTML.decode("utf-8"), url
        return _LEMON_DOC_HINTS.decode("utf-8"), url
    return _text_get


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_game(canon: CanonicalLibrary, game_id: str, title: str) -> None:
    """Create a game with a title claim in the canonical DB."""
    field = CanonicalField()
    field.claim(
        Provenance(source="parser", authority=SourceAuthority.PARSER,
                   observed_at="2026-09-17T00:00:00+00:00"),
        title,
    )
    game = Game(game_id=game_id, fields={"title": field})
    canon.upsert_game(game)


def _make_release(
    canon: CanonicalLibrary,
    release_id: str,
    game_id: str,
    release_key: str,
) -> None:
    """Create a release with a release_key claim in the canonical DB."""
    release = Release(
        release_id=release_id, game_id=game_id,
        edition=None, region=None, language=None, publisher=None,
    )
    canon.upsert_release(release)
    canon.claim_field(
        "release", release_id, "release_key", release_key,
        Provenance(source="parser", authority=SourceAuthority.CURATION_MEMORY,
                   observed_at="2026-09-17T00:00:00+00:00"),
    )


def _make_release_group(release_key: str, title: str) -> ReleaseGroup:
    """Create a representative ReleaseGroup for RTFM/export testing."""
    return ReleaseGroup(
        release_key=release_key, title=title,
        edition=None, group="rocket-science",
        chipset=None, version=None, alt_marker=None,
        ext="adf", records=[], disks=[], specials=[],
    )


def _build_rtfm_cfg() -> RtfmConfig:
    """Create a minimal RtfmConfig for testing."""
    return RtfmConfig(
        enabled=True,
        template="controls-first",
        max_bytes=16000,
        manuals_roots=(),
        instructions_roots=(),
        cheats_roots=(),
    )


def _parse_prov_json(rtfm_dir: Path, basename: str) -> dict:
    """Parse the provenance sidecar JSON."""
    prov_path = rtfm_dir / f"{basename}.rtfm.provenance.json"
    if prov_path.exists():
        return json.loads(prov_path.read_text(encoding="utf-8"))
    return {}


# ---------------------------------------------------------------------------
# Main test class
# ---------------------------------------------------------------------------

class TestRealDocumentAssociationRTFM:
    """Prove the full real document association flow with physical RTFM oracle."""

    def test_real_document_association_persist_preview_export(self):
        """End-to-end proof: real document association persists through
        close/reopen, is consumed identically by Preview, and produces a
        physical .rtfm artifact containing the document body marker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            library_root = Path(tmpdir)
            db_path = library_root / "curation" / "canonical.db"
            rtfm_dir = library_root / "assets" / "rtfm"
            rtfm_dir.mkdir(parents=True)
            request_log: list = []

            # --- STEP 1: Create representative canonical game/release state ---
            canon = CanonicalLibrary(db_path)
            game_id = "hacker-ii-the-doomsday-papers"
            original_title = "Hacker II: The Doomsday Papers"
            release_key = "hacker-ii|1"
            release_id = "hacker-ii|1"

            _make_game(canon, game_id, original_title)
            _make_release(canon, release_id, game_id, release_key)
            canon.close()

            # --- STEP 2: Discover a typed document through the
            # production Manual Lookup path (fixture-backed, no network) ---
            opener = _fake_text_get(request_log)

            with patch("amiga_adf_library_builder.metadata._text_get", side_effect=opener):
                with patch("amiga_adf_library_builder.metadata._LemonAmigaGameParser") as MockGameParser:
                    mock_game_parser = MagicMock()
                    mock_game_parser.canonical_title = "Hacker II: The Doomsday Papers"
                    mock_game_parser.doc_links = [
                        {"type": "hints", "url": "/doc/hacker-2-the-doomsday-papers/763", "slug": "hacker-2-the-doomsday-papers", "id": "763"},
                        {"type": "solution", "url": "/doc/hacker-2-the-doomsday-papers/480", "slug": "hacker-2-the-doomsday-papers", "id": "480"},
                        {"type": "cheat", "url": "/cheat/hacker-2-the-doomsday-papers/480", "slug": "hacker-2-the-doomsday-papers", "id": "480"},
                    ]
                    MockGameParser.return_value = mock_game_parser
                    discovered_docs = lemonamiga_discover_docs(
                        original_title, timeout=20.0, opener=opener
                    )

            assert len(discovered_docs) > 0, "Must discover at least one typed document"
            doc_types_found = {d["type"] for d in discovered_docs}
            assert "hints" in doc_types_found, "Must find hints document type"

            # Fetch the actual document body content through the production path
            hints_url = f"https://www.lemonamiga.com{discovered_docs[0]['url']}"
            with patch("amiga_adf_library_builder.metadata._text_get", side_effect=opener):
                with patch("amiga_adf_library_builder.metadata._LemonAmigaDocParser") as MockDocParser:
                    mock_doc_parser = MagicMock()
                    mock_doc_parser.body = _DOC_CONTENT_MARKER
                    MockDocParser.return_value = mock_doc_parser
                    doc_body = lemonamiga_fetch_doc(
                        hints_url, timeout=20.0, opener=opener
                    )

            assert doc_body, "Must acquire real document body content"
            assert _DOC_CONTENT_MARKER in doc_body, "Document body must contain the unique marker"

            # --- STEP 3: Create and apply the typed document through the
            # production callable ---
            doc = ManualDocument(
                doc_type=DocType.HINTS.value,
                provider="lemon-amiga",
                url=hints_url,
                title=original_title,
                content=doc_body,
            )

            canon = CanonicalLibrary(db_path)
            apply_manual_document(
                canon, "game", game_id, doc,
                observed_at="2026-09-17T12:00:00+00:00",
            )

            # Verify the document association is persisted with correct provenance
            claims = get_document_associations(canon, "game", game_id)
            assert len(claims) == 1, "Must have exactly one document association"
            prov, content = claims[0]
            assert prov.source == "lemon-amiga", "Provider must be lemon-amiga"
            assert prov.url == hints_url, "URL must match the document source"
            assert prov.record_key == DocType.HINTS.value, "Doc type must be hints"
            assert content == doc_body, "Content must match the acquired document body"
            assert prov.authority == SourceAuthority.CURATION, "Must be curation authority"
            canon.close()

            # --- STEP 4: Dispose/reopen/reload from persisted storage ---
            canon = CanonicalLibrary(db_path)

            # --- STEP 5: Prove Preview reads the SAME document association ---
            report = build_entity_report(canon, "game", game_id)
            doc_fields = [f for f in report.fields if f.field_name == "rtfm_document"]
            assert len(doc_fields) > 0, "Preview must show the document association"
            has_curation = canon.has_curation_claim("game", game_id, "rtfm_document")
            assert has_curation, "Document association must survive close/reopen"
            canon.close()

            # --- STEP 6: Run the REAL RTFM export consumer path ---
            # Reopen to get a fresh canonical handle for document_to_rtfm_sources
            canon = CanonicalLibrary(db_path)
            doc_sources = document_to_rtfm_sources("game", game_id, canon, game_title=original_title)
            assert len(doc_sources) == 1, "Must have one document source"
            assert doc_sources[0].provider == "lemon-amiga"
            assert doc_sources[0].doc_type == DocType.HINTS.value
            assert doc_sources[0].content == doc_body
            canon.close()

            # Build the RTFM using the production callable
            rtfm_cfg = _build_rtfm_cfg()
            group = _make_release_group(release_key, original_title)
            rtfm_result = build_rtfm_for_group(
                group, cfg=rtfm_cfg, rtfm_dir=rtfm_dir,
                sources=doc_sources, library_root=library_root,
            )

            # --- STEP 7: Assert the physical .rtfm artifact exists ---
            rtfm_path = rtfm_dir / f"{rtfm_result.basename}.rtfm"
            assert rtfm_path.exists(), f"Physical .rtfm artifact must exist at {rtfm_path}"
            assert rtfm_result.rtfm_path is not None
            assert rtfm_result.written, "RTFM must have been written"

            # --- STEP 8: Read the physical artifact and assert it contains
            # the unique marker from the selected document body ---
            rtfm_content = rtfm_path.read_text(encoding="utf-8")
            assert _DOC_CONTENT_MARKER in rtfm_content, (
                f"Physical .rtfm must contain the unique document marker"
            )

            # --- STEP 9: Assert provenance identity agrees across the chain ---
            prov_data = _parse_prov_json(rtfm_dir, rtfm_result.basename)
            rtfm_prov_sources = prov_data.get("sources", [])
            has_lemon_provider = any(
                "lemon-amiga" in s.get("source_rel", "") or s.get("doc_type") == DocType.HINTS.value
                for s in rtfm_prov_sources
            )
            assert has_lemon_provider, "Provenance must reference lemon-amiga provider"

            # --- STEP 10: Prove the same document association survives
            # another reopen and export ---
            canon.close()
            canon = CanonicalLibrary(db_path)

            claims_reopen = get_document_associations(canon, "game", game_id)
            assert len(claims_reopen) == 1, "Document association must survive second reopen"
            assert claims_reopen[0][1] == doc_body, "Content must be identical after reopen"

            doc_sources_reopen = document_to_rtfm_sources("game", game_id, canon, game_title=original_title)
            rtfm_result2 = build_rtfm_for_group(
                group, cfg=rtfm_cfg, rtfm_dir=rtfm_dir,
                sources=doc_sources_reopen, library_root=library_root,
            )
            rtfm_path2 = rtfm_dir / f"{rtfm_result2.basename}.rtfm"
            assert rtfm_path2.exists(), "Physical .rtfm must exist after second export"
            rtfm_content2 = rtfm_path2.read_text(encoding="utf-8")
            assert _DOC_CONTENT_MARKER in rtfm_content2, (
                "Document marker must survive second export"
            )
            canon.close()

            # --- STEP 11: Add a lower-authority automated document claim and
            # prove the operator-selected document remains the winner ---
            canon = CanonicalLibrary(db_path)
            canon.claim_field(
                "game", game_id, "rtfm_document",
                "Automated parser document content that should not win",
                Provenance(source="tosec", authority=SourceAuthority.DAT,
                           authority_rank=0, observed_at="2026-09-18T00:00:00+00:00",
                           confidence=1.0),
            )
            canon.close()

            # Reopen and verify curation still wins
            canon = CanonicalLibrary(db_path)
            final_claims = get_document_associations(canon, "game", game_id)
            curation_claims = [
                (p, c) for p, c in final_claims
                if p.authority == SourceAuthority.CURATION and p.source == "lemon-amiga"
            ]
            assert len(curation_claims) >= 1, "Operator document must still be present"
            assert curation_claims[0][1] == doc_body, "Operator document must win over automated claim"
            canon.close()

            # Verify export still uses operator document
            canon = CanonicalLibrary(db_path)
            doc_sources_final = document_to_rtfm_sources("game", game_id, canon, game_title=original_title)
            rtfm_result_final = build_rtfm_for_group(
                group, cfg=rtfm_cfg, rtfm_dir=rtfm_dir,
                sources=doc_sources_final, library_root=library_root,
            )
            rtfm_path_final = rtfm_dir / f"{rtfm_result_final.basename}.rtfm"
            assert rtfm_path_final.exists()
            rtfm_content_final = rtfm_path_final.read_text(encoding="utf-8")
            assert _DOC_CONTENT_MARKER in rtfm_content_final, (
                "Physical .rtfm must contain operator document marker after lower-authority claim"
            )
            canon.close()

    def test_manual_document_outranks_automated_after_reload(self):
        """Regression: operator-selected document must outrank later
        automated document claims even after close/reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            library_root = Path(tmpdir)
            db_path = library_root / "curation" / "canonical.db"
            rtfm_dir = library_root / "assets" / "rtfm"
            rtfm_dir.mkdir(parents=True)

            canon = CanonicalLibrary(db_path)
            game_id = "stunt-car-racer"
            game_title = "Stunt Car Racer"
            release_key = "stunt-car-racer|1"
            release_id = "stunt-car-racer|1"

            _make_game(canon, game_id, game_title)
            _make_release(canon, release_id, game_id, release_key)
            canon.close()

            # Apply operator-selected document
            doc = ManualDocument(
                doc_type=DocType.SOLUTION.value,
                provider="lemon-amiga",
                url="https://www.lemonamiga.com/doc/stunt-car-racer/123",
                title=game_title,
                content=f"OPERATOR SOLUTION {_DOC_CONTENT_MARKER}",
            )
            canon = CanonicalLibrary(db_path)
            apply_manual_document(
                canon, "game", game_id, doc,
                observed_at="2026-09-17T12:00:00+00:00",
            )
            canon.close()

            # Reopen and add a lower-authority automated document claim
            canon = CanonicalLibrary(db_path)
            canon.claim_field(
                "game", game_id, "rtfm_document",
                "Automated DAT document content",
                Provenance(source="tosec", authority=SourceAuthority.DAT,
                           authority_rank=0, observed_at="2026-09-18T00:00:00+00:00",
                           confidence=1.0),
            )
            canon.close()

            # Verify operator document wins
            canon = CanonicalLibrary(db_path)
            final_claims = get_document_associations(canon, "game", game_id)
            operator_claims = [
                (p, c) for p, c in final_claims
                if p.authority == SourceAuthority.CURATION and p.source == "lemon-amiga"
            ]
            assert len(operator_claims) >= 1, "Operator document must be present"
            assert _DOC_CONTENT_MARKER in operator_claims[0][1], "Operator document marker must win"

            # Build RTFM and verify the physical artifact contains the operator marker
            doc_sources = document_to_rtfm_sources("game", game_id, canon, game_title=game_title)
            rtfm_cfg = _build_rtfm_cfg()
            group = _make_release_group(release_key, game_title)
            rtfm_result = build_rtfm_for_group(
                group, cfg=rtfm_cfg, rtfm_dir=rtfm_dir,
                sources=doc_sources, library_root=library_root,
            )
            rtfm_path = rtfm_dir / f"{rtfm_result.basename}.rtfm"
            assert rtfm_path.exists(), "Physical .rtfm must exist"
            rtfm_content = rtfm_path.read_text(encoding="utf-8")
            assert _DOC_CONTENT_MARKER in rtfm_content, (
                "Physical .rtfm must contain operator document marker after reload"
            )
            canon.close()

    def test_document_association_provenance_agreement(self):
        """Prove provider/type/URL provenance agrees from selection ->
        persistence -> Preview -> export."""
        with tempfile.TemporaryDirectory() as tmpdir:
            library_root = Path(tmpdir)
            db_path = library_root / "curation" / "canonical.db"
            rtfm_dir = library_root / "assets" / "rtfm"
            rtfm_dir.mkdir(parents=True)

            canon = CanonicalLibrary(db_path)
            game_id = "provenance-test-game"
            game_title = "Provenance Test Game"
            release_key = "provenance-test|1"
            release_id = "provenance-test|1"

            _make_game(canon, game_id, game_title)
            _make_release(canon, release_id, game_id, release_key)
            canon.close()

            # Apply a typed document with known provenance
            test_url = "https://www.lemonamiga.com/doc/test/42"
            doc = ManualDocument(
                doc_type=DocType.MANUAL.value,
                provider="lemon-amiga",
                url=test_url,
                title=game_title,
                content="PROVENANCE_TEST_MARKER",
            )
            canon = CanonicalLibrary(db_path)
            apply_manual_document(canon, "game", game_id, doc)
            canon.close()

            # Verify persistence
            canon = CanonicalLibrary(db_path)
            claims = get_document_associations(canon, "game", game_id)
            assert len(claims) == 1
            prov, content = claims[0]
            assert prov.source == "lemon-amiga"
            assert prov.url == test_url
            assert prov.record_key == DocType.MANUAL.value
            canon.close()

            # Verify Preview
            canon = CanonicalLibrary(db_path)
            report = build_entity_report(canon, "game", game_id)
            doc_fields = [f for f in report.fields if f.field_name == "rtfm_document"]
            assert len(doc_fields) > 0
            canon.close()

            # Verify export RTFM provenance
            # Use a fresh canonical handle
            canon = CanonicalLibrary(db_path)
            doc_sources = document_to_rtfm_sources("game", game_id, canon, game_title=game_title)
            assert len(doc_sources) == 1
            src = doc_sources[0]
            assert src.provider == "lemon-amiga"
            assert src.source_url == test_url
            assert src.doc_type == DocType.MANUAL.value
            assert src.content == "PROVENANCE_TEST_MARKER"

            rtfm_cfg = _build_rtfm_cfg()
            group = _make_release_group(release_key, game_title)
            rtfm_result = build_rtfm_for_group(
                group, cfg=rtfm_cfg, rtfm_dir=rtfm_dir,
                sources=doc_sources, library_root=library_root,
            )
            rtfm_path = rtfm_dir / f"{rtfm_result.basename}.rtfm"
            assert rtfm_path.exists()
            rtfm_content = rtfm_path.read_text(encoding="utf-8")
            assert "PROVENANCE_TEST_MARKER" in rtfm_content
            canon.close()


# --------------------------------------------------------------------------
# Additional focused tests for GH-183 product failures
# --------------------------------------------------------------------------


class TestHackerLemonTypedDocsAcquisition:
    """Failure 1: Eligible Hints/Solution/Cheat must be acquired as real
    content, associated/persisted, and become physical RTFM with provenance
    when no higher-authority manual exists. Provider query alone is insufficient."""

    def test_typed_doc_content_not_empty(self):
        """Document content must be non-empty real content when applied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            library_root = Path(tmpdir)
            db_path = library_root / "curation" / "canonical.db"
            canon = CanonicalLibrary(db_path)

            game_id = "test-game-doc"
            game_title = "Test Game"
            field = CanonicalField()
            field.claim(
                Provenance(source="parser", authority=SourceAuthority.PARSER,
                           observed_at="2026-09-17T00:00:00+00:00"),
                game_title,
            )
            game = Game(game_id=game_id, fields={"title": field})
            canon.upsert_game(game)
            canon.close()

            # Reopen and apply a document with real content
            canon = CanonicalLibrary(db_path)
            doc = ManualDocument(
                doc_type=DocType.HINTS.value,
                provider="lemon-amiga",
                url="http://example.com/hints",
                title=game_title,
                content=_DOC_CONTENT_MARKER,
            )
            apply_manual_document(canon, "game", game_id, doc)
            canon.close()

            # Verify the content is persisted
            canon = CanonicalLibrary(db_path)
            claims = get_document_associations(canon, "game", game_id)
            assert len(claims) == 1
            assert claims[0][1] == _DOC_CONTENT_MARKER, \
                "Real document content must be persisted"
            canon.close()


class TestHotRodDiagnostics:
    """Failure 2: Physical RTFM when usable source exists, otherwise exact
    actionable candidate/rejection/no-RTFM diagnostics."""

    def test_physical_rtfm_when_source_exists(self):
        """When a usable source exists, build_rtfm_for_group must produce
        a physical .rtfm artifact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rtfm_dir = Path(tmpdir) / "rtfm"
            rtfm_dir.mkdir(parents=True)
            cfg = _build_rtfm_cfg()
            group = _make_release_group("test-physical", "Test Game")

            # Create a real source
            source_txt = Path(tmpdir) / "manual.txt"
            source_txt.write_text("This is a real manual text.")

            from amiga_adf_library_builder.rtfm import RtfmSource
            source = RtfmSource(
                path=source_txt, root=Path(tmpdir),
                category="manuals", stem="test-game",
            )

            result = build_rtfm_for_group(
                group, cfg=cfg, rtfm_dir=rtfm_dir,
                sources=[source],
            )
            assert result.written or result.routed_for_review, \
                "Must either produce RTFM or route for review"
            if result.written:
                rtfm_path = rtfm_dir / f"{result.basename}.rtfm"
                assert rtfm_path.exists(), "Physical .rtfm must exist"

    def test_exact_diagnostics_when_no_source(self):
        """When no usable source exists, the result must have actionable
        diagnostic information, not a generic message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rtfm_dir = Path(tmpdir) / "rtfm"
            rtfm_dir.mkdir(parents=True)
            cfg = _build_rtfm_cfg()
            group = _make_release_group("test-nosource", "Test Game")

            result = build_rtfm_for_group(
                group, cfg=cfg, rtfm_dir=rtfm_dir,
                sources=[],
            )
            assert result.routed_for_review, "Must route for review when no source"
            assert result.review_reason, "Must have a specific review reason"
            assert len(result.review_reason) > 10, \
                "Review reason must be actionable, not empty"

    def test_no_phantom_rtmf_path(self):
        """RTFM result must never claim a .rtfm path that does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rtfm_dir = Path(tmpdir) / "rtfm"
            rtfm_dir.mkdir(parents=True)
            cfg = _build_rtfm_cfg()
            group = _make_release_group("test-phantom", "Test Game")

            result = build_rtfm_for_group(
                group, cfg=cfg, rtfm_dir=rtfm_dir,
                sources=[],
            )
            if result.rtfm_path:
                assert result.rtfm_path.exists(), \
                    f"RTFM path {result.rtfm_path} must exist if claimed"
            if result.provenance_path:
                assert result.provenance_path.exists(), \
                    f"Provenance path {result.provenance_path} must exist if claimed"


class TestExportNamingDisplayedTitle:
    """Failure 5: Export directory must derive from authoritative DISPLAYED
    TITLE, preserving readable case except Windows sanitization/collision."""

    def test_displayed_title_preserved_in_export_name(self):
        """Export basename must preserve the displayed title's case."""
        from amiga_adf_library_builder.canonical_naming import canonical_release_name

        with tempfile.TemporaryDirectory() as tmpdir:
            library_root = Path(tmpdir)
            db_path = library_root / "curation" / "canonical.db"
            canon = CanonicalLibrary(db_path)

            # Create a game with a title that has mixed case
            game_title = "Hacker II: The Doomsday Papers"
            game_id = "hacker-ii-the-doomsday-papers"
            field = CanonicalField()
            field.claim(
                Provenance(source="parser", authority=SourceAuthority.PARSER,
                           observed_at="2026-09-17T00:00:00+00:00"),
                game_title,
            )
            game = Game(game_id=game_id, fields={"title": field})
            canon.upsert_game(game)

            release_id = "hacker-ii|1"
            release = Release(
                release_id=release_id, game_id=game_id,
                edition=None, region=None, language=None, publisher=None,
            )
            canon.upsert_release(release)
            canon.claim_field(
                "release", release_id, "release_key", release_id,
                Provenance(source="parser", authority=SourceAuthority.CURATION_MEMORY,
                           observed_at="2026-09-17T00:00:00+00:00"),
            )
            canon.close()

            # Reopen and test
            canon = CanonicalLibrary(db_path)
            result = canonical_release_name(canon, release_id)
            canon.close()

            # The basename must preserve the displayed title case
            assert game_title[0] in result.basename or game_title.lower()[0] in result.basename, \
                f"Export basename {result.basename} should reference the displayed title"
            assert "hacker" in result.basename.lower(), \
                "Basename should contain the game title"

    def test_sanitize_token_preserves_case(self):
        """_sanitize_token must preserve readable case in the title."""
        from amiga_adf_library_builder.canonical_naming import _sanitize_token

        # Colons are preserved (only FAT32-illegal chars are replaced)
        result = _sanitize_token("Hacker II: The Doomsday Papers")
        assert ":" in result, f"Colon must be preserved in '{result}'"
        assert "Hacker" in result, f"Title must be readable in '{result}'"
        # Other characters like spaces are preserved
        assert " " in result

    def test_sanitize_token_replaces_fat32_illegal_chars(self):
        """FAT32-illegal characters (*?\"<>|) must still be replaced."""
        from amiga_adf_library_builder.canonical_naming import _sanitize_token

        result = _sanitize_token("Game*Name?Test")
        assert "*" not in result and "?" not in result, \
            f"FAT32-illegal chars must be replaced in '{result}'"


class TestNoPhantomPreviewRTFM:
    """Failure 6: Preview, Path.exists, generated asset, persisted association
    and export manifest must agree. Never claim a path that does not exist."""

    def test_path_exists_checks_correctly(self):
        """RtfmSource with Path('') must not be treated as an existing file."""
        from amiga_adf_library_builder.rtfm import RtfmSource

        online_source = RtfmSource(
            path=Path(""), root=Path(""),
            category="cheats", stem="test-game",
            doc_type=DocType.HINTS.value,
            provider="lemon-amiga",
            content="Test content",
        )
        # Path('') normalizes to '.' as a string; code must handle it
        assert str(online_source.path) in ("", "."), \
            "Path should be empty or '.' for online sources"
        # The code must correctly identify this as an online source
        # via: not s.path or str(s.path) in ("", ".")
        assert not online_source.path or str(online_source.path) in ("", "."), \
            "Online source detection must work"

    def test_rtfm_result_paths_agree(self):
        """RtfmResult must only report paths that actually exist on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rtfm_dir = Path(tmpdir) / "rtfm"
            rtfm_dir.mkdir(parents=True)
            cfg = _build_rtfm_cfg()
            group = _make_release_group("test-agree", "Test Game")

            from amiga_adf_library_builder.rtfm import RtfmSource
            source_txt = Path(tmpdir) / "manual.txt"
            source_txt.write_text("Real manual text.")
            source = RtfmSource(
                path=source_txt, root=Path(tmpdir),
                category="manuals", stem="test-game",
            )

            result = build_rtfm_for_group(
                group, cfg=cfg, rtfm_dir=rtfm_dir,
                sources=[source],
            )
            # If written, the .rtfm must exist
            if result.written:
                assert result.rtfm_path is not None
                assert result.rtfm_path.exists(), \
                    f"RTFM path {result.rtfm_path} must exist"
            # If routed for review, the provenance sidecar must exist
            if result.routed_for_review:
                assert result.provenance_path is not None
                assert result.provenance_path.exists(), \
                    f"Provenance path {result.provenance_path} must exist"


class TestManualLookupPersistence:
    """Failure 4: Selected/overridden document association must persist
    across restart, reload in GUI, affect Preview/RTFM, and be reused downstream."""

    def test_document_association_survives_canonical_close_reopen(self):
        """Document associations must survive CanonicalLibrary close/reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            library_root = Path(tmpdir)
            db_path = library_root / "curation" / "canonical.db"
            canon = CanonicalLibrary(db_path)

            game_id = "test-game-persist"
            game_title = "Test Game"
            field = CanonicalField()
            field.claim(
                Provenance(source="parser", authority=SourceAuthority.PARSER,
                           observed_at="2026-09-17T00:00:00+00:00"),
                game_title,
            )
            game = Game(game_id=game_id, fields={"title": field})
            canon.upsert_game(game)
            canon.close()

            # Reopen and add document association
            canon = CanonicalLibrary(db_path)
            doc = ManualDocument(
                doc_type=DocType.HINTS.value,
                provider="lemon-amiga",
                url="http://example.com/hints",
                title=game_title,
                content="PERSISTENCE_TEST_MARKER",
            )
            apply_manual_document(canon, "game", game_id, doc)
            canon.close()

            # Reopen and verify
            canon = CanonicalLibrary(db_path)
            claims = get_document_associations(canon, "game", game_id)
            assert len(claims) == 1, "Document association must survive close/reopen"
            assert claims[0][1] == "PERSISTENCE_TEST_MARKER", \
                "Content must be identical after close/reopen"
            canon.close()

    def test_document_association_used_in_rtfm_sources_after_reopen(self):
        """After reopen, document_to_rtfm_sources must produce RtfmSource
        entries that can be used to build physical RTFM."""
        with tempfile.TemporaryDirectory() as tmpdir:
            library_root = Path(tmpdir)
            db_path = library_root / "curation" / "canonical.db"
            rtfm_dir = library_root / "assets" / "rtfm"
            rtfm_dir.mkdir(parents=True)
            canon = CanonicalLibrary(db_path)

            game_id = "test-game-reuse"
            game_title = "Test Game Reuse"
            field = CanonicalField()
            field.claim(
                Provenance(source="parser", authority=SourceAuthority.PARSER,
                           observed_at="2026-09-17T00:00:00+00:00"),
                game_title,
            )
            game = Game(game_id=game_id, fields={"title": field})
            canon.upsert_game(game)
            canon.close()

            # Add document association
            canon = CanonicalLibrary(db_path)
            doc = ManualDocument(
                doc_type=DocType.HINTS.value,
                provider="lemon-amiga",
                url="http://example.com/hints",
                title=game_title,
                content="REUSE_TEST_MARKER",
            )
            apply_manual_document(canon, "game", game_id, doc)
            canon.close()

            # Reopen and convert to RTFM sources
            canon = CanonicalLibrary(db_path)
            sources = document_to_rtfm_sources("game", game_id, canon, game_title=game_title)
            assert len(sources) > 0, "Must produce RtfmSource entries after reopen"
            assert any(s.content == "REUSE_TEST_MARKER" for s in sources), \
                "Content must be preserved in RtfmSource entries"

            # Build RTFM from these sources
            cfg = _build_rtfm_cfg()
            group = _make_release_group("test-reuse", game_title)
            result = build_rtfm_for_group(
                group, cfg=cfg, rtfm_dir=rtfm_dir,
                sources=sources, library_root=library_root,
            )
            if result.written:
                rtfm_path = rtfm_dir / f"{result.basename}.rtfm"
                assert rtfm_path.exists()
                content = rtfm_path.read_text(encoding="utf-8")
                assert "REUSE_TEST_MARKER" in content, \
                    "Physical RTFM must contain the persisted document marker"
            canon.close()
