"""Shared utility helpers — single source of truth for common primitives.

This module holds small, dependency-free helpers that were duplicated
across multiple modules. It MUST NOT import any sibling modules
(circular-import safety). All functions here are pure-ish utilities
with no application state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return hex SHA-256 of a file without loading it fully into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Return hex SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON to ``path`` via a temp file + atomic replace (no partial reads)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
