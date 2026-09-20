"""Canonical naming/export policy layer.

GH-107 Slice 5. Derives deterministic, explainable export names from the
canonical Game/Release/Disk model — the single input authority.

Never reparses filenames. Never creates a second metadata truth. The same
canonical state always produces the same proposed names.

Public API:

* ``canonical_export_name(canon, release_id)`` -> proposed release basename
  (string) + per-disk basenames + provenance explanation.
* ``canonical_disk_name(canon, release_id, disk_id)`` -> single-disk basename.
* ``explain_canonical_name(canon, release_id)`` -> structured provenance map.
* ``detect_export_collisions(groups, staging_root)`` -> pre-write collision
  report without performing any filesystem write.
* ``sanitize_export_component(value)`` -> FAT32-safe component (exposed for
  callers that need it).

The existing ``release_basename(group)`` in this module is preserved for
backward compatibility (pipeline preview path, manual approvals). The new
functions are the canonical path: they read the canonical model, not the
ReleaseGroup filename-derived fields.

.. deprecated:: 0.3.0
    ``release_basename`` usage in this module is limited to the compatibility
    fallback path only. All primary naming goes through
    :func:`canonical_release_name`. See AR-005.
"""
from __future__ import annotations

import os
import re
import sqlite3
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .canonical import (
    CanonicalLibrary,
    Provenance,
    SourceAuthority,
    Disk,
    Release,
    Game,
    slugify_title,
)
from .exporter import _sanitize_component as _base_sanitize
from .naming import _sanitize, release_basename

# FAT32-illegal characters that the firmware/gate cannot handle.
_FAT32_ILLEGAL = set('*?"<>|')

# Deterministic multi-disk ordering: canonical disk_number (1-based), then
# disk_id for stable tiebreak when disk_number is absent.
_DISK_ORDER_KEY = lambda d: (d.disk_number or 0, d.disk_id)


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProposedName:
    """One proposed export name with full provenance."""

    entity_type: str  # "release" | "disk"
    entity_id: str
    basename: str
    components: list  # list[(label, value, source, authority_tier)]
    provenance_text: str  # human-readable explanation
    qualifier_policy_applied: str  # which qualifier policy produced this


@dataclass(frozen=True)
class ReleaseNaming:
    """Proposed names for one release + all its disks."""

    release_id: str
    game_id: str
    release_basename: str
    release_provenance: str
    disk_names: list  # list[ProposedName]
    qualifier_policy: str


@dataclass(frozen=True)
class CollisionReport:
    """Pre-write collision detection result. No filesystem write occurred."""

    collisions: list  # list[str] descriptive messages
    folders_reserved: dict  # folder_path -> release_id
    blocked_releases: list  # release_ids refused


# ---------------------------------------------------------------------------
# Qualifier policy
# ---------------------------------------------------------------------------

DOCUMENTED_QUALIFIER_POLICY = (
    "Authoritative displayed title only; deterministic identity suffixes only "
    "when Windows case-insensitive sanitized names collide."
)

# Descriptor fields retained in naming explanations, never appended to titles.
_QUALIFIER_FIELDS: list[tuple[str, str, str]] = [
    ("edition", "release", "edition"),
    ("region", "release", "region"),
    ("language", "release", "language"),
    ("publisher", "release", "publisher"),
]

# Identity fields that must appear in the release basename for collision
# safety (carried from ReleaseGroup lineage: group, chipset, version, alt_marker).
_IDENTITY_FIELDS: list[tuple[str, str, str]] = [
    ("group", "release", "group"),
    ("chipset", "release", "chipset"),
    ("version", "release", "version"),
    ("alt_marker", "release", "alt_marker"),
]


# ---------------------------------------------------------------------------
# Core naming functions
# ---------------------------------------------------------------------------

def _resolve_field(
    canon: CanonicalLibrary,
    entity_type: str,
    entity_id: str,
    field_name: str,
) -> tuple[Optional[str], Provenance]:
    """Resolve one canonical field; returns (value, provenance) or (None, dummy)."""
    value, prov = canon.resolve_field(entity_type, entity_id, field_name)
    if prov is None:
        prov = Provenance(source="", authority=SourceAuthority.PARSER)
    return value, prov


def _claim_values(
    canon: CanonicalLibrary,
    entity_type: str,
    entity_id: str,
    field_name: str,
) -> list[tuple[str, Provenance]]:
    """All preserved claims for a field, most-preferred first."""
    return canon.claims_for(entity_type, entity_id, field_name)


def _sanitize_token(value: str) -> str:
    """Preserve displayed text except characters unsafe in Windows components."""
    try:
        return _base_sanitize(value or "Unknown")
    except ValueError:
        return "Unknown"


