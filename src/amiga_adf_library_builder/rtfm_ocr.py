"""Bounded offline OCR fallback for image-only / scanned PDF manuals.

The existing ``extract_pdf_text`` path already handles native-text PDFs and
per-page Tesseract OCR. This module adds the missing production piece for the
packaged Windows GUI, where no Tesseract binary exists:

  * a bundled, fully offline OCR engine (RapidOCR: OpenCV + ONNX Runtime with
    the models shipped inside the wheel — no external binaries, no network);
  * a document-level fallback that runs ONLY when normal extraction yields no
    usable text;
  * a content-addressed OCR text cache keyed by SHA-256 of the source PDF so
    the same scanned manual used by several releases is OCR'd exactly once.

Everything degrades truthfully: a missing engine or a failed OCR returns an
empty result with a concise reason, never fabricated text.

Ordering contract (do not reorder):

  1. normal PDF text extraction (fast, default);
  2. only when that yields no usable text -> OCR cache lookup;
  3. cache hit -> reuse; cache miss -> render + OCR, then cache success only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Provenance/backend tag recorded on extraction results.
ENGINE_NAME = "rapidocr"

#: Cache format version. Bumping this invalidates every previous cache entry.
CACHE_VERSION = 1

#: Maximum cached OCR payload we will read back (bytes).
MAX_CACHE_TEXT_BYTES = 4 * 1024 * 1024

_LOG_PREFIX = "RTFM OCR:"


def _log(msg: str) -> None:
    """Concise operator-facing diagnostic line (no tracebacks in normal logs)."""
    try:
        logger.info("%s %s", _LOG_PREFIX, msg)
    except Exception:  # pragma: no cover - logging must never break extraction
        pass


# --- Engine availability -----------------------------------------------------


def ocr_backend() -> str:
    """Name of the bundled OCR engine, or a concrete reason it is unavailable.

    Mirrors ``pdf_backend()``: an import alone is not proof in a frozen build,
    so a real inference is attempted against a tiny synthetic image when the
    package imports. Deterministic and side-effect free (in-memory only).
    """
    try:
        import numpy as np  # type: ignore
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except Exception as exc:
        return f"unavailable ({ENGINE_NAME} import failed: {type(exc).__name__})"
    try:
        engine = RapidOCR()
        img = np.full((48, 160, 3), 255, dtype=np.uint8)
        engine(img)
    except Exception as exc:
        return f"unavailable ({ENGINE_NAME} not usable: {type(exc).__name__})"
    return ENGINE_NAME


def have_ocr() -> bool:
    return ocr_backend() == ENGINE_NAME


# --- Engine singleton --------------------------------------------------------


_ENGINE: Optional[object] = None
_ENGINE_LOCK = threading.Lock()


def _get_engine():
    """Return the process-wide RapidOCR engine, or ``None`` if unavailable."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            _ENGINE = RapidOCR()
        except Exception as exc:
            _log(f"backend={ENGINE_NAME} result=unavailable "
                 f"reason=engine load failed ({type(exc).__name__})")
            return None
    return _ENGINE


def reset_engine_for_tests() -> None:
    """Drop the cached engine (test helper; never used by production paths)."""
    global _ENGINE
    with _ENGINE_LOCK:
        _ENGINE = None


# --- Image conversion --------------------------------------------------------


def _pixmap_to_ndarray(pix):
    """Convert a PyMuPDF pixmap to a contiguous 3-channel uint8 ndarray."""
    import numpy as np  # type: ignore

    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    try:
        arr = arr.reshape(pix.height, pix.width, pix.n)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"unexpected pixmap layout: {exc}") from exc
    if pix.n == 3:
        return np.ascontiguousarray(arr)
    if pix.n == 4:
        return np.ascontiguousarray(arr[:, :, :3])
    if pix.n == 1:
        return np.ascontiguousarray(np.dstack([arr] * 3))
    return np.ascontiguousarray(arr[:, :, :3])


def _pil_to_ndarray(img):
    """Convert a PIL image to a contiguous 3-channel uint8 ndarray."""
    import numpy as np  # type: ignore

    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.asarray(img)
    if arr.ndim == 2:
        arr = np.dstack([arr] * 3)
    return np.ascontiguousarray(arr)


def _ocr_ndarray(arr, timeout: float) -> Optional[str]:
    """Run the bundled engine on an ndarray, bounded by ``timeout`` seconds.

    Returns the recognized text, or ``None`` on failure/timeout. Never raises.
    """
    engine = _get_engine()
    if engine is None:
        return None

    result: dict[str, Optional[str]] = {"text": None}

    def _run() -> None:
        try:
            res, _elapse = engine(arr)
            if res:
                result["text"] = " ".join(str(item[1]) for item in res)
            else:
                result["text"] = ""
        except Exception:
            result["text"] = None

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        # Timed out: leave the result as None (caller reports unavailable).
        return None
    return result["text"]


# --- OCR cache ---------------------------------------------------------------


def _cache_root(cache_dir: Path) -> Path:
    """Kept for callers that pass the *parent* cache dir (back-compat)."""
    return Path(cache_dir) / "rtfm-ocr"


