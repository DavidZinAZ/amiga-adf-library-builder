"""Gotek NFO contract: unified Gotek-facing NFO contract.

Every Gotek-facing ``.nfo`` (enriched export, offline export, exporter
fallback generation, and manually approved releases) MUST:

* begin on line 1 with ``Title: <canonical title>``;
* have line 2 be a concise labelled ``Blurb:``;
* build the blurb gracefully with only available trusted metadata (no empty
  separators, no invented fields);
* stay at or below 512 UTF-8 bytes;
* carry no rich provenance (source hashes, approval URLs, metadata
  provenance, enrichment mode) — that lives durably OUTSIDE the NFO.

And the exporter must never copy extra provenance artifacts into the final
``/ADF`` or ``/DSK`` SD-card staging output.
"""
import json

import pytest

from amiga_adf_library_builder.enrich import enrich_group
from amiga_adf_library_builder.exporter import export_release
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.models import ScanRecord
from amiga_adf_library_builder.naming import release_basename
from amiga_adf_library_builder.nfo_render import MAX_NFO_BYTES, render_gotek_nfo
from amiga_adf_library_builder.parser import parse_filename


def _assert_gotek_nfo_contract(text: str, title: str) -> None:
    lines = text.splitlines()
    assert lines[0] == f"Title: {title}", f"line 1 must be Title: {title!r}, got {lines[0]!r}"
    assert lines[1].startswith("Blurb:"), f"line 2 must be Blurb:, got {lines[1]!r}"
    assert len(text.encode("utf-8")) <= MAX_NFO_BYTES, "NFO exceeds 512 bytes"
    # No rich provenance must leak into the Gotek-facing NFO.
    assert "SHA256" not in text
    assert "Approved source:" not in text
    assert "Enrichment mode:" not in text
    assert "Metadata provenance:" not in text


def test_render_gotek_nfo_title_line1_blurb_line2():
    text = render_gotek_nfo(
        title="Foo Quest",
        year="1990",
        publisher="Acme",
        description="A platformer with tight controls.",
    )
    _assert_gotek_nfo_contract(text, "Foo Quest")
    assert text.splitlines()[1] == "Blurb: 1990 - Acme - A platformer with tight controls."


def test_render_gotek_nfo_missing_metadata_no_empty_separators():
    # Only a title: blurb present but empty, no " - " separators invented.
    text = render_gotek_nfo(title="Mystery Game")
    _assert_gotek_nfo_contract(text, "Mystery Game")
    assert text.splitlines()[1] == "Blurb:"
    assert " - " not in text

    # Partial metadata: only publisher.
    text2 = render_gotek_nfo(title="T", publisher="EA")
    _assert_gotek_nfo_contract(text2, "T")
    assert text2.splitlines()[1] == "Blurb: EA"

    # Partial metadata: only year and description.
    text3 = render_gotek_nfo(title="T", year="1995", description="RTS")
    _assert_gotek_nfo_contract(text3, "T")
    assert text3.splitlines()[1] == "Blurb: 1995 - RTS"


