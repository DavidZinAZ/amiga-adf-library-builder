"""Grouper: cluster parsed records into release sets and flag ambiguity.

Grouping rules (ARCHITECTURE stage 3; documented behavior):
  * Records sharing ``release_key`` (title+edition+group+chipset+lang+version+alt)
    are the same release. Editions (e.g. Platinum) are intentionally distinct keys.
  * Disks are ordered by parsed ordinal; a set is "complete" when it contains
    every ordinal 1..total_disks (when total is known). A single-or-multidisk set
    with ordered disks and no declared total is treated as complete enough to
    catalogue (we cannot prove otherwise without a total).
  * Special disks (boot/character/save/intro) are kept separate from main disks.
  * A release whose ONLY members are special disks (no determinable main disk)
    is incomplete and must be quarantined -- never guessed.
  * Near-duplicate SPELLING variants: base titles that match after collapsing the
    special-disk token but differ in the full normalized key are flagged for
    human review -- e.g. ``Example_Quest_III`` vs ``Example_Qest3``.
    Legitimate edition/group differences (Example Game vs Example Game Platinum) are NOT duplicates.

This module never guesses. It only reports.
"""
from __future__ import annotations

import difflib
import re
from collections import defaultdict
from typing import Iterable

from .models import ParsedRecord, ReleaseGroup

# Special-disk role tokens stripped when computing a base-title for dup detection.
_NEAR_DUP_ROLE_TOKENS = ("boot", "character", "char", "save", "intro")


def group_records(records: Iterable[ParsedRecord]) -> list[ReleaseGroup]:
    """Cluster parsed records into release groups and mark incomplete sets."""
    by_key: dict[str, list[ParsedRecord]] = defaultdict(list)
    for rec in records:
        by_key[rec.release_key].append(rec)

    groups: list[ReleaseGroup] = []
    for key, recs in by_key.items():
        disks = sorted(
            (r for r in recs if not r.special_disk),
            key=lambda r: (r.disk_number or 0),
        )
        specials = [r for r in recs if r.special_disk]
        first = recs[0]
        grp = ReleaseGroup(
            release_key=key,
            title=first.title,
            edition=first.edition,
            group=first.group,
            chipset=first.chipset,
            language=first.language,
            version=first.version,
            alt_marker=first.alt_marker,
            ext=first.ext,
            records=list(recs),
            disks=disks,
            specials=specials,
        )
        grp.has_main_disk = len(disks) > 0
        grp.is_complete = _is_complete(disks)
        groups.append(grp)

    _flag_near_duplicate_spellings(groups)
    _flag_incomplete_special_only(groups)
    return groups


def _is_complete(disks: list[ParsedRecord]) -> bool:
    if not disks:
        return False
    totals = [d.total_disks for d in disks if d.total_disks]
    if totals:
        expected = max(totals)
        have = {d.disk_number for d in disks if d.disk_number}
        return all(n in have for n in range(1, expected + 1))
    return True


def _flag_incomplete_special_only(groups: list[ReleaseGroup]) -> None:
    """Acceptance A7: special-disks without a main disk are quarantined.

    Appends to any existing quarantine_reason (e.g. a near-duplicate spelling
    flag) so a group can carry BOTH findings instead of one replacing the other.
    """
    for g in groups:
        if not g.has_main_disk and g.specials:
            roles = sorted({s.special_role for s in g.specials if s.special_role})
            reason = (
                "Incomplete set: only special disk(s) present ("
                + ", ".join(roles)
                + "), no determinable main game disk. Quarantined; not guessed "
                "into a game folder."
            )
            g.quarantine_reason = (
                reason if g.quarantine_reason is None
                else f"{g.quarantine_reason} | {reason}"
            )
        elif not g.has_main_disk and not g.specials:
            if g.quarantine_reason is None:
                g.quarantine_reason = "No main disk and no special disk resolved."


def _norm_title(title: str | None) -> str:
    """Lowercased, punctuation/space-stripped title for comparison.

    Also normalizes a trailing ordinal so roman numerals and digits compare
    equally (for example, ``Example Quest III`` and ``Example Qest3`` normalize
    to comparable trailing ordinals). Genuinely different titles remain well below
    the near-duplicate threshold.
    """
    t = re.sub(r"[\W_]+", "", (title or "").lower())
    # Normalize a trailing roman-numeral or arabic ordinal to a plain digit.
    m = re.match(r"^(.*?)(\d+|i{1,3}v?i{0,3}|iv|v?i|ix|x|vi{0,3})$", t)
    if m and m.group(2):
        word = m.group(2)
        num = int(word) if word.isdigit() else _ROMAN.get(word, None)
        if num is not None:
            t = f"{m.group(1)}{num}"
    return t


# Roman-numeral equivalence so "III" and "3" normalize to the same token.
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
          "vii": 7, "viii": 8, "ix": 9, "x": 10}


# Two titles are "near-duplicate spellings" of the same game when their
# normalized forms are at least this similar (difflib ratio). 0.90 admits synthetic spelling variants while excluding legitimately different titles.
_NEAR_DUP_RATIO = 0.90


def _flag_near_duplicate_spellings(groups: list[ReleaseGroup]) -> None:
    """Acceptance A8: spelling variants of the same game => flagged for review.

    Groups whose normalized titles are highly similar (difflib ratio >=
    ``_NEAR_DUP_RATIO``) but whose *source* spellings differ are cross-flagged for
    human review. Synthetic spelling variants are kept as separate release keys and flagged
    for human review rather than auto-merged. Legitimately distinct titles remain
    below the threshold.
    """
    titled: list[ReleaseGroup] = [g for g in groups if g.title]
    for i, g in enumerate(titled):
        gi = _norm_title(g.title)
        if not gi:
            continue
        siblings = []
        for h in titled[i + 1 :]:
            gh = _norm_title(h.title)
            if not gh or gi == gh:
                continue
            if difflib.SequenceMatcher(None, gi, gh).ratio() >= _NEAR_DUP_RATIO:
                siblings.append(h)
        if siblings and g.quarantine_reason is None:
            names = sorted({g.title or "", *(s.title or "" for s in siblings)})
            g.quarantine_reason = (
                "Near-duplicate spelling of the same game: source spelling "
                f"variant(s) {names!r} match closely but differ. Human review "
                "required; not auto-merged."
            )
        for h in siblings:
            if h.quarantine_reason is None:
                names = sorted({g.title or "", h.title or ""})
                h.quarantine_reason = (
                    "Near-duplicate spelling of the same game: source spelling "
                    f"variant(s) {names!r} match closely but differ. Human review "
                    "required; not auto-merged."
                )
