"""Tests for the Phase 5 Gotek exporter (documented behavior, path safety).

Covers: flat folder layout, -N multidisk naming, single-disk naming, NFO +
artwork filename match, idempotent reruns, no-silent-overwrite conflict
detection, path-traversal safety, and run-owned staging only.
"""
from pathlib import Path

import io

import pytest

from amiga_adf_library_builder import artwork as artwork_mod
from amiga_adf_library_builder.exporter import (
    _sanitize_component,
    export_all,
    export_release,
)
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.parser import parse_filename


def _group(filenames, ext="adf"):
    return group_records([parse_filename(f"{n}.{ext}") for n in filenames])[0]


def _write_original(original_dir: Path, name: str, size=2048):
    original_dir.mkdir(parents=True, exist_ok=True)
    (original_dir / name).write_bytes(b"A" * size)


def _make_original(original_dir: Path, names, ext="adf"):
    original_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        _write_original(original_dir, f"{n}.{ext}")


# --- naming / layout ---------------------------------------------------------


def test_multidisk_set_writes_flat_folder_with_dash_n(tmp_path):
    original_dir = tmp_path / "original"
    names = [
        f"E.X.A.M.P.L.E. II - Galactic Bureau (1994)(UBI Soft)"
        f"[cr SKR](Disk {n} of 5)"
        for n in range(1, 6)
    ]
    _make_original(original_dir, names)
    g = _group(names)
    res = export_release(g, tmp_path / "staging" / "run1", original_dir=original_dir)
    written, unchanged, conflicts = res
    assert not conflicts
    adf = tmp_path / "staging" / "run1" / "ADF"
    folders = [p.name for p in adf.iterdir() if p.is_dir()]
    assert len(folders) == 1
    folder = adf / folders[0]
    files = sorted(p.name for p in folder.iterdir())
    expected = [
        "E.X.A.M.P.L.E. II Galactic Bureau cr SKR-1.adf",
        "E.X.A.M.P.L.E. II Galactic Bureau cr SKR-2.adf",
        "E.X.A.M.P.L.E. II Galactic Bureau cr SKR-3.adf",
        "E.X.A.M.P.L.E. II Galactic Bureau cr SKR-4.adf",
        "E.X.A.M.P.L.E. II Galactic Bureau cr SKR-5.adf",
        "E.X.A.M.P.L.E. II Galactic Bureau cr SKR.nfo",
    ]
    assert files == expected


def test_single_disk_omits_dash_one(tmp_path):
    original_dir = tmp_path / "original"
    name = "Example_Castle_Quest_Disk_A"
    _make_original(original_dir, [name])
    rec = parse_filename(f"{name}.adf")
    # give it a determinable main disk so it isn't quarantined
    rec.disk_number = 1
    g = ReleaseGroup(
        release_key="x", title="Example Castle Quest", edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        records=[rec], disks=[rec], specials=[],
        has_main_disk=True, is_complete=True,
    )
    written, unchanged, conflicts = export_release(
        g, tmp_path / "staging" / "run1", original_dir=original_dir
    )
    assert not conflicts
    folder = tmp_path / "staging" / "run1" / "ADF" / "Example Castle Quest"
    assert (folder / "Example Castle Quest.adf").exists()
    assert not (folder / "Example Castle Quest-1.adf").exists()


def test_artwork_and_nfo_match_basename(tmp_path):
    original_dir = tmp_path / "original"
    names = [f"Example - Space Tactics (Disk {n} of 4)" for n in range(1, 5)]
    _make_original(original_dir, names)
    g = _group(names)
    # operator-provided master art
    art_dir = tmp_path / "art"
    art_dir.mkdir()
    from PIL import Image

    Image.new("RGB", (640, 480), "green").save(art_dir / "examplespacetactics.png")
    written, unchanged, conflicts = export_release(
        g, tmp_path / "staging" / "run1", original_dir=original_dir,
        artwork_original_dir=art_dir,
    )
    assert not conflicts
    folder = tmp_path / "staging" / "run1" / "ADF" / "Example Space Tactics"
    assert (folder / "Example Space Tactics.nfo").exists()
    art = folder / "Example Space Tactics.jpg"
    assert art.exists()
    from amiga_adf_library_builder.exporter import _sha256_bytes
    data = art.read_bytes()
    assert len(data) <= artwork_mod.ARTWORK_MAX_BYTES
    with Image.open(art) as im:
        assert im.size[0] <= artwork_mod.ARTWORK_MAX_W
        assert im.size[1] <= artwork_mod.ARTWORK_MAX_H


def test_quarantined_groups_skipped_by_export_all(tmp_path):
    original_dir = tmp_path / "original"
    names = ["Example_Quest_III_Boot", "Example_Quest_III_Character"]
    _make_original(original_dir, names)
    g = group_records([parse_filename(f"{n}.adf") for n in names])[0]
    assert g.quarantine_reason is not None
    res = export_all(
        [g], staging_dir=tmp_path / "staging", run_id="r1",
        upstream_task_closed=True,
        verified_artwork_width=artwork_mod.ARTWORK_MAX_W,
        verified_artwork_height=artwork_mod.ARTWORK_MAX_H,
        original_dir=original_dir,
    )
    assert res.releases_exported == 0
    assert g.release_key in res.skipped_quarantined


# --- idempotency / no silent overwrite ---------------------------------------


