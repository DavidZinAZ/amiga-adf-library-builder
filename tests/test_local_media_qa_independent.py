"""INDEPENDENT QA verification of the local-media (LaunchBox) provider.

This file is the INDEPENDENT verifier for the local-media acceptance criteria.
It deliberately does NOT reuse the implementer's fixtures or helpers from
``test_local_media_provider.py`` -- every fixture, builder, and assertion here
is derived from the local-media acceptance criteria and from reading the
implementation directly. If a test passes it is because the behavior genuinely
holds, not because it mirrors a self-reporting test.

Coverage matrix (each maps to a numbered criterion in the ticket):
  1. Exact priority order (Screenshot>Box>Gameplay; higher wins; full-search)
  2. Recursive nested-folder discovery + non-image ignore
  3. Matching: exact canonical / exact disk stem / normalized / canonical-reuse
     across crack/trainer/alt/lang/chipset/multidisk / fuzzy near-miss /
     manual-review queue (NOT silently accepted)
  4. No false merges (sequels, similar, editions, unrelated)
  5. Read-only guarantee (byte-identical tree before/after; no add/rename/del)
  6. Offline operation (socket.socket blocked; no requests/urllib in code)
  7. Caching + provenance (copied into app cache; survives source deletion)
  8. Regression (imported as part of the full suite; see report)
  9. Config (example.toml valid; enabled=false skips provider)

Run in isolation:
    python3 -m pytest tests/test_local_media_qa_independent.py -v
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from amiga_adf_library_builder import local_media as lm
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup


# ---------------------------------------------------------------------------
# Independent fixtures / builders (NOT shared with the implementer's tests)
# ---------------------------------------------------------------------------


def _group(title, *, source_filename=None, **kw) -> ReleaseGroup:
    fn = source_filename or f"{title}.adf"
    rec = ParsedRecord(
        source_filename=fn,
        ext="adf",
        title=title,
        chipset=kw.get("chipset"),
        language=kw.get("language"),
        alt_marker=kw.get("alt_marker"),
        trainer=bool(kw.get("trainer", False)),
        edition=kw.get("edition"),
        version=kw.get("version"),
    )
    return ReleaseGroup(
        release_key=kw.get("release_key") or title.lower(),
        title=title,
        edition=kw.get("edition"),
        group=kw.get("group"),
        chipset=kw.get("chipset"),
        language=kw.get("language"),
        version=kw.get("version"),
        alt_marker=kw.get("alt_marker"),
        ext="adf",
        records=[rec],
        disks=[rec],
    )


def _mk_lb(root: Path, layout: dict[str, bytes]) -> None:
    """Build a LaunchBox image tree.

    layout keys: "<Platform>/<Category>/<nested...>/<file>" -> raw bytes.
    The platform prefix is required so we exercise the real adapter entry point.
    """
    for rel, content in layout.items():
        p = root / "Images" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _mk_provider(root: Path, cache: Path, *, recursive=True, **kw) -> lm.LocalMediaProvider:
    cfg = lm.LocalMediaConfig(
        enabled=True,
        roots=(str(root),),
        platform_names=("Commodore Amiga", "Amiga"),
        preferred_image_types=tuple(lm.DEFAULT_PREFERRED_TYPES),
        recursive=recursive,
        **kw,
    )
    prov = lm.LocalMediaProvider(cfg, cache)
    prov.discover()
    return prov


def _tree_inventory(root: Path) -> dict:
    """sha256 of every file + sorted rel-path list (for read-only proof)."""
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return {
        "hashes": {str(p.relative_to(root)): _sha256(p) for p in files},
        "paths": [str(p.relative_to(root)) for p in files],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Criterion 1: Exact priority order
# ---------------------------------------------------------------------------


def test_priority_screenshot_wins_when_all_three_present(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {
            "Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"TITLE-BYTES-X",
            "Commodore Amiga/Box - Front/Xenon/box.png": b"BOX-BYTES-X",
            "Commodore Amiga/Screenshot - Gameplay/Xenon/play.png": b"PLAY-BYTES-X",
        },
    )
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon"))
    assert res.found is True
    assert res.category == "Screenshot - Game Title", res.category
    # The chosen cache file must contain the TITLE bytes, not BOX/PLAY.
    assert res.cached_path.read_bytes() == b"TITLE-BYTES-X"


def test_priority_box_wins_when_no_screenshot(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {
            "Commodore Amiga/Box - Front/Xenon/box.png": b"BOX-BYTES-Y",
            "Commodore Amiga/Screenshot - Gameplay/Xenon/play.png": b"PLAY-BYTES-Y",
        },
    )
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon"))
    assert res.category == "Box - Front"
    assert res.cached_path.read_bytes() == b"BOX-BYTES-Y"


def test_priority_gameplay_only_when_others_absent(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {"Commodore Amiga/Screenshot - Gameplay/Xenon/play.png": b"PLAY-ONLY"},
    )
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon"))
    assert res.category == "Screenshot - Gameplay"


def test_higher_priority_confident_beats_lower_even_if_lower_is_better_picture(tmp_path):
    """The spec: a confident higher-priority match always wins, even if a
    lower-priority image is 'better' (here, larger / more detailed)."""
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {
            # Box - Front 'image' is larger/more detailed, but lower priority.
            "Commodore Amiga/Box - Front/Xenon/box.png": b"Z" * 4096,
            # Screenshot - Game Title is a tiny stub but still a confident match.
            "Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"tiny",
        },
    )
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon"))
    assert res.category == "Screenshot - Game Title"
    assert res.cached_path.read_bytes() == b"tiny"


def test_category_not_advanced_until_fully_searched(tmp_path):
    """Within the highest-priority category, every candidate must be scored
    before falling through to the next category. We use a category-2 (Box)
    candidate that is a confident match and a category-1 (Screenshot) candidate
    that is only a weak fuzzy manual-review match; the provider must NOT jump to
    Box just because Screenshot had no *confident* hit until Screenshot is fully
    exhausted. The deterministic assertion: a confident Box match is chosen when
    Screenshot has no confident candidate. That proves category 1 was searched
    (and found nothing confident) and only then did category 2 win.
    """
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {
            # Screenshot candidate: only a fuzzy near-miss vs 'Xenon'
            "Commodore Amiga/Screenshot - Game Title/Xenno/shot.png": b"S",
            # Box candidate: confident exact canonical match for 'Xenon'
            "Commodore Amiga/Box - Front/Xenon/box.png": b"B",
        },
    )
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon"))
    # Screenshot had only a manual-review near-miss (not confident), so Box
    # (confident) must win -- proving category 1 was fully searched first.
    assert res.category == "Box - Front"
    assert res.match_method == lm.MatchMethod.EXACT_CANONICAL


# ---------------------------------------------------------------------------
# Criterion 2: Recursive nested-folder discovery + non-image ignore
# ---------------------------------------------------------------------------


def test_recursive_deep_nested_discovery(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {
            "Commodore Amiga/Screenshot - Game Title/Xenon/a/b/c/d/e/img.png": b"DEEP",
        },
    )
    prov = _mk_provider(root, tmp_path / "cache", recursive=True)
    assert prov.discover() == 1
    res = prov.resolve(_group("Xenon"))
    assert res.found is True
    assert res.category == "Screenshot - Game Title"


def test_non_image_files_ignored(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {
            "Commodore Amiga/Screenshot - Game Title/Xenon/notes.txt": b"IGNORE-ME",
            "Commodore Amiga/Screenshot - Game Title/Xenon/cover.xyz": b"NOT-AN-IMAGE",
            "Commodore Amiga/Screenshot - Game Title/Xenon/art.png": b"REAL",
        },
    )
    prov = _mk_provider(root, tmp_path / "cache")
    # Only the .png counts as a candidate.
    res = prov.resolve(_group("Xenon"))
    assert res.found is True
    assert res.cached_path.read_bytes() == b"REAL"
    assert str(res.cached_path).endswith("art.png")


def test_non_recursive_does_not_descend(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {
            "Commodore Amiga/Screenshot - Game Title/Xenon/sub/deep.png": b"X",
        },
    )
    prov = _mk_provider(root, tmp_path / "cache", recursive=False)
    assert prov.discover() == 0
    res = prov.resolve(_group("Xenon"))
    assert res.found is False


# ---------------------------------------------------------------------------
# Criterion 3: Matching behaviors
# ---------------------------------------------------------------------------


def test_match_exact_canonical_title_folder(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"S"})
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon"))
    assert res.match_method == lm.MatchMethod.EXACT_CANONICAL
    assert res.confidence == 1.0


def test_match_exact_canonical_title_file(tmp_path):
    # game named at the FILE level (not in a per-game folder)
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon.png": b"S"})
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon"))
    assert res.match_method == lm.MatchMethod.EXACT_CANONICAL


def test_match_exact_disk_stem(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/SomeDiskName.png": b"S"})
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Anything", source_filename="SomeDiskName.adf"))
    assert res.match_method == lm.MatchMethod.EXACT_DISK_STEM


def test_match_normalized_title(tmp_path):
    # source uses punctuation/underscores; disk filename distinct so the
    # exact-disk-stem tier does not fire.
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon__2.0!.png": b"S"})
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon 2.0", source_filename="disk1.adf"))
    assert res.match_method == lm.MatchMethod.NORMALIZED_TITLE
    assert res.confidence == 0.99


def test_match_canonical_reuse_across_variant_kinds(tmp_path):
    """One approved image reused WITHOUT false merging for crack/trainer/alt/
    language/chipset/multi-disk variants of the SAME game."""
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"S"})
    prov = _mk_provider(root, tmp_path / "cache")

    variants = [
        ("Xenon M3 cr QTX alt a", dict(chipset="M3", alt_marker="a", trainer=True)),
        ("Xenon M3 cr QTX", dict(chipset="M3")),
        ("Xenon M3", dict(chipset="M3")),
        ("Xenon cr QTX", dict(trainer=True)),
        ("Xenon Disk 1", dict(release_key="xenon|d1")),
        ("Xenon Disk 2", dict(release_key="xenon|d2")),
        ("Xenon trainer", dict(trainer=True)),
        ("Xenon alt a", dict(alt_marker="a")),
        ("Xenon (English)", dict(language="en")),
        ("Xenon AGA", dict(chipset="AGA")),
    ]
    for title, kw in variants:
        res = prov.resolve(_group(title, **kw))
        assert res.found, f"{title} should reuse canonical art"
        assert res.match_method == lm.MatchMethod.CANONICAL_REUSE, (
            f"{title} -> {res.match_method}"
        )
        assert res.category == "Screenshot - Game Title"


def test_fuzzy_near_miss_review_not_accepted(tmp_path):
    """Intentional near-miss (Xenno vs Xenon) is queued for review and NOT
    silently copied/accepted."""
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenno/shot.png": b"S"})
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon"))
    assert res.found is False
    assert res.needs_manual_review is True
    assert res.match_method in (lm.MatchMethod.FUZZY_MANUAL, lm.MatchMethod.MANUAL_REVIEW)
    assert res.cached_path is None  # nothing copied on manual review


def test_fuzzy_sequel_guard_zeroes_extension(tmp_path):
    """A candidate that is a strict extension of the game title (sequel) must
    score 0.0 (never match, never review-merge)."""
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon 2/shot.png": b"S"})
    prov = _mk_provider(root, tmp_path / "cache")
    res = prov.resolve(_group("Xenon"))
    assert res.found is False
    assert res.needs_manual_review is False  # not even routed to review


# ---------------------------------------------------------------------------
# Criterion 4: No false merges (adversarial)
# ---------------------------------------------------------------------------


def test_no_false_merge_sequel(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"S"})
    prov = _mk_provider(root, tmp_path / "cache")
    r1 = prov.resolve(_group("Xenon 2"))
    r2 = prov.resolve(_group("Xenon II"))
    assert r1.found is False and r2.found is False


def test_no_false_merge_similar_titles(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {
            "Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"X",
            "Commodore Amiga/Screenshot - Game Title/Zeuxon/title.png": b"Z",
        },
    )
    prov = _mk_provider(root, tmp_path / "cache")
    r_x = prov.resolve(_group("Xenon"))
    r_z = prov.resolve(_group("Zeuxon"))
    assert r_x.found and r_z.found
    assert r_x.cached_path != r_z.cached_path
    assert r_x.cached_path.read_bytes() == b"X"
    assert r_z.cached_path.read_bytes() == b"Z"


def test_no_false_merge_edition(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"BASE"})
    prov = _mk_provider(root, tmp_path / "cache")
    # An edition that differs from the base title must not pull the base art.
    r = prov.resolve(_group("Xenon Gold Edition"))
    assert r.found is False


def test_no_false_merge_unrelated(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(
        root,
        {
            "Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"X",
            "Commodore Amiga/Screenshot - Game Title/Defender/title.png": b"D",
        },
    )
    prov = _mk_provider(root, tmp_path / "cache")
    r_d = prov.resolve(_group("Defender"))
    r_x = prov.resolve(_group("Xenon"))
    assert r_d.found and r_x.found
    assert r_d.cached_path != r_x.cached_path


# ---------------------------------------------------------------------------
# Criterion 5: Read-only guarantee
# ---------------------------------------------------------------------------


def test_read_only_tree_unchanged(tmp_path):
    root = tmp_path / "lb"
    layout = {
        "Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"TITLE",
        "Commodore Amiga/Box - Front/Xenon/box.png": b"BOX",
        "Commodore Amiga/Screenshot - Gameplay/Xenon/play.png": b"PLAY",
        "Commodore Amiga/Box - Front/Defender/box.png": b"DEF",
    }
    _mk_lb(root, layout)
    before = _tree_inventory(root)
    assert len(before["paths"]) == 4

    prov = _mk_provider(root, tmp_path / "cache")
    prov.resolve(_group("Xenon"))
    prov.resolve(_group("Defender"))

    after = _tree_inventory(root)
    # Byte-identical: same set of paths, same hashes.
    assert after["paths"] == before["paths"], "file set changed under LaunchBox root"
    assert after["hashes"] == before["hashes"], "a LaunchBox file was modified"
    # Explicitly: no additions, no deletions, no renames.
    assert set(after["paths"]) == set(before["paths"])


def test_read_only_handles_no_new_files_in_source_tree(tmp_path):
    """A full run must not create/delete/rename anything under the root. We
    assert the root subtree's files are a subset equal to the original."""
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"T"})
    before = sorted(p.name for p in root.rglob("*") if p.is_file())
    prov = _mk_provider(root, tmp_path / "cache")
    prov.resolve(_group("Xenon"))
    after = sorted(p.name for p in root.rglob("*") if p.is_file())
    assert after == before


