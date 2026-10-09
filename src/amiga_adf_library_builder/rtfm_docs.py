"""Issue #5 RTFM PDF / scanned-image text extraction layer (LOCAL, OFFLINE).

This module extracts manual text from PDF and scanned image sources so the
RTFM builder (``rtfm.py``) can fold them into the same deterministic, offline
``.rtfm`` pipeline used for ``.txt`` / ``.rtfm`` sources.

Hard design constraints (see issue #5):

  * LOCAL/OFFLINE ONLY. No network, no cloud OCR, no paid APIs, no AI/LLM.
  * OPTIONAL DEPENDENCIES. The core library stays dependency-free. ``pypdf``,
    ``pymupdf``, ``pillow`` and ``pytesseract`` are imported LAZILY inside the
    function that needs them. If they are absent, extraction returns a clear
    *unavailable* result with a human-readable reason — it never fabricates
    text and never crashes on import.
  * TESSERACT-ABSENT GRACEFUL DEGRADATION. Native PDF text extraction works
    WITHOUT Tesseract. When a page/image has no usable native text and
    Tesseract (the binary) is not installed, the page is marked
    ``unavailable`` / ``needs_ocr`` — never invented.
  * ORIGINALS ARE READ-ONLY. Source files are only ever read. Any rasterization
    happens in a contained temp dir that is removed on exit. We never write to
    the source path.
  * RESOURCE BOUNDS. Inputs are capped by size and page count; OCR runs under a
    bounded timeout so hostile/malformed inputs cannot hang or exhaust memory.
  * DETERMINISTIC. Identical input bytes produce identical extracted text and
    identical per-page provenance.

Extraction methods recorded in provenance:
  * ``native_text`` — text already embedded in the PDF page.
  * ``ocr``        — rasterized, then OCR'd with Tesseract.
  * ``unavailable``— no usable native text and OCR not possible/performed.
  * ``passthrough``— reserved for non-extracted sources (not produced here).

Confidence is reported per page (``high`` | ``low`` | ``unavailable``) and as
an overall verdict (``high`` | ``low`` | ``unavailable``).
"""

from __future__ import annotations

import io
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from .utils import sha256_bytes
from typing import Optional

# --- Configuration -----------------------------------------------------------


@dataclass(frozen=True)
class RtfmDocsConfig:
    """Optional knobs for PDF/image extraction (all safe defaults).

    Read from the ``[rtfm.docs]`` TOML table by ``rtfm.RtfmConfig``. Every knob
    is optional; when unset the value below applies and public behavior is
    unchanged.
    """

    # Reject source files larger than this (bytes) before attempting to open.
    # Defense against decompression-bomb / oversized inputs.
    max_bytes: int = 64 * 1024 * 1024
    # Bound the number of pages we inspect/rasterize (DoS guard).
    page_cap: int = 500
    # Minimum non-whitespace characters for a page to count as "has native
    # text". Below this we treat the page as scanned/empty and try OCR.
    native_text_min_chars: int = 32
    # Document-level OCR fallback for image-only/scanned PDFs when normal
    # extraction yields no usable text. 0 disables it (legacy behavior).
    ocr_fallback_max_pages: int = 500
    # Document-level OCR rasterization zoom.
    ocr_fallback_zoom: float = 2.0
    # Hard cap on rasterized pixels per page during the fallback OCR. Scanned
    # manuals are legitimately large, so this is far above the per-page cap
    # used elsewhere: it only refuses genuinely explosive MediaBoxes.
    ocr_fallback_max_pixels: int = 40_000_000
    # Per-page and whole-document OCR time budgets (seconds).
    ocr_fallback_page_timeout: float = 30.0
    ocr_fallback_total_timeout: float = 900.0
    # Enable the content-addressed OCR text cache.
    ocr_cache_enabled: bool = True
    # If True, missing Tesseract is treated as a hard failure (unavailable with
    # reason) rather than a silent route-to-review. Default False = graceful.
    ocr_required: bool = False
    # Per-OCR-operation timeout (seconds). Bounds time spent in Tesseract.
    ocr_timeout: float = 30.0
    # Rasterization zoom factor for pages/images sent to OCR (higher = more
    # detail, more memory). Kept modest for safety.
    ocr_zoom: float = 2.0
    # HARD CAP on rasterized pixel count for OCR: w = int(rect.width*ocr_zoom),
    # h = int(rect.height*ocr_zoom); if w*h > ocr_max_pixels the page is NOT
    # rasterized (routes to `unavailable`, native-text extraction preserved).
    # Defends against zip-bomb-style MediaBox expansion: pymupdf's get_pixmap
    # imposes no default pixel cap, so a tiny PDF declaring a huge page can
    # otherwise attempt a multi-hundred-GB bitmap (memory-exhaustion DoS).
    ocr_max_pixels: int = 4_000_000

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "RtfmDocsConfig":
        if not data:
            return cls()
        raw = data or {}

        def _int(key, default):
            try:
                v = int(raw.get(key, default))
            except (TypeError, ValueError):
                return default
            return v if v > 0 else default

        def _float(key, default):
            try:
                v = float(raw.get(key, default))
            except (TypeError, ValueError):
                return default
            return v if v > 0 else default

        return cls(
            max_bytes=_int("max_bytes", cls.max_bytes),
            page_cap=_int("page_cap", cls.page_cap),
            native_text_min_chars=_int("native_text_min_chars", cls.native_text_min_chars),
            ocr_required=bool(raw.get("ocr_required", False)),
            ocr_timeout=_float("ocr_timeout", cls.ocr_timeout),
            ocr_zoom=_float("ocr_zoom", cls.ocr_zoom),
            ocr_max_pixels=_int("ocr_max_pixels", cls.ocr_max_pixels),
            ocr_fallback_max_pages=_int(
                "ocr_fallback_max_pages", cls.ocr_fallback_max_pages
            ),
            ocr_fallback_zoom=_float("ocr_fallback_zoom", cls.ocr_fallback_zoom),
            ocr_fallback_max_pixels=_int(
                "ocr_fallback_max_pixels", cls.ocr_fallback_max_pixels
            ),
            ocr_fallback_page_timeout=_float(
                "ocr_fallback_page_timeout", cls.ocr_fallback_page_timeout
            ),
            ocr_fallback_total_timeout=_float(
                "ocr_fallback_total_timeout", cls.ocr_fallback_total_timeout
            ),
            ocr_cache_enabled=bool(raw.get("ocr_cache_enabled", True)),
        )


