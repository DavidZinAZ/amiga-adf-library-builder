"""Issue #5 — PDF / scanned-image RTFM source extraction (synthetic, offline).

ALL fixtures are generated programmatically inside the test run — NO real
corpus data, NO private paths, NO network. Covers:

  * PDF/JPG/JPEG/PNG discovery under category roots (confinement preserved).
  * Native-text PDF extraction WITHOUT OCR (Tesseract absent on this host).
  * Scanned (image-only) PDF -> unavailable / needs_ocr (no crash, no fiction).
  * Standalone JPG/JPEG/PNG -> unavailable / needs_ocr (no crash).
  * Mixed PDF (native + scanned pages) preserves native text, marks scanned.
  * Empty/unreadable input fails safely (routes to review, no crash).
  * Duplicate-source suppression (txt + pdf same manual -> no duplicate RTFM).
  * Provenance / per-page recording (method + root-relative path captured).
  * Originals remain byte-identical before/after extraction.
  * Deterministic output (same input -> identical text + provenance).
  * Existing RTFM behavior unchanged (doc sources compose via the .txt path).

The Tesseract binary is NOT installed in this environment. OCR paths therefore
assert ``needs_ocr`` / ``unavailable`` rather than decoded text — exactly the
graceful-degradation contract.
"""

from __future__ import annotations

import io
import sys
import hashlib
from pathlib import Path

import pytest
from PIL import Image
import fitz  # type: ignore  # noqa: E402  (used by the rasterization-bound regression tests below)

# Force the worktree's source tree to win over the editable main-repo install.
_WT = Path(__file__).resolve().parents[1] / "src"
if str(_WT) not in sys.path:
    sys.path.insert(0, str(_WT))

from amiga_adf_library_builder import rtfm as rc  # noqa: E402
from amiga_adf_library_builder import rtfm_docs  # noqa: E402
from amiga_adf_library_builder.rtfm_docs import (  # noqa: E402
    ExtractionResult,
    PageProvenance,
    RtfmDocsConfig,
    extract_image_text,
    extract_pdf_text,
)
from amiga_adf_library_builder.grouper import group_records  # noqa: E402
from amiga_adf_library_builder.models import ReleaseGroup  # noqa: E402
from amiga_adf_library_builder.parser import parse_filename  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic fixture builders
# ---------------------------------------------------------------------------


def _native_pdf(path: Path, text: str) -> None:
    """Write a PDF whose pages carry real (native) text."""
    path.write_bytes(_make_native_pdf_bytes(text))