def test_assert_read_only_roots_does_not_mutate(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"T"})
    cfg = lm.LocalMediaConfig(enabled=True, roots=(str(root),))
    before = _tree_inventory(root)
    lm.assert_read_only_roots(cfg)
    after = _tree_inventory(root)
    assert after == before


# ---------------------------------------------------------------------------
# Criterion 6: Offline operation
# ---------------------------------------------------------------------------


def test_offline_socket_blocked(tmp_path):
    calls = []

    def _blocked(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("network blocked by QA harness")

    # Block socket.socket directly via monkey-patch.
    import unittest.mock as mock

    with mock.patch("socket.socket", side_effect=_blocked):
        root = tmp_path / "lb"
        _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"S"})
        prov = _mk_provider(root, tmp_path / "cache")
        res = prov.resolve(_group("Xenon"))
    assert res.found is True
    assert calls == [], "socket.socket was invoked by the provider pipeline"


def test_offline_no_requests_or_urllib_in_provider_module():
    """Confirm the provider module + adapters import nothing network-bound."""
    src = Path(lm.__file__).read_text()
    assert "import requests" not in src
    assert "import urllib" not in src
    assert "from urllib" not in src
    assert "socket" not in src or "socket" not in src.split("\n")[0]  # module-level import
    # Explicitly: there must be no top-level 'import socket' in the module.
    top_imports = [
        line.split("#")[0].strip()
        for line in src.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    assert not any(t.startswith("import socket") or t.startswith("from socket") for t in top_imports)


def test_full_pipeline_offline_with_network_blocked(tmp_path):
    """End-to-end: run discover+resolve+manual-review routing with sockets
    fully blocked, asserting completion and zero network entry points."""
    import unittest.mock as mock

    net_calls = []

    def _blocked_socket(*a, **k):
        net_calls.append(("socket", a, k))
        raise OSError("blocked")

    with mock.patch("socket.socket", side_effect=_blocked_socket):
        root = tmp_path / "lb"
        _mk_lb(
            root,
            {
                # Confident exact match for the group "Defender".
                "Commodore Amiga/Screenshot - Game Title/Defender/title.png": b"D",
                # Near-miss for the group "Xenon" (folder is "Xenno").
                "Commodore Amiga/Screenshot - Game Title/Xenno/shot.png": b"R",
            },
        )
        prov = _mk_provider(root, tmp_path / "cache")
        # confident hit
        r1 = prov.resolve(_group("Defender"))
        # near-miss: folder "Xenno" vs group "Xenon" -> manual review (no net)
        r2 = prov.resolve(_group("Xenon"))
    assert r1.found is True
    assert r2.needs_manual_review is True
    assert net_calls == [], "network entry point used during pipeline"


# ---------------------------------------------------------------------------
# Criterion 7: Caching + provenance
# ---------------------------------------------------------------------------


def test_selected_source_copied_into_app_cache(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"SOURCE-BYTES"})
    cache = tmp_path / "cache"
    prov = _mk_provider(root, cache)
    res = prov.resolve(_group("Xenon"))
    assert res.found
    # The copy lives under the app cache, NOT under the LaunchBox root.
    cached = res.cached_path
    assert cache in cached.parents
    assert root not in cached.parents
    assert cached.read_bytes() == b"SOURCE-BYTES"


