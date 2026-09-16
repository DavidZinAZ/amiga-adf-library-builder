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
from pathlib import Path
from typing import Iterable

from .models import ReleaseGroup, ScanRecord
from .utils import now_iso as _now


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
    review_items: list = None,
) -> dict[str, list[str]]:
    """Write quarantine/review records for flagged groups.

    (GH-164 RC3) review_items from enrich review events are persisted
    alongside quarantine records so that review_routed is non-empty
    whenever enrichment identifies a review-worthy candidate.

    Returns a summary dict: {'review': [...filenames...], 'unknown': [...]}.
    """
    scans = scans or {}
    review_items = review_items or []
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

    # (GH-164 RC3) Persist enrich review_items as separate review
    # records so review_routed is non-empty when enrichment identifies
    # review-worthy candidates that grouper did not flag.
    for item in review_items:
        item_dict = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        release_key = item_dict.get("release_key", "")
        if not release_key:
            continue
        safe_name = "".join(
            ch if ch.isalnum() or ch in " .-[]()" else "_"
            for ch in (item_dict.get("candidate_title", "") or release_key)
        ).strip().replace("  ", " ")
        item_out = review_dir / f"review_{release_key}_{item_dict.get('reason', 'unknown')}.json"
        item_out.write_text(json.dumps(item_dict, indent=2, ensure_ascii=False), encoding="utf-8")
        if str(item_out) not in review_files:
            review_files.append(str(item_out))

    return {"review": review_files, "unknown": unknown_files}
