"""Focused tests for the release-basename collision / silent-overwrite fix.

These cover the mandatory safety defect reproduced in the collision regression:
two distinct release groups that differ only in version / language /
alt_marker (or whose names collide after FAT32 sanitization) must NOT silently
clobber each other's export folder or .adf file.

Each test fails on the pre-fix implementation (basename ignored those fields;
export_all had no cross-release folder guard) and passes after remediation.
"""
from pathlib import Path

import pytest

from amiga_adf_library_builder import artwork as artwork_mod
from amiga_adf_library_builder.exporter import export_all, export_release
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.naming import release_basename
from amiga_adf_library_builder.parser import parse_filename


def _write_original(original_dir: Path, name: str, content: bytes = b"A" * 2048):
    original_dir.mkdir(parents=True, exist_ok=True)
    (original_dir / name).write_bytes(content)


def _export_paired(original_dir, filenames):
    """Parse, group, export two filenames; return (result, staging_root)."""
    groups = group_records([parse_filename(f) for f in filenames])
    assert len(groups) == 2, f"expected 2 distinct release groups, got {len(groups)}"
    work_root = original_dir.parent
    res = export_all(
        groups,
        staging_dir=work_root / "work" / "staging",
        run_id="run1",
        upstream_task_closed=True,
        verified_artwork_width=artwork_mod.ARTWORK_MAX_W,
        verified_artwork_height=artwork_mod.ARTWORK_MAX_H,
        original_dir=original_dir,
    )
    # export_all writes beneath <staging_dir>/<run_id>.
    staging = work_root / "work" / "staging" / "run1"
    return res, staging


# --- A. Version collision ----------------------------------------------------


def test_version_collision_preserves_both_releases(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"VERSION_1_DISK")
    _write_original(original_dir, "Game One (v2.0) (Disk 1 of 1).adf", b"VERSION_2_DISK")
    res, staging = _export_paired(
        original_dir,
        ["Game One (v1.0) (Disk 1 of 1).adf", "Game One (v2.0) (Disk 1 of 1).adf"],
    )
    assert res.releases_exported == 2
    adf = staging / "ADF"
    folders = sorted(p.name for p in adf.iterdir() if p.is_dir())
    # Two distinct, deterministic folders (no clobber).
    assert "Game One ver v1.0" in folders
    assert "Game One ver v2.0" in folders
    assert len(folders) == 2
    v1 = adf / "Game One ver v1.0" / "Game One ver v1.0.adf"
    v2 = adf / "Game One ver v2.0" / "Game One ver v2.0.adf"
    assert v1.read_bytes() == b"VERSION_1_DISK"
    assert v2.read_bytes() == b"VERSION_2_DISK"


# --- B. Language collision --------------------------------------------------