# --- Result types ------------------------------------------------------------


@dataclass(frozen=True)
class PageProvenance:
    """Per-page extraction record (deterministic, no host paths)."""

    page_index: int
    method: str            # native_text | ocr | unavailable
    confidence: str        # high | low | unavailable
    note: str = ""


@dataclass
class ExtractionResult:
    """Structured outcome of document/image text extraction.

    ``text`` is always normalized (LF endings, trimmed). When extraction is
    empty, ``text`` is ``""`` and callers MUST route for review — never emit it
    as RTFM content.
    """

    text: str                          # normalized LF text ("" when none)
    pages: tuple[PageProvenance, ...]  # per-page provenance, in order
    confidence: str                    # high | low | unavailable
    needs_ocr: bool                    # True when OCR was required but absent
    source_kind: str                   # "pdf" | "image"
    # PDF backend that served (or refused) this extraction: "pymupdf" |
    # "pypdf" | "unavailable (...)". Always populated for PDFs so a failure
    # log can name the backend instead of a generic "failed to decode".
    backend: str = ""
    reason: str = ""                   # why unavailable / what happened
    # Convenience flag: True when no usable text was extracted.
    empty: bool = field(init=False)

    def __post_init__(self):
        # empty = no non-whitespace text extracted.
        self.empty = len(self.text.strip()) == 0


# --- Optional dependency probes (lazy) ---------------------------------------


def _have_pymupdf() -> bool:
    try:
        import fitz  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def _pymupdf_backend() -> str:
    """Name of the PDF backend that would actually serve PDF extraction.

    An import alone does not prove the backend can serve a frozen build:
    PyMuPDF loads its MuPDF C library through a compiled extension plus a
    shared library at runtime, so a PyInstaller bundle can carry the Python
    modules while missing the native pieces. We therefore verify a real
    backend call before reporting the backend as usable.

    Deterministic, side-effect free (in-memory document, no host files).
    """
    if not _have_pymupdf():
        return "unavailable"
    try:
        import fitz  # type: ignore

        doc = fitz.open()
        doc.insert_page(0)
        pages = doc.page_count
        doc.close()
    except Exception as exc:  # native library missing / unpackaged
        return f"unavailable ({type(exc).__name__})"
    if pages == 1:
        return "pymupdf"
    return f"unavailable (unexpected page count {pages})"


