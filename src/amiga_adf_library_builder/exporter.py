"""Phase 5 Gotek exporter.

Writes the deterministic single-level Gotek SD-card tree under a *run-owned*
staging directory only:

    <work-root>/staging/<run-id>/ADF/<Release Name>/<Release Name>-N.adf
    <work-root>/staging/<run-id>/ADF/<Release Name>/<Release Name>.nfo
    <work-root>/staging/<run-id>/ADF/<Release Name>/<Release Name>.jpg
    (and an analogous DSK/ branch for .dsk files)

Contract (docs/gotek-export-format.md + docs/upstream-gotek-requirements.md +
documented behavior):

  * Exactly one release folder per release directly under ADF/ or DSK/.
  * No nested version/group/edition directories.
  * Single disk   : <Release Name>.<ext>
  * Multidisk     : <Release Name>-1.<ext> .. -N.<ext>  (digits after final dash)
  * Companion .nfo and .jpg share the release basename.
  * Edition/group/chipset/language/trainer/alt/meaningful-variant separation is
    already in the release_key, so distinct releases get distinct folders.
  * Paths are sanitized; FAT32-unsafe and path-traversal-derived names rejected
    or safely collapsed (no "..", no absolute components).
  * No silent overwrite. Reruns are idempotent (identical content -> no change).
    Conflicting existing staged output is reported and the run refuses to clobber.
  * Nothing is ever written to the shared SD-card destination. The host that owns
    the SD-card write is configured separately and out of scope here.

Quarantined groups (have a quarantine_reason) are never exported.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import artwork as artwork_mod
from .exporter_guard import export_gate_open
from .models import ParsedRecord, ReleaseGroup
from .naming import release_basename
from .nfo_render import render_gotek_nfo

# FAT32-illegal characters that the firmware/gate cannot handle.
_INVALID_FILENAME_CHARS = set('*?"<>|')


@dataclass
class ExportResult:
    run_id: str
    staging_root: Path
    releases_exported: int = 0
    folders_written: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    files_unchanged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    skipped_quarantined: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    export_gate_open: bool = False
    export_gate_reason: str = ""


def _sanitize_component(name: str) -> str:
    """Sanitize a single folder/file basename component.

    Returns a non-empty safe component. Rejects path traversal / absolute
    components by collapsing them to a safe token.
    """
    # Split on the OS path sep and on '/' (Gotek uses '/'); treat each piece.
    pieces = name.replace("\\", "/").split("/")
    cleaned_pieces = []
    for piece in pieces:
        if piece in ("", ".", ".."):
            continue  # drop traversal / empty components
        # Strip FAT32-illegal chars.
        safe = "".join(ch if ch not in _INVALID_FILENAME_CHARS else "_" for ch in piece)
        safe = safe.strip().strip(".").strip()
        if safe:
            cleaned_pieces.append(safe)
    if not cleaned_pieces:
        raise ValueError(f"release name resolves to nothing safe: {name!r}")
    component = "_".join(cleaned_pieces)
    # Fat32-safe overall guard.
    component = "".join(
        ch if ch not in _INVALID_FILENAME_CHARS else "_" for ch in component
    )
    if not component:
        raise ValueError(f"release name resolves to nothing safe: {name!r}")
    return component


def _sanitize_run_id(run_id: str) -> str:
    """Sanitize a caller-supplied staging run identifier.

    A run id only ever becomes a single path component beneath
    ``<work-root>/work/staging``. Reject any value that could escape that
    directory: absolute paths, separator characters (``/`` or ``\\``), or
    ``..`` / ``.`` traversal segments. Uses the same component sanitizer that
    guards release basenames so the two code paths share one notion of "safe".

    Safe run ids (the generated ids and normal single-component ids such as
    ``run-A`` / ``conflict-run-001``) pass through unchanged. Any input that
    the sanitizer must alter carries separators or traversal segments, so it is
    rejected rather than silently rewritten.
    """
    if not run_id or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    # _sanitize_component drops '/', '\\', '..', '.' and strips FAT32-illegal
    # chars. If it changes the string, the input carried traversal/separator
    # characters and is therefore unsafe as a staging directory component.
    safe = _sanitize_component(run_id)
    if safe != run_id:
        raise ValueError(
            f"run_id {run_id!r} contains path separators or traversal "
            f"segments; refusing to use it as a staging directory"
        )
    return safe


def _disk_filename(basename: str, index: int, ext: str, total: int) -> str:
    """Return the on-disk filename for disk ``index`` (1-based).

    Per the ticket + docs/gotek-export-format.md:
      * a single-disk release (total == 1) uses ``<Release Name>.<ext>``
        (no ``-1`` suffix);
      * a multidisk set uses ``<Release Name>-1.<ext>`` .. ``-N.<ext>``
        (digits after the final dash), so disk 1 is ``-1``.
    """
    if total <= 1:
        return f"{basename}.{ext}"
    return f"{basename}-{index}.{ext}"


def _copy_if_changed(src_bytes: bytes, dest: Path) -> str:
    """Write ``src_bytes`` to ``dest`` unless identical content already exists.

    Returns 'written' or 'unchanged'. Never overwrites with different content
    without the caller first checking (caller handles conflicts).
    """
    if dest.exists():
        try:
            if dest.read_bytes() == src_bytes:
                return "unchanged"
        except OSError:
            pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src_bytes)
    return "written"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_release(
    group: ReleaseGroup,
    staging_root: Path,
    *,
    original_dir: Optional[Path] = None,
    artwork_original_dir: Optional[Path] = None,
    artwork_processed_dir: Optional[Path] = None,
    nfo_dir: Optional[Path] = None,
    verify_only: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    """Export one release group to the staging tree.

    Returns (written, unchanged, conflicts). ``group`` must NOT be quarantined;
    the caller filters those out. ``verify_only`` makes a dry pass that records
    conflicts but performs no writes.
    """
    ext = (group.ext or "adf").lower()
    root = staging_root / ("ADF" if ext == "adf" else "DSK")
    try:
        basename = _sanitize_component(release_basename(group))
    except ValueError as exc:
        return [], [], [str(exc)]

    folder = root / basename
    written: list[str] = []
    unchanged: list[str] = []
    conflicts: list[str] = []

    # Disk ordering: pure main disks, then specials (each gets its own ordinal
    # slot after the main set). Specials already carry their ordinal in the
    # group; we order by disk_number so special disks (Disk A/B etc.) land in
    # stable positions.
    ordered: list[ParsedRecord] = sorted(
        list(group.disks) + list(group.specials),
        key=lambda r: (r.disk_number or 0),
    )
    if not ordered:
        return written, unchanged, conflicts

    for idx, rec in enumerate(ordered, start=1):
        fname = _disk_filename(basename, idx, ext, len(ordered))
        dest = folder / fname
        if original_dir is not None:
            src_path = Path(original_dir) / rec.source_filename
        else:
            src_path = Path(rec.source_filename)
        if not src_path.is_file():
            conflicts.append(f"source missing for {rec.source_filename}")
            continue
        data = src_path.read_bytes()
        if verify_only:
            if dest.exists() and dest.read_bytes() != data:
                conflicts.append(str(dest))
            continue
        status = _copy_if_changed(data, dest)
        if status == "written":
            written.append(str(dest))
        else:
            unchanged.append(str(dest))

    # NFO: prefer the enrichment artifact (rich metadata + provenance).
    enriched_nfo = Path(nfo_dir) / f"{basename}.nfo" if nfo_dir is not None else None
    if enriched_nfo is not None and enriched_nfo.is_file():
        nfo_bytes = enriched_nfo.read_bytes()
    else:
        nfo_bytes = _build_nfo(group).encode("utf-8")
    nfo_dest = folder / f"{basename}.nfo"
    if verify_only:
        if nfo_dest.exists() and nfo_dest.read_bytes() != nfo_bytes:
            conflicts.append(str(nfo_dest))
    else:
        status = _copy_if_changed(nfo_bytes, nfo_dest)
        if status == "written":
            written.append(str(nfo_dest))
        else:
            unchanged.append(str(nfo_dest))

    # Artwork: prefer the processed enrichment artifact; otherwise process a master.
    processed = Path(artwork_processed_dir) / f"{basename}.jpg" if artwork_processed_dir is not None else None
    try:
        if processed is not None and processed.is_file():
            data = processed.read_bytes()
        elif artwork_original_dir is not None:
            master = artwork_mod.find_artwork_master(group, artwork_original_dir)
            data = artwork_mod.process_artwork_bytes(master) if master is not None else None
        else:
            data = None
        if data is not None:
            art_dest = folder / f"{basename}.jpg"
            if verify_only:
                if art_dest.exists() and art_dest.read_bytes() != data:
                    conflicts.append(str(art_dest))
            else:
                status = _copy_if_changed(data, art_dest)
                (written if status == "written" else unchanged).append(str(art_dest))
    except RuntimeError as exc:
        conflicts.append(f"artwork processing failed: {exc}")

    return written, unchanged, conflicts


def _build_nfo(group: ReleaseGroup) -> str:
    """Deterministic, display-friendly Gotek `.nfo` (Gotek NFO contract contract).

    This is the FALLBACK path used only when no enrichment artifact exists
    (``nfo_dir`` not supplied or no ``<basename>.nfo`` present). It uses the
    same ``render_gotek_nfo`` contract as the enriched/manually-approved paths:
    ``Title:`` on line 1, ``Blurb:`` on line 2 (built only from available
    filename-derived facts), and <= 512 bytes. No rich provenance is embedded
    here; provenance lives outside the Gotek-facing NFO (enrich.py sidecars).
    """
    title = (group.title or "Unknown").strip() or "Unknown"
    nd = len(group.disks)
    ns = len(group.specials)
    disk_desc = f"{nd} main disk(s)"
    if ns:
        disk_desc += f" + {ns} special disk(s)"
    rep = group.records[0] if group.records else None
    parts = [p for p in (rep.year if rep else None, rep.publisher if rep else None, group.group) if p]
    blurb = " - ".join(parts) if parts else disk_desc
    return render_gotek_nfo(title=title, description=blurb)


def export_all(
    groups: list[ReleaseGroup],
    *,
    staging_dir: Path,
    run_id: str,
    upstream_task_closed: bool,
    verified_artwork_width: Optional[int] = None,
    verified_artwork_height: Optional[int] = None,
    artwork_original_dir: Optional[Path] = None,
    artwork_processed_dir: Optional[Path] = None,
    nfo_dir: Optional[Path] = None,
    verify_only: bool = False,
    require_artwork: bool = False,
    # Internal: original/ path used to resolve source bytes.
    original_dir: Optional[Path] = None,
) -> ExportResult:
    """Run the full Phase-5 export for a set of release groups.

    Honors the exporter gate. Writes only beneath
    ``<staging_dir>/<run-id>`` (staging_dir comes from
    ``PathConfig.staging_dir``, never the private ``work/staging`` path). The SD
    card is never written to. Quarantined groups are skipped.
    """
    gate_open, gate_reason = export_gate_open(
        upstream_task_closed, verified_artwork_width, verified_artwork_height
    )
    # Harden against path traversal / arbitrary write: run_id must be a safe
    # single path component. Any absolute path, separator, or '..' segment is
    # rejected before a single byte is written. (Path-traversal hardening.)
    safe_run_id = _sanitize_run_id(run_id)
    staging_parent = Path(staging_dir)
    staging_root = (staging_parent / safe_run_id).resolve()
    if not staging_root.is_relative_to(staging_parent.resolve()):
        raise ValueError(
            f"refusing to export outside {staging_parent}: resolved run_id "
            f"{safe_run_id!r} -> {staging_root}"
        )
    result = ExportResult(
        run_id=safe_run_id,
        staging_root=staging_root,
        export_gate_open=gate_open,
        export_gate_reason=gate_reason,
    )
    if not gate_open:
        result.errors.append(f"export gate closed: {gate_reason}")
        return result

    if require_artwork:
        missing = []
        for group in groups:
            if group.quarantine_reason or (not group.has_main_disk and not group.specials):
                continue
            basename = release_basename(group)
            processed = Path(artwork_processed_dir) / f"{basename}.jpg" if artwork_processed_dir is not None else None
            if processed is None or not processed.is_file():
                missing.append(basename)
        if missing:
            result.errors.append("required artwork missing for: " + "; ".join(sorted(missing)))
            return result

    (staging_root / "ADF").mkdir(parents=True, exist_ok=True)
    (staging_root / "DSK").mkdir(parents=True, exist_ok=True)

    # Collision guard (mandatory silent-overwrite fix): two *distinct* release
    # identities must never be allowed to converge on the same export folder.
    # Map each sanitized folder path to the release_key that owns it. If a
    # different release_key maps to a folder already claimed by another release,
    # refuse to write it (record a clear conflict) instead of clobbering the
    # other release's disk. A repeated release_key is a legitimate rerun and is
    # allowed; release_basename already disambiguates version/language/alt_marker
    # so most distinct releases get distinct folders, but FAT32 sanitization can
    # still collapse two distinct human-readable names to one component, and this
    # guard catches that residual case safely.
    folder_owner: dict[str, str] = {}

    for g in groups:
        if g.quarantine_reason:
            result.skipped_quarantined.append(g.release_key)
            continue
        if not g.has_main_disk and not g.specials:
            result.skipped_quarantined.append(g.release_key)
            continue
        try:
            basename = _sanitize_component(release_basename(g))
        except ValueError as exc:
            result.conflicts.append(str(exc))
            continue
        folder_path = str(staging_root / _ext_root(g) / basename)
        owner = folder_owner.get(folder_path)
        if owner is not None and owner != g.release_key:
            result.conflicts.append(
                f"folder collision: {folder_path!r} already owned by release "
                f"{owner!r}; refusing to overwrite with distinct release "
                f"{g.release_key!r}"
            )
            continue
        folder_owner[folder_path] = g.release_key

        written, unchanged, conflicts = export_release(
            g,
            staging_root,
            original_dir=original_dir,
            artwork_original_dir=artwork_original_dir,
            artwork_processed_dir=artwork_processed_dir,
            nfo_dir=nfo_dir,
            verify_only=verify_only,
        )
        result.files_written.extend(written)
        result.files_unchanged.extend(unchanged)
        result.conflicts.extend(conflicts)
        if written or unchanged:
            result.folders_written.append(folder_path)
            result.releases_exported += 1

    return result


def _ext_root(group: ReleaseGroup) -> str:
    ext = (group.ext or "adf").lower()
    return "ADF" if ext == "adf" else "DSK"