def _make_native_pdf_bytes(text: str) -> bytes:
    """Build a PDF with real (extractable) native text using pymupdf.

    Each blank-line-separated paragraph becomes its own page with an inserted
    text block, so extraction returns deterministic, verbatim content.
    """
    import fitz  # type: ignore

    doc = fitz.open()
    for para in text.split("\n\n"):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 720), para, fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_scanned_pdf_bytes() -> bytes:
    """A PDF with NO native text (just a blank raster page)."""
    import fitz  # type: ignore

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # Draw a blank white rectangle: no extractable text at all.
    page.draw_rect(page.rect, color=(1, 1, 1), fill=(1, 1, 1))
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_mixed_pdf_bytes(native_text: str) -> bytes:
    """Page 0 = native text; page 1 = image-only (no native text)."""
    import fitz  # type: ignore

    doc = fitz.open()
    # Native page.
    p0 = doc.new_page(width=612, height=792)
    p0.insert_text((72, 720), native_text, fontsize=12)
    # Scanned page (raster only, no text).
    p1 = doc.new_page(width=612, height=792)
    p1.draw_rect(p1.rect, color=(1, 1, 1), fill=(1, 1, 1))
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_text_image_bytes(text: str) -> bytes:
    """Render ``text`` into a white PNG (OCR path — empty here w/o Tesseract)."""
    img = Image.new("RGB", (600, 200), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _manual_group(title: str, source_filename: str | None = None) -> ReleaseGroup:
    src_name = source_filename or f"{title} (Disk 1 of 1)"
    rec = parse_filename(f"{src_name}.adf")
    rec.disk_number = 1
    return ReleaseGroup(
        release_key=title.lower(),
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
        specials=[],
        has_main_disk=True,
        is_complete=True,
    )


def _cfg(roots: dict[str, Path]) -> rc.RtfmConfig:
    return rc.RtfmConfig(
        enabled=True,
        manuals_roots=(str(roots["manuals"]),) if "manuals" in roots else (),
        instructions_roots=(str(roots["instructions"]),) if "instructions" in roots else (),
        cheats_roots=(str(roots["cheats"]),) if "cheats" in roots else (),
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_pdf_and_image_discovery(tmp_path):
    root = tmp_path / "manuals"
    (root / "doc").mkdir(parents=True)
    (root / "doc" / "Example Space Tactics.pdf").write_bytes(_make_native_pdf_bytes("Boot the game."))
    (root / "doc" / "Synthetic Quest III.png").write_bytes(_make_text_image_bytes("press start"))
    cfg = _cfg({"manuals": root})
    srcs = rc.discover_sources(cfg)
    suffixes = sorted(s.path.suffix.lower() for s in srcs)
    assert suffixes == [".pdf", ".png"]
    assert all(s.category == rc.CATEGORY_MANUALS for s in srcs)


def test_discovery_rejects_unknown_suffix(tmp_path):
    root = tmp_path / "manuals"
    root.mkdir(parents=True)
    (root / "x.rtf").write_bytes(b"nope")
    (root / "y.pdf").write_bytes(_make_native_pdf_bytes("ok"))
    cfg = _cfg({"manuals": root})
    srcs = rc.discover_sources(cfg)
    assert [s.path.suffix.lower() for s in srcs] == [".pdf"]


def test_suffix_constants_exposed():
    assert rc.PDF_SUFFIXES == frozenset({".pdf"})
    assert rc.IMAGE_SUFFIXES == frozenset({".jpg", ".jpeg", ".png"})
    assert rc.DOC_SUFFIXES == rc.PDF_SUFFIXES | rc.IMAGE_SUFFIXES


# ---------------------------------------------------------------------------
# PDF extraction (Tesseract absent -> native only, or unavailable)
# ---------------------------------------------------------------------------


def test_native_pdf_extracts_without_ocr(tmp_path):
    pdf = tmp_path / "m.pdf"
    text = "CONTROLS: use the joystick to steer the ship and press fire to shoot."
    data = _make_native_pdf_bytes(text)
    pdf.write_bytes(data)
    res = extract_pdf_text(pdf)
    assert not res.needs_ocr
    assert res.confidence == "high"
    assert res.text.strip() == text
    assert res.pages[0].method == "native_text"


def test_scanned_pdf_routes_unavailable_no_crash(tmp_path):
    pdf = tmp_path / "scanned.pdf"
    pdf.write_bytes(_make_scanned_pdf_bytes())
    res = extract_pdf_text(pdf)
    # No native text + no Tesseract => unavailable, never fabricated.
    assert res.text.strip() == ""
    assert res.confidence == "unavailable"
    assert res.needs_ocr is True
    assert all(p.method == "unavailable" for p in res.pages)


def test_mixed_pdf_preserves_native_marks_scanned(tmp_path):
    pdf = tmp_path / "mixed.pdf"
    pdf.write_bytes(_make_mixed_pdf_bytes(
        "GETTING STARTED\n\nInsert the disk and power on the machine."
    ))
    res = extract_pdf_text(pdf)
    # Native page text is preserved; scanned page is unavailable.
    assert "GETTING STARTED" in res.text
    assert "Insert the disk and power on the machine." in res.text
    methods = [p.method for p in res.pages]
    assert "native_text" in methods
    assert "unavailable" in methods
    # No fabricated OCR content.
    assert res.needs_ocr is True


def test_extract_pdf_empty_file_routes_unavailable(tmp_path):
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"")
    res = extract_pdf_text(pdf)
    assert res.confidence == "unavailable"
    assert res.text == ""


def test_extract_pdf_size_cap_routes_unavailable(tmp_path):
    pdf = tmp_path / "big.pdf"
    pdf.write_bytes(_make_native_pdf_bytes("x" * 40))
    res = extract_pdf_text(pdf, cfg=RtfmDocsConfig(max_bytes=1))
    assert res.confidence == "unavailable"
    assert "size cap" in res.reason


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------


def test_image_routes_unavailable_no_crash(tmp_path):
    for suf, b in (
        (".png", _make_text_image_bytes("press start")),
        (".jpg", _make_text_image_bytes("jump")),
        (".jpeg", _make_text_image_bytes("run")),
    ):
        img = tmp_path / f"src{suf}"
        img.write_bytes(b)
        res = extract_image_text(img)
        assert res.text.strip() == ""
        assert res.confidence == "unavailable"
        assert res.needs_ocr is True
        assert res.source_kind == "image"


def test_extract_image_empty_file_routes_unavailable(tmp_path):
    img = tmp_path / "empty.png"
    img.write_bytes(b"")
    res = extract_image_text(img)
    assert res.confidence == "unavailable"
    assert res.text == ""


# ---------------------------------------------------------------------------
# Composition load integration (via the existing .txt path)
# ---------------------------------------------------------------------------


def test_native_pdf_composes_into_rtfm(tmp_path):
    root = tmp_path / "instructions"
    root.mkdir()
    pdf = root / "Example Space Tactics.pdf"
    pdf.write_bytes(_make_native_pdf_bytes(
        "CONTROLS\n\nFire: button one to launch the primary weapon."
    ))
    g = _manual_group("Example Space Tactics", source_filename="x")
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    sections, prov, order, skipped = rc._compose_sections(srcs, g, cfg=cfg)
    # Native PDF text landed verbatim in [CONTROLS].
    assert rc.MARKER_CONTROLS in sections
    assert "Fire: button one to launch the primary weapon." in sections[rc.MARKER_CONTROLS]
    assert not skipped
    kinds = [p.kind for p in prov]
    assert any(k.startswith("pdf:native_text") for k in kinds)


def test_scanned_pdf_skipped_routes_for_review(tmp_path):
    root = tmp_path / "instructions"
    root.mkdir()
    pdf = root / "Example Space Tactics.pdf"
    pdf.write_bytes(_make_scanned_pdf_bytes())
    g = _manual_group("Example Space Tactics", source_filename="x")
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    sections, prov, order, skipped = rc._compose_sections(srcs, g, cfg=cfg)
    # No fabricated content composed; source recorded as skipped.
    assert rc.MARKER_CONTROLS not in sections
    assert any("extraction unavailable" in s for s in skipped)
    assert any(p.kind == "pdf" and p.extraction_method == "pdf:unavailable" for p in prov)


def test_duplicate_suppression_txt_over_pdf(tmp_path):
    root = tmp_path / "manuals"
    root.mkdir(parents=True)
    (root / "Example Space Tactics.txt").write_bytes(b"CONTROLS\n\nFire: button 1")
    (root / "Example Space Tactics.pdf").write_bytes(_make_native_pdf_bytes(
        "CONTROLS\n\nFire: button one to launch the primary weapon."
    ))
    g = _manual_group("Example Space Tactics", source_filename="x")
    cfg = _cfg({"manuals": root})
    srcs = rc.discover_sources(cfg)
    sections, prov, order, skipped = rc._compose_sections(srcs, g, cfg=cfg)
    # Only ONE contribution to [GETTING STARTED] — no duplicate manual text.
    contributing = [p for p in prov if p.sections]
    assert len(contributing) == 1
    # The PDF was suppressed (recorded) in favor of the .txt source.
    deduped = [p for p in prov if p.deduped_by]
    assert deduped
    assert deduped[0].extraction_method == "deduped"
    assert "Example Space Tactics.txt" in deduped[0].deduped_by


# ---------------------------------------------------------------------------
# Provenance / determinism
# ---------------------------------------------------------------------------


def test_provenance_root_relative_path(tmp_path):
    root = tmp_path / "manuals"
    (root / "sub").mkdir(parents=True)
    pdf = root / "sub" / "Example Space Tactics.pdf"
    pdf.write_bytes(_make_native_pdf_bytes("GETTING STARTED\n\nBoot the system now."))
    g = _manual_group("Example Space Tactics", source_filename="x")
    cfg = _cfg({"manuals": root})
    srcs = rc.discover_sources(cfg)
    sections, prov, order, skipped = rc._compose_sections(srcs, g, cfg=cfg)
    assert prov
    p = prov[0]
    assert p.source_rel == "sub/Example Space Tactics.pdf"
    assert p.sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
    # No absolute host path leaks into provenance.
    assert "/" not in p.source_rel or not p.source_rel.startswith("/")


def test_deterministic_extraction_output(tmp_path):
    pdf = tmp_path / "m.pdf"
    data = _make_native_pdf_bytes("CONTROLS\n\nAlpha\n\nBeta")
    pdf.write_bytes(data)
    r1 = extract_pdf_text(pdf)
    r2 = extract_pdf_text(pdf)
    assert r1.text == r2.text
    assert [p.method for p in r1.pages] == [p.method for p in r2.pages]
    assert r1.confidence == r2.confidence


def test_originals_unchanged(tmp_path):
    pdf = tmp_path / "m.pdf"
    text = "CONTROLS: use the joystick to steer and press fire to shoot the enemy."
    data = _make_native_pdf_bytes(text)
    pdf.write_bytes(data)
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    res = extract_pdf_text(pdf)
    after = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert before == after
    assert res.text.strip()  # extraction succeeded (sanity)


# ---------------------------------------------------------------------------
# Optional dependency graceful degradation (simulate missing extras)
# ---------------------------------------------------------------------------


def test_extract_pdf_missing_libs_returns_unavailable(monkeypatch, tmp_path):
    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(_make_native_pdf_bytes("hello"))
    # Force both PDF backends "absent".
    monkeypatch.setattr(rtfm_docs, "_have_pymupdf", lambda: False)
    monkeypatch.setattr(rtfm_docs, "_have_pypdf", lambda: False)
    res = extract_pdf_text(pdf)
    assert res.confidence == "unavailable"
    assert "pypdf/pymupdf" in res.reason


def test_docs_config_from_dict_defaults():
    cfg = RtfmDocsConfig.from_dict(None)
    assert cfg.page_cap == 500
    assert cfg.native_text_min_chars == 32
    cfg2 = RtfmDocsConfig.from_dict({"page_cap": 10, "ocr_required": True})
    assert cfg2.page_cap == 10
    assert cfg2.ocr_required is True


def test_rtfm_config_carries_docs():
    cfg = rc.RtfmConfig.from_dict({"enabled": True, "docs": {"max_bytes": 123}})
    assert isinstance(cfg.docs, RtfmDocsConfig)
    assert cfg.docs.max_bytes == 123
    # defaults when omitted
    cfg2 = rc.RtfmConfig.from_dict({"enabled": True})
    assert cfg2.docs.page_cap == RtfmDocsConfig().page_cap


# ---------------------------------------------------------------------------
# Regression: Issue #5 security defect — bounded PDF rasterization memory
# (memory-exhaustion DoS via zip-bomb-style MediaBox when Tesseract present)
# ---------------------------------------------------------------------------


def _huge_mediabox_pdf_bytes() -> bytes:
    """A tiny (<=32 MB) PDF declaring a 200000x200000 pt page at zoom 2.

    Without the pixel cap this would attempt a 400000 x 400000 px bitmap
    (~480 GB) in pymupdf's get_pixmap. With the cap the page must route to
    `unavailable` WITHOUT ever calling get_pixmap.
    """
    doc = fitz.open()
    page = doc.new_page(width=200000, height=200000)
    # A single small text block, then a drawn white rect to ensure it is a
    # real (non-trivial) page.
    page.insert_text((72, 720), "BOOT", fontsize=12)
    page.draw_rect(page.rect, color=(1, 1, 1), fill=(1, 1, 1))
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _large_native_pdf_bytes() -> bytes:
    """A normal native-text PDF whose page fits comfortably under the cap.

    The inserted text carries well over ``native_text_min_chars`` (32) so the
    page is recognized as native text and extracted directly, proving the
    pixel cap does NOT suppress legitimate extraction.
    """
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        (72, 720),
        "GETTING STARTED\n\nInsert the disk and power on the machine, then follow the on-screen prompts to begin play.",
        fontsize=12,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_ocr_max_pixels_default_is_conservative():
    cfg = RtfmDocsConfig()
    assert cfg.ocr_max_pixels == 4_000_000
    # A default (zoom 2) 2000x2000 page is exactly at the cap; anything larger
    # exceeds it. Guard uses strict `>`.
    assert (2000 * 2000) == cfg.ocr_max_pixels
    assert (2001 * 2001) > cfg.ocr_max_pixels


def test_ocr_max_pixels_wired_through_from_dict():
    cfg = RtfmDocsConfig.from_dict({"ocr_max_pixels": 1_000_000})
    assert cfg.ocr_max_pixels == 1_000_000
    # Invalid (non-int / <=0) falls back to the default, never a broken value.
    assert RtfmDocsConfig.from_dict({"ocr_max_pixels": 0}).ocr_max_pixels == 4_000_000
    assert RtfmDocsConfig.from_dict({"ocr_max_pixels": -5}).ocr_max_pixels == 4_000_000
    assert RtfmDocsConfig.from_dict({"ocr_max_pixels": "nope"}).ocr_max_pixels == 4_000_000


def test_huge_mediabox_pdf_routes_unavailable_no_rasterization(monkeypatch, tmp_path):
    """THE regression test for the DoS defect.

    Simulate the trigger (Tesseract present) and prove a huge-MediaBox PDF
    routes to `unavailable` WITHOUT invoking get_pixmap (no OOM). The spy is
    the authoritative check: if the bound is removed, get_pixmap is called on
    the 400000x400000 page and this test fails (or exhausts memory).
    """
    pdf = tmp_path / "huge.pdf"
    pdf.write_bytes(_huge_mediabox_pdf_bytes())
    # Tiny on disk; the danger is the declared page size, not the file size.
    assert pdf.stat().st_size <= 32 * 1024 * 1024

    monkeypatch.setattr(rtfm_docs, "_tesseract_available", lambda: True)
    get_pixmap_spy = {"calls": 0}

    real_get_pixmap = fitz.Page.get_pixmap

    def _spy(self, *args, **kwargs):
        get_pixmap_spy["calls"] += 1
        return real_get_pixmap(self, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", _spy)
    monkeypatch.setattr(rtfm_docs, "_have_pymupdf", lambda: True)

    res = extract_pdf_text(pdf, cfg=RtfmDocsConfig(ocr_max_pixels=4_000_000))

    # The over-cap page must NOT be rasterized.
    assert get_pixmap_spy["calls"] == 0, "get_pixmap called on over-cap page"
    # It must route to unavailable, flagging OCR as needed.
    assert res.confidence == "unavailable"
    assert res.needs_ocr is True
    assert all(p.method == "unavailable" for p in res.pages)
    assert any(p.note == "page exceeds OCR pixel cap" for p in res.pages)
    # No fabricated text.
    assert res.text.strip() == ""


def test_cap_suppresses_only_over_cap_pages_preserves_native(monkeypatch, tmp_path):
    """The cap must NOT suppress legitimate extraction on a normal page."""
    pdf = tmp_path / "normal.pdf"
    pdf.write_bytes(_large_native_pdf_bytes())
    monkeypatch.setattr(rtfm_docs, "_tesseract_available", lambda: True)
    monkeypatch.setattr(rtfm_docs, "_have_pymupdf", lambda: True)

    res = extract_pdf_text(pdf, cfg=RtfmDocsConfig(ocr_max_pixels=4_000_000))
    # Normal page is well under the cap -> native text extracted normally.
    assert res.confidence == "high"
    assert "GETTING STARTED" in res.text
    assert res.pages[0].method == "native_text"
    assert res.needs_ocr is False


def test_ocr_max_pixels_configurable_tightens_bound(monkeypatch, tmp_path):
    """A tight cap must deflect a normal-sized scanned page without rasterizing.

    The scanned page is 612x792 pt; at zoom 2 that is 1224x1584 = 1_939_584 px.
    With the default cap (4_000_000) it would be rasterized (get_pixmap called);
    with a tight cap (1_000_000) it must route to `unavailable` WITHOUT calling
    get_pixmap. This proves the cap — not the absence of text — is what deflects.
    """
    pdf = tmp_path / "scanned.pdf"
    # No native text -> would enter the OCR branch at the cap check.
    pdf.write_bytes(_make_scanned_pdf_bytes())
    monkeypatch.setattr(rtfm_docs, "_tesseract_available", lambda: True)
    monkeypatch.setattr(rtfm_docs, "_have_pymupdf", lambda: True)

    # Sanity: under the default cap the page WOULD be rasterized (spy fires).
    spy_default = {"calls": 0}
    real_get_pixmap = fitz.Page.get_pixmap

    def _spy_default(self, *args, **kwargs):
        spy_default["calls"] += 1
        return real_get_pixmap(self, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", _spy_default)
    res_default = extract_pdf_text(pdf, cfg=RtfmDocsConfig(ocr_max_pixels=4_000_000))
    assert spy_default["calls"] >= 1, "default cap should permit rasterization"
    assert res_default.needs_ocr is True

    # Under a tight cap the same page must NOT be rasterized.
    spy_tight = {"calls": 0}

    def _spy_tight(self, *args, **kwargs):
        spy_tight["calls"] += 1
        return real_get_pixmap(self, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", _spy_tight)
    res_tight = extract_pdf_text(pdf, cfg=RtfmDocsConfig(ocr_max_pixels=1_000_000))
    assert spy_tight["calls"] == 0, "tight cap must skip rasterization"
    assert res_tight.needs_ocr is True
    assert any(p.note == "page exceeds OCR pixel cap" for p in res_tight.pages)
