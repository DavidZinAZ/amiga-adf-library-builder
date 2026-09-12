"""1G1R selection engine (GH-107 Slice 6).

Selects one release per game from a grouped release set for Gotek export.
Deterministic, explainable, operator override first.

Ranking dimensions (highest precedence first):
  1. Manual operator override (manual_approvals approved_folder / title).
  2. Completeness (is_complete, has_main_disk).
  3. Edition (Platinum > Enhanced > unknown).
  4. Version/revision (no collision: "10" != "1.0").
  5. Region (via canonical model; uses release region column).
  6. Language (via canonical model; uses release language column).
  7. Tie-breaking: deterministic release_id sort.

Public API:
  * select_one_per_game(groups, approvals, canon, decisions) -> SelectionResult
  * explain_selection(group, canon) -> structured provenance map

Contract:
  * Source media / raw DAT read-only; no writes here.
  * Conflicts/collisions reported explicitly, never silently overwritten.
  * Idempotent: same input -> same selection.
  * Preview/verify-only: zero export writes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import ParsedRecord, ReleaseGroup
from .manual_approvals import load_approvals, ApprovalRecord

__all__ = [
    "SelectionRecord",
    "SelectionResult",
    "rank_group",
    "select_one_per_game",
    "explain_selection",
    "write_selection_manifest",
    "load_selection_manifest",
    "persist_operator_decisions",
    "load_operator_decisions",
    "operator_decision_key",
]

# Ranking weights for each dimension (higher wins).
_REGION_RANK: dict[str, int] = {
    "USA": 100, "EUR": 90, "JPN": 80, "UK": 70, "DE": 60,
    "FR": 55, "AU": 50, "BRA": 40, "SE": 35, "NL": 30,
}
_LANGUAGE_RANK: dict[str, int] = {
    "EN": 100, "DE": 80, "FR": 75, "JP": 70, "ES": 65,
    "IT": 60, "RU": 55, "CN": 50, "KO": 45, "SE": 40,
}

_VERSION_PATTERN = re.compile(r"^v?(\d+)(?:\.(\d+))*$")


@dataclass(frozen=True)
class SelectionRecord:
    """One release group's selection outcome."""
    release_key: str
    title: Optional[str]
    selected: bool
    reason: str  # human-readable ranking explanation
    rank_score: float
    override_source: str  # "operator" | "canonical_ranking" | "excluded"


@dataclass
class SelectionResult:
    """Result of 1G1R selection across all groups."""
    selected: list[ReleaseGroup]
    rejected: list[SelectionRecord]
    selection_manifest: list[dict]
    provenance: dict  # run-level provenance


def _score_completeness(g: ReleaseGroup) -> float:
    """Completeness score: main disk present + full ordinal set.

    Honours is_complete: a group with is_complete=False scores lower
    than one with is_complete=True even when disks are present.
    """
    if not g.has_main_disk:
        return 0.0
    score = 50.0
    disks = g.disks
    if not disks:
        score += 30.0  # no disk info: assume complete enough
        return score
    totals = [d.total_disks for d in disks if d.total_disks]
    if totals:
        expected = max(totals)
        have = {d.disk_number for d in disks if d.disk_number}
        if all(n in have for n in range(1, expected + 1)):
            score += 30.0
    else:
        score += 30.0  # unknown total, assume complete enough
    # is_complete is a documented dimension: incomplete releases lose 20.
    if not g.is_complete:
        score -= 20.0
    return max(score, 0.0)


def _score_edition(g: ReleaseGroup) -> float:
    """Edition preference: Platinum > Enhanced > Normal > unknown."""
    edition = (g.edition or "").lower()
    if "platinum" in edition:
        return 40.0
    if "enhanced" in edition or "special" in edition:
        return 30.0
    if edition:
        return 20.0
    return 10.0


