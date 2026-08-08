"""Tests for offline NFO enrichment and the artwork-resize guard.

Gotek NFO contract: every Gotek-facing .nfo begins with ``Title:`` (line 1) and a
``Blurb:`` (line 2) at <= 512 bytes. Detailed source / metadata / approval
provenance is preserved durably OUTSIDE the Gotek-facing NFO in a
``<basename>.provenance.json`` (machine-readable) + ``<basename>.provenance.txt``
(human-readable) sidecar under ``assets/nfo`` — which the exporter never copies
into the SD-card /ADF or /DSK output.
"""
import json
from pathlib import Path

from amiga_adf_library_builder.enrich import (
    VERIFIED_ARTWORK_HEIGHT,
    VERIFIED_ARTWORK_WIDTH,
    enrich_group,
    resize_artwork,
)
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ScanRecord
from amiga_adf_library_builder.nfo_render import MAX_NFO_BYTES
from amiga_adf_library_builder.parser import parse_filename


def _ufo_group():
    recs = [parse_filename(f"Example - Space Tactics (Disk {n} of 4).adf") for n in range(1, 5)]
    return group_records(recs)[0]


def _ufo_scans(group, tmp_path):
    return {
        r.source_filename: ScanRecord(
            path=tmp_path / r.source_filename,
            filename=r.source_filename,
            size=901120,
            sha256="abc",
            scanned_at="t",
        )
        for r in group.records
    }


def test_nfo_written_offline_with_title_blurb_and_size_cap(tmp_path: Path) -> None:
    g = _ufo_group()
    scans = _ufo_scans(g, tmp_path)
    nfo_dir = tmp_path / "nfo"
    res = enrich_group(g, nfo_dir=nfo_dir, scans=scans,
                       artwork_original_dir=tmp_path / "art", artwork_processed_dir=tmp_path / "proc")
    assert res.nfo_path is not None and res.nfo_path.exists()
    text = res.nfo_path.read_text()

    lines = text.splitlines()
    # Gotek NFO contract: Title on line 1, Blurb on line 2.
    assert lines[0].startswith("Title: ")
    assert lines[0] == "Title: Example Space Tactics"
    assert lines[1].startswith("Blurb:")
    # No rich provenance in the Gotek-facing NFO.
    assert "SHA256:" not in text
    assert "Approved source:" not in text
    assert "Enrichment mode:" not in text
    # Hard 512-byte display limit.
    assert len(text.encode("utf-8")) <= MAX_NFO_BYTES


def test_gotek_nfo_missing_metadata_has_no_empty_separators(tmp_path: Path) -> None:
    g = _ufo_group()
    scans = _ufo_scans(g, tmp_path)
    nfo_dir = tmp_path / "nfo"
    res = enrich_group(g, nfo_dir=nfo_dir, scans=scans,
                       artwork_original_dir=tmp_path / "art", artwork_processed_dir=tmp_path / "proc")
    text = res.nfo_path.read_text()
    lines = text.splitlines()
    # With no year/publisher/description available, Blurb is present (line 2)
    # but carries no invented content or empty separators.
    assert lines[1] == "Blurb:"
    # No " - - " or leading/trailing " - " separators anywhere in the blurb.
    assert " -  - " not in text
    assert text.count(" - ") == 0  # blurb has no populated fields here


def test_provenance_sidecar_durable_outside_nfo(tmp_path: Path) -> None:
    g = _ufo_group()
    scans = _ufo_scans(g, tmp_path)
    nfo_dir = tmp_path / "nfo"
    res = enrich_group(g, nfo_dir=nfo_dir, scans=scans,
                       artwork_original_dir=tmp_path / "art", artwork_processed_dir=tmp_path / "proc")
    basename = "Example Space Tactics"
    prov_json = nfo_dir / f"{basename}.provenance.json"
    prov_txt = nfo_dir / f"{basename}.provenance.txt"
    assert prov_json.is_file(), "provenance JSON sidecar must be written"
    assert prov_txt.is_file(), "provenance text sidecar must be written"

    data = json.loads(prov_json.read_text())
    # Source filenames + SHA-256 + sizes preserved outside the NFO.
    assert data["source_images"]
    assert data["source_images"][0]["filename"].startswith("Example - Space Tactics")
    assert data["source_images"][0]["sha256"] == "abc"
    assert data["source_images"][0]["size"] == 901120
    # Enrichment mode recorded.
    assert data["enrichment_mode"] == "offline"
    # The Gotek-facing NFO must NOT contain the durable provenance.
    nfo_text = res.nfo_path.read_text()
    assert "abc" not in nfo_text  # no SHA256 leakage
    assert "Enrichment mode:" not in nfo_text


def test_artwork_resize_refuses_without_verified_dims(tmp_path: Path) -> None:
    # No Pillow needed to prove the guard; with width/height None it must refuse.
    master = tmp_path / "cover.jpg"
    master.write_bytes(b"not really jpeg")
    try:
        resize_artwork(master, tmp_path / "out")
    except RuntimeError as exc:
        assert "unresolved" in str(exc).lower() or "verified" in str(exc).lower()
    else:
        raise AssertionError("resize must refuse without verified dimensions")


def test_artwork_resize_runs_with_verified_dims(tmp_path: Path) -> None:
    # Skip if Pillow is unavailable in the environment.
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        import pytest

        pytest.skip("Pillow not installed; verified-dim resize path untested here")
    master = tmp_path / "cover.png"
    Image.new("RGB", (640, 480), "blue").save(master)
    out = resize_artwork(master, tmp_path / "out", width=320, height=256)
    assert out.exists()
    with Image.open(out) as img:
        assert img.size[0] <= 320 and img.size[1] <= 256