def _have_pypdf() -> bool:
    try:
        import pypdf  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def _pypdf_backend() -> str:
    """Name of the pypdf fallback backend, or why it cannot serve PDFs."""
    if not _have_pypdf():
        return "unavailable"
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(b"%PDF-1.4\n"))
        len(reader.pages)
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"
    return "pypdf"


def pdf_backend() -> str:
    """Name of the backend ``extract_pdf_text`` would actually use.

    Returns ``"pymupdf"`` / ``"pypdf"`` when that backend can genuinely serve
    a PDF, or ``"unavailable (...)"`` naming the concrete reason. Used for
    diagnostics so a failure log can state which backend was in play instead
    of a generic "failed to decode".
    """
    pymupdf = _pymupdf_backend()
    if pymupdf == "pymupdf":
        return "pymupdf"
    pypdf = _pypdf_backend()
    if pypdf == "pypdf":
        return "pypdf"
    return f"unavailable (pymupdf: {pymupdf}; pypdf: {pypdf})"


def _have_pillow() -> bool:
    try:
        from PIL import Image  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def _tesseract_available() -> bool:
    """True only when pytesseract import succeeds AND the binary runs.

    We probe the actual binary (``get_tesseract_version``) rather than just
    ``shutil.which`` because pytesseract shells out and the resolution path
    matters; a non-functional binary must be reported as unavailable.
    """
    try:
        import pytesseract  # type: ignore

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


# --- Text normalization ------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Normalize extracted text: LF endings only, no trailing whitespace runs.

    We do NOT reflow or invent content. Paragraph/heading structure carried by
    blank lines is preserved by the page-joining logic in the callers.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse trailing whitespace on each line (keep the line itself).
    lines = [ln.rstrip() for ln in text.split("\n")]
    # Drop a single trailing empty line, then guarantee one trailing newline.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).rstrip()


def _count_non_ws(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


# --- OCR (Tesseract) with timeout --------------------------------------------

#: Extraction-method tag recorded when the bundled document OCR fallback
#: supplied the text (``pdf:document_ocr``). Keeps provenance explicit so a
#: scanned manual is never reported as native text.
OCR_METHOD_TAG = "document_ocr"


def _ocr_pil_image(img, timeout: float) -> Optional[str]:
    """Run Tesseract OCR on a PIL image, bounded by ``timeout`` seconds.

    Returns the recognized text, or ``None`` on failure/timeout. Never raises.
    """
    try:
        import pytesseract  # type: ignore
    except Exception:
        return None

    result: dict[str, Optional[str]] = {"text": None}

    def _run():
        try:
            result["text"] = pytesseract.image_to_string(img) or ""
        except Exception:
            result["text"] = None

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        # Timed out: leave result as None (caller marks unavailable).
        return None
    return result["text"]


# --- PDF extraction ----------------------------------------------------------


def extract_pdf_text(path, cfg: Optional[RtfmDocsConfig] = None) -> ExtractionResult:
    """Extract text from a PDF at ``path`` (read-only, offline, bounded).

    Per page:
      * If the page has usable native text (>= ``native_text_min_chars``), use
        it (method ``native_text``).
      * Otherwise rasterize the page and, IF Tesseract is available, OCR it
        (method ``ocr``); if Tesseract is absent, mark the page
        ``unavailable`` and set ``needs_ocr`` (no fabrication).

    Mixed PDFs (native + scanned pages) are supported at page granularity.

    When the whole document yields no usable text (a scanned manual), the
    bundled offline OCR fallback in :mod:`rtfm_ocr` runs EXACTLY ONCE for the
    document, caches its result by source SHA-256, and that text is returned.
    A PDF with a usable native text layer NEVER pays OCR cost.
    """
    cfg = cfg or RtfmDocsConfig()
    path = Path(path)
    backend = pdf_backend()

    # --- Size guard (before opening). ---
    try:
        size = path.stat().st_size
    except OSError as exc:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=False,
            source_kind="pdf", backend=backend,
            reason=f"cannot stat source: {path.name} ({exc})",
        )
    if size == 0:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=False,
            source_kind="pdf", backend=backend,
            reason="source is empty (0 bytes)",
        )
    if size > cfg.max_bytes:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=False,
            source_kind="pdf", backend=backend,
            reason=f"source exceeds size cap ({size} > {cfg.max_bytes} bytes)",
        )

    tesseract = _tesseract_available()
    if cfg.ocr_required and not tesseract:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=True,
            source_kind="pdf", backend=backend,
            reason="OCR required by config but Tesseract is unavailable",
        )

    # --- Primary path: pymupdf (text + rasterization). ---
    if _pymupdf_backend() == "pymupdf":
        res = _extract_pdf_with_fitz(path, cfg, tesseract, backend)
        return _maybe_ocr_fallback(path, cfg, res)

    # --- Fallback path: pypdf (text only, no raster/OCR). ---
    if _pypdf_backend() == "pypdf":
        res = _extract_pdf_with_pypdf(path, cfg, tesseract, backend)
        return _maybe_ocr_fallback(path, cfg, res)

    # --- No usable PDF backend. Name the concrete reason. ---
    return ExtractionResult(
        text="", pages=(), confidence="unavailable", needs_ocr=False,
        source_kind="pdf", backend=backend,
        reason=f"PDF extraction unavailable: {backend}",
    )