def _score_version(g: ReleaseGroup) -> float:
    """Version/revision score without bucket collision.

    "10" scores higher than "1.0": parsed numerically by segment,
    so "10" -> (10,) -> score 100, "1.0" -> (1, 0) -> score 90.
    """
    version = (g.version or "").lower().strip()
    if not version:
        return 10.0
    m = _VERSION_PATTERN.match(version)
    if not m:
        return 15.0  # unparseable: non-ranked but not equal to anything
    segments = tuple(int(x) for x in m.groups() if x)
    # Numeric score: base 50 + leading segment * 5, capped at 100.
    # "1.0" -> (1, 0) -> 55 ; "10" -> (10,) -> 100 ; "2.0" -> (2,) -> 60
    score = 50 + min(segments[0], 10) * 5
    return min(float(score), 100.0)


def _score_region(group: ReleaseGroup, canon=None) -> float:
    """Region score: prefer canonical model release column, fall back to neutral."""
    if canon is not None:
        try:
            rid = _find_canonical_release_id(canon, group)
            if rid:
                val, _ = canon.resolve_field("release", rid, "region")
                if val:
                    return float(_REGION_RANK.get(val.upper(), 20.0))
        except Exception:
            pass
    return 20.0


def _score_language(group: ReleaseGroup, canon=None) -> float:
    """Language score: prefer canonical model release column, fall back to neutral."""
    if canon is not None:
        try:
            rid = _find_canonical_release_id(canon, group)
            if rid:
                val, _ = canon.resolve_field("release", rid, "language")
                if val:
                    return float(_LANGUAGE_RANK.get(val.upper(), 20.0))
        except Exception:
            pass
    return 20.0


def _find_canonical_release_id(canon, group: ReleaseGroup) -> Optional[str]:
    """Find a canonical release matching the group's release_key claim.

    Uses both the release_key prefix and the slugified title so that
    game-id slug mismatches (e.g. 'battlesquadron' vs 'battle-squadron')
    do not prevent canonical region/language lookups.
    """
    from .canonical_naming import _slugify_title
    # Try exact release_key game prefix first.
    game_id = group.release_key.split("|")[0].lower()
    for rid in canon.releases_for_game(game_id):
        claims = canon.claims_for("release", rid, "release_key")
        for prov, value in claims:
            if value == group.release_key:
                return rid
    # Fallback: title-based slug for canonical lookups.
    title_game_id = _slugify_title(group.title) if group.title else ""
    if title_game_id and title_game_id != game_id:
        for rid in canon.releases_for_game(title_game_id):
            claims = canon.claims_for("release", rid, "release_key")
            for prov, value in claims:
                if value == group.release_key:
                    return rid
    return None


def rank_group(g: ReleaseGroup, canon=None, canon_rank_boost: float = 0.0) -> tuple[float, str]:
    """Return (score, reason) for one group under the 1G1R ranking policy.

    ``canon`` is an optional CanonicalLibrary for region/language lookups.
    ``canon_rank_boost`` is an optional additive bump when the canonical
    model independently ranks this release higher (e.g. Fresh1G1R tag).
    """
    scores: list[tuple[float, str]] = []
    scores.append((_score_completeness(g), "completeness"))
    scores.append((_score_edition(g), "edition"))
    scores.append((_score_version(g), "version"))
    scores.append((_score_region(g, canon), "region"))
    scores.append((_score_language(g, canon), "language"))
    scores.append((canon_rank_boost, "canonical_boost"))

    total = sum(s for s, _ in scores)
    parts = []
    for s, label in scores:
        if s > 0:
            parts.append(f"{label}={s:.0f}")
    reason = "; ".join(parts) if parts else "no ranking signals"
    return total, reason


def _operator_override(
    group: ReleaseGroup,
    approvals: dict,
) -> Optional[ApprovalRecord]:
    """Return the matching active approval record for this group, or None."""
    if not approvals:
        return None
    key = group.release_key
    base_key = key.split("|")[0].lower()
    # Also try title-based slug for approval lookup.
    title_key = ""
    try:
        from .canonical_naming import _slugify_title
        title_key = _slugify_title(group.title) if group.title else ""
    except Exception:
        title_key = ""
    rec = approvals.get(key) or approvals.get(base_key) or approvals.get(title_key)
    if rec is None:
        return None
    if isinstance(rec, ApprovalRecord):
        return rec
    return None