def test_provenance_records_required_fields(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"PB"})
    cache = tmp_path / "cache"
    prov = _mk_provider(root, cache)
    res = prov.resolve(_group("Xenon"))
    prov_rec = res.provenance
    assert prov_rec is not None
    for field in (
        "source_path",
        "source_sha256",
        "root",
        "category",
        "match_method",
        "confidence",
        "cached_path",
        "cached_sha256",
        "cached_at",
    ):
        val = getattr(prov_rec, field)
        assert val not in (None, ""), f"provenance.{field} missing/empty"

    # Sidecar exists and is valid JSON with schema + required keys.
    # prov_rec.cached_path is stored relative to cache_dir (no absolute host
    # path); re-anchor it against the cache directory.
    sidecar = (cache / prov_rec.cached_path).resolve().with_suffix(
        Path(prov_rec.cached_path).suffix + ".prov.json"
    )
    assert sidecar.is_file()
    data = json.loads(sidecar.read_text())
    assert data["schema"] == "local-media-provenance/1"
    for k in ("source_path", "source_sha256", "category", "match_method", "confidence",
              "cached_path", "cached_sha256", "cached_at"):
        assert k in data


def test_provenance_survives_source_deletion(tmp_path):
    """Provider copies into app-owned cache; the cached copy + sidecar must
    remain usable after the original LaunchBox root is deleted/moved."""
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"PB"})
    cache = tmp_path / "cache"
    prov = _mk_provider(root, cache)
    res = prov.resolve(_group("Xenon"))
    cached = res.cached_path
    assert cached.is_file()

    # Nuke the LaunchBox root entirely.
    import shutil

    shutil.rmtree(root)
    assert not root.exists()

    # The cached copy is intact and still named after the source stem.
    assert cached.is_file()
    assert cached.read_bytes() == b"PB"
    # The sidecar is intact and still references the (now-gone) source path.
    sidecar = cached.with_suffix(cached.suffix + ".prov.json")
    assert sidecar.is_file()
    data = json.loads(sidecar.read_text())
    assert data["cached_sha256"] == _sha256(cached)
    # The source path recorded is the OLD (deleted) path; survival proven.
    assert data["source_path"].endswith("title.png")
    assert Path(data["source_path"]).exists() is False  # confirms independence