def cache_key_for_file(path: Path) -> str:
    """SHA-256 hex of the source document (reliable source identity)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    # Shard by the first two hex chars to keep directories bounded. The caller
    # supplies the OCR cache root itself (already .../<cache>/rtfm-ocr), so do
    # NOT append another segment here -- that produced a nested
    # "rtfm-ocr/rtfm-ocr" tree.
    return Path(cache_dir) / key[:2] / f"{key}.json"


def _read_cache(cache_dir: Optional[Path], key: str) -> Optional[str]:
    if cache_dir is None:
        return None
    p = _cache_path(cache_dir, key)
    try:
        if not p.is_file():
            return None
        raw = p.read_text(encoding="utf-8")
        if len(raw) > MAX_CACHE_TEXT_BYTES:
            return None
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    if data.get("key") != key or data.get("engine") != ENGINE_NAME:
        return None
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return text


def _write_cache(cache_dir: Optional[Path], key: str, text: str, pages: int) -> bool:
    """Persist a successful OCR result atomically. Never caches failures."""
    if cache_dir is None:
        return False
    p = _cache_path(cache_dir, key)
    payload = {
        "version": CACHE_VERSION,
        "engine": ENGINE_NAME,
        "key": key,
        "pages": pages,
        "text": text,
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, p)
        return True
    except Exception as exc:
        _log(f"cache=write_failed reason={type(exc).__name__}")
        return False


# --- Result type -------------------------------------------------------------


@dataclass
class OcrResult:
    """Outcome of the document-level OCR fallback."""

    text: str
    pages: int
    cache: str          # "miss" | "hit" | "disabled" | "none"
    elapsed: float
    backend: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


# --- Document-level OCR fallback ---------------------------------------------


def ocr_pdf_document(
    path,
    *,
    page_cap: int = 500,
    max_pixels: int = 40_000_000,
    zoom: float = 2.0,
    page_timeout: float = 30.0,
    total_timeout: float = 900.0,
    cache_dir: Optional[Path] = None,
    name: Optional[str] = None,
    native_chars: int = 0,
    use_cache: bool = True,
) -> OcrResult:
    """OCR an image-only PDF with the bundled offline engine.

    Only called by ``extract_pdf_text`` after normal extraction produced no
    usable text. Reads the PDF read-only, never writes to the source tree,
    and caches successful results by source SHA-256 when ``cache_dir`` is set.
    """
    started = time.monotonic()
    path = Path(path)
    name = name or path.name
    backend = ocr_backend()

    if backend != ENGINE_NAME:
        return OcrResult(
            text="", pages=0, cache="none",
            elapsed=time.monotonic() - started, backend=backend,
            reason="OCR engine unavailable",
        )

    key: Optional[str] = None
    if use_cache and cache_dir is not None:
        try:
            key = cache_key_for_file(path)
        except Exception as exc:
            _log(f"{name} cache=key_failed reason={type(exc).__name__}")
            key = None

    if key is not None:
        cached = _read_cache(cache_dir, key)  # type: ignore[arg-type]
        if cached is not None:
            _log(f"{name} backend={ENGINE_NAME} cache=hit "
                 f"text_chars={_count_non_ws(cached)}")
            return OcrResult(
                text=cached, pages=0, cache="hit",
                elapsed=time.monotonic() - started, backend=ENGINE_NAME,
            )

    _log(f"{name} backend={ENGINE_NAME} cache=miss ocr_fallback=started")

    import fitz  # type: ignore  (imported lazily: core stays dependency-free)

    try:
        doc = fitz.open(path)
    except Exception as exc:
        return OcrResult(
            text="", pages=0, cache="none",
            elapsed=time.monotonic() - started, backend=ENGINE_NAME,
            reason=f"PDF open failed ({type(exc).__name__})",
        )

    page_texts: list[str] = []
    total_pages = 0
    truncated = False
    try:
        total_pages = min(len(doc), page_cap)
        if len(doc) > page_cap:
            truncated = True
        matrix = fitz.Matrix(zoom, zoom)
        for idx in range(total_pages):
            if time.monotonic() - started > total_timeout:
                truncated = True
                break
            try:
                page = doc.load_page(idx)
                w = int(page.rect.width * zoom)
                h = int(page.rect.height * zoom)
                if w * h > max_pixels:
                    # A page whose declared size would explode the bitmap is
                    # skipped; other pages still OCR normally.
                    continue
                pix = page.get_pixmap(matrix=matrix)
                arr = _pixmap_to_ndarray(pix)
                del pix
            except Exception:
                continue
            text = _ocr_ndarray(arr, page_timeout)
            del arr
            if text and text.strip():
                page_texts.append(text.strip())
    finally:
        doc.close()

    joined = "\n\n".join(page_texts).strip()
    elapsed = time.monotonic() - started

    if not joined:
        _log(f"{name} backend={ENGINE_NAME} result=failure "
             f"reason=OCR produced no usable text elapsed={elapsed:.1f}s")
        return OcrResult(
            text="", pages=total_pages, cache="none", elapsed=elapsed,
            backend=ENGINE_NAME,
            reason=(
                f"OCR produced no usable text from {total_pages} page(s)"
                if total_pages else "OCR produced no usable text"
            ),
        )

    if key is not None:
        _write_cache(cache_dir, key, joined, total_pages)  # type: ignore[arg-type]

    _log(f"{name} backend={ENGINE_NAME} pages={total_pages} cache=miss "
         f"elapsed={elapsed:.1f}s result=success "
         f"text_chars={_count_non_ws(joined)}")
    if truncated:
        _log(f"{name} result=partial reason=page/time budget reached "
             f"pages_ocr_attempted={len(page_texts)}")
    if native_chars:
        _log(f"{name} native_text_chars={native_chars} (OCR used as fallback)")
    return OcrResult(
        text=joined, pages=total_pages, cache="none" if key is None else "miss",
        elapsed=elapsed, backend=ENGINE_NAME,
    )


def ocr_pil_image(img, timeout: float) -> Optional[str]:
    """OCR a standalone PIL image with the bundled engine (None on failure)."""
    if ocr_backend() != ENGINE_NAME:
        return None
    try:
        arr = _pil_to_ndarray(img)
    except Exception:
        return None
    return _ocr_ndarray(arr, timeout)


def _count_non_ws(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())
