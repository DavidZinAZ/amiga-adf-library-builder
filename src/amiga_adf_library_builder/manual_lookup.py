"""Unified manual lookup service: canonical browse + source candidates.

GH-107 Slice 4. Pure data/coordination layer behind the GUI source-browser
panel. No Qt here; the GUI calls these functions and renders the results.

Design contract (binding):

- Precedence is NOT duplicated here. Winner/explanation are produced by the
  Slice 3 APIs themselves: ``CanonicalLibrary.claims_for`` (already sorted by
  ``Provenance.sort_key()``) and ``CanonicalLibrary.resolve_field``. The
  explanation is derived strictly from the returned claim ordering.
- Curation overrides are persisted via ``CanonicalLibrary.claim_field`` with
  ``SourceAuthority.CURATION`` and source ``"operator"`` — the same store and
  claim table used by Slice 3. There is no second curation store. Because the
  curation tier outranks every automated tier and manual claims are never
  deleted, provider refreshes cannot silently overwrite an override.
- Reverting an override deletes only ``curation``/``operator`` claims for that
  field (raw provider/parser claims are preserved), so resolution falls back
  to the documented automated precedence.
- The MetadataSourceManager index is read-only here: it is queried for
  candidate values; neither DAT files nor the index are rewritten.
- Source media is never touched by this module (no filesystem writes at all
  beyond the canonical/metadata SQLite stores owned by their own modules).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from .canonical import (
    CanonicalLibrary,
    Provenance,
    SourceAuthority,
)
from .metadata_source import MetadataSourceManager


@dataclass(frozen=True)
class CandidateValue:
    """One observed candidate value with its full provenance."""

    value: Optional[str]
    source: str
    record_key: str = ""
    url: str = ""
    authority: str = "parser"
    authority_rank: int = 0
    confidence: Optional[float] = None
    observed_at: str = ""
    is_winning: bool = False
    is_manual: bool = False

    def explanation(self) -> str:
        """Why this candidate ranks where it does (deterministic wording)."""
        tier = {
            "curation": "manual operator decision (highest authority)",
            "curation_memory": "operator decision remembered for file content",
            "dat": "DAT source (Knowledge Layer)",
            "parser": "filename parser (lowest authority)",
        }.get(self.authority, self.authority)
        bits = [f"{tier} '{self.source}'"]
        if self.authority == "dat" and self.authority_rank:
            bits.append(f"rank {self.authority_rank}")
        if self.confidence is not None:
            bits.append(f"confidence {self.confidence:.2f}")
        return ", ".join(bits)


@dataclass
class FieldReport:
    """Side-by-side view of one canonical field: claims, winner, conflicts."""

    field_name: str
    claims: list = field(default_factory=list)  # list[CandidateValue]
    canonical_value: Optional[str] = None
    winning_source: str = ""
    winner_explanation: str = ""
    conflict: bool = False

    def distinct_values(self) -> list:
        seen = []
        for c in self.claims:
            if c.value not in seen:
                seen.append(c.value)
        return seen


@dataclass
class EntityReport:
    """Full lookup report for one selected Game/Release/Disk entity."""

    entity_type: str
    entity_id: str
    fields: list = field(default_factory=list)  # list[FieldReport]
    missing_fields: list = field(default_factory=list)  # list[str]

    @property
    def conflicts(self) -> list:
        return [f for f in self.fields if f.conflict]


def _authority_tier(prov: Provenance) -> str:
    return prov.authority.tier


def _normalize_hash(col: str, value: str) -> str:
    return value.strip().lower() if col != "size" else value.strip()


def _claim_order(claims: list) -> list:
    # Mirror of the persisted ordering claims_for() already returns; keep
    # it explicit and stable here (never invents new precedence).
    return sorted(claims, key=lambda pc: (pc[0].sort_key(), str(pc[1])))


def _report_for_field(canon: CanonicalLibrary, entity_type: str,
                      entity_id: str, field_name: str) -> FieldReport:
    claims = _claim_order(
        canon.claims_for(entity_type, entity_id, field_name)
    )
    rep = FieldReport(field_name=field_name)
    manual_sources = ("curation", "curation_memory")
    for i, (prov, value) in enumerate(claims):
        tier = _authority_tier(prov)
        rep.claims.append(
            CandidateValue(
                value=value,
                source=prov.source,
                record_key=prov.record_key,
                url=prov.url,
                authority=tier,
                authority_rank=prov.authority_rank,
                confidence=prov.confidence,
                observed_at=prov.observed_at,
                is_winning=(i == 0),
                is_manual=tier in manual_sources,
            )
        )
    if claims:
        winner_value, winner_prov = canon.resolve_field(
            entity_type, entity_id, field_name
        )
        rep.canonical_value = winner_value
        rep.winning_source = rep.claims[0].source
        if winner_prov is not None:
            rep.winner_explanation = _authority_tier(winner_prov)
        rep.conflict = len(rep.distinct_values()) > 1
    return rep


def fields_for_entity(entity_type: str, release=None, disk=None) -> list:
    """Canonical field names probed for one entity (documented set)."""
    if entity_type == "game":
        return ["title"]
    if entity_type == "release":
        return ["title", "edition", "region", "language", "publisher"]
    if entity_type == "disk":
        return ["filename", "disk_number", "sha256", "size"]
    return []


def build_entity_report(canon: CanonicalLibrary, entity_type: str,
                        entity_id: str, *, release=None, disk=None) -> EntityReport:
    """Assemble the full side-by-side report for one entity."""
    rep = EntityReport(entity_type=entity_type, entity_id=entity_id)
    for fname in fields_for_entity(
        entity_type, release=release, disk=disk
    ):
        rep.fields.append(
            _report_for_field(canon, entity_type, entity_id, fname)
        )
    rep.missing_fields = [
        f.field_name for f in rep.fields
        if f.canonical_value is None and not f.claims
    ]
    return rep


def list_library_entities(canon: CanonicalLibrary) -> dict:
    """Selectable records from the canonical model (for the GUI picker)."""
    games = []
    for game_id in canon.list_games():
        games.append(
            {
                "entity_type": "game",
                "entity_id": game_id,
                "label": _game_label(canon, game_id),
            }
        )
    return {"games": games}


def _game_label(canon: CanonicalLibrary, game_id: str) -> str:
    value, _ = canon.resolve_field("game", game_id, "title")
    return value if value else game_id


def releases_for(canon: CanonicalLibrary, game_id: str) -> list:
    out = []
    for release_id in canon.releases_for_game(game_id):
        value, _ = canon.resolve_field("release", release_id, "title")
        out.append(
            {
                "entity_type": "release",
                "entity_id": release_id,
                "label": value if value else release_id,
            }
        )
    return out


def disks_for(canon: CanonicalLibrary, release_id: str) -> list:
    out = []
    for disk_id in canon.disks_for_release(release_id):
        row = canon.disk_row(disk_id, release_id) or {}
        value, _ = canon.resolve_field("disk", disk_id, "filename")
        label = value or row.get("filename") or disk_id
        out.append(
            {
                "entity_type": "disk",
                "entity_id": disk_id,
                "label": label,
            }
        )
    return out


def candidates_from_sources(manager: MetadataSourceManager, *,
                            sha1: str = "", md5: str = "", crc32: str = "",
                            title: str = "") -> list:
    """Query the read-only Slice 1 index for candidate metadata entries.

    Hash lookups are exact; a title lookup is a bounded, deterministic
    substring filter. Results are provenance-annotated DAT candidates only —
    they are never auto-applied to the canonical model.
    """
    if sha1:
        return _entries_by_hash(manager, "sha1", sha1)
    if md5:
        return _entries_by_hash(manager, "md5", md5)
    if crc32:
        return _entries_by_hash(manager, "crc32", crc32)
    if title:
        return _entries_by_title(manager, title)
    return []


def _entries_by_hash(manager: MetadataSourceManager, col: str,
                     value: str) -> list:
    try:
        rows = manager._conn.execute(
            f"""
            SELECT e.*, s.name AS source_name
            FROM metadata_source_entries e
            JOIN metadata_sources s ON s.source_id = e.source_id
            WHERE e.{col} = ? COLLATE NOCASE AND s.enabled = 1
            ORDER BY s.name, e.title
            """,
            (_normalize_hash(col, value),),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [_row_to_candidate(r) for r in rows]


def _entries_by_title(manager: MetadataSourceManager, title: str) -> list:
    needle = title.strip().lower()
    if not needle:
        return []
    try:
        rows = manager._conn.execute(
            """
            SELECT e.*, s.name AS source_name
            FROM metadata_source_entries e
            JOIN metadata_sources s ON s.source_id = e.source_id
            WHERE s.enabled = 1 AND LOWER(e.title) LIKE ?
            ORDER BY s.name, e.title
            LIMIT 200
            """,
            (f"%{needle}%",),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [_row_to_candidate(r) for r in rows]


def _row_to_candidate(row: sqlite3.Row) -> dict:
    return {
        "source_name": row["source_name"],
        "title": row["title"],
        "year": row["year"],
        "publisher": row["publisher"],
        "region": row["region"],
        "language": row["language"],
        "sha1": row["sha1"],
        "md5": row["md5"],
        "crc32": row["crc32"],
        "size": row["size"],
        "disk_number": row["disk_number"],
        "disk_total": row["disk_total"],
        "record_key": row["title"] or "",
    }


def apply_manual_override(canon: CanonicalLibrary, entity_type: str,
                          entity_id: str, field_name: str, value: str,
                          *, observed_at: str = "") -> Provenance:
    """Persist an operator override as a ``curation``-authority claim.

    Same storage surface as Slice 3 (``field_claim`` table). Coexists with
    provider claims; survives any provider refresh because the curation tier
    dominates resolution and automated claims never delete manual ones.
    """
    from .canonical import _now_iso  # module-private helper, single caller

    prov = Provenance(
        source="operator",
        record_key="",
        url="",
        authority=SourceAuthority.CURATION,
        observed_at=observed_at or _now_iso(),
    )
    canon.claim_field(entity_type, entity_id, field_name, value, prov)
    return prov


def revert_manual_override(canon: CanonicalLibrary, entity_type: str,
                           entity_id: str, field_name: str) -> int:
    """Remove only this field's manual (curation/operator) claims.

    Returns the number of removed claims. Provider/parser claims remain, so
    resolution deterministically falls back to the documented precedence.
    """
    removed = 0
    for prov, value in canon.claims_for(entity_type, entity_id, field_name):
        if prov.authority == SourceAuthority.CURATION and prov.source == "operator":
            canon._conn.execute(
                """
                DELETE FROM field_claim
                WHERE entity_type = ? AND entity_id = ? AND field_name = ?
                  AND authority = 'curation' AND source = 'operator'
                  AND value IS ?
                """,
                (entity_type, entity_id, field_name, value),
            )
            removed += 1
    canon._conn.commit()
    return removed


def provider_refresh_preserves_override(canon: CanonicalLibrary, entity_type: str,
                                        entity_id: str, field_name: str,
                                        provider_source: str,
                                        provider_value: str) -> bool:
    """Regression helper mirroring the real refresh path (claim-only).

    Simulates a provider refresh exactly the way Slice 3 providers do: an
    appended automated claim. Returns True iff the manual override still wins
    after the refresh. This is a pure-claim probe, never a data mutation of
    source media.
    """
    prov = Provenance(
        source=provider_source,
        authority=SourceAuthority.DAT,
        observed_at="2099-01-01T00:00:00+00:00",
        confidence=1.0,
    )
    canon.claim_field(entity_type, entity_id, field_name, provider_value, prov)
    value, win_prov = canon.resolve_field(entity_type, entity_id, field_name)
    return (
        value is not None
        and win_prov is not None
        and win_prov.authority == SourceAuthority.CURATION
    )