def test_language_collision_preserves_both_releases(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (DE) (Disk 1 of 1).adf", b"DISK_DE")
    _write_original(original_dir, "Game One (EN) (Disk 1 of 1).adf", b"DISK_EN")
    res, staging = _export_paired(
        original_dir,
        ["Game One (DE) (Disk 1 of 1).adf", "Game One (EN) (Disk 1 of 1).adf"],
    )
    assert res.releases_exported == 2
    adf = staging / "ADF"
    folders = sorted(p.name for p in adf.iterdir() if p.is_dir())
    assert len(folders) == 2
    # Distinct folders carry the language token.
    assert any("lang DE" in f for f in folders)
    assert any("lang EN" in f for f in folders)
    contents = {p.read_bytes() for p in adf.rglob("*.adf")}
    assert contents == {b"DISK_DE", b"DISK_EN"}


# --- C. Alternate-marker collision ------------------------------------------


def test_alt_marker_collision_preserves_both_releases(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One [a] (Disk 1 of 1).adf", b"DISK_A")
    _write_original(original_dir, "Game One [a2] (Disk 1 of 1).adf", b"DISK_A2")
    res, staging = _export_paired(
        original_dir,
        ["Game One [a] (Disk 1 of 1).adf", "Game One [a2] (Disk 1 of 1).adf"],
    )
    assert res.releases_exported == 2
    adf = staging / "ADF"
    folders = sorted(p.name for p in adf.iterdir() if p.is_dir())
    assert len(folders) == 2
    assert any("alt a" in f for f in folders)
    assert any("alt a2" in f for f in folders)
    contents = {p.read_bytes() for p in adf.rglob("*.adf")}
    assert contents == {b"DISK_A", b"DISK_A2"}


# --- D. Sanitization collision (residual FAT32-collapse guard) --------------


def test_sanitization_collision_refused_with_conflict(tmp_path):
    # Two distinct release identities whose human-readable basenames sanitize
    # to the SAME FAT32-safe folder component (same title, no disambiguating
    # identity field). The residual collision guard must refuse the second
    # distinct release (record a clear conflict) instead of clobbering the
    # first. Both carry a real disk so the writer actually attempts the folder.
    original_dir = tmp_path / "original"
    src1 = "CollisionOne (Disk 1 of 1).adf"
    src2 = "CollisionTwo (Disk 1 of 1).adf"
    _write_original(original_dir, src1, b"DISK_ONE")
    _write_original(original_dir, src2, b"DISK_TWO")
    r1 = parse_filename(src1)
    r1.disk_number = 1
    r2 = parse_filename(src2)
    r2.disk_number = 1
    gx = ReleaseGroup(
        release_key="k1", title="CollisionName", edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        records=[r1], disks=[r1], specials=[],
        has_main_disk=True, is_complete=True,
    )
    gy = ReleaseGroup(
        release_key="k2", title="CollisionName", edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        records=[r2], disks=[r2], specials=[],
        has_main_disk=True, is_complete=True,
    )
    res = export_all(
        [gx, gy], staging_dir=tmp_path / "work", run_id="run1",
        upstream_task_closed=True,
        verified_artwork_width=artwork_mod.ARTWORK_MAX_W,
        verified_artwork_height=artwork_mod.ARTWORK_MAX_H,
        original_dir=original_dir,
    )
    # Both share the same title -> same basename -> same folder. Distinct keys.
    assert res.releases_exported == 1, res.folders_written
    assert res.conflicts, "expected a reported folder collision, got none"
    assert any("folder collision" in c for c in res.conflicts)


# --- E. Same-release idempotency --------------------------------------------


def test_same_release_idempotent_rerun(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"VERSION_1_DISK")
    g = group_records([parse_filename("Game One (v1.0) (Disk 1 of 1).adf")])[0]
    r1 = export_release(g, tmp_path / "staging" / "run1", original_dir=original_dir)
    r2 = export_release(g, tmp_path / "staging" / "run2", original_dir=original_dir)
    assert not r1[2] and not r2[2]
    tree1 = sorted(p.read_bytes() for p in (tmp_path / "staging" / "run1").rglob("*") if p.is_file())
    tree2 = sorted(p.read_bytes() for p in (tmp_path / "staging" / "run2").rglob("*") if p.is_file())
    assert tree1 == tree2
    # Folder name carries the version, but is stable across reruns.
    folder = tmp_path / "staging" / "run1" / "ADF" / "Game One ver v1.0"
    assert (folder / "Game One ver v1.0.adf").read_bytes() == b"VERSION_1_DISK"


# --- F. Same-run verify-only does not clobber tampered same-release ---------


def test_same_run_verify_only_keeps_tampered(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"VERSION_1_DISK")
    g = group_records([parse_filename("Game One (v1.0) (Disk 1 of 1).adf")])[0]
    export_release(g, tmp_path / "staging" / "run1", original_dir=original_dir)
    victim = tmp_path / "staging" / "run1" / "ADF" / "Game One ver v1.0" / "Game One ver v1.0.adf"
    victim.write_bytes(b"TAMPERED")
    written, unchanged, conflicts = export_release(
        g, tmp_path / "staging" / "run1", original_dir=original_dir, verify_only=True
    )
    assert not written
    assert conflicts
    assert victim.read_bytes() == b"TAMPERED"


# --- basename sanity: identity fields included deterministically -----------


def test_release_basename_includes_identity_fields():
    g = ReleaseGroup(
        release_key="k", title="Game One", edition=None, group="SKR",
        chipset="AGA", language="DE", version="v2.0", alt_marker="a2",
        ext="adf", records=[], disks=[], specials=[],
        has_main_disk=True, is_complete=True,
    )
    base = release_basename(g)
    assert "Game One" in base
    assert "cr SKR" in base
    assert "lang DE" in base
    assert "ver v2.0" in base
    assert "alt a2" in base


def test_release_basename_no_identity_fields_unchanged():
    # Corpus releases carry none of these -> basename unchanged from legacy.
    g = ReleaseGroup(
        release_key="k", title="Example Space Tactics", edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        records=[], disks=[], specials=[],
        has_main_disk=True, is_complete=True,
    )
    assert release_basename(g) == "Example Space Tactics"
