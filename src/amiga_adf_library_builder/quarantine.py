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

# (GH-192/#189 RC4) Characters Windows forbids in a path component. An
# internal release key legitimately contains '|' (releases are keyed
# ``title||||edition|group|...``), so the raw key must never be embedded
# in a review/ filename: on Windows that raises [Errno 22] Invalid argument
# and aborts the run.
_WIN_FORBIDDEN = set('<>:"/\\|?*')

# Windows reserved device names (stem match, extension ignored).
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
_WIN_RESERVED |= {f"{p}{n}" for p in ("COM", "LPT") for n in "123456789¹²³"}

# Escape marker for an encoded character. '%' is not forbidden by Windows, so
# it is safe as the escape prefix -- but it MUST itself be escaped as %25, or
# a key that literally contains "%7c" would collide with the encoding of "|".
_ESCAPE = "%"


def encode_filename_component(value: str, *, max_length: int = 120) -> str:
    """Encode an arbitrary internal key into ONE Windows-safe filename stem.

    The transformation is *reversible* and *injective*:

      * every Windows-forbidden character, and every C0 control character,
        is percent-encoded as ``%<lowercase hex>`` (so ``|`` -> ``%7c``);
      * every other character is preserved verbatim, so readable keys stay
        readable and existing safe filenames are unchanged;
      * a literal ``%`` in the input is encoded as ``%25``, which keeps the
        mapping unambiguous in both directions;
      * if truncation is required, a ``-``-separated SHA-256 digest of the
        FULL original value is appended, so two keys sharing a truncated
        prefix never converge on one filename;
      * reserved device names and trailing dots/spaces are neutralized.

    Internal key semantics are untouched: this is purely the filesystem
    component. Callers persist the unmodified key inside the JSON payload.
    """
    raw = value or ""

    out: list[str] = []
    for ch in raw:
        if ch == _ESCAPE or ch in _WIN_FORBIDDEN or ord(ch) < 32:
            out.append(f"{_ESCAPE}{ord(ch):02x}")
        else:
            out.append(ch)

    stem = "".join(out)

    # Neutralize trailing dots/spaces (Windows silently strips them, which
    # would make the file we wrote differ from the path we reported).
    stem = stem.rstrip(" .")

    if len(stem) > max_length:
        import hashlib

        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        stem = f"{stem[:max_length]}-{digest}"

    if not stem:
        stem = "unknown"

    # A reserved device name is only safe when not the leading stem.
    if stem.split(".", 1)[0].rstrip(" ").upper() in _WIN_RESERVED:
        stem = f"{_ESCAPE}20{stem}"

    return stem


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
            ch if ch.isalnum() or ch in " .-[]()'’" else "_"
            for ch in (g.title or g.release_key)
        ).strip().replace("  ", " ")
        # (GH-192/#189 RC4) The filename is the ONLY sanitized part: the
        # record above still carries the verbatim title/release_key. Encoding
        # (rather than dropping characters) keeps the mapping to the original
        # key recoverable and collision-free.
        stem = encode_filename_component(safe_name or g.release_key)
        out = target_dir / f"{stem}.json"
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
        # (GH-192/#189 RC4) THE CRASH SITE: the raw internal release key
        # (e.g. 'defender||||') was interpolated into this filename, and Windows
        # rejects '|' with [Errno 22] Invalid argument. Encode only the
        # filesystem component; item_dict still persists the verbatim key.
        reason = encode_filename_component(str(item_dict.get("reason") or "unknown"))
        item_out = review_dir / (
            f"review_{encode_filename_component(release_key)}_{reason}.json"
        )
        item_out.write_text(json.dumps(item_dict, indent=2, ensure_ascii=False), encoding="utf-8")
        if str(item_out) not in review_files:
            review_files.append(str(item_out))

    return {"review": review_files, "unknown": unknown_files}