def _provenance_text(claims: list[tuple[str, Provenance]]) -> str:
    """Human-readable explanation of why the winning claim won."""
    if not claims:
        return "no canonical claims"
    winner_prov = claims[0][1]
    tier = winner_prov.authority.tier
    source = winner_prov.source
    bits = [f"{tier} '{source}'"]
    if winner_prov.authority == SourceAuthority.DAT and winner_prov.authority_rank:
        bits.append(f"rank {winner_prov.authority_rank}")
    if winner_prov.confidence is not None:
        bits.append(f"confidence {winner_prov.confidence:.2f}")
    if winner_prov.record_key:
        bits.append(f"record '{winner_prov.record_key}'")
    # List conflicting values for transparency
    values = []
    for v, _ in claims:
        if v not in values:
            values.append(v)
    if len(values) > 1:
        bits.append(f"conflicting: {', '.join(repr(v) for v in values[1:])}")
    return "; ".join(bits)


def _build_provenance_text(
    components: list[tuple[str, str, Provenance]],
) -> str:
    """Build a provenance explanation from a list of (label, value, provenance)."""
    parts = []
    for label, value, prov in components:
        tier = prov.authority.tier
        source = prov.source
        parts.append(f"{label}={value!r} from {tier} '{source}'")
    return "; ".join(parts) if parts else "no components"


def canonical_release_name(
    canon: CanonicalLibrary,
    release_id: str,
) -> ProposedName:
    """Propose the export basename for one release from the canonical model.

    Uses the authoritative game title without release qualifiers. The batch
    export planner adds deterministic suffixes only when these names collide.

    The same canonical state always produces the same result.
    """
    release_row = canon.release_row(release_id)
    game_id = release_row["game_id"] if release_row else release_id

    # Resolve the authoritative displayed title.
    game_title, game_prov = _resolve_field(canon, "game", game_id, "title")
    if game_title is None:
        game_title = game_id

    components = [("title", game_title, game_prov)]
    return ProposedName(
        entity_type="release", entity_id=release_id,
        basename=_sanitize_token(game_title),
        components=[("title", game_title, game_prov.source, game_prov.authority.tier)],
        provenance_text=_build_provenance_text(components),
        qualifier_policy_applied=DOCUMENTED_QUALIFIER_POLICY,
    )


def canonical_disk_name(
    canon: CanonicalLibrary,
    release_id: str,
    disk_id: str,
    *,
    total_disks: int = 1,
    index: int = 1,
) -> ProposedName:
    """Propose the export filename for one disk from the canonical model.

    Uses the canonical disk filename field when available; falls back to
    the disk_id. Appends -N suffix for multidisk sets (total > 1).
    """
    # Resolve the disk's filename claim.
    filename, prov = _resolve_field(canon, "disk", disk_id, "filename")
    if filename is None:
        filename = disk_id

    base = _sanitize_component(filename)

    # Multidisk ordering: -N suffix for total > 1, using canonical disk_number.
    disk_row = canon.disk_row(disk_id, release_id) or {}
    disk_number = disk_row.get("disk_number")
    if total_disks > 1:
        idx = disk_number if disk_number and disk_number >= 1 else index
        base = f"{base}-{idx}"

    components = [("filename", filename, prov)]
    provenance = _build_provenance_text(components)

    return ProposedName(
        entity_type="disk",
        entity_id=disk_id,
        basename=base,
        components=[(label, value, prov.source, prov.authority.tier)
                     for (label, value, prov) in components],
        provenance_text=provenance,
        qualifier_policy_applied=DOCUMENTED_QUALIFIER_POLICY,
    )


def explain_canonical_name(
    canon: CanonicalLibrary,
    release_id: str,
) -> dict:
    """Return a structured provenance map for a release's proposed name.

    Keys: release_id, game_id, title, qualifiers (edition/region/language/
    publisher), identity (group/chipset/version/alt_marker), disk_names,
    policy, provenance_explanation.
    """
    release_row = canon.release_row(release_id)
    game_id = release_row["game_id"] if release_row else release_id

    release_name = canonical_release_name(canon, release_id)

    # Disk names in canonical order.
    disk_names: list[dict] = []
    for disk_id in canon.disks_for_release(release_id):
        dn = canonical_disk_name(canon, release_id, disk_id)
        disk_names.append({
            "disk_id": disk_id,
            "basename": dn.basename,
            "provenance": dn.provenance_text,
        })

    return {
        "release_id": release_id,
        "game_id": game_id,
        "release_basename": release_name.basename,
        "release_provenance": release_name.provenance_text,
        "qualifiers": {
            label: _resolve_field(canon, etype, release_id, fname)[0]
            for label, etype, fname in _QUALIFIER_FIELDS
        },
        "identity": {
            label: _resolve_field(canon, etype, release_id, fname)[0]
            for label, etype, fname in _IDENTITY_FIELDS
        },
        "disk_names": disk_names,
        "qualifier_policy": DOCUMENTED_QUALIFIER_POLICY,
        "provenance_explanation": release_name.provenance_text,
    }


