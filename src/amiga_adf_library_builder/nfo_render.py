"""Single Gotek-facing NFO rendering contract (Gotek NFO contract).

Every Gotek-facing ``.nfo`` begins on line 1 with::

    Title: <canonical title>

and line 2 is a concise labelled blurb::

    Blurb: <year> - <publisher> - <short description>

The blurb is built only from available trusted metadata; missing fields are
omitted rather than invented, and no empty separators are emitted. The whole
file is kept at or below 512 UTF-8 bytes (the Gotek firmware reads only the
first 512 bytes of an NFO).

Detailed source / metadata / manual-approval provenance is NOT embedded here.
It is preserved durably outside the Gotek-facing NFO (see ``enrich.py`` which
writes a per-release ``<basename>.provenance.json`` sidecar under
``assets/nfo``). The exporter copies only the ``.nfo`` into the SD-card
staging tree, so provenance never leaks into the final ``/ADF`` or ``/DSK``
output.
"""
from __future__ import annotations

# The Gotek Touchscreen Interface firmware reads only the first 512 bytes of a
# release .nfo (verified in upstream source: docs/upstream-gotek-requirements.md
# §5). Keep every Gotek-facing NFO at or below this bound.
MAX_NFO_BYTES = 512

_ELLIPSIS = "\u2026"  # '…' — 3 UTF-8 bytes


def _truncate_chars(s: str, max_bytes: int) -> str:
    """Truncate ``s`` on a character boundary so its UTF-8 encoding fits.

    Returns ``s`` unchanged when it already fits. Never splits a multi-byte
    character.
    """
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    out: list[str] = []
    total = 0
    for ch in s:
        b = len(ch.encode("utf-8"))
        if total + b > max_bytes:
            break
        out.append(ch)
        total += b
    return "".join(out)


def render_gotek_nfo(
    *,
    title: str,
    year: str = "",
    publisher: str = "",
    description: str = "",
) -> str:
    """Render the Gotek-facing NFO text.

    ``Title:`` is always line 1. ``Blurb:`` is line 2, joining whichever of
    year / publisher / description are present with ``" - "`` separators. No
    field is invented; an entirely-empty blurb yields a bare ``Blurb:`` line.

    The result is guaranteed to be <= ``MAX_NFO_BYTES`` UTF-8 bytes. When the
    natural content would exceed that, the blurb is truncated first (keeping the
    ``Title:`` line intact); only if the title itself is too long is it
    truncated.
    """
    title = (title or "").strip() or "Unknown"
    parts: list[str] = []
    for value in (year, publisher, description):
        v = (value or "").strip()
        if v:
            parts.append(v)
    blurb = " - ".join(parts)

    title_line = f"Title: {title}"
    blurb_label = "Blurb:"
    blurb_line = f"{blurb_label} {blurb}" if blurb else blurb_label

    text = f"{title_line}\n{blurb_line}\n"
    if len(text.encode("utf-8")) <= MAX_NFO_BYTES:
        return text

    # Over budget: truncate the blurb while keeping the Title line intact.
    prefix = f"{title_line}\n{blurb_label} "
    suffix = "\n"
    ell_b = len(_ELLIPSIS.encode("utf-8"))
    avail = (
        MAX_NFO_BYTES
        - len(prefix.encode("utf-8"))
        - len(suffix.encode("utf-8"))
        - ell_b
    )
    if avail >= 0:
        blurb_trunc = _truncate_chars(blurb, avail) + _ELLIPSIS
        return f"{title_line}\n{blurb_label} {blurb_trunc}\n"

    # Title alone (with its labels and the bare Blurb: line) is too long:
    # truncate the title. The complete emitted output is:
    #
    #     Title: <truncated title>…\nBlurb:\n
    #
    # so the byte budget must cover "Title: ", the separator newline, the
    # ellipsis, the "Blurb:" label, AND the trailing newline -- not just a
    # single newline. The previous budget omitted the "Blurb:" label plus the
    # final newline (7 bytes) and could overflow MAX_NFO_BYTES.
    title_only_overhead = (
        len("Title: ".encode("utf-8"))
        + len("\n".encode("utf-8"))
        + ell_b
        + len(blurb_label.encode("utf-8"))
        + len("\n".encode("utf-8"))
    )
    title_avail = MAX_NFO_BYTES - title_only_overhead
    if title_avail < 0:
        # Extremely degenerate: fall back to a hard byte truncation of the
        # minimal text, preserving the Title: label.
        minimal = "Title: "
        raw = minimal.encode("utf-8")[: MAX_NFO_BYTES]
        return raw.decode("utf-8", errors="ignore")
    title = _truncate_chars(title, title_avail) + _ELLIPSIS
    return f"Title: {title}\n{blurb_label}\n"