def _assert_title_only_truncation_contract(text: str, full_title: str) -> None:
    """Assertments for the title-only truncation branch (Gotek NFO contract).

    Invariants: <= 512 UTF-8 bytes, valid UTF-8, Title: line 1, Blurb: line 2,
    no replacement char, and the title is visibly truncated.
    """
    encoded = text.encode("utf-8")
    assert len(encoded) <= MAX_NFO_BYTES, (
        f"NFO exceeds {MAX_NFO_BYTES} bytes: got {len(encoded)}"
    )
    # Output decodes as valid UTF-8 (round-trip proves no truncation mid-codepoint).
    assert text == encoded.decode("utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("Title: "), f"line 1 must start with 'Title: ', got {lines[0]!r}"
    assert lines[1].startswith("Blurb:"), f"line 2 must start with 'Blurb:', got {lines[1]!r}"
    # No replacement character from a split multi-byte sequence.
    assert "\ufffd" not in text, "replacement character present in NFO"
    # The title is visibly truncated: line 1 ends with the ellipsis and the
    # rendered title is shorter than the full input title.
    rendered_title = lines[0][len("Title: "):]
    assert rendered_title.endswith("\u2026"), f"truncated title must end with ellipsis, got {rendered_title!r}"
    assert len(rendered_title) < len(full_title), "title was not visibly truncated"


def test_render_gotek_nfo_title_only_truncation_multibyte_within_512():
    # Regression for the title-only truncation branch: a >500-byte multibyte title with no blurb
    # metadata must hit the title-only truncation branch and stay <= 512 bytes.
    title = "\u30bf\u30a4\u30c8\u30eb" * 200  # 1200 chars / 3600 UTF-8 bytes
    text = render_gotek_nfo(title=title)
    _assert_title_only_truncation_contract(text, title)


def test_render_gotek_nfo_title_only_truncation_ascii_within_512():
    # ASCII long-title coverage for the same branch (no blurb metadata).
    title = "A" * 500  # 500 UTF-8 bytes, > 493-byte threshold into title-only branch
    text = render_gotek_nfo(title=title)
    _assert_title_only_truncation_contract(text, title)


def test_render_gotek_nfo_always_within_512_bytes():
    # Even with a very long description, the contract holds at <= 512 bytes and
    # the Title line stays intact.
    huge = "Z" * 5000
    text = render_gotek_nfo(title="Long Title", description=huge)
    assert len(text.encode("utf-8")) <= MAX_NFO_BYTES
    assert text.splitlines()[0] == "Title: Long Title"


def _ufo_group():
    recs = [parse_filename(f"Example - Space Tactics (Disk {n} of 4).adf") for n in range(1, 5)]
    return group_records(recs)[0]


def _offline_nfo_for(tmp_path, group) -> str:
    scans = {
        r.source_filename: ScanRecord(
            path=tmp_path / r.source_filename,
            filename=r.source_filename, size=901120, sha256="deadbeef", scanned_at="t",
        )
        for r in group.records
    }
    nfo_dir = tmp_path / "nfo"
    res = enrich_group(group, nfo_dir=nfo_dir, scans=scans,
                       artwork_original_dir=tmp_path / "art", artwork_processed_dir=tmp_path / "proc")
    return res.nfo_path.read_text()


def test_enriched_offline_path_uses_unified_contract(tmp_path):
    text = _offline_nfo_for(tmp_path, _ufo_group())
    _assert_gotek_nfo_contract(text, "Example Space Tactics")


def test_enriched_path_persists_provenance_sidecar_outside_nfo(tmp_path):
    group = _ufo_group()
    scans = {
        r.source_filename: ScanRecord(
            path=tmp_path / r.source_filename,
            filename=r.source_filename, size=901120, sha256="deadbeef", scanned_at="t",
        )
        for r in group.records
    }
    nfo_dir = tmp_path / "nfo"
    enrich_group(group, nfo_dir=nfo_dir, scans=scans,
                 artwork_original_dir=tmp_path / "art", artwork_processed_dir=tmp_path / "proc")
    basename = "Example Space Tactics"
    prov = nfo_dir / f"{basename}.provenance.json"
    prov_txt = nfo_dir / f"{basename}.provenance.txt"
    assert prov.is_file() and prov_txt.is_file()
    data = json.loads(prov.read_text())
    # Source filename + SHA-256 + size durable.
    assert data["source_images"][0]["sha256"] == "deadbeef"
    assert data["source_images"][0]["size"] == 901120
    # NFO does not contain them.
    assert "deadbeef" not in (nfo_dir / f"{basename}.nfo").read_text()


def test_fallback_path_uses_unified_contract(tmp_path):
    # Exporter fallback when no enrichment artifact exists: uses _build_nfo.
    g = _ufo_group()
    original_dir = tmp_path / "original"
    original_dir.mkdir()
    for r in g.records:
        (original_dir / r.source_filename).write_bytes(b"ADF" * 100)
    written, unchanged, conflicts = export_release(
        g, tmp_path / "staging" / "run1", original_dir=original_dir,
    )
    assert not conflicts
    nfo = tmp_path / "staging" / "run1" / "ADF" / "Example Space Tactics" / "Example Space Tactics.nfo"
    text = nfo.read_text()
    _assert_gotek_nfo_contract(text, "Example Space Tactics")


def test_exporter_copies_only_nfo_not_provenance_into_staging(tmp_path):
    # A release with a pre-built enrichment NFO + provenance sidecars in nfo_dir:
    # the exporter must copy only <basename>.nfo into staging, never the
    # .provenance.json / .provenance.txt sidecars.
    group = _ufo_group()
    nfo_dir = tmp_path / "nfo"
    nfo_dir.mkdir()
    basename = release_basename(group)
    (nfo_dir / f"{basename}.nfo").write_text(
        render_gotek_nfo(title="Example Space Tactics", description="4 disks"))
    (nfo_dir / f"{basename}.provenance.json").write_text(json.dumps({"x": 1}))
    (nfo_dir / f"{basename}.provenance.txt").write_text("provenance details")

    original_dir = tmp_path / "original"
    original_dir.mkdir()
    for r in group.records:
        (original_dir / r.source_filename).write_bytes(b"ADF" * 100)

    export_release(group, tmp_path / "staging" / "run1", original_dir=original_dir,
                   nfo_dir=nfo_dir)
    folder = tmp_path / "staging" / "run1" / "ADF" / basename
    files = sorted(p.name for p in folder.iterdir())
    assert f"{basename}.nfo" in files
    assert f"{basename}.provenance.json" not in files
    assert f"{basename}.provenance.txt" not in files
