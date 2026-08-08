"""Scanner: read-only walk of ``original/`` with SHA-256 hashing.

This module NEVER writes to the intake directory. It produces ScanRecord
objects and can verify that a previously recorded record still matches
byte-for-byte (preservation proof, documented behavior).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .models import ScanRecord

SUPPORTED_EXTENSIONS = (".adf", ".dsk")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_of_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return hex SHA-256 of a file without loading it fully into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_file(path: Path) -> ScanRecord:
    """Hash a single intake file. Read-only; no mutation of ``path``."""
    if not path.is_file():
        raise IsADirectoryError(f"not a regular file: {path}")
    return ScanRecord(
        path=path,
        filename=path.name,
        size=path.stat().st_size,
        sha256=sha256_of_file(path),
        scanned_at=_now_iso(),
    )


def scan_intake(original_dir: Path) -> list[ScanRecord]:
    """Walk ``original_dir`` and hash every supported image file.

    Only ``.adf`` / ``.dsk`` files are considered. The directory is read-only;
    nothing is created, renamed, or modified.
    """
    original_dir = Path(original_dir)
    if not original_dir.is_dir():
        raise NotADirectoryError(f"intake directory missing: {original_dir}")

    records: list[ScanRecord] = []
    for entry in sorted(original_dir.iterdir()):
        if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
            records.append(scan_file(entry))
    return records


def records_byte_identical(
    records: list[ScanRecord], rehash: bool = False
) -> tuple[bool, list[str]]:
    """Verify recorded intake files are unchanged.

    Without ``rehash`` it only checks size + previously recorded SHA-256 against
    a fresh re-hash of the current file (preservation proof). Returns
    (all_ok, list_of_problem_filenames).
    """
    problems: list[str] = []
    ok = True
    for rec in records:
        current = rec
        if rehash or True:
            # Always re-hash: this is the authoritative preservation check.
            current_sha = sha256_of_file(rec.path)
            current_size = rec.path.stat().st_size
        if current_sha != rec.sha256 or current_size != rec.size:
            ok = False
            problems.append(rec.filename)
    return ok, problems
