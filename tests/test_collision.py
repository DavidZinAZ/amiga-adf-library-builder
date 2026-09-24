"""Focused tests for the release-basename collision / silent-overwrite fix.

These cover the mandatory safety defect reproduced in the collision regression —
two distinct release groups that differ only in version / language /
alt_marker (or whose names collide after FAT32 sanitization) must NOT silently
clobber each other's export folder or .adf file.

Each test fails on the pre-fix implementation (basename ignored those fields;
export_all had no cross-release folder guard) and passes after remediation.

AR-005: release_basename is deprecated; verify the deprecation warning fires.
"""
from pathlib import Path
import warnings

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
    # GH-183 contract: title-only basename with deterministic hash suffix
    # when case-insensitive sanitized titles collide. No version qualifier.
    assert len(folders) == 2
    for name in folders:
        assert name.startswith("Game One [") and name.endswith("]"), name
    for name in folders:
        assert (adf / name / f"{name}.adf").is_file()
    contents = {p.read_bytes() for p in adf.rglob("*.adf")}
    assert contents == {b"VERSION_1_DISK", b"VERSION_2_DISK"}


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
    # GH-183: no language qualifier in folder name; hash suffix disambiguates.
    assert len(folders) == 2
    for name in folders:
        assert name.startswith("Game One [") and name.endswith("]"), name
    contents = {p.read_bytes() for p in adf.rglob("*.adf")}
    assert contents == {b"DISK_DE", b"DISK_EN"}


# --- C. Alternate-marker collision ------------------------------------------


def test_alt_marker_same_release_not_split(tmp_path):
    """GH-190: Different alt markers ([a] vs [a2]) on the same game must
    NOT split into separate releases. They are disk provenance, not
    release identity.

    Regression test for GH-190: pre-fix, _build_release_key() included
    alt_marker, causing [a] and [a2] to create different release keys.
    """
    from amiga_adf_library_builder.parser import parse_filename
    from amiga_adf_library_builder.grouper import group_records

    r1 = parse_filename("Game One [a] (Disk 1 of 1).adf")
    r2 = parse_filename("Game One [a2] (Disk 1 of 1).adf")
    groups = group_records([r1, r2])
    assert len(groups) == 1, (
        f"GH-190: Different alt markers must NOT split releases. "
        f"Expected 1 group, got {len(groups)}"
    )
    assert len(groups[0].records) == 2
    assert groups[0].alt_marker == "a"  # first record's alt_marker preserved


# --- D. Sanitization collision (residual FAT32-collapse guard) --------------


def test_sanitization_collision_refused_with_conflict(tmp_path):
    # Two distinct release identities whose human-readable basenames sanitize
    # to the SAME FAT32-safe folder component (same title, no disambiguating
    # identity field). Under GH-183 the deterministic suffixing resolves
    # the collision by appending a hash, so both releases export with
    # distinct folders instead of being refused.
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
    # GH-183: deterministic hash suffix resolves collision; both exported.
    assert res.releases_exported == 2, res.folders_written
    assert len(res.folders_written) == 2
    for folder in res.folders_written:
        assert "CollisionName [" in folder
    # No folder collision conflict because suffixes disambiguate.
    folder_conflicts = [c for c in res.conflicts if "folder collision" in c]
    assert not folder_conflicts


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
    # GH-183: single release gets the sanitized title as folder name.
    folder = tmp_path / "staging" / "run1" / "ADF"
    folders = [p.name for p in folder.iterdir() if p.is_dir()]
    assert len(folders) == 1
    assert folders[0] == "Game One"
    assert (folder / "Game One" / "Game One.adf").read_bytes() == b"VERSION_1_DISK"


# --- F. Same-run verify-only does not clobber tampered same-release ---------


def test_same_run_verify_only_keeps_tampered(tmp_path):
    original_dir = tmp_path / "original"
    _write_original(original_dir, "Game One (v1.0) (Disk 1 of 1).adf", b"VERSION_1_DISK")
    g = group_records([parse_filename("Game One (v1.0) (Disk 1 of 1).adf")])[0]
    export_release(g, tmp_path / "staging" / "run1", original_dir=original_dir)
    # Single release: folder is the sanitized title.
    victim = tmp_path / "staging" / "run1" / "ADF" / "Game One" / "Game One.adf"
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


def test_release_basename_emits_deprecation_warning():
    """AR-005: release_basename must emit a DeprecationWarning."""
    g = ReleaseGroup(
        release_key="k", title="Game One", edition=None, group="SKR",
        chipset="AGA", language="DE", version="v2.0", alt_marker="a2",
        ext="adf", records=[], disks=[], specials=[],
        has_main_disk=True, is_complete=True,
    )
    with pytest.warns(DeprecationWarning, match="release_basename"):
        result = release_basename(g)
    assert result  # still functional
