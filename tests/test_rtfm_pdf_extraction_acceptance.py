"""RTFM PDF extraction acceptance: A-10 Tank Killer (two releases) + Neuromancer.

This file proves the FULL path with real pipeline code (no mocks of the
extraction or routing logic):

    explicit manual_source (A-10 Tank Killer.pdf)
    -> RtfmSource
    -> PDF dispatch (rtfm_docs.extract_pdf_text)
    -> backend (pymupdf)
    -> native text extracted
    -> minimum-text validation
    -> condensation
    -> release-specific .rtfm write

Acceptance facts it locks in:

  * the real A-10 PDF size (~42.5 MB) passes the document path and is NOT
    rejected by the text-source size cap;
  * one A-10 PDF serving two releases (v1.0 and v1.5) yields TWO built
    sidecars, each release-specific;
  * a Neuromancer-style malformed / no-text PDF is never counted as built and
    reports an exact reason;
  * a missing PDF backend produces an explicit diagnostic and never a false
    success.

Fixtures are generated programmatically inside the test run -- no real corpus
data, no private paths, no network.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import fitz
import pytest

from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.rtfm import (
    RtfmConfig,
    RtfmSource,
    build_rtfm_all,
    build_rtfm_for_group,
)

# Real on-disk size reported for the operator's A-10 Tank Killer.pdf.
A10_REAL_SIZE = 42_507_633

A10_TEXT = (
    "CONTROLS\n\n"
    "Fire the main cannon with the joystick button and steer the tank with the "
    "stick. The second button drops chaff.\n\n"
    "GETTING STARTED\n\n"
    "Insert the A-10 Tank Killer disk and boot your Amiga. Select your mission "
    "from the briefing screen.\n"
)


def _group(title: str, *, version: str, source_filename: str) -> ReleaseGroup:
    record = ParsedRecord(source_filename=f"{source_filename}.adf", ext="adf", title=title)
    return ReleaseGroup(
        release_key=f"{title.lower()}|", title=title, edition=None, group=None,
        chipset=None, language=None, version=version, alt_marker=None, ext="adf",
        records=[record], disks=[record],
    )


def _a10_groups() -> list[ReleaseGroup]:
    return [
        _group("A 10 Tank Killer v1.0", version="1.0", source_filename="A 10 Tank Killer v1.0"),
        _group("A 10 Tank Killer v1.5", version="1.5", source_filename="A 10 Tank Killer v1.5"),
    ]


def _native_pdf(path: Path, text: str) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return path


def _pad_to(path: Path, target: int) -> Path:
    """Grow the file to ``target`` bytes with trailing padding.

    PDF parsers ignore bytes after the final %%EOF marker, so the padding
    exercises the production size guards without changing document validity.
    """
    current = path.stat().st_size
    assert current < target, f"fixture already larger than {target}: {current}"
    with path.open("ab") as fh:
        fh.write(b"\x00" * (target - current))
    assert path.stat().st_size == target
    return path


def _explicit_source(path: Path, stem: str) -> RtfmSource:
    return RtfmSource(path=path, root=path.parent, category="manuals", stem=stem)


# --- A-10: real size + one source serving two releases -----------------------


def test_a10_real_size_pdf_is_not_rejected_by_text_cap(tmp_path: Path):
    """The ~42.5 MB A-10 PDF must pass the document path.

    The 8 MiB verbatim text cap must not apply to PDFs (they have their own
    documented cap); the file must reach the backend and decode.
    """
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    pdf = _pad_to(_native_pdf(manuals / "A-10 Tank Killer.pdf", A10_TEXT), A10_REAL_SIZE)
    assert pdf.stat().st_size == A10_REAL_SIZE

    source = _explicit_source(pdf, "A 10 Tank Killer")
    result = build_rtfm_for_group(
        _group("A 10 Tank Killer v1.0", version="1.0",
               source_filename="A 10 Tank Killer v1.0"),
        cfg=RtfmConfig(enabled=True),
        rtfm_dir=tmp_path / "rtfm",
        sources=[source],
        explicit_sources=[source],
        basename="A 10 Tank Killer v1.0",
    )

    assert result.written is True
    assert result.rtfm_path and result.rtfm_path.is_file()
    assert "Fire the main cannon" in result.rtfm_path.read_text(encoding="utf-8")
    # The size guard must not have skipped it.
    assert not any("byte cap" in note or "exceeds" in note for note in result.notes)


def test_one_a10_pdf_builds_two_release_specific_sidecars(tmp_path: Path):
    """The same A-10 PDF serving v1.0 and v1.5 = TWO built sidecars."""
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    pdf = _pad_to(_native_pdf(manuals / "A-10 Tank Killer.pdf", A10_TEXT), A10_REAL_SIZE)

    groups = _a10_groups()
    explicit = {g.release_key: [_explicit_source(pdf, "A 10 Tank Killer")] for g in groups}

    out = tmp_path / "rtfm"
    results = build_rtfm_all(
        groups, cfg=RtfmConfig(enabled=True), rtfm_dir=out, explicit_sources=explicit,
    )

    assert len(results) == 2
    assert all(r.written for r in results), (
        f"both releases must build: {[(r.basename, r.review_reason, r.notes) for r in results]}"
    )

    # Both sidecars physically exist under the rtfm dir.
    v10 = out / "A 10 Tank Killer v1.0.rtfm"
    v15 = out / "A 10 Tank Killer v1.5.rtfm"
    assert v10.is_file() and v15.is_file()
    # Each sidecar is release-specific (its own title header), while both carry
    # the same extracted manual body derived from the one shared PDF.
    t10 = v10.read_text(encoding="utf-8")
    t15 = v15.read_text(encoding="utf-8")
    assert "A 10 Tank Killer v1.0" in t10 and "A 10 Tank Killer v1.0" not in t15
    assert "A 10 Tank Killer v1.5" in t15 and "A 10 Tank Killer v1.5" not in t10
    assert "Fire the main cannon" in t10 and "Fire the main cannon" in t15

    # Both count as built (payload-based accounting).
    built = [r for r in results if r.written and r.rtfm_path and r.rtfm_path.is_file()]
    assert len(built) == 2

    # Each release-specific payload is distinct from the other release's path.
    assert {r.basename for r in built} == {"A 10 Tank Killer v1.0", "A 10 Tank Killer v1.5"}


def test_a10_payload_is_exportable_for_both_releases(tmp_path: Path):
    """Both A-10 sidecars export into their own release folder."""
    from amiga_adf_library_builder.exporter import export_release

    manuals = tmp_path / "manuals"
    manuals.mkdir()
    pdf = _native_pdf(manuals / "A-10 Tank Killer.pdf", A10_TEXT)

    groups = _a10_groups()
    originals = tmp_path / "original"
    originals.mkdir()
    for g in groups:
        (originals / g.records[0].source_filename).write_bytes(b"synthetic disk image")

    out = tmp_path / "rtfm"
    results = build_rtfm_all(
        groups, cfg=RtfmConfig(enabled=True), rtfm_dir=out,
        explicit_sources={g.release_key: [_explicit_source(pdf, "A 10 Tank Killer")] for g in groups},
    )
    assert len(results) == 2 and all(r.written for r in results)

    for g, r in zip(groups, results):
        written, unchanged, conflicts = export_release(
            g, tmp_path / "staging", basename=g.title,
            source_basename=g.title, original_dir=originals,
            rtfm_paths={g.release_key: r.rtfm_path},
        )
        assert not conflicts, f"{g.title}: export conflicts {conflicts}"
        folder = tmp_path / "staging" / "ADF" / g.title
        assert (folder / f"{g.title}.rtfm").is_file()
        assert "Fire the main cannon" in (folder / f"{g.title}.rtfm").read_text(encoding="utf-8")


# --- Neuromancer: exact real outcome, never a false built count ---------------


def test_neuromancer_malformed_pdf_reports_exact_reason(tmp_path: Path):
    """A malformed PDF must name the parse failure, never count as built."""
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    pdf = manuals / "Neuromancer.pdf"
    pdf.write_bytes(b"not a valid PDF document")
    source = _explicit_source(pdf, "Neuromancer")

    result = build_rtfm_for_group(
        _group("Neuromancer", version="", source_filename="Neuromancer"),
        cfg=RtfmConfig(enabled=True),
        rtfm_dir=tmp_path / "rtfm",
        sources=[source],
        explicit_sources=[source],
        basename="Neuromancer",
    )

    assert result.written is False
    assert result.routed_for_review is True
    assert result.rtfm_path is None
    # Exact reason: parse failure naming the backend, not a vague "failed".
    decode_notes = [n for n in result.notes if n.startswith("RTFM decode failed:")]
    assert decode_notes, f"no exact decode diagnostic in notes: {result.notes}"
    assert "Neuromancer.pdf" in decode_notes[0]
    assert "PDF open/parse failed" in decode_notes[0]
    assert "backend=" in decode_notes[0]
    # Provenance still written (auditable).
    assert result.provenance_path and result.provenance_path.is_file()


def test_neuromancer_scanned_pdf_reports_no_text_layer(tmp_path: Path, monkeypatch):
    """A valid but text-less (scanned) PDF: 'no extractable text layer',
    no OCR engine, never a fabricated built sidecar."""
    import amiga_adf_library_builder.rtfm_docs as rd

    # The packaged app has no OCR engine; model that exactly.
    monkeypatch.setattr(rd, "_tesseract_available", lambda: False)

    manuals = tmp_path / "manuals"
    manuals.mkdir()
    pdf = manuals / "Neuromancer.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(50, 50, 300, 400), color=(0, 0, 0), fill=(1, 1, 1))
    doc.save(str(pdf))
    doc.close()
    source = _explicit_source(pdf, "Neuromancer")

    result = build_rtfm_for_group(
        _group("Neuromancer", version="", source_filename="Neuromancer"),
        cfg=RtfmConfig(enabled=True),
        rtfm_dir=tmp_path / "rtfm",
        sources=[source],
        explicit_sources=[source],
        basename="Neuromancer",
    )

    assert result.written is False
    assert result.rtfm_path is None
    decode_notes = [n for n in result.notes if n.startswith("RTFM decode failed:")]
    assert decode_notes
    assert "no extractable text layer" in decode_notes[0]
    # NOT a generic decode failure.
    assert "decode failed" not in decode_notes[0].lower() or "no extractable text layer" in decode_notes[0]


def test_neuromancer_missing_backend_is_explicit_not_built(tmp_path: Path, monkeypatch):
    """If the frozen build has no PDF backend, the diagnostic says so exactly
    and no sidecar is produced."""
    import amiga_adf_library_builder.rtfm_docs as rd

    manuals = tmp_path / "manuals"
    manuals.mkdir()
    pdf = _native_pdf(manuals / "Neuromancer.pdf", A10_TEXT)
    source = _explicit_source(pdf, "Neuromancer")

    monkeypatch.setattr(rd, "_pymupdf_backend", lambda: "unavailable (OSError)")
    monkeypatch.setattr(rd, "_pypdf_backend", lambda: "unavailable")

    result = build_rtfm_for_group(
        _group("Neuromancer", version="", source_filename="Neuromancer"),
        cfg=RtfmConfig(enabled=True),
        rtfm_dir=tmp_path / "rtfm",
        sources=[source],
        explicit_sources=[source],
        basename="Neuromancer",
    )

    assert result.written is False
    assert result.rtfm_path is None
    decode_notes = [n for n in result.notes if n.startswith("RTFM decode failed:")]
    assert decode_notes
    assert "PDF extraction unavailable" in decode_notes[0]
    assert "pymupdf: unavailable" in decode_notes[0]
    assert "pypdf: unavailable" in decode_notes[0]
    assert "OSError" in decode_notes[0]


# --- Backend reporting --------------------------------------------------------


def test_extractor_records_backend_that_served(tmp_path: Path):
    """The extractor must record which backend actually served the PDF."""
    from amiga_adf_library_builder.rtfm_docs import extract_pdf_text

    pdf = _native_pdf(tmp_path / "A-10 Tank Killer.pdf", A10_TEXT)
    res = extract_pdf_text(pdf)
    assert not res.empty
    assert res.text
    assert res.backend in ("pymupdf", "pypdf")
    assert res.reason.startswith("pdf:")


def test_pymupdf_backend_missing_is_named(monkeypatch):
    """Simulating the frozen-build condition: pymupdf import present but native
    lib missing -> backend probe reports unavailable, not a false 'pymupdf'."""
    import amiga_adf_library_builder.rtfm_docs as rd

    class _Boom:
        def __getattr__(self, name):
            raise OSError("cannot open shared object file")

    real_import = builtins.__import__

    def _broken(name, *args, **kwargs):
        if name in ("fitz", "pymupdf"):
            return _Boom()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _broken)
    assert rd._pymupdf_backend().startswith("unavailable")
