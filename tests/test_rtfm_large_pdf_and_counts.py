from __future__ import annotations

from pathlib import Path

import fitz

from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.rtfm import RtfmConfig, RtfmSource, build_rtfm_for_group


def _group(title: str) -> ReleaseGroup:
    record = ParsedRecord(source_filename=f"{title}.adf", ext="adf", title=title)
    return ReleaseGroup(
        release_key=f"{title.lower()}|", title=title, edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        records=[record], disks=[record],
    )


def test_large_pdf_above_text_source_cap_can_build_rtfm(tmp_path: Path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    pdf = manuals / "A-10 Tank Killer.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "CONTROLS\n\nFire the cannon with the joystick button and move the tank.",
    )
    doc.save(str(pdf), garbage=4, deflate=True)
    doc.close()
    # Extend the real PDF with trailing binary padding. PDF parsers ignore bytes
    # after the final EOF marker; the file exercises the production size guard.
    with pdf.open("ab") as fh:
        fh.write(b"x" * (42_507_633 - pdf.stat().st_size))
    assert pdf.stat().st_size == 42_507_633

    source = RtfmSource(path=pdf, root=manuals, category="manuals", stem="A-10 Tank Killer")
    result = build_rtfm_for_group(
        _group("A 10 Tank Killer v1.0"),
        cfg=RtfmConfig(enabled=True),
        rtfm_dir=tmp_path / "rtfm",
        sources=[source],
        basename="A 10 Tank Killer v1.0",
    )

    assert result.written
    assert result.rtfm_path and result.rtfm_path.is_file()
    assert "Fire the cannon" in result.rtfm_path.read_text(encoding="utf-8")


def test_pdf_over_extraction_cap_is_explicit_no_output(tmp_path: Path):
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    pdf = manuals / "A-10 Tank Killer.pdf"
    pdf.write_bytes(b"not a PDF" * (8 * 1024 * 1024))
    source = RtfmSource(path=pdf, root=manuals, category="manuals", stem="A-10 Tank Killer")
    result = build_rtfm_for_group(
        _group("A 10 Tank Killer v1.0"),
        cfg=RtfmConfig(enabled=True),
        rtfm_dir=tmp_path / "rtfm",
        sources=[source],
        basename="A 10 Tank Killer v1.0",
    )

    assert not result.written
    assert result.routed_for_review
    assert result.review_reason
    assert result.provenance_path and result.provenance_path.is_file()
    assert result.rtfm_path is None or not result.rtfm_path.exists()
    assert any("exceeds" in note for note in result.notes)
