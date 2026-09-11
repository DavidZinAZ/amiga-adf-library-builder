"""Artwork processing for Gotek export (Phase 5).

Verified upstream behavior (docs/upstream-gotek-requirements.md and the Gotek
export design):

- Accepted formats: JPEG or PNG (``.jpg .jpeg .png``).
- Upstream states artwork may be *any size*; the firmware fits it into the
  display box preserving aspect ratio and never upscales.
- Hard limits (verified in firmware source, not guessed):
    * file size  <= 500 000 bytes (500 KB)
    * pixel size <= 2000 x 2000
- There is NO single required resize pixel dimension.

Project choice (documented, not an upstream requirement): the processed copy
targets the firmware's default landscape display box, 138 x 112 px (upstream
recommended target, §4.3), to minimize letterboxing and waste. Because the
firmware never upscales, a source smaller than the target is kept at native
size. The hard limits above are always enforced regardless of the target.

Master artwork is preserved untouched under ``assets/artwork-original``; the
processed copy is what ships in the export tree.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

# Verified upstream hard limits (see docs/upstream-gotek-requirements.md). NOT a resize target.
ARTWORK_MAX_BYTES: int = 500_000
ARTWORK_MAX_W: int = 2000
ARTWORK_MAX_H: int = 2000

# Documented project processed-target (firmware landscape display box, §4.3).
# Quality/space optimization; never represented as an upstream requirement.
ARTWORK_TARGET_W: int = 150
ARTWORK_TARGET_H: int = 150

# JPEG encode start quality; reduced only if the hard byte cap is exceeded.
_ARTWORK_QUALITY_START: int = 90
_ARTWORK_QUALITY_FLOOR: int = 20


def find_artwork_master(group, artwork_original_dir: Path) -> Optional[Path]:
    """Locate an operator-provided master artwork image for the release.

    Read-only; never downloads. Returns the first matching image or None.
    """
    from .models import ReleaseGroup  # local import to avoid cycle at load

    d = Path(artwork_original_dir)
    if not d.is_dir():
        return None
    base = _norm(group.title or "")
    if not base:
        return None
    for entry in sorted(d.iterdir()):
        if entry.is_file() and entry.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            e_norm = _norm(entry.stem)
            if base and (base in e_norm or e_norm in base):
                return entry
    return None


def _norm(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", text.lower())


def is_jpeg_progressive_from_bytes(data: bytes) -> bool:
    """Detect whether a JPEG byte stream uses progressive (SOF2) encoding.

    Inspects the Start-Of-Frame marker directly; never trusts the file
    extension. Returns ``True`` only when an SOF2 marker is found, ``False``
    for baseline (SOF0) or any non-JPEG / unparseable input. Deterministic.
    """
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        return False
    i = 2
    last = len(data) - 1
    while i < last:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xC2:  # SOF2 = Progressive DCT
            return True
        if marker == 0xC0 or marker == 0xC1:  # SOF0/1 = Baseline/Extended seq
            return False
        if 0xD0 <= marker <= 0xD7 or marker == 0x00:  # RST / byte stuffing
            i += 2
            continue
        if marker == 0xFF:  # padding byte
            i += 1
            continue
        # All other markers carry a 2-byte length (big-endian) after the marker.
        if i + 3 < len(data):
            length = (data[i + 2] << 8) | data[i + 3]
            if length < 2:
                break
            i += 2 + length
        else:
            break
    return False


def progressive_to_baseline(
    data: bytes,
    *,
    quality: int = 95,
    on_error_return_original: bool = True,
) -> tuple[bytes, str]:
    """If ``data`` is a progressive JPEG, return baseline-encoded bytes.

    Returns ``(bytes, action)`` where ``action`` is one of:
      * ``"baseline"`` – source was already baseline, returned unchanged;
      * ``"converted"`` – source was progressive, re-encoded as baseline;
      * ``"failed"`` – conversion failed; returned original (if
        ``on_error_return_original``) or an empty bytes.

    Never silently fakes a conversion: the caller can inspect ``action`` to
    distinguish "unchanged because baseline" from "unchanged because failed".
    """
    if not is_jpeg_progressive_from_bytes(data):
        return data, "baseline"
    try:
        from PIL import Image
    except Exception:
        return (data if on_error_return_original else b""), "failed"
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        out = io.BytesIO()
        # Preserve EXIF orientation so visual orientation is correct.
        im.save(
            out,
            "JPEG",
            quality=quality,
            progressive=False,
            exif=im.info.get("exif", b""),
        )
        converted = out.getvalue()
        if is_jpeg_progressive_from_bytes(converted):
            # PIL did not honor progressive=False (unlikely but guard anyway).
            return (data if on_error_return_original else b""), "failed"
        return converted, "converted"
    except Exception:
        return (data if on_error_return_original else b""), "failed"


def process_artwork_bytes(
    master: Path,
    *,
    target_w: int = ARTWORK_TARGET_W,
    target_h: int = ARTWORK_TARGET_H,
    max_w: int = ARTWORK_MAX_W,
    max_h: int = ARTWORK_MAX_H,
    max_bytes: int = ARTWORK_MAX_BYTES,
    progressive: bool = False,
) -> bytes:
    """Read ``master`` and return deterministic processed JPEG bytes.

    Guarantees:
      * aspect ratio preserved;
      * never upscaled (scale capped at 1.0);
      * pixel dimensions <= (max_w, max_h);
      * file size <= max_bytes (quality stepped down if needed);
      * deterministic for identical input + environment;
      * progressive JPEG encoding when ``progressive=True`` (GH-102).

    Raises ``RuntimeError`` only if Pillow is unavailable; never silently fakes.
    """
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            f"Artwork processing blocked: Pillow unavailable: {exc}"
        ) from exc

    master = Path(master)
    if not master.is_file():
        raise FileNotFoundError(f"artwork master not found: {master}")

    with Image.open(master) as im:
        im = im.convert("RGB")  # drop alpha; Gotek cover is opaque RGB
        w, h = im.size
        # Fit within target preserving aspect; cap scale at 1.0 (never upscale).
        scale = min(target_w / w, target_h / h, 1.0)
        new_w = max(1, round(w * scale))
        new_h = max(1, round(h * scale))
        # Enforce hard pixel cap (target is far smaller; this is a safety net).
        if new_w > max_w or new_h > max_h:
            cap = min(max_w / new_w, max_h / new_h, 1.0)
            new_w = max(1, round(new_w * cap))
            new_h = max(1, round(new_h * cap))
        im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)

        quality = _ARTWORK_QUALITY_START
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, progressive=progressive)
        while buf.tell() > max_bytes and quality > _ARTWORK_QUALITY_FLOOR:
            quality -= 5
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, progressive=progressive)
        if buf.tell() > max_bytes:
            raise RuntimeError(
                "Artwork exceeds hard 500 KB cap even at minimum quality"
            )
        return buf.getvalue()


def process_artwork(
    master: Path,
    dest: Path,
    *,
    target_w: int = ARTWORK_TARGET_W,
    target_h: int = ARTWORK_TARGET_H,
    max_w: int = ARTWORK_MAX_W,
    max_h: int = ARTWORK_MAX_H,
    max_bytes: int = ARTWORK_MAX_BYTES,
    progressive: bool = False,
) -> Path:
    """Process ``master`` to ``dest`` and return ``dest`` (convenience wrapper)."""
    data = process_artwork_bytes(
        master,
        target_w=target_w,
        target_h=target_h,
        max_w=max_w,
        max_h=max_h,
        max_bytes=max_bytes,
        progressive=progressive,
    )
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest
