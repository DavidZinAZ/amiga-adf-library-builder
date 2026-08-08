"""Tests for quarantine routing (documented behavior, A8)."""
from pathlib import Path

from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ScanRecord
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.quarantine import route_quarantine


def _scans(filenames, tmp_path):
    out = {}
    for f in filenames:
        out[f] = ScanRecord(path=tmp_path / f, filename=f, size=1, sha256="x", scanned_at="t")
    return out


def test_special_only_routed_to_unknown(tmp_path: Path) -> None:
    names = ["Example_Quest_III_Boot.adf", "Example_Quest_III_Character.adf"]
    groups = group_records([parse_filename(n) for n in names])
    summary = route_quarantine(
        groups, review_dir=tmp_path / "review", unknown_dir=tmp_path / "unknown",
        scans=_scans(names, tmp_path),
    )
    assert len(summary["unknown"]) == 1
    assert summary["review"] == []
    # The JSON explains why.
    import json

    data = json.loads(Path(summary["unknown"][0]).read_text())
    assert "Incomplete set" in data["reason"]
    assert set(data["source_files"]) == set(names)


def test_near_duplicate_spelling_routed_to_review(tmp_path: Path) -> None:
    names = ["Example_Quest_III_Character.adf", "Example_Qest3_Char.adf"]
    groups = group_records([parse_filename(n) for n in names])
    summary = route_quarantine(
        groups, review_dir=tmp_path / "review", unknown_dir=tmp_path / "unknown",
        scans=_scans(names, tmp_path),
    )
    # Both are special-only so they go to unknown; reason still explains.
    assert len(summary["unknown"]) == 2
    import json

    reasons = [json.loads(Path(p).read_text())["reason"] for p in summary["unknown"]]
    assert all("no determinable main game disk" in r for r in reasons)