def _maybe_ocr_fallback(
    path: Path, cfg: RtfmDocsConfig, res: ExtractionResult,
) -> ExtractionResult:
    """Run the bundled document OCR fallback ONLY when nothing was extracted.

    ``res`` is the normal-extraction result. If it already carries usable text
    it is returned untouched (normal PDFs never pay OCR cost). Otherwise the
    image-only path in :mod:`rtfm_ocr` runs, and only a successful OCR replaces
    the result — a failed OCR keeps the truthful "no extractable text layer"
    reason, extended with the OCR backend's own reason. Never fabricates.
    """
    if res.text.strip():
        return res
    if cfg.ocr_fallback_max_pages <= 0:
        return res

    from .rtfm_ocr import ocr_pdf_document

    cache_dir: Optional[Path] = None
    if cfg.ocr_cache_enabled:
        cache_dir = ocr_cache_dir()

    native_chars = 0
    for p in res.pages:
        if p.method == "native_text":
            native_chars += 1

    ocr = ocr_pdf_document(
        path,
        page_cap=cfg.ocr_fallback_max_pages,
        max_pixels=cfg.ocr_fallback_max_pixels,
        zoom=cfg.ocr_fallback_zoom,
        page_timeout=cfg.ocr_fallback_page_timeout,
        total_timeout=cfg.ocr_fallback_total_timeout,
        cache_dir=cache_dir,
        name=path.name,
        native_chars=native_chars,
    )
    if not ocr.ok:
        # Truthful no-output: keep the original exact diagnostic and append the
        # OCR reason (concise, no traceback).
        detail = f"{res.reason}; OCR fallback: {ocr.reason or 'no text'}"
        return ExtractionResult(
            text="",
            pages=res.pages,
            confidence="unavailable",
            needs_ocr=True,
            source_kind="pdf",
            backend=res.backend,
            reason=detail,
        )

    text = _normalize_text(ocr.text)
    return ExtractionResult(
        text=text,
        pages=res.pages,
        confidence="low",
        needs_ocr=False,
        source_kind="pdf",
        backend=res.backend,
        reason=f"pdf:{OCR_METHOD_TAG}",
    )


def ocr_cache_dir() -> Optional[Path]:
    """Directory for the content-addressed OCR text cache.

    Never inside a manual/source tree: the application cache dir when it can be
    resolved, otherwise ``None`` (cache disabled, extraction still works).
    """
    try:
        from .paths import _xdg_cache_path

        base = Path(_xdg_cache_path()) / "amiga-adf-library-builder"
        return base / "rtfm-ocr"
    except Exception:
        return None


