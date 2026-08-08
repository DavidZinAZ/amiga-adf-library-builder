"""Quarantine & review routing (Phase 6).

Ambiguous or incomplete material is routed to ``review/`` (with a human-readable
explanation) and/or ``unknown/`` (quarantined files). Nothing is guessed
(Acceptance A7, A8). This module only records the routing;
it never alters ``original/``.

  * Incomplete special-only sets -> ``unknown/`` with reason.
  * Near-duplicate spellings -> ``review/`` with reason.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import ReleaseGroup, ScanRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _route_dir(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def route_quarantine(
    groups: Iterable[ReleaseGroup],
    *,
    review_dir: Path,
    unknown_dir: Path,
    scans: dict[str, ScanRecord] | None = None,
) -> dict[str, list[str]]:
    """Write quarantine/review records for flagged groups.

    Returns a summary dict: {'review': [...filenames...], 'unknown': [...]}.
    """
    scans = scans or {}
    review = _route_dir(Path(review_dir), ".")
    unk = _route_dir(Path(unknown_dir), ".")

    review_files: list[str] = []
    unknown_files: list[str] = []

    for g in groups:
        reason = g.quarantine_reason
        if not reason:
            continue
        # Classify: special-only incomplete sets are quarantined (unknown);
        # spelling/near-dup issues are review items.
        special_only = (not g.has_main_disk) and bool(g.specials)
        target_dir = unk if special_only else review
        bucket = unknown_files if special_only else review_files

        record = {
            "release_key": g.release_key,
            "title": g.title,
            "edition": g.edition,
            "group": g.group,
            "ext": g.ext,
            "reason": reason,
            "source_files": [r.source_filename for r in g.records],
            "source_hashes": [scans[s].sha256 for s in (r.source_filename for r in g.records) if s in scans],
            "routed_at": _now(),
        }
        safe_name = "".join(
            ch if ch.isalnum() or ch in " .-[]()" else "_"
            for ch in (g.title or g.release_key)
        ).strip().replace("  ", " ")
        out = target_dir / f"{safe_name[:120] or g.release_key[:120]}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        bucket.append(str(out))

    return {"review": review_files, "unknown": unknown_files}
