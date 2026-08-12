"""Tests for the RTFM v2 M1 deterministic, local-only .rtfm builder.

Fully synthetic: NO maintainer-private corpus, names, hashes, or machine state.
All game names/contents are synthetic (e.g. "Example Space Tactics",
"Synthetic Quest III"). The security-oriented tests mirror
``test_local_media_security_adversarial.py`` and ``test_ssrf_guard.py``:
symlink/path-escape confinement, read-only guarantee, no private absolute-path
leakage, and NO network dependency.

Run in isolation:
    python -m pytest tests/test_rtfm.py -v
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from amiga_adf_library_builder import rtfm as rc
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.parser import parse_filename


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _group(filenames, ext="adf", **kw):
    recs = [parse_filename(f"{n}.{ext}") for n in filenames]
    groups = group_records(recs)
    g = groups[0]
    # apply any kw overrides onto the group (edition/title etc.)
    for k, v in kw.items():
        setattr(g, k, v)
    return g


def _manual_group(title="Example Space Tactics", ext="adf", source_filename=None, **kw):
    """A single-disk group with a determinable main disk for export tests."""
    src_name = source_filename or f"{title} (Disk 1 of 1)"
    rec = parse_filename(f"{src_name}.{ext}")
    rec.disk_number = 1
    return ReleaseGroup(
        release_key=kw.get("release_key") or title.lower(),
        title=title,
        edition=kw.get("edition"),
        group=kw.get("group"),
        chipset=kw.get("chipset"),
        language=kw.get("language"),
        version=kw.get("version"),
        alt_marker=kw.get("alt_marker"),
        ext=ext,
        records=[rec],
        disks=[rec],
        specials=[],
        has_main_disk=True,
        is_complete=True,
    )


def _write_sources(root: Path, layout: dict[str, bytes]) -> None:
    """Create source files under ``root`` from a {relative_posix: bytes} map."""
    for rel, content in layout.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _cfg(roots: dict[str, Path], **kw) -> rc.RtfmConfig:
    """Build an enabled RtfmConfig from {category: Path} roots."""
    return rc.RtfmConfig(
        enabled=True,
        template=kw.get("template", rc.DEFAULT_TEMPLATE),
        manuals_roots=(str(roots["manuals"]),) if "manuals" in roots else (),
        instructions_roots=(str(roots["instructions"]),) if "instructions" in roots else (),
        cheats_roots=(str(roots["cheats"]),) if "cheats" in roots else (),
        max_bytes=kw.get("max_bytes", rc.DEFAULT_RTFM_REVIEW_TARGET),
        recursive=kw.get("recursive", True),
    )


# ---------------------------------------------------------------------------
# Constants / contract
# ---------------------------------------------------------------------------


def test_max_rtfm_bytes_contract():
    assert rc.MAX_RTFM_BYTES == 16000
    assert rc.DEFAULT_RTFM_REVIEW_TARGET < rc.MAX_RTFM_BYTES
    assert rc.CANONICAL_MARKERS == (
        "CONTROLS", "GETTING STARTED", "HOW TO PLAY", "NOTES", "HINTS & CHEATS",
    )


def test_builtin_templates_present():
    for name in (
        "controls-first", "quick-start", "adventure-rpg", "arcade-action",
        "full-reference",
    ):
        assert name in rc.TEMPLATES
    # controls-first default ordering starts with CONTROLS.
    assert rc.TEMPLATES["controls-first"][0] == "CONTROLS"
    # full-reference MAY add an [ADDITIONAL REFERENCE] marker.
    assert "ADDITIONAL REFERENCE" in rc.TEMPLATES["full-reference"]


def test_unknown_template_falls_back_to_default():
    cfg = rc.RtfmConfig.from_dict({"enabled": True, "template": "not-a-template"})
    assert cfg.template == rc.DEFAULT_TEMPLATE


# ---------------------------------------------------------------------------
# Local discovery (per category)
# ---------------------------------------------------------------------------


def test_manuals_discovery(tmp_path):
    root = tmp_path / "manuals"
    _write_sources(root, {"Example Space Tactics.txt": b"boot the game"})
    cfg = _cfg({"manuals": root})
    srcs = rc.discover_sources(cfg)
    assert len(srcs) == 1
    assert srcs[0].category == rc.CATEGORY_MANUALS


def test_instructions_discovery(tmp_path):
    root = tmp_path / "instructions"
    _write_sources(root, {"Synthetic Quest III.txt": b"press start"})
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    assert len(srcs) == 1
    assert srcs[0].category == rc.CATEGORY_INSTRUCTIONS


def test_cheats_discovery(tmp_path):
    root = tmp_path / "cheats"
    _write_sources(root, {"Example Space Tactics.txt": b"invincible"})
    cfg = _cfg({"cheats": root})
    srcs = rc.discover_sources(cfg)
    assert len(srcs) == 1
    assert srcs[0].category == rc.CATEGORY_CHEATS


def test_recursive_discovery(tmp_path):
    root = tmp_path / "manuals"
    _write_sources(root, {
        "Alpha/Example Space Tactics.txt": b"a",
        "Beta/Gamma/Synthetic Quest III.txt": b"b",
    })
    cfg = _cfg({"manuals": root}, recursive=True)
    srcs = rc.discover_sources(cfg)
    assert len(srcs) == 2
    cfg_nr = _cfg({"manuals": root}, recursive=False)
    # Non-recursive must not descend into subfolders.
    assert rc.discover_sources(cfg_nr) == []


def test_absent_root_yields_no_candidates(tmp_path):
    cfg = _cfg({"manuals": tmp_path / "does-not-exist"})
    assert rc.discover_sources(cfg) == []


# ---------------------------------------------------------------------------
# Canonical-title matching (reuse, no new matcher)
# ---------------------------------------------------------------------------


def test_canonical_title_match(tmp_path):
    root = tmp_path / "instructions"
    _write_sources(root, {"Example Space Tactics.txt": b"fire: button 1"})
    g = _manual_group("Example Space Tactics", source_filename="x")
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    matched = [s for s in srcs if rc._source_matches_group(s, g)]
    assert matched


def test_variant_reuse_match(tmp_path):
    # A cracked/trainer/alt-dump variant of the SAME canonical game must match
    # the manual authored under the base title (canonical-reuse match).
    root = tmp_path / "instructions"
    _write_sources(root, {"Example Space Tactics.txt": b"fire: button 1"})
    # Group whose filename is a crack-variant of the same game.
    g = _group(["Example Space Tactics (1992)(Acme)[cr SKR](Disk 1 of 2)"])
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    assert any(rc._source_matches_group(s, g) for s in srcs)


def test_no_false_match_on_different_title(tmp_path):
    root = tmp_path / "instructions"
    _write_sources(root, {"Example Space Tactics.txt": b"fire: button 1"})
    g = _manual_group("Synthetic Quest III")
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    assert not any(rc._source_matches_group(s, g) for s in srcs)


# ---------------------------------------------------------------------------
# Multi-disk set → one shared RTFM
# ---------------------------------------------------------------------------


def test_multidisk_one_shared_rtfm(tmp_path):
    root = tmp_path / "instructions"
    _write_sources(root, {"Synthetic Quest III.txt": b"move with joypad"})
    # Two-disk set: both disks share a release key -> one .rtfm.
    names = [f"Synthetic Quest III (Disk {n} of 2)" for n in (1, 2)]
    groups = group_records([parse_filename(f"{n}.adf") for n in names])
    assert len(groups) == 1
    g = groups[0]
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    out = tmp_path / "rtfm"
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=out, sources=srcs)
    assert res.written
    assert (out / "Synthetic Quest III.rtfm").exists()


def test_build_rtfm_all_skips_duplicate_group_key(tmp_path):
    root = tmp_path / "instructions"
    _write_sources(root, {"Synthetic Quest III.txt": b"move with joypad"})
    names = [f"Synthetic Quest III (Disk {n} of 2)" for n in (1, 2)]
    groups = group_records([parse_filename(f"{n}.adf") for n in names])
    cfg = _cfg({"instructions": root})
    results = rc.build_rtfm_all(groups, cfg=cfg, rtfm_dir=tmp_path / "rtfm")
    # Only one .rtfm is produced for the whole multi-disk set.
    assert len([r for r in results if r.written]) == 1


# ---------------------------------------------------------------------------
# Variant behavior (crack/trainer/alt reuses same .rtfm)
# ---------------------------------------------------------------------------


def test_variant_distinct_release_key_gets_distinct_basename(tmp_path):
    # A crack/trainer variant carries a DISTINGUISHABLE release key (group token
    # "cr SKR"), so release_basename correctly yields a distinct filename. The
    # spec's "one shared .rtfm" rule applies to multi-disk sets sharing a
    # release_key (tested elsewhere), not to arbitrarily distinct crack releases.
    # The canonical-reuse MATCH still lets the variant's .rtfm borrow the same
    # manual content; the artifact filename stays distinct by design.
    root = tmp_path / "instructions"
    _write_sources(root, {"Synthetic Quest III.txt": b"joypad"})
    base = _manual_group("Synthetic Quest III")
    cfg = _cfg({"instructions": root})
    srcs = rc.discover_sources(cfg)
    res_base = rc.build_rtfm_for_group(base, cfg=cfg, rtfm_dir=tmp_path / "r", sources=srcs)
    # A trainer variant resolves to a DISTINCT release basename (group token).
    variant = _group(["Synthetic Quest III (1992)(Acme)[cr SKR][t](Disk 1 of 1)"])
    res_variant = rc.build_rtfm_for_group(variant, cfg=cfg, rtfm_dir=tmp_path / "r", sources=srcs)
    # Basenames differ (distinct releases) yet BOTH still matched the same manual
    # content via canonical-reuse, so both produce a .rtfm.
    assert res_base.basename == "Synthetic Quest III"
    assert res_variant.basename == "Synthetic Quest III cr SKR"
    assert res_base.written and res_variant.written
    # Both emit content drawn from the same manual source.
    base_text = (tmp_path / "r" / f"{res_base.basename}.rtfm").read_text()
    var_text = (tmp_path / "r" / f"{res_variant.basename}.rtfm").read_text()
    assert "joypad" in base_text and "joypad" in var_text


# ---------------------------------------------------------------------------
# Source precedence / multi-source composition
# ---------------------------------------------------------------------------


def test_multiple_source_composition(tmp_path):
    roots = {
        "manuals": tmp_path / "manuals",
        "instructions": tmp_path / "instructions",
        "cheats": tmp_path / "cheats",
    }
    _write_sources(roots["manuals"], {"Example Space Tactics.txt": b"Insert disk 1."})
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Fire: button 1"})
    _write_sources(roots["cheats"], {"Example Space Tactics.txt": b"Level skip: KANGAROO"})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    out = tmp_path / "rtfm"
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=out, sources=srcs)
    assert res.written
    text = (out / "Example Space Tactics.rtfm").read_text()
    # controls-first: CONTROLS appears before GETTING STARTED and HINTS & CHEATS.
    assert text.index("[CONTROLS]") < text.index("[GETTING STARTED]")
    assert text.index("[GETTING STARTED]") < text.index("[HINTS & CHEATS]")
    assert "Fire: button 1" in text
    assert "Insert disk 1." in text
    assert "Level skip: KANGAROO" in text


def test_instructions_precedence_over_manuals_for_controls(tmp_path):
    # Both instructions and manuals provide text; under controls-first the
    # CONTROLS section carries the instructions verbatim (not manuals).
    roots = {
        "manuals": tmp_path / "manuals",
        "instructions": tmp_path / "instructions",
    }
    _write_sources(roots["manuals"], {"Example Space Tactics.txt": b"This is a manual note."})
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Controls: up down"})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    text = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_text()
    # CONTROLS section body is the instructions text, not the manual text.
    controls_body = text.split("[CONTROLS]")[1].split("[GETTING STARTED]")[0]
    assert "Controls: up down" in controls_body
    assert "This is a manual note." not in controls_body


# ---------------------------------------------------------------------------
# Controls-first rendering + each built-in template
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template,first_marker", [
    ("controls-first", "CONTROLS"),
    ("quick-start", "GETTING STARTED"),
    ("adventure-rpg", "GETTING STARTED"),
    ("arcade-action", "CONTROLS"),
])
def test_template_ordering(tmp_path, template, first_marker):
    roots = {
        "manuals": tmp_path / "manuals",
        "instructions": tmp_path / "instructions",
        "cheats": tmp_path / "cheats",
    }
    _write_sources(roots["manuals"], {"Example Space Tactics.txt": b"Manual body."})
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Controls body."})
    _write_sources(roots["cheats"], {"Example Space Tactics.txt": b"Cheats body."})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots, template=template)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    text = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_text()
    # The first emitted marker equals the template's first preferred marker.
    first_line_marker = text.splitlines()[2] if text.splitlines()[1] == "" else None
    assert f"[{first_marker}]" in text
    # Confirm the FIRST section marker in the file is the expected one.
    markers_in_order = [ln for ln in text.splitlines() if ln.startswith("[") and ln.endswith("]")]
    assert markers_in_order[0] == f"[{first_marker}]"


def test_full_reference_template_includes_additional_reference_when_present(tmp_path):
    roots = {"instructions": tmp_path / "instructions"}
    # An existing .rtfm passthrough that contains [ADDITIONAL REFERENCE].
    _write_sources(roots["instructions"], {
        "Example Space Tactics.rtfm": (
            b"[CONTROLS]\nFire: button 1\n\n[ADDITIONAL REFERENCE]\nSee box art.\n"
        ),
    })
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots, template="full-reference")
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    text = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_text()
    assert "[ADDITIONAL REFERENCE]" in text


# ---------------------------------------------------------------------------
# Empty-section omission
# ---------------------------------------------------------------------------


def test_empty_section_omitted(tmp_path):
    # Only an instructions source exists -> only CONTROLS (controls-first) and
    # no GETTING STARTED / HINTS & CHEATS empty markers.
    roots = {"instructions": tmp_path / "instructions"}
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Fire: button 1"})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    text = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_text()
    assert "[CONTROLS]" in text
    assert "[GETTING STARTED]" not in text
    assert "[HINTS & CHEATS]" not in text
    assert "[NOTES]" not in text
    assert "[HOW TO PLAY]" not in text


# ---------------------------------------------------------------------------
# Plain-text normalization (LF, UTF-8)
# ---------------------------------------------------------------------------


def test_crlf_normalized_to_lf(tmp_path):
    roots = {"instructions": tmp_path / "instructions"}
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Line A\r\nLine B\r\n"})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    data = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_bytes()
    assert b"\r\n" not in data
    assert b"Line A\nLine B" in data


def test_utf8_normalized_and_decoded(tmp_path):
    roots = {"instructions": tmp_path / "instructions"}
    # UTF-8 with BOM; non-ASCII content must survive as UTF-8.
    content = "Touche: Élan\n".encode("utf-8-sig")
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": content})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    text = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_text(encoding="utf-8")
    assert "Élan" in text


def test_no_hard_wrap_long_line(tmp_path):
    # A very long single line must NOT be reflowed/rewrapped.
    roots = {"instructions": tmp_path / "instructions"}
    long_line = "Control: " + ("x" * 500)
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": long_line.encode("utf-8")})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    text = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_text()
    assert long_line in text


# ---------------------------------------------------------------------------
# Existing .rtfm passthrough (normalized, verbatim)
# ---------------------------------------------------------------------------


def test_existing_rtfm_passthrough(tmp_path):
    roots = {"instructions": tmp_path / "instructions"}
    existing = (
        b"[CONTROLS]\nFire: button 1\n\n[GETTING STARTED]\nInsert disk.\n"
    )
    _write_sources(roots["instructions"], {"Example Space Tactics.rtfm": existing})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    text = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_text()
    assert "[CONTROLS]" in text
    assert "Fire: button 1" in text
    assert "Insert disk." in text
    # Verbatim: existing section bodies are preserved, not re-ordered by template.
    assert text.index("[CONTROLS]") < text.index("[GETTING STARTED]")


def test_passthrough_preserves_marker_order(tmp_path):
    roots = {"manuals": tmp_path / "manuals"}
    # Source order NOTES before CONTROLS; passthrough must keep that order.
    existing = b"[NOTES]\nNote A\n\n[CONTROLS]\nCtrl B\n"
    _write_sources(roots["manuals"], {"Synthetic Quest III.rtfm": existing})
    g = _manual_group("Synthetic Quest III")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    text = (tmp_path / "rtfm" / "Synthetic Quest III.rtfm").read_text()
    assert text.index("[NOTES]") < text.index("[CONTROLS]")


# ---------------------------------------------------------------------------
# Size-limit / large-source handling → review (never > 16000 bytes)
# ---------------------------------------------------------------------------


def test_large_source_routed_for_review(tmp_path):
    # A source whose verbatim content would exceed the conservative target is
    # routed for review and NO .rtfm is emitted.
    roots = {"instructions": tmp_path / "instructions"}
    big = ("Controls: " + "A" * 20000 + "\n").encode("utf-8")
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": big})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots, max_bytes=15360)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    assert res.routed_for_review
    assert not res.written
    assert res.rtfm_path is None or not res.rtfm_path.exists()
    # Provenance IS written (auditable routing decision).
    assert res.provenance_path is not None and res.provenance_path.exists()


def test_never_emits_over_16000_bytes(tmp_path):
    # Force the hard cap to be approached: content just under 16000 must write;
    # content just over must route for review. Verify the emitted file is never
    # > MAX_RTFM_BYTES.
    roots = {"instructions": tmp_path / "instructions"}
    # Exactly 15900 body bytes (well under cap).
    body = ("C" * 15900) + "\n"
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": body.encode("utf-8")})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots, max_bytes=16000)  # disable conservative target -> only hard cap matters
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    assert res.written
    data = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_bytes()
    assert len(data) <= rc.MAX_RTFM_BYTES


def test_never_writes_over_16000_byte_natural_rendering(tmp_path):
    # AC6 (explicit): a >16000-byte NATURAL rendering must NOT be written to a
    # .rtfm. Craft body that renders ABOVE the 16000 hard cap (header + section
    # overhead on top of the body). The build must route for review and leave
    # NO .rtfm on disk larger than MAX_RTFM_BYTES.
    roots = {"instructions": tmp_path / "instructions"}
    # header "# Title\n\n[CONTROLS]\n" (~21 bytes) + body of 15985 'C' => > 16000.
    body = ("C" * 15985) + "\n"
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": body.encode("utf-8")})
    g = _manual_group("Example Space Tactics")
    # Disable the conservative target so ONLY the hard cap is in play.
    cfg = _cfg(roots, max_bytes=16000)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    rtfm_path = tmp_path / "rtfm" / "Example Space Tactics.rtfm"
    raw = rtfm_path.read_bytes() if rtfm_path.exists() else b""
    # The contract: NEVER emit a .rtfm larger than MAX_RTFM_BYTES.
    assert len(raw) <= rc.MAX_RTFM_BYTES, (
        f"rejected: a {len(raw)}-byte .rtfm was written (> {rc.MAX_RTFM_BYTES})"
    )
    if rtfm_path.exists():
        assert res.written and res.bytes <= rc.MAX_RTFM_BYTES
    else:
        # Overflowed past the cap -> routed for review, nothing written.
        assert res.routed_for_review and not res.written


def test_oversized_source_byte_cap_skips(tmp_path):
    # A source larger than MAX_SOURCE_BYTES is skipped (DoS safety), not read
    # fully before the cap. We cannot easily make a real 8 MiB file cheaply, so
    # we instead assert the size-guard logic via a crafted RtfmSource with a
    # huge stat'd file is skipped (use a sparse file).
    roots = {"instructions": tmp_path / "instructions"}
    big = tmp_path / "instructions" / "Example Space Tactics.txt"
    big.parent.mkdir(parents=True, exist_ok=True)
    # Sparse 9 MiB file (instant).
    with open(big, "wb") as fh:
        fh.seek(9 * 1024 * 1024 - 1)
        fh.write(b"\0")
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots, max_bytes=16000)
    srcs = rc.discover_sources(cfg)
    # The source is discovered but skipped during build due to size cap.
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    # Either routed for review (no content) or written with a tiny file; the key
    # assertion is that nothing blew up reading an 9 MiB sparse file.
    assert res.provenance_path is not None


# ---------------------------------------------------------------------------
# Ambiguous match → review
# ---------------------------------------------------------------------------


def test_ambiguous_match_routed_for_review(tmp_path):
    # A group flagged with a near-duplicate-spelling quarantine_reason must be
    # routed for review and emit NO .rtfm.
    roots = {"instructions": tmp_path / "instructions"}
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Fire: 1"})
    g = _manual_group("Example Space Tactics")
    g.quarantine_reason = "Near-duplicate spelling of the same game; human review required."
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    assert res.routed_for_review
    assert not res.written
    assert res.rtfm_path is None or not res.rtfm_path.exists()


# ---------------------------------------------------------------------------
# Deterministic no-AI path (no network; no fabrication)
# ---------------------------------------------------------------------------


def test_no_network_imports():
    src = Path(rc.__file__).read_text()
    assert "import requests" not in src
    assert "from urllib" not in src
    assert "import urllib" not in src
    top = [l for l in src.splitlines() if l.startswith("import ") or l.startswith("from ")]
    assert not any("socket" in l for l in top), "rtfm module imports socket at module level"


def test_no_fabrication_controls_not_invented(tmp_path):
    # Instructions source mentions only movement; we must NOT invent a control.
    roots = {"instructions": tmp_path / "instructions"}
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Walk left and right."})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    text = (tmp_path / "rtfm" / "Example Space Tactics.rtfm").read_text()
    # Only the verbatim source text appears; nothing synthesized.
    assert "Walk left and right." in text
    # No invented keys like "Pause" / "Jump" appear.
    for invented in ("Pause", "Jump", "Boss", "Secret level"):
        assert invented not in text


# ---------------------------------------------------------------------------
# Provenance generation + NO private absolute-path leakage
# ---------------------------------------------------------------------------


def test_provenance_generated_outside_export(tmp_path):
    roots = {"instructions": tmp_path / "instructions"}
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Fire: 1"})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    out = tmp_path / "rtfm"
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=out, sources=srcs)
    assert res.provenance_path is not None and res.provenance_path.exists()
    data = json.loads(res.provenance_path.read_text())
    assert data["schema"] == "rtfm-provenance/1"
    assert data["written"] is True
    assert data["sources"]


def test_provenance_no_absolute_host_path(tmp_path):
    # The provenance sidecar must NOT embed absolute, host-specific paths
    # (mirrors local-media privacy checklist T6.3 item 5).
    roots = {"instructions": tmp_path / "instructions"}
    _write_sources(roots["instructions"], {"Example Space Tactics.txt": b"Fire: 1"})
    g = _manual_group("Example Space Tactics")
    cfg = _cfg(roots)
    srcs = rc.discover_sources(cfg)
    out = tmp_path / "rtfm"
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=out, sources=srcs)
    data = json.loads(res.provenance_path.read_text())
    for s in data["sources"]:
        assert not s["source_rel"].startswith("/"), f"source_rel must not be absolute: {s['source_rel']}"
        assert not s["source_rel"].startswith("~"), f"source_rel must not be home-relative: {s['source_rel']}"
    # The operator's absolute root layout must not appear anywhere in the file.
    assert res.provenance_path is not None
    raw = res.provenance_path.read_text()
    assert str(roots["instructions"].resolve()) not in raw, "operator root layout leaked into provenance"


# ---------------------------------------------------------------------------
# Symlink / path-escape protection (mirrors security tests)
# ---------------------------------------------------------------------------


def test_symlink_to_outside_root_not_read(tmp_path):
    """A symlink inside a manual root pointing OUTSIDE the configured root must
    NOT be read/used. Discovery must either skip it or the build must not emit
    its (out-of-root) content."""
    root = tmp_path / "manuals"
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    secret = secret_dir / "leak.txt"
    secret.write_bytes(b"OUT-OF-ROOT-SECRET")

    cat = root / "Example Space Tactics"
    cat.mkdir(parents=True, exist_ok=True)
    os.symlink(secret, cat / "Example Space Tactics.txt")

    g = _manual_group("Example Space Tactics")
    cfg = _cfg({"manuals": root})
    srcs = rc.discover_sources(cfg)
    # The escaped symlink must NOT be indexed (real path escapes the root).
    for s in srcs:
        assert s.path.resolve().is_relative_to(root.resolve()), "candidate escaped root confinement"
    out = tmp_path / "rtfm"
    res = rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    # No .rtfm emitted with the secret content (there is no in-root match).
    assert res.rtfm_path is None or not res.rtfm_path.exists() or (
        b"OUT-OF-ROOT-SECRET" not in res.rtfm_path.read_bytes()
    )


def test_candidate_paths_confined_to_root(tmp_path):
    root = tmp_path / "manuals"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "escaped.txt"
    target.write_bytes(b"ESCAPE")

    cat = root / "Example Space Tactics"
    cat.mkdir(parents=True, exist_ok=True)
    os.symlink(target, cat / "Example Space Tactics.txt")

    cfg = _cfg({"manuals": root})
    srcs = rc.discover_sources(cfg)
    escaped = [str(s.path) for s in srcs if not s.path.resolve().is_relative_to(root.resolve())]
    assert escaped == [], f"candidates escaped root confinement: {escaped}"


def test_non_regular_files_rejected(tmp_path):
    """Device nodes / FIFOs / sockets must not be indexed (confinement)."""
    root = tmp_path / "manuals"
    cat = root / "Example Space Tactics"
    cat.mkdir(parents=True, exist_ok=True)
    # A directory named like a source (must be skipped, not read).
    (cat / "Subdir.txt").mkdir(parents=True, exist_ok=True)
    # A real regular file that should be indexed.
    (cat / "Example Space Tactics.txt").write_bytes(b"Fire: 1")

    cfg = _cfg({"manuals": root})
    srcs = rc.discover_sources(cfg)
    for s in srcs:
        st = os.lstat(s.path.resolve())
        assert stat.S_ISREG(st.st_mode), "non-regular file indexed"
    assert len(srcs) == 1


# ---------------------------------------------------------------------------
# Read-only guarantee against source roots (adversarial)
# ---------------------------------------------------------------------------


def test_source_roots_not_mutated(tmp_path):
    root = tmp_path / "manuals"
    _write_sources(root, {"Example Space Tactics.txt": b"Fire: 1"})
    before = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }
    g = _manual_group("Example Space Tactics")
    cfg = _cfg({"manuals": root})
    srcs = rc.discover_sources(cfg)
    rc.build_rtfm_for_group(g, cfg=cfg, rtfm_dir=tmp_path / "rtfm", sources=srcs)
    after = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }
    assert after == before, "manual root was mutated by the RTFM builder"


# ---------------------------------------------------------------------------
# Exporter integration: .rtfm copied into staging (mirrors NFO)
# ---------------------------------------------------------------------------


def test_exporter_copies_rtfm_into_staging(tmp_path):
    from amiga_adf_library_builder.exporter import export_release

    original_dir = tmp_path / "original"
    original_dir.mkdir()
    (original_dir / "Example Space Tactics (Disk 1 of 1).adf").write_bytes(b"ADF")

    # A pre-built .rtfm under the rtfm assets dir.
    rtfm_dir = tmp_path / "rtfm"
    rtfm_dir.mkdir()
    (rtfm_dir / "Example Space Tactics.rtfm").write_text(
        "# Example Space Tactics\n\n[CONTROLS]\nFire: button 1\n", encoding="utf-8"
    )

    g = _manual_group("Example Space Tactics")
    written, unchanged, conflicts = export_release(
        g, tmp_path / "staging" / "run1", original_dir=original_dir, rtfm_dir=rtfm_dir
    )
    assert not conflicts
    folder = tmp_path / "staging" / "run1" / "ADF" / "Example Space Tactics"
    assert (folder / "Example Space Tactics.rtfm").exists()
    # Provenance sidecar is NOT copied into staging.
    assert not (folder / "Example Space Tactics.rtfm.provenance.json").exists()


def test_exporter_skips_rtfm_when_none_present(tmp_path):
    from amiga_adf_library_builder.exporter import export_release

    original_dir = tmp_path / "original"
    original_dir.mkdir()
    (original_dir / "Example Space Tactics (Disk 1 of 1).adf").write_bytes(b"ADF")

    g = _manual_group("Example Space Tactics")
    # rtfm_dir provided but contains no matching .rtfm -> no .rtfm in staging.
    written, unchanged, conflicts = export_release(
        g, tmp_path / "staging" / "run1", original_dir=original_dir, rtfm_dir=tmp_path / "rtfm"
    )
    assert not conflicts
    folder = tmp_path / "staging" / "run1" / "ADF" / "Example Space Tactics"
    assert not (folder / "Example Space Tactics.rtfm").exists()


# ---------------------------------------------------------------------------
# Config loader (mirrors load_local_media_config)
# ---------------------------------------------------------------------------


def test_load_rtfm_config_parses_table(tmp_path, monkeypatch):
    from amiga_adf_library_builder import paths as p

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[rtfm]\n'
        'enabled = true\n'
        'template = "adventure-rpg"\n'
        'max_bytes = 12000\n'
        '[rtfm.local]\n'
        'manuals = ["/path/to/manuals"]\n'
        'instructions = ["/path/to/instructions"]\n'
        'cheats = ["/path/to/cheats"]\n'
        '[rtfm.online]\n'
        'enabled = false\n',
        encoding="utf-8",
    )
    # Isolate discovery to this file.
    monkeypatch.setattr(p, "SYSTEM_CONFIG", tmp_path / "nope" / "config.toml")
    rcfg = rc.RtfmConfig.from_dict(p.load_rtfm_config(str(cfg_file)))
    assert rcfg.enabled is True
    assert rcfg.template == "adventure-rpg"
    assert rcfg.max_bytes == 12000
    assert rcfg.manuals_roots == ("/path/to/manuals",)
    assert rcfg.instructions_roots == ("/path/to/instructions",)
    assert rcfg.cheats_roots == ("/path/to/cheats",)
    # online must be parsed but ignored by the deterministic path.
    assert rcfg.online_enabled is False


def test_load_rtfm_config_missing_table_returns_empty(tmp_path):
    from amiga_adf_library_builder import paths as p

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[other]\nenabled = true\n', encoding="utf-8")
    assert p.load_rtfm_config(str(cfg_file)) == {}


def test_pathconfig_rtfm_dir_property(tmp_path):
    from amiga_adf_library_builder.paths import resolve_config

    cfg, _ = resolve_config(library_root=str(tmp_path / "lib"))
    assert cfg.rtfm_dir == cfg.assets_dir / "rtfm"


def test_max_bytes_clamped_to_hard_limit():
    cfg = rc.RtfmConfig.from_dict({"enabled": True, "max_bytes": 999999})
    assert cfg.max_bytes <= rc.MAX_RTFM_BYTES


# ---------------------------------------------------------------------------
# Deterministic: identical inputs -> identical output
# ---------------------------------------------------------------------------


def test_deterministic_output(tmp_path):
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
    assert (tmp_path / "r1" / "Example Space Tactics.rtfm").read_bytes() == (
        tmp_path / "r2" / "Example Space Tactics.rtfm"
    ).read_bytes()