# ---------------------------------------------------------------------------
# Pre-write collision detection (no filesystem write)
# ---------------------------------------------------------------------------

def detect_export_collisions(
    release_ids: list[str],
    canon: CanonicalLibrary,
    staging_root: Path,
) -> CollisionReport:
    """Detect output-name collisions BEFORE any filesystem write.

    Returns a CollisionReport with descriptive messages and the folder
    ownership map. No write, no mkdir, no file modification occurs.

    Two distinct releases that sanitize to the same folder path are a
    collision; a repeated release_id is a legitimate rerun and is allowed.
    """
    collisions: list[str] = []
    folders_reserved: dict[str, str] = {}
    blocked_releases: list[str] = []

    for release_id in release_ids:
        naming = canonical_release_name(canon, release_id)
        # Determine the ADF/DSK root per release (mirror export_all logic).
        # We cannot know the ext from canonical alone without disk records,
        # so check both ADF/DSK and report collisions for each.
        for ext_root in ("ADF", "DSK"):
            folder_path = str(staging_root / ext_root / naming.basename)
            owner = folders_reserved.get(folder_path)
            if owner is not None and owner != release_id:
                collisions.append(
                    f"folder collision: {folder_path!r} already reserved by "
                    f"release {owner!r}; refusing to overwrite with distinct "
                    f"release {release_id!r}"
                )
                blocked_releases.append(release_id)
                continue
            folders_reserved[folder_path] = release_id

    return CollisionReport(
        collisions=collisions,
        folders_reserved=folders_reserved,
        blocked_releases=blocked_releases,
    )


# ---------------------------------------------------------------------------
# Deterministic multi-disk ordering
# ---------------------------------------------------------------------------

def ordered_disk_ids(canon: CanonicalLibrary, release_id: str) -> list[str]:
    """Return disk ids in deterministic canonical order.

    Sorts by canonical disk_number (1-based), then disk_id for stable
    tiebreak. Mirrors the exporter's ordering contract.
    """
    disks = []
    for disk_id in canon.disks_for_release(release_id):
        row = canon.disk_row(disk_id, release_id) or {}
        disk_number = row.get("disk_number")
        disks.append((disk_number or 0, disk_id))
    disks.sort(key=lambda d: (d[0], d[1]))
    return [d[1] for d in disks]


# ---------------------------------------------------------------------------
# Integration helpers: ReleaseGroup -> canonical name mapping
# ---------------------------------------------------------------------------

def export_name_for_release_group(
    canon: CanonicalLibrary,
    group,  # ReleaseGroup — for backward compat when canonical DB absent
    fallback_basename: Optional[str] = None,
) -> ProposedName:
    """Return a ProposedName for a ReleaseGroup using the canonical model.

    When the canonical DB has a matching release_id (derived from the
    group's release_key), uses canonical fields. Otherwise falls back to
    the existing release_basename(group) so the preview path never breaks.
    """
    identity = identity_for_release_group(canon, group)
    if identity is not None:
        return canonical_release_name(canon, identity[0])
    basename = _sanitize_token(group.title or "Unknown")
    return ProposedName(
        entity_type="release", entity_id=group.release_key, basename=basename,
        components=[("title", group.title, "pipeline", "parser")],
        provenance_text="displayed release title (no canonical match)",
        qualifier_policy_applied=DOCUMENTED_QUALIFIER_POLICY,
    )


def identity_for_release_group(canon: CanonicalLibrary, group):
    """Resolve by stable release key, including after a title override."""
    for gid in canon.list_games():
        for rid in canon.releases_for_game(gid):
            if rid == group.release_key or any(
                value == group.release_key
                for _, value in canon.claims_for("release", rid, "release_key")
            ):
                return rid, gid
    return None





def _sanitize_component(value: str) -> str:
    """Re-use the exporter's FAT32 sanitizer (same contract)."""
    return _base_sanitize(value)


def _load_canonical_library(library_root: Path) -> Optional["CanonicalLibrary"]:
    """Open the canonical.db for this library, or return None.

    The DB lives at <library_root>/curation/canonical.db (the same
    path pipeline.py uses in _persist_canonical_library).  Returns
    None when the DB does not exist yet or cannot be opened — callers
    fall back to release_basename(group).
    """
    try:
        from .canonical import CanonicalLibrary
    except ImportError:  # pragma: no cover
        return None
    db_path = library_root / "curation" / "canonical.db"
    if not db_path.is_file():
        return None
    try:
        return CanonicalLibrary(db_path)
    except (OSError, sqlite3.Error):
        return None
