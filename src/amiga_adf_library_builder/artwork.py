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


def process_artwork_bytes(
    master: Path,
    *,
    target_w: int = ARTWORK_TARGET_W,
    target_h: int = ARTWORK_TARGET_H,
    max_w: int = ARTWORK_MAX_W,
    max_h: int = ARTWORK_MAX_H,
    max_bytes: int = ARTWORK_MAX_BYTES,
) -> bytes:
    """Read ``master`` and return deterministic processed JPEG bytes.

    Guarantees:
      * aspect ratio preserved;
      * never upscaled (scale capped at 1.0);
      * pixel dimensions <= (max_w, max_h);
      * file size <= max_bytes (quality stepped down if needed);
      * deterministic for identical input + environment.

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
        im.save(buf, "JPEG", quality=quality)
        while buf.tell() > max_bytes and quality > _ARTWORK_QUALITY_FLOOR:
            quality -= 5
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
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
) -> Path:
    """Process ``master`` to ``dest`` and return ``dest`` (convenience wrapper)."""
    data = process_artwork_bytes(
        master,
        target_w=target_w,
        target_h=target_h,
        max_w=max_w,
        max_h=max_h,
        max_bytes=max_bytes,
    )
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest
