"""Tests for the persistent JSONL catalog (documented behavior reuse)."""
from pathlib import Path

from amiga_adf_library_builder.catalog import (
    catalog_exists,
    read_groups,
    read_parse_records,
    write_groups,
    write_parse_records,
    write_scan_records,
)
from amiga_adf_library_builder.models import ParsedRecord, ScanRecord
from amiga_adf_library_builder.parser import parse_filename


def _sample_scan(tmp_path: Path):
    f = tmp_path / "a.adf"
    f.write_bytes(b"x")
    return ScanRecord(path=f, filename="a.adf", size=1, sha256="deadbeef", scanned_at="t")


def test_scan_and_parse_records_deduplicated(tmp_path: Path) -> None:
    cat = tmp_path / "catalog"
    scan = _sample_scan(tmp_path)
    rec = parse_filename("Example - Space Tactics (Disk 1 of 4).adf")

    n1 = write_scan_records(cat, [scan])
    n2 = write_scan_records(cat, [scan])  # duplicate -> no new lines
    assert n1 == 1 and n2 == 0
    assert catalog_exists(cat)

    p1 = write_parse_records(cat, [rec])
    p2 = write_parse_records(cat, [rec])
    assert p1 == 1 and p2 == 0
    assert len(read_parse_records(cat)) == 1


def test_groups_persisted_and_readable(tmp_path: Path) -> None:
    from amiga_adf_library_builder.grouper import group_records

    cat = tmp_path / "catalog"
    recs = [parse_filename(f"Example - Space Tactics (Disk {n} of 4).adf") for n in range(1, 5)]
    groups = group_records(recs)
    written = write_groups(cat, groups, run_id="run-1")
    assert written == 1
    rows = read_groups(cat)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["disk_count"] == 4
