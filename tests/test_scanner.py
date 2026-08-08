"""Tests for scanner (sha256) and preservation verification."""
from pathlib import Path

from amiga_adf_library_builder.models import ScanRecord
from amiga_adf_library_builder.scanner import (
    records_byte_identical,
    scan_file,
    scan_intake,
    sha256_of_file,
)


def test_scan_file_hashes_and_is_read_only(tmp_path: Path) -> None:
    f = tmp_path / "x.adf"
    f.write_bytes(b"amiga disk image content")
    rec = scan_file(f)
    assert rec.filename == "x.adf"
    assert rec.size == len(b"amiga disk image content")
    # Recompute independently and compare.
    assert rec.sha256 == sha256_of_file(f)
    # File is untouched.
    assert f.read_bytes() == b"amiga disk image content"


def test_scan_intake_walks_only_adf_dsk(tmp_path: Path) -> None:
    (tmp_path / "a.adf").write_bytes(b"1")
    (tmp_path / "b.dsk").write_bytes(b"22")
    (tmp_path / "notes.txt").write_bytes(b"ignore me")
    recs = scan_intake(tmp_path)
    names = sorted(r.filename for r in recs)
    assert names == ["a.adf", "b.dsk"]


def test_records_byte_identical_passes_when_unchanged(tmp_path: Path) -> None:
    f = tmp_path / "y.adf"
    f.write_bytes(b"stable")
    rec = scan_file(f)
    ok, problems = records_byte_identical([rec])
    assert ok is True
    assert problems == []


def test_records_byte_identical_fails_on_change(tmp_path: Path) -> None:
    f = tmp_path / "y.adf"
    f.write_bytes(b"stable")
    rec = scan_file(f)
    f.write_bytes(b"TAMPERED")  # mutate after scan (test only; not original/)
    ok, problems = records_byte_identical([rec])
    assert ok is False
    assert problems == ["y.adf"]
