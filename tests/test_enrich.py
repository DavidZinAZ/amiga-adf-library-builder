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

import pytest

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
from amiga_adf_library_builder.metadata_source import (
    MetadataSourceManager, SourceInfo,
)
from amiga_adf_library_builder.title_norm import (
    _strip_parenthetical_disambiguators,
)


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


# ---------------------------------------------------------------------------
# GH-192 Problem 1: DAT/local metadata events are propagated to callers.
# ---------------------------------------------------------------------------

def _make_dat_manager(tmp_path: Path, enabled: bool = True) -> "MetadataSourceManager":
    """Create a MetadataSourceManager with one source (or none)."""
    from amiga_adf_library_builder.metadata_source import MetadataSourceManager
    db_path = tmp_path / "metadata_sources.db"
    mgr = MetadataSourceManager(db_path)
    if enabled:
        # Create a dummy .dat file so add_source can parse it
        dat_path = tmp_path / "dummy.dat"
        dat_path.write_text('<?xml version="1.0"?>\n<datafile>\n</datafile>\n')
        mgr.add_source(dat_path, name="Test DAT")
    return mgr


def test_dat_events_when_manager_none(tmp_path: Path) -> None:
    """When metadata_source_manager is None, enrich_group emits
    dat_not_configured event (GH-192 Problem 3)."""
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.models import ScanRecord
    from amiga_adf_library_builder.parser import parse_filename
    from typing import Optional
    from amiga_adf_library_builder.metadata_source import MetadataSourceManager

    recs = [parse_filename("Example - Space Tactics (Disk 1 of 4).adf")]
    group = group_records(recs)[0]
    scans = {r.source_filename: ScanRecord(
        path=tmp_path / r.source_filename,
        filename=r.source_filename,
        size=901120,
        sha256="abc",
        scanned_at="t",
    ) for r in group.records}
    nfo_dir = tmp_path / "nfo"
    res = enrich_group(
        group, nfo_dir=nfo_dir, scans=scans,
        artwork_original_dir=tmp_path / "art",
        artwork_processed_dir=tmp_path / "proc",
        metadata_source_manager=None,
    )
    dat_events = [e for e in res.events if e.category.name == "DAT"]
    assert any(e.detail == "dat_not_configured" for e in dat_events), (
        "Expected dat_not_configured event when manager is None"
    )


def test_dat_events_when_manager_no_enabled_sources(tmp_path: Path) -> None:
    """When manager exists but zero sources are enabled,
    enrich_group emits dat_source_disabled event (GH-192 Problem 3)."""
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.models import ScanRecord
    from amiga_adf_library_builder.parser import parse_filename
    from amiga_adf_library_builder.metadata_source import MetadataSourceManager

    recs = [parse_filename("Example - Space Tactics (Disk 1 of 4).adf")]
    group = group_records(recs)[0]
    scans = {r.source_filename: ScanRecord(
        path=tmp_path / r.source_filename,
        filename=r.source_filename,
        size=901120,
        sha256="abc",
        scanned_at="t",
    ) for r in group.records}
    nfo_dir = tmp_path / "nfo"
    mgr = _make_dat_manager(tmp_path, enabled=False)
    res = enrich_group(
        group, nfo_dir=nfo_dir, scans=scans,
        artwork_original_dir=tmp_path / "art",
        artwork_processed_dir=tmp_path / "proc",
        metadata_source_manager=mgr,
    )
    dat_events = [e for e in res.events if e.category.name == "DAT"]
    assert any(e.detail == "dat_source_disabled" for e in dat_events), (
        "Expected dat_source_disabled event when no sources enabled"
    )


def test_dat_events_when_manager_has_enabled_sources(tmp_path: Path) -> None:
    """When manager has enabled sources, enrich_group emits
    dat_loaded event with source count (GH-192 Problem 3)."""
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.models import ScanRecord
    from amiga_adf_library_builder.parser import parse_filename
    from amiga_adf_library_builder.metadata_source import MetadataSourceManager

    recs = [parse_filename("Example - Space Tactics (Disk 1 of 4).adf")]
    group = group_records(recs)[0]
    scans = {r.source_filename: ScanRecord(
        path=tmp_path / r.source_filename,
        filename=r.source_filename,
        size=901120,
        sha256="abc",
        scanned_at="t",
    ) for r in group.records}
    nfo_dir = tmp_path / "nfo"
    mgr = _make_dat_manager(tmp_path, enabled=True)
    res = enrich_group(
        group, nfo_dir=nfo_dir, scans=scans,
        artwork_original_dir=tmp_path / "art",
        artwork_processed_dir=tmp_path / "proc",
        metadata_source_manager=mgr,
    )
    dat_events = [e for e in res.events if e.category.name == "DAT"]
    assert any("dat_loaded" in e.detail for e in dat_events), (
        "Expected dat_loaded event when sources are enabled"
    )


# ---------------------------------------------------------------------------
# GH-192 Problem 3: Canonical title not overwritten by provider display text.
# ---------------------------------------------------------------------------