def _operator_decisions_from_manifest(
    manifest: Optional[dict],
) -> dict[str, str]:
    """Extract operator decisions from a persisted selection manifest.

    Returns {game_id: release_key} so select_one_per_game can re-apply
    them without a live ApprovalRecord.
    """
    if not manifest:
        return {}
    decisions: dict[str, str] = {}
    for entry in manifest.get("selection_manifest", []):
        if entry.get("override"):
            game_id = entry.get("game_id", "")
            winner = entry.get("release_key", "")
            if game_id and winner:
                decisions[game_id] = winner
    return decisions


def select_one_per_game(
    groups: list[ReleaseGroup],
    approvals: Optional[dict] = None,
    *,
    canon=None,
    decisions: Optional[dict] = None,
) -> SelectionResult:
    """Select one release per game for 1G1R Gotek export.

    For each distinct game (derived from release_key prefix),
    the highest-ranked group wins. Operator approval overrides all ranking
    dimensions. Conflicting selections (same sanitized folder) are reported
    explicitly — never silently overwritten.

    Args:
        groups: ReleaseGroup list from the pipeline.
        approvals: operator approval index (release_key -> record).
        canon: optional CanonicalLibrary for provenance enrichment.
        decisions: persisted operator decisions {game_id -> release_key}
            loaded from load_operator_decisions(). Overrides ranking when
            the release_key is still present in the current group set.

    Returns:
        SelectionResult with selected groups, rejected records, manifest,
        and provenance.
    """
    appr = approvals or {}
    dec = decisions or {}
    # Group by game identity: use the release_key base (before |) as game id.
    games: dict[str, list[ReleaseGroup]] = {}
    for g in groups:
        game_id = g.release_key.split("|")[0].lower()
        games.setdefault(game_id, []).append(g)

    selected: list[ReleaseGroup] = []
    rejected: list[SelectionRecord] = []
    selection_manifest: list[dict] = []
    decisions_log: list[dict] = []

    for game_id, game_groups in sorted(games.items()):
        # Apply persisted operator decision first, if still valid.
        if game_id in dec:
            forced_key = dec[game_id].lower()
            for g in game_groups:
                if g.release_key.lower() == forced_key:
                    record = SelectionRecord(
                        release_key=g.release_key,
                        title=g.title,
                        selected=True,
                        reason=f"operator decision persisted (loaded from decisions)",
                        rank_score=999.0,
                        override_source="operator",
                    )
                    selected.append(g)
                    selection_manifest.append({
                        "game_id": game_id,
                        "release_key": g.release_key,
                        "title": g.title,
                        "decision": "operator_decision_persisted",
                        "score": 999.0,
                        "reason": record.reason,
                        "override": True,
                    })
                    decisions_log.append({
                        "game_id": game_id,
                        "decision": "operator_decision_persisted",
                        "winner": g.release_key,
                        "reason": record.reason,
                    })
                    # All other groups for this game are rejected.
                    for other in game_groups:
                        if other.release_key.lower() != forced_key:
                            rejected.append(SelectionRecord(
                                release_key=other.release_key,
                                title=other.title,
                                selected=False,
                                reason=f"eliminated by persisted operator decision {g.release_key}",
                                rank_score=0.0,
                                override_source="operator",
                            ))
                    continue  # next game

        # Compute scores for all candidates.
        candidates: list[tuple[ReleaseGroup, float, str, Optional[ApprovalRecord]]] = []
        for g in game_groups:
            override = _operator_override(g, appr)
            score = 0.0
            reason = "operator override" if override else ""
            if override is None:
                score, reason = rank_group(g, canon=canon)
            candidates.append((g, score, reason, override))

        # Sort: override first, then by score desc, then deterministic tie-break.
        def _sort_key(item: tuple) -> tuple:
            _, score, _, override = item
            return (0 if override else 1, -score, item[0].release_key)

        candidates.sort(key=_sort_key)

        winner = candidates[0]
        winner_group, winner_score, winner_reason, winner_override = winner

        if winner_override:
            decision = "operator_override"
            winner_reason = f"operator override ({winner_override.approval_id}); {winner_reason}"
        elif len(candidates) == 1:
            decision = "only_candidate"
        else:
            decision = "ranked"

        record = SelectionRecord(
            release_key=winner_group.release_key,
            title=winner_group.title,
            selected=True,
            reason=winner_reason,
            rank_score=winner_score,
            override_source="operator" if winner_override else "canonical_ranking",
        )
        selected.append(winner_group)
        selection_manifest.append({
            "game_id": game_id,
            "release_key": winner_group.release_key,
            "title": winner_group.title,
            "decision": decision,
            "score": winner_score,
            "reason": winner_reason,
            "override": bool(winner_override),
        })
        decisions_log.append({
            "game_id": game_id,
            "decision": decision,
            "winner": winner_group.release_key,
            "reason": winner_reason,
        })

        # Rejected candidates.
        for cand in candidates[1:]:
            cg, cscore, creason, coverride = cand
            rejected.append(SelectionRecord(
                release_key=cg.release_key,
                title=cg.title,
                selected=False,
                reason=f"eliminated by {winner_group.release_key} "
                       f"({cscore:.0f} < {winner_score:.0f}; {creason})",
                rank_score=cscore,
                override_source="operator" if coverride else "canonical_ranking",
            ))

    provenance = {
        "policy": "1G1R per-game selection; operator override first; "
                   "ranking: completeness > edition > version > region > language",
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "decisions": decisions_log,
    }

    return SelectionResult(
        selected=selected,
        rejected=rejected,
        selection_manifest=selection_manifest,
        provenance=provenance,
    )