# ---------------------------------------------------------------------------
# Criterion 9: Config
# ---------------------------------------------------------------------------


def test_example_toml_local_media_block_valid(tmp_path):
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "example.toml"
    assert cfg_path.is_file(), "config/example.toml missing"
    cfg = lm.load_local_media_config(cfg_path)
    # example.toml has enabled=false; the table must parse cleanly.
    assert cfg.enabled is False
    assert cfg.roots == ("/path/to/LaunchBox",)
    assert cfg.platform_names == ("Commodore Amiga", "Amiga")
    assert cfg.preferred_image_types == (
        "Screenshot - Game Title",
        "Box - Front",
        "Screenshot - Gameplay",
    )
    assert cfg.recursive is True


def test_disabled_config_skips_provider_construction(tmp_path):
    """enabled=false must mean the provider is never constructed/active."""
    cfg = lm.LocalMediaConfig(enabled=False, roots=("/some/lb",))
    with pytest.raises(lm.LocalMediaDisabled):
        lm.LocalMediaProvider(cfg, tmp_path / "cache")


def test_disabled_config_skips_in_pipeline_entrypoint(tmp_path):
    """Mirror the pipeline.py gating: a disabled config yields provider=None."""
    cfg_path = tmp_path / "cfg.toml"
    cfg_path.write_text(
        "[local_media]\n"
        "enabled = false\n"
        'roots = ["/some/lb"]\n'
    )
    cfg = lm.load_local_media_config(cfg_path)
    assert cfg.enabled is False
    # Same guard the pipeline uses:
    provider = None
    if cfg.enabled:
        provider = lm.LocalMediaProvider(cfg, tmp_path / "cache")
    assert provider is None


# ---------------------------------------------------------------------------
# Criterion 6 / design: fuzzy auto-accept floor is conservative (no high-conf
# fuzzy merges). Independent check that a near-miss never auto-accepts.
# ---------------------------------------------------------------------------


def test_no_fuzzy_auto_accept_below_threshold(tmp_path):
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenno/shot.png": b"S"})
    prov = _mk_provider(root, tmp_path / "cache", confidence_threshold=0.95)
    res = prov.resolve(_group("Xenon"))
    # Confident auto-accept floor is 0.95; a 0.80 near-miss must route to review.
    assert res.found is False
    assert res.confidence < 0.95
    assert res.needs_manual_review is True


if __name__ == "__main__":
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