def test_idempotent_rerun_produces_identical_tree(tmp_path):
    original_dir = tmp_path / "original"
    names = [f"Example - Space Tactics (Disk {n} of 4)" for n in range(1, 5)]
    _make_original(original_dir, names)
    g = _group(names)
    r1 = export_release(g, tmp_path / "staging" / "run1", original_dir=original_dir)
    r2 = export_release(g, tmp_path / "staging" / "run2", original_dir=original_dir)
    assert r1[2] == [] and r2[2] == []  # no conflicts
    tree1 = sorted(p.read_bytes() for p in (tmp_path / "staging" / "run1").rglob("*") if p.is_file())
    tree2 = sorted(p.read_bytes() for p in (tmp_path / "staging" / "run2").rglob("*") if p.is_file())
    assert tree1 == tree2


def test_conflicting_existing_output_reported_by_verify_only(tmp_path):
    original_dir = tmp_path / "original"
    names = [f"Example - Space Tactics (Disk {n} of 4)" for n in range(1, 5)]
    _make_original(original_dir, names)
    g = _group(names)
    # first real export
    export_release(g, tmp_path / "staging" / "run1", original_dir=original_dir)
    # tamper one staged file so content differs
    victim = tmp_path / "staging" / "run1" / "ADF" / "Example Space Tactics" / "Example Space Tactics-1.adf"
    victim.write_bytes(b"TAMPERED")
    # verify_only should flag the conflict, not clobber
    written, unchanged, conflicts = export_release(
        g, tmp_path / "staging" / "run1", original_dir=original_dir, verify_only=True
    )
    assert not written
    assert conflicts  # conflict detected
    assert victim.read_bytes() == b"TAMPERED"  # not silently overwritten


# --- path safety -------------------------------------------------------------


def test_path_traversal_name_collapsed_safely(tmp_path):
    # A malicious/erroneous title with traversal must not escape the folder.
    rec = parse_filename("X (Disk 1 of 1).adf")
    rec.title = "../../../escape"
    g = ReleaseGroup(
        release_key="esc", title="../../../escape", edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        records=[rec], disks=[rec], specials=[],
        has_main_disk=True, is_complete=True,
    )
    original_dir = tmp_path / "original"
    _make_original(original_dir, ["X (Disk 1 of 1)"])
    export_release(g, tmp_path / "staging" / "run1", original_dir=original_dir)
    # Nothing escaped under staging/run1; a safe folder component was used.
    escaped = (tmp_path / "escape").exists()
    assert escaped is False
    # The staging root still contains exactly ADF/ (and DSK/) plus the folder.
    adf = tmp_path / "staging" / "run1" / "ADF"
    assert adf.is_dir()
    assert not (tmp_path / "staging" / "run1" / "escape").exists()


def test_fat32_invalid_chars_replaced(tmp_path):
    safe = _sanitize_component('Game: *? "Bad" <Name>')
    assert "*" not in safe and "?" not in safe and '"' not in safe
    assert "<" not in safe and ">" not in safe
    assert "|" not in safe


# --- artwork processing (upstream caps) -----------------------


def test_artwork_process_enforces_caps_and_no_upscale(tmp_path):
    from PIL import Image

    # Oversize master must shrink below the hard caps.
    big = tmp_path / "big.png"
    Image.new("RGB", (4000, 3000), "red").save(big)
    data = artwork_mod.process_artwork_bytes(big)
    assert len(data) <= artwork_mod.ARTWORK_MAX_BYTES
    with Image.open(io.BytesIO(data)) as im:
        assert im.size[0] <= artwork_mod.ARTWORK_MAX_W
        assert im.size[1] <= artwork_mod.ARTWORK_MAX_H

    # Small master must NOT be upscaled (firmware never upscales).
    small = tmp_path / "small.png"
    Image.new("RGB", (80, 64), "blue").save(small)
    d2 = artwork_mod.process_artwork_bytes(small)
    with Image.open(io.BytesIO(d2)) as im2:
        assert im2.size[0] <= 80 and im2.size[1] <= 64


# --- gate enforcement --------------------------------------------------------


def test_export_all_respects_closed_gate(tmp_path):
    original_dir = tmp_path / "original"
    _make_original(original_dir, ["X (Disk 1 of 1)"])
    g = _group(["X (Disk 1 of 1)"])
    # Gate closed (upstream not closed).
    res = export_all(
        [g], staging_dir=tmp_path / "work", run_id="r1",
        upstream_task_closed=False,
        verified_artwork_width=None, verified_artwork_height=None,
        original_dir=original_dir,
    )
    assert res.export_gate_open is False
    assert res.releases_exported == 0
    assert not (tmp_path / "work" / "staging").exists()


def test_require_artwork_preflight_writes_nothing(tmp_path: Path) -> None:
    from amiga_adf_library_builder.exporter import export_all
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.parser import parse_filename

    original = tmp_path / "original"
    original.mkdir()
    src = original / "Solo Game.adf"
    src.write_bytes(b"ADF")
    group = group_records([parse_filename(src.name)])[0]
    result = export_all(
        [group], staging_dir=tmp_path, run_id="required-art",
        upstream_task_closed=True, verified_artwork_width=2000,
        verified_artwork_height=2000, original_dir=original,
        artwork_original_dir=tmp_path / "masters",
        artwork_processed_dir=tmp_path / "processed",
        nfo_dir=tmp_path / "nfo", require_artwork=True,
    )
    assert result.errors and "required artwork missing" in result.errors[0]
    assert not result.staging_root.exists()
    assert result.files_written == []