def _extract_pdf_with_fitz(
    path: Path, cfg: RtfmDocsConfig, tesseract: bool, backend: str,
) -> ExtractionResult:
    import fitz  # type: ignore

    page_texts: list[str] = []
    pages: list[PageProvenance] = []
    needs_ocr = False
    any_native = False
    any_ocr = False

    try:
        doc = fitz.open(path)
    except Exception as exc:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=needs_ocr,
            source_kind="pdf", backend=backend,
            reason=f"PDF open/parse failed ({backend}): {type(exc).__name__}",
        )

    try:
        total = min(len(doc), cfg.page_cap)
        for idx in range(total):
            page = doc.load_page(idx)
            raw = page.get_text() or ""
            norm = _normalize_text(raw)
            if _count_non_ws(norm) >= cfg.native_text_min_chars:
                page_texts.append(norm)
                pages.append(PageProvenance(
                    page_index=idx, method="native_text", confidence="high",
                ))
                any_native = True
                continue
            # Low/empty native text -> attempt OCR via rasterization.
            if tesseract:
                # Bounds the zip-bomb expansion path: pixmap allocation
                # scales with the declared MediaBox x zoom, and pymupdf
                # applies no default pixel cap. Refuse to rasterize pages
                # that would exceed ocr_max_pixels; native-text extraction
                # on other pages is unaffected.
                mat = fitz.Matrix(cfg.ocr_zoom, cfg.ocr_zoom)
                w = int(page.rect.width * cfg.ocr_zoom)
                h = int(page.rect.height * cfg.ocr_zoom)
                if w * h > cfg.ocr_max_pixels:
                    pages.append(PageProvenance(
                        page_index=idx, method="unavailable",
                        confidence="unavailable",
                        note="page exceeds OCR pixel cap",
                    ))
                    needs_ocr = True
                    continue
                try:
                    pix = page.get_pixmap(matrix=mat)
                    from PIL import Image  # type: ignore

                    img = Image.frombytes(
                        "RGB", (pix.width, pix.height), pix.samples
                    )
                    ocr = _ocr_pil_image(img, cfg.ocr_timeout)
                except Exception:
                    ocr = None
                if ocr is not None:
                    ocr_norm = _normalize_text(ocr)
                    if _count_non_ws(ocr_norm) > 0:
                        page_texts.append(ocr_norm)
                        pages.append(PageProvenance(
                            page_index=idx, method="ocr", confidence="low",
                        ))
                        any_ocr = True
                        continue
                pages.append(PageProvenance(
                    page_index=idx, method="unavailable",
                    confidence="unavailable",
                    note="ocr produced no text",
                ))
                needs_ocr = True
            else:
                pages.append(PageProvenance(
                    page_index=idx, method="unavailable",
                    confidence="unavailable",
                    note="no native text; Tesseract unavailable",
                ))
                needs_ocr = True
    finally:
        doc.close()

    joined = _normalize_text("\n\n".join(page_texts))
    return _finalize_pdf_result(joined, pages, needs_ocr, any_native, any_ocr, backend)


def _extract_pdf_with_pypdf(
    path: Path, cfg: RtfmDocsConfig, tesseract: bool, backend: str,
) -> ExtractionResult:
    import pypdf  # type: ignore

    page_texts: list[str] = []
    pages: list[PageProvenance] = []
    needs_ocr = False
    any_native = False

    try:
        reader = pypdf.PdfReader(str(path))
        total = min(len(reader.pages), cfg.page_cap)
        for idx in range(total):
            try:
                raw = reader.pages[idx].extract_text() or ""
            except Exception:
                raw = ""
            norm = _normalize_text(raw)
            if _count_non_ws(norm) >= cfg.native_text_min_chars:
                page_texts.append(norm)
                pages.append(PageProvenance(
                    page_index=idx, method="native_text", confidence="high",
                ))
                any_native = True
            else:
                # pypdf fallback cannot rasterize; OCR only if Tesseract is
                # present AND we could rasterize... but pypdf has no raster, so
                # any scanned page is unavailable here.
                pages.append(PageProvenance(
                    page_index=idx, method="unavailable",
                    confidence="unavailable",
                    note=(
                        "no native text; pypdf-only path cannot rasterize"
                        + ("" if tesseract else "; Tesseract unavailable")
                    ),
                ))
                needs_ocr = True
    except Exception as exc:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=needs_ocr,
            source_kind="pdf", backend=backend,
            reason=f"PDF open/parse failed ({backend}): {type(exc).__name__}",
        )

    joined = _normalize_text("\n\n".join(page_texts))
    return _finalize_pdf_result(
        joined, pages, needs_ocr, any_native, any_ocr=False, backend=backend,
    )


def _finalize_pdf_result(
    joined: str, pages: list[PageProvenance], needs_ocr: bool,
    any_native: bool, any_ocr: bool, backend: str,
) -> ExtractionResult:
    if not joined.strip():
        kind = "unavailable"
        confidence = "unavailable"
        # No text at all: state exactly why. An all-scanned PDF with no OCR
        # engine must read as "no extractable text layer", not "decode failed".
        if not any_native:
            reason = "PDF contains no extractable text layer"
        else:
            reason = "PDF text layer present but yielded no usable characters"
    elif any_ocr and any_native:
        kind = "mixed"
        confidence = "low"
        reason = f"pdf:{kind}"
    elif any_ocr:
        kind = "page_ocr"
        confidence = "low"
        reason = f"pdf:{kind}"
    else:
        kind = "native_text"
        confidence = "high"
        reason = f"pdf:{kind}"
    return ExtractionResult(
        text=joined,
        pages=tuple(pages),
        confidence=confidence,
        needs_ocr=needs_ocr,
        source_kind="pdf",
        backend=backend,
        reason=reason,
    )


