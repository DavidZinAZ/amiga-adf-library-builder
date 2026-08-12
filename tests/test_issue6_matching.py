"""Issue #6 — manual title matching + shared game-level RTFM discovery.

Focused, fully synthetic regression tests for the deterministic
``score_source_match`` matcher and the auto-accept vs. route-for-review
decision in ``build_rtfm_for_group``.

All fixtures are synthetic (tmp_path + synthetic .txt). No maintainer-private
corpus, no host paths, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amiga_adf_library_builder import rtfm as rc
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ReleaseGroup
from amiga_adf_library_builder.parser import parse_filename

# Reuse synthetic helpers from the main RTFM test module (same package dir).
from test_rtfm import _cfg, _group, _manual_group, _write_sources


# ---------------------------------------------------------------------------
# Direct unit tests of score_source_match (deterministic, no build side effects)
# ---------------------------------------------------------------------------


def _src(stem: str, category=rc.CATEGORY_INSTRUCTIONS) -> "rc.RtfmSource":
    """Build a minimal RtfmSource for scoring (stem only)."""
    return rc.RtfmSource(
        path=Path(f"/roots/{category}/{stem}.txt"),
        root=Path("/roots"),
        category=category,
        stem=stem,
    )


def test_matchscore_dataclass_fields():
    sc = rc.score_source_match(_src("Example Space Tactics"), _manual_group("Example Space Tactics"))
    assert sc.matched is True
    assert 0.0 <= sc.confidence <= 1.0
    assert sc.kind in {
        "exact", "basename", "normalized", "canonical_reuse",
        "roman_arabic", "minor_spelling", "none",
    }
    assert isinstance(sc.evidence, list) and sc.evidence


def test_legacy_helper_still_truthy():
    # Acceptance: _source_matches_group remains importable + truthy.
    g = _manual_group("Example Space Tactics")
    assert rc._source_matches_group(_src("Example Space Tactics"), g) is True
    assert rc._source_matches_group(_src("Unrelated Game"), g) is False


# ---------------------------------------------------------------------------
# Normalization wins — all 9 classes (issue #6)
# ---------------------------------------------------------------------------


def test_normalization_articles():
    # "The X" <-> "X, The" canonicalized (article preserved, not discarded).
    g = _manual_group("The Chaos Engine")
    sc = rc.score_source_match(_src("Chaos Engine, The"), g)
    assert sc.matched
    assert sc.confidence >= rc.HIGH_CONFIDENCE
    assert sc.kind in ("normalized", "canonical_reuse")
    # Article is preserved in evidence so identity is auditable.
    assert any("article" in e for e in sc.evidence) or sc.kind == "canonical_reuse"


def test_normalization_punctuation():
    g = _manual_group("Laser Squad")
    # Subtitle separators / punctuation reduced to word breaks.
    assert rc.score_source_match(_src("Laser-Squad"), g).matched
    assert rc.score_source_match(_src("Laser: Squad"), g).matched
    assert rc.score_source_match(_src("Laser Squad!"), g).matched


def test_normalization_roman_arabic():
    # III == 3 on the canonical base.
    g = _manual_group("Synthetic Quest III")
    sc = rc.score_source_match(_src("Synthetic Quest 3"), g)
    assert sc.matched
    assert sc.kind == "roman_arabic"
    assert sc.confidence == 0.95
    assert any(e.startswith("roman_arabic") for e in sc.evidence)
    # And the reverse direction.
    g2 = _manual_group("Synthetic Quest 3")
    assert rc.score_source_match(_src("Synthetic Quest III"), g2).matched


def test_normalization_subtitle_separators():
    # ":", "-", "—", "/" treated as word breaks.
    g = _manual_group("Banshee The Game")
    assert rc.score_source_match(_src("Banshee: The Game"), g).matched
    assert rc.score_source_match(_src("Banshee - The Game"), g).matched
    assert rc.score_source_match(_src("Banshee/The Game"), g).matched


def test_normalization_underscores_and_spaces():
    g = _manual_group("Super Putty")
    assert rc.score_source_match(_src("Super_Putty"), g).matched
    assert rc.score_source_match(_src("Super  Putty"), g).matched


def test_normalization_crack_trainer_suffix():
    g = _manual_group("Example Space Tactics")
    sc = rc.score_source_match(_src("Example Space Tactics (cr SKR)"), g)
    assert sc.matched
    assert sc.kind == "canonical_reuse"
    assert sc.confidence == 0.95


def test_normalization_language_platform_tokens():
    # PAL/NTSC/en/de stripped only as release tokens, not identity.
    g = _manual_group("Solar Winds")
    sc = rc.score_source_match(_src("Solar Winds (PAL)"), g)
    assert sc.matched and sc.kind == "canonical_reuse"
    # A token that MATERIALLY identifies a different game is NOT stripped.
    # "Paladin" must not match "Solar Winds".
    sc2 = rc.score_source_match(_src("Paladin"), g)
    assert not sc2.matched


def test_normalization_multidisk_naming():
    g = _manual_group("Example Space Tactics")
    sc = rc.score_source_match(_src("Example Space Tactics (Disk 2 of 3)"), g)
    assert sc.matched and sc.kind == "canonical_reuse"


def test_normalization_minor_spelling_unique():
    # Single unambiguous Levenshtein==1 fix -> minor_spelling, but NOT
    # auto-accepted (below HIGH_CONFIDENCE).
    g = _manual_group("Space Crusade")
    sc = rc.score_source_match(_src("Spase Crusade"), g)
    assert sc.matched
    assert sc.kind == "minor_spelling"
    assert sc.confidence < rc.HIGH_CONFIDENCE
    assert any(e.startswith("minor_spelling_distance_1") for e in sc.evidence)


# ---------------------------------------------------------------------------
# Ambiguous near-matches route to REVIEW (no .rtfm emitted)
# ---------------------------------------------------------------------------


def test_minor_spelling_routes_to_review(tmp_path):
    # A plausible-but-not-high (0.92) match must be reviewed, not merged.
    root = tmp_path / "instructions"
    _write_sources(root, {"Spase Crusade.txt": b"move up"})
    g = _manual_group("Space Crusade")
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    assert res.routed_for_review
    assert not res.written
    # Provenance still written (auditable) and records the low confidence.
    assert res.provenance_path is not None and res.provenance_path.exists()
    data = __import__("json").loads(res.provenance_path.read_text())
    assert data["routed_for_review"] is True
    assert any(s["match_kind"] == "minor_spelling" for s in data["sources"])


def test_two_distinct_high_confidence_near_tie_routes_to_review(tmp_path):
    # Two DIFFERENT manuals (distinct canonical keys) both match the group at
    # 1.00 -> a genuine >=2 near-tie -> route for review, emit nothing.
    root = tmp_path / "instructions"
    _write_sources(root, {
        "Apidia Starfighter.txt": b"controls A",
        "Borealis Starfighter.txt": b"controls B",
    })
    g = _manual_group("Apidia Starfighter")
    g.folder = "Borealis Starfighter"  # operator override -> basename differs
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    assert res.routed_for_review
    assert not res.written
    assert res.rtfm_path is None or not res.rtfm_path.exists()


# ---------------------------------------------------------------------------
# Similar-but-DIFFERENT titles must NOT be merged
# ---------------------------------------------------------------------------


def test_sequel_not_merged():
    # "Alien Breed" vs "Alien Breed 2" are distinct games.
    g = _manual_group("Alien Breed")
    sc = rc.score_source_match(_src("Alien Breed 2"), g)
    assert not sc.matched


def test_edition_not_merged():
    # "Game" vs "Game Deluxe" distinct (Deluxe is a different product).
    g = _manual_group("Game")
    assert not rc.score_source_match(_src("Game Deluxe"), g).matched


def test_article_prefix_disambiguates():
    # "The Quest" vs "Quest" are different games (article preserved).
    g = _manual_group("The Quest")
    assert not rc.score_source_match(_src("Quest"), g).matched


# ---------------------------------------------------------------------------
# Multiple release variants reuse ONE correct game-level manual
# ---------------------------------------------------------------------------


def test_variant_reuses_base_game_manual(tmp_path):
    # A release variant whose title carries a (Demo) tag still matches the
    # base-title manual via canonical-reuse, and AUTO-ACCEPTS it (single
    # unambiguous high-confidence match).
    root = tmp_path / "instructions"
    _write_sources(root, {"Synthetic Quest III.txt": b"joypad move"})
    g = _manual_group("Synthetic Quest III (Demo)")
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    assert res.written
    text = (tmp_path / "rtfm" / f"{res.basename}.rtfm").read_text()
    assert "joypad move" in text


def test_multidisk_variants_share_one_rtfm(tmp_path):
    # Two multi-disk groups sharing a release_basename reuse ONE .rtfm path.
    root = tmp_path / "instructions"
    _write_sources(root, {"Synthetic Quest III.txt": b"joypad"})
    names = [f"Synthetic Quest III (Disk {n} of 2)" for n in (1, 2)]
    groups = group_records([parse_filename(f"{n}.adf") for n in names])
    assert len(groups) == 1
    g = groups[0]
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    r1 = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "r1", sources=srcs)
    r2 = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "r2", sources=srcs)
    # Same canonical .rtfm path is reused across the variant set.
    assert r1.written and r2.written
    assert r1.basename == r2.basename == "Synthetic Quest III"
    assert (tmp_path / "r1" / "Synthetic Quest III.rtfm").exists()
    assert (tmp_path / "r2" / "Synthetic Quest III.rtfm").exists()


# ---------------------------------------------------------------------------
# Determinism: identical inputs -> identical results
# ---------------------------------------------------------------------------


def test_score_source_match_is_stable():
    g = _manual_group("The Chaos Engine")
    s = _src("Chaos Engine, The")
    a = rc.score_source_match(s, g)
    b = rc.score_source_match(s, g)
    assert a == b
    # Also stable via the legacy helper.
    assert rc._source_matches_group(s, g) == rc._source_matches_group(s, g)


def test_build_results_identical_across_runs(tmp_path):
    roots = {
        "manuals": tmp_path / "manuals",
        "instructions": tmp_path / "instructions",
        "cheats": tmp_path / "cheats",
    }
    _write_sources(roots["manuals"], {"Example Space Tactics.txt": b"Insert disk 1."})
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Fire: button 1"})
    _write_sources(roots["cheats"], {"Example Space Tactics.txt": b"Cheat: xyz"})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    r1 = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "r1", sources=srcs)
    r2 = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "r2", sources=srcs)
    assert r1.written and r2.written
    assert (tmp_path / "r1" / "Example Space Tactics.rtfm").read_bytes() == (
        tmp_path / "r2" / "Example Space Tactics.rtfm"
    ).read_bytes()


def test_auto_accept_exact_emits_no_review(tmp_path):
    # A clean exact match auto-accepts (no review routing).
    root = tmp_path / "instructions"
    _write_sources(root, {"Example Space Tactics.txt": b"Fire: button 1"})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    assert res.written
    assert not res.routed_for_review