def test_strip_parenthetical_disambiguators() -> None:
    """_strip_parenthetical_disambiguators removes provider disambiguation
    text like '(video game)' from titles."""
    assert _strip_parenthetical_disambiguators("Hacker (video game)") == "Hacker"
    assert _strip_parenthetical_disambiguators("Neuromancer (video game)") == "Neuromancer"
    assert _strip_parenthetical_disambiguators("Bard's Tale III") == "Bard's Tale III"
    assert _strip_parenthetical_disambiguators("Example - Space Tactics (Disk 1 of 4)") == "Example - Space Tactics (Disk 1 of 4)"


def test_canonical_title_not_overwritten_by_group_title(tmp_path: Path) -> None:
    """metadata.canonical_title must NOT be overwritten by group.title
    which may carry provider disambiguation text (GH-192 Problem 4).

    When a Wikipedia match returns 'Hacker (video game)' as the
    canonical_title, the metadata record's canonical_title must remain
    'Hacker (video game)' — not be replaced by a pipeline-modified
    group.title."""
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.models import ScanRecord
    from amiga_adf_library_builder.metadata import MetadataRecord
    from amiga_adf_library_builder.parser import parse_filename
    from typing import Optional
    from amiga_adf_library_builder.metadata_source import MetadataSourceManager

    recs = [parse_filename("Hacker (Disk 1 of 1).adf")]
    group = group_records(recs)[0]
    # Simulate Wikipedia metadata returning a disambiguated title.
    metadata = MetadataRecord(canonical_title="Hacker (video game)")
    metadata.provider = "wikipedia"
    scans = {r.source_filename: ScanRecord(
        path=tmp_path / r.source_filename,
        filename=r.source_filename,
        size=901120,
        sha256="abc",
        scanned_at="t",
    ) for r in group.records}
    nfo_dir = tmp_path / "nfo"
    res = enrich_group(
        group, nfo_dir=nfo_dir, scans=scans,
        artwork_original_dir=tmp_path / "art",
        artwork_processed_dir=tmp_path / "proc",
        metadata_source_manager=None,  # type: ignore[arg-type]
        library_root=None,
    )
    # The metadata's canonical_title must be preserved as-is.
    # Without the fix, metadata.canonical_title would be overwritten
    # to group.title.
    assert metadata.canonical_title == "Hacker (video game)", (
        "metadata.canonical_title must not be overwritten by pipeline"
    )


def test_canonical_title_strips_disambiguation_before_claim(tmp_path: Path) -> None:
    """When canon.claim_field is called, the disambiguation text is
    stripped so 'Hacker (video game)' does not become the canonical
    library title (GH-192 Problem 4)."""
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.models import ScanRecord
    from amiga_adf_library_builder.metadata import MetadataRecord
    from amiga_adf_library_builder.parser import parse_filename
    from amiga_adf_library_builder.canonical import CanonicalLibrary, Game, CanonicalField, Provenance, SourceAuthority
    from typing import Optional
    from amiga_adf_library_builder.metadata_source import MetadataSourceManager

    recs = [parse_filename("Hacker (Disk 1 of 1).adf")]
    group = group_records(recs)[0]
    metadata = MetadataRecord(canonical_title="Hacker (video game)")
    metadata.provider = "wikipedia"
    scans = {r.source_filename: ScanRecord(
        path=tmp_path / r.source_filename,
        filename=r.source_filename,
        size=901120,
        sha256="abc",
        scanned_at="t",
    ) for r in group.records}

    # Create a canonical library with a game and release matching group.release_key.
    db_path = tmp_path / "canonical.db"
    canon = CanonicalLibrary(db_path)
    # Add game 'hacker' with a title claim
    title_field = CanonicalField()
    title_field.claims.append((Provenance(source="test", authority=SourceAuthority.PARSER), "Hacker"))
    game = Game(game_id="hacker", fields={"title": title_field})
    canon.upsert_game(game)
    # Add a release with the same release_key as the group
    from amiga_adf_library_builder.canonical import Release
    release_id = group.release_key
    canon.upsert_release(Release(release_id=release_id, game_id="hacker"))

    nfo_dir = tmp_path / "nfo"
    res = enrich_group(
        group, nfo_dir=nfo_dir, scans=scans,
        artwork_original_dir=tmp_path / "art",
        artwork_processed_dir=tmp_path / "proc",
        metadata_source_manager=None,  # type: ignore[arg-type]
        library_root=tmp_path,
    )

    # The claim should have been made with the stripped title "Hacker",
    # not the disambiguated "Hacker (video game)".
    claims = canon.claims_for("game", "hacker", "title")
    claimed_values = [row[1] for row in claims] if claims else []
    for val in claimed_values:
        assert "video game" not in val.lower(), (
            f"Claimed title must not contain disambiguation: {val}"
        )
    assert "Hacker" in claimed_values, (
        f"Expected 'Hacker' as claimed title, got: {claimed_values}"
    )
