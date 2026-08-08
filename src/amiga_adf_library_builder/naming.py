"""Release naming: deterministic, sanitized basename for export folders/files.

The same basename is used for the Gotek release folder, the ``.nfo`` metadata
file, and the cover-artwork file so they always match (documented behavior /
parent requirement "NFO and artwork filenames match the release basename").

This is the single canonical source for release naming; ``enrich.write_nfo``
and ``exporter`` both import it to stay consistent.
"""
from __future__ import annotations

from .models import ReleaseGroup


def _sanitize(value) -> str:
    return (value or "Unknown").strip() or "Unknown"


def release_basename(group: ReleaseGroup) -> str:
    """Deterministic, filesystem-safe release basename.

    Encodes enough of the release identity to stay unique within the flat Gotek
    layout (DECISION #12). FAT32-unsafe characters are replaced with ``_``; the
    result is never empty.

    Identity scope (mandatory collision safety, remediation of the silent
    silent-overwrite defect): the basename must preserve enough of the release
    identity that two *distinct* release groups never converge on the same
    export folder or ``.adf`` filename. The grouper's ``release_key`` is built
    from ``title + edition + chipset + group + language + version + alt_marker``;
    the export basename now carries the same human-readable identity fields so a
    release differing only by ``language`` / ``version`` / ``alt_marker`` (or any
    combination) produces a distinct, deterministic, FAT32-safe name instead of
    silently clobbering another release's disk.

    Operator override: when an approval has assigned ``group.folder`` (see
    ``manual_approvals``), that value is used verbatim (FAT32-sanitized) and the
    derived identity fields are bypassed. This is the single sanctioned path for
    naming a release whose disk set would otherwise be quarantined
    (e.g. special-only sets approved for publication).
    """
    # Operator-approved folder override.
    if group.folder:
        return _sanitize(group.folder)

    parts = [_sanitize(group.title)]
    if group.edition:
        parts.append(group.edition)
    if group.chipset:
        parts.append(group.chipset)
    if group.group:
        parts.append(f"cr {group.group}")
    if group.language:
        parts.append(f"lang {group.language}")
    if group.version:
        parts.append(f"ver {group.version}")
    if group.alt_marker:
        parts.append(f"alt {group.alt_marker}")
    raw = " ".join(parts)
    out = "".join(
        ch if ch.isalnum() or ch in " .-[]()" else "_" for ch in raw
    )
    return out.strip().replace("  ", " ")