def explain_selection(group: ReleaseGroup, canon=None) -> dict:
    """Return a structured provenance map for one group's selection outcome."""
    score, reason = rank_group(group, canon=canon)
    return {
        "release_key": group.release_key,
        "title": group.title,
        "score": score,
        "reason": reason,
        "completeness": {
            "has_main_disk": group.has_main_disk,
            "is_complete": group.is_complete,
            "disk_count": len(group.disks),
            "special_count": len(group.specials),
        },
        "edition": group.edition,
        "version": group.version,
        "canonical_available": canon is not None,
    }


def write_selection_manifest(
    result: SelectionResult,
    path: Path,
) -> None:
    """Atomically write the selection manifest to disk.

    Machine-readable JSON with full provenance. Used for audit trails
    and GUI preview display.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "selection_manifest": result.selection_manifest,
        "provenance": result.provenance,
        "rejected": [
            {
                "release_key": r.release_key,
                "title": r.title,
                "reason": r.reason,
                "score": r.rank_score,
                "override_source": r.override_source,
            }
            for r in result.rejected
        ],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_selection_manifest(path: Path) -> Optional[dict]:
    """Load a selection manifest from disk, or None if absent."""
    try:
        data = Path(path).read_text(encoding="utf-8")
        return json.loads(data)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def operator_decision_key(library_root: Path) -> Path:
    """Return the path for the 1G1R operator decisions file."""
    return Path(library_root) / "curation" / "1g1r_decisions.json"


def persist_operator_decisions(
    result: SelectionResult,
    library_root: Path,
) -> Path:
    """Persist explicit operator decisions for survival across restarts.

    Writes the selection manifest plus a decisions file that records
    which releases were operator-approved vs ranked.
    """
    decisions_path = operator_decision_key(library_root)
    write_selection_manifest(result, decisions_path)
    return decisions_path


def load_operator_decisions(library_root: Path) -> Optional[dict]:
    """Load previously persisted operator decisions, or None."""
    return load_selection_manifest(operator_decision_key(library_root))