# --- Standalone image extraction ---------------------------------------------


def extract_image_text(path, cfg: Optional[RtfmDocsConfig] = None) -> ExtractionResult:
    """Extract text from a standalone JPG/JPEG/PNG image (read-only, offline).

    If Tesseract is available, OCR the image (method ``ocr``). Otherwise mark
    ``unavailable`` / ``needs_ocr`` (no fabrication, no crash).
    """
    cfg = cfg or RtfmDocsConfig()
    path = Path(path)

    try:
        size = path.stat().st_size
    except OSError as exc:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=False,
            source_kind="image", reason=f"cannot stat source: {path.name} ({exc})",
        )
    if size == 0:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=False,
            source_kind="image", reason="source is empty (0 bytes)",
        )
    if size > cfg.max_bytes:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=False,
            source_kind="image",
            reason=f"source exceeds size cap ({size} > {cfg.max_bytes} bytes)",
        )

    if not _have_pillow():
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=False,
            source_kind="image",
            backend="unavailable (pillow: unavailable)",
            reason="image extraction unavailable (pillow not installed)",
        )

    tesseract = _tesseract_available()
    if cfg.ocr_required and not tesseract:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=True,
            source_kind="image",
            reason="OCR required by config but Tesseract is unavailable",
        )
    try:
        from PIL import Image  # type: ignore

        img = Image.open(path)
        img.load()  # force read; validates the image without writing
    except Exception as exc:
        return ExtractionResult(
            text="", pages=(), confidence="unavailable", needs_ocr=False,
            source_kind="image",
            backend="pillow",
            reason=f"image open failed: {type(exc).__name__}",
        )

    if not tesseract:
        # No Tesseract binary: try the bundled offline OCR engine before
        # giving up (packaged Windows builds have no Tesseract at all).
        from .rtfm_ocr import ocr_pil_image

        bundled = ocr_pil_image(img, cfg.ocr_timeout)
        if bundled is not None and bundled.strip():
            norm = _normalize_text(bundled)
            return ExtractionResult(
                text=norm,
                pages=(PageProvenance(
                    page_index=0, method="ocr", confidence="low",
                ),),
                confidence="low", needs_ocr=False, source_kind="image",
                backend=f"pillow (OCR engine: {OCR_METHOD_TAG})",
                reason=f"image:{OCR_METHOD_TAG}",
            )
        return ExtractionResult(
            text="", pages=(PageProvenance(
                page_index=0, method="unavailable", confidence="unavailable",
                note="Tesseract unavailable; bundled OCR produced no text"
                if bundled is not None else "Tesseract unavailable",
            ),),
            confidence="unavailable", needs_ocr=True, source_kind="image",
            backend="pillow (OCR engine: tesseract unavailable)",
            reason="image is not OCR'd: no OCR engine available",
        )

    text = _ocr_pil_image(img, cfg.ocr_timeout)
    if text is None:
        return ExtractionResult(
            text="", pages=(PageProvenance(
                page_index=0, method="unavailable", confidence="unavailable",
                note="ocr failed or timed out",
            ),),
            confidence="unavailable", needs_ocr=False, source_kind="image",
            backend="pillow (OCR engine: tesseract)",
            reason="image OCR produced no text",
        )

    norm = _normalize_text(text)
    if not norm.strip():
        return ExtractionResult(
            text="", pages=(PageProvenance(
                page_index=0, method="unavailable", confidence="unavailable",
                note="ocr produced no text",
            ),),
            confidence="unavailable", needs_ocr=False, source_kind="image",
            backend="pillow (OCR engine: tesseract)",
            reason="image OCR produced no text",
        )

    return ExtractionResult(
        text=norm,
        pages=(PageProvenance(page_index=0, method="ocr", confidence="low"),),
        confidence="low", needs_ocr=False, source_kind="image",
        backend="pillow (OCR engine: tesseract)",
        reason="image:ocr",
    )


# --- Determinism helper (used by tests + provenance) -------------------------
