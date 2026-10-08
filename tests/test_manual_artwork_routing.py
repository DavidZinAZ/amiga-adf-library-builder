"""Regression tests: manual-vs-artwork routing after LocalMediaProvider resolution.

Defect being guarded (v0.3.4 Windows run):

  After the local-media matcher correctly resolved a ``Manual`` category
  candidate, the enrich phase treated it generically as an ARTWORK master:

    * the provider cached the PDF/TXT manual (plus its ``.prov.json``) into
      ``assets/artwork-original`` (the provider's cache dir for pipeline runs);
    * enrich emitted "Found artwork in a local library." and fed the manual
      into the image decoder -> ``artwork_invalid_image``
      ("cannot identify image file ...Neuromancer.pdf");
    * the 25 MB image size safety cap rejected a legitimate 42.5 MB PDF
      manual (A-10 Tank Killer.pdf).

Correct contract under test:

  * Manual candidate -> read-only manual source (``manual_source``), NEVER
    copied into artwork-original, never image-decoded, never safety-capped,
    never given artwork provenance, never "Found artwork";
  * Image candidate -> existing artwork path unchanged;
  * matched manual reaches the RTFM sidecar flow (extra-sources union with
    the deterministic RTFM build).

Uses representative real-world filenames from the v0.3.4 failure report.
Fully synthetic: no host paths beyond pytest tmp dirs.
"""

from __future__ import annotations

import json
from pathlib import Path

from amiga_adf_library_builder import local_media as lm
from amiga_adf_library_builder.enrich import enrich_group
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup, ScanRecord
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.rtfm import (
    CATEGORY_MANUALS,
    RtfmConfig,
    RtfmSource,
    build_rtfm_for_group,
)


# --- helpers -----------------------------------------------------------------


def _group(title: str) -> ReleaseGroup:
    rec = parse_filename(f"{title} (Disk 1 of 1).adf")
    return ReleaseGroup(
        release_key=rec.title.lower(),
        title=rec.title,
        edition=None,
        group=None,
        chipset=None,
        language=None,
        version=None,
        alt_marker=None,
        ext="adf",
        records=[rec],
        disks=[rec],
    )


def _scans(group: ReleaseGroup, tmp_path: Path) -> dict[str, ScanRecord]:
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


def _pipeline_provider(
    tmp_path: Path,
    manuals: dict[str, bytes],
    *,
    images: dict[str, bytes] | None = None,
) -> tuple[lm.LocalMediaProvider, Path]:
    """Provider anchored EXACTLY like the pipeline: cache_dir IS
    ``artwork-original`` (pipeline.py: LocalMediaProvider(lm_cfg,
    cfg.artwork_original_dir)). Returns (provider, artwork_original_dir)."""
    mroot = tmp_path / "manual-root"
    mroot.mkdir(parents=True, exist_ok=True)
    for stem, body in manuals.items():
        (mroot / str(stem)).write_bytes(body)
    artwork_original = tmp_path / "artwork-original"
    cfg = lm.LocalMediaConfig(
        enabled=True,
        media_roots=(),
        manual_roots=(lm.ManualRoot(path=str(mroot)),),
    )
    prov = lm.LocalMediaProvider(cfg, artwork_original)
    prov.discover()
    return prov, artwork_original


# --- 1) TXT manual: matched, never artwork ------------------------------------


def test_txt_manual_never_becomes_artwork(tmp_path: Path) -> None:
    """A matched TXT manual is retained as a manual source: not copied to
    artwork-original, not used as artwork master, no invalid-image event."""
    prov, artwork_original = _pipeline_provider(
        tmp_path,
        {"Hacker II_ The Doomsday Papers.txt": b"CONTROLS\n\nFire: Space\n"},
    )
    g = _group("Hacker II The Doomsday Papers")
    res = enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=_scans(g, tmp_path),
        artwork_original_dir=artwork_original,
        artwork_processed_dir=tmp_path / "artwork-processed",
        local_media_provider=prov,
    )
    # Nothing written into artwork-original (no manual copy, no .prov.json).
    assert not artwork_original.exists() or not list(artwork_original.iterdir()), (
        "matched manual must NOT be copied into assets/artwork-original"
    )
    # Never an artwork master; no image decode; no artwork_invalid_image.
    assert res.artwork_master is None
    assert not any(
        e.category.value == "artwork_invalid_image" for e in res.events
    ), "manual must never trigger image validation"
    # Manual source retained for RTFM, with the real filename.
    assert len(res.manual_sources) == 1
    ms = res.manual_sources[0]
    assert ms["filename"] == "Hacker II_ The Doomsday Papers.txt"
    assert Path(ms["path"]).is_file()


def test_pdf_manual_never_becomes_artwork(tmp_path: Path) -> None:
    """Same contract for a PDF manual (Neuromancer.pdf failure case)."""
    prov, artwork_original = _pipeline_provider(
        tmp_path,
        {"Neuromancer.pdf": b"%PDF-1.4 synthetic"},
    )
    g = _group("Neuromancer")
    res = enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=_scans(g, tmp_path),
        artwork_original_dir=artwork_original,
        artwork_processed_dir=tmp_path / "artwork-processed",
        local_media_provider=prov,
    )
    assert not artwork_original.exists() or not list(artwork_original.iterdir())
    assert res.artwork_master is None
    assert not any(
        e.category.value == "artwork_invalid_image" for e in res.events
    )
    assert res.manual_sources and res.manual_sources[0]["filename"] == "Neuromancer.pdf"


# --- 2) Large PDF manual bypasses the image safety cap ------------------------


def test_large_pdf_manual_bypasses_image_safety_cap(tmp_path: Path) -> None:
    """A manual LARGER than the 25 MB image cap is accepted as a manual
    source (A-10 Tank Killer.pdf, 42,507,633 bytes in the field). The image
    safety cap itself is NOT removed: image candidates are still bounded."""
    big = b"%PDF-1.4 " + b"\x00" * 26_000_000  # > 25 MB
    prov, artwork_original = _pipeline_provider(
        tmp_path,
        {"A-10 Tank Killer.pdf": big},
    )
    g = _group("A 10 Tank Killer")
    res = enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=_scans(g, tmp_path),
        artwork_original_dir=artwork_original,
        artwork_processed_dir=tmp_path / "artwork-processed",
        local_media_provider=prov,
    )
    # No safety-cap rejection: the manual is retained.
    assert res.manual_sources, "large PDF manual must be accepted as a manual"
    assert Path(res.manual_sources[0]["path"]).stat().st_size > 25_000_000
    assert not any(
        e.category.value == "artwork_invalid_image" and "safety cap" in (e.error or "")
        for e in res.events
    )
    # And no oversized copy in artwork-original.
    assert not artwork_original.exists() or not list(artwork_original.iterdir())


def test_image_safety_cap_still_enforced_for_images(tmp_path: Path) -> None:
    """The cap is image-specific: an oversized IMAGE candidate still rejects
    with the safety-cap error (cap not globally removed)."""
    big_png = b"\x89PNG fake oversized" + b"\x00" * 26_000_000
    root = tmp_path / "img-root"
    root.mkdir()
    (root / "Xenon.png").write_bytes(big_png)
    artwork_original = tmp_path / "artwork-original"
    cfg = lm.LocalMediaConfig(
        enabled=True,
        media_roots=(lm.MediaRoot(path=str(root), asset_type="Box - Front"),),
        manual_roots=(),
    )
    prov = lm.LocalMediaProvider(cfg, artwork_original)
    prov.discover()
    import pytest as _pytest

    with _pytest.raises(lm.LocalMediaError, match="safety cap"):
        prov.resolve(_group("Xenon"))


# --- 3) Image candidate: artwork path unchanged --------------------------------


def test_image_candidate_still_follows_artwork_path(
    tmp_path: Path, monkeypatch
) -> None:
    """An image match still produces an artwork master + processed artwork;
    the manual routing change does not touch the image path."""
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        import pytest

        pytest.skip("Pillow not installed; artwork path untested here")
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGB", (320, 240), "blue").save(buf, "PNG")
    root = tmp_path / "img-root"
    root.mkdir()
    (root / "Xenon 2").mkdir(parents=True)
    (root / "Xenon 2" / "cover.png").write_bytes(buf.getvalue())
    artwork_original = tmp_path / "artwork-original"
    cfg = lm.LocalMediaConfig(
        enabled=True,
        media_roots=(lm.MediaRoot(path=str(root), asset_type="Box - Front"),),
        manual_roots=(),
    )
    prov = lm.LocalMediaProvider(cfg, artwork_original)
    prov.discover()
    g = _group("Xenon 2")
    res = enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=_scans(g, tmp_path),
        artwork_original_dir=artwork_original,
        artwork_processed_dir=tmp_path / "artwork-processed",
        local_media_provider=prov,
    )
    assert res.artwork_master is not None
    assert Path(res.artwork_master).suffix.lower() == ".png"
    assert res.artwork_resized is not None and Path(res.artwork_resized).is_file()
    assert res.manual_sources == []
    assert not any(e.category.value == "artwork_invalid_image" for e in res.events)


# --- 4) Messaging: manual vs artwork -------------------------------------------


def test_manual_match_logs_manual_not_artwork_message(tmp_path: Path) -> None:
    """The live activity log must say 'Found manual in a local library.' and
    must NOT say 'Found artwork in a local library.' for a manual match."""
    prov, artwork_original = _pipeline_provider(
        tmp_path,
        {"Rocket Ranger.txt": b"CONTROLS\n\nLaunch: Space\n"},
    )
    lines: list[str] = []
    g = _group("Rocket Ranger")
    res = enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=_scans(g, tmp_path),
        artwork_original_dir=artwork_original,
        artwork_processed_dir=tmp_path / "artwork-processed",
        local_media_provider=prov,
        activity=lines.append,
    )
    assert any("Found manual in a local library." in ln for ln in lines), lines
    assert not any("Found artwork in a local library." in ln for ln in lines), lines


def test_image_match_logs_artwork_message(tmp_path: Path) -> None:
    """Image matches keep the existing 'Found artwork' message."""
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        import pytest

        pytest.skip("Pillow not installed; artwork path untested here")
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGB", (320, 240), "red").save(buf, "PNG")
    root = tmp_path / "img-root"
    root.mkdir()
    (root / "Stunt Car Racer").mkdir(parents=True)
    (root / "Stunt Car Racer" / "cover.png").write_bytes(buf.getvalue())
    artwork_original = tmp_path / "artwork-original"
    cfg = lm.LocalMediaConfig(
        enabled=True,
        media_roots=(lm.MediaRoot(path=str(root), asset_type="Box - Front"),),
        manual_roots=(),
    )
    prov = lm.LocalMediaProvider(cfg, artwork_original)
    prov.discover()
    lines: list[str] = []
    g = _group("Stunt Car Racer")
    enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=_scans(g, tmp_path),
        artwork_original_dir=artwork_original,
        artwork_processed_dir=tmp_path / "artwork-processed",
        local_media_provider=prov,
        activity=lines.append,
    )
    assert any("Found artwork in a local library." in ln for ln in lines), lines


# --- 5) End-to-end: matched manual -> RTFM sidecar ------------------------------


def test_matched_manual_reaches_rtfm_sidecar(tmp_path: Path) -> None:
    """The full contract: enrich records the manual source; converting it to
    an RtfmSource (as the pipeline does) feeds the EXISTING RTFM builder,
    which emits the .rtfm sidecar with the manual's content and provenance
    carrying the REAL filename."""
    manual_body = "GETTING STARTED\n\nInsert the Ogre disk and boot the machine.\n"
    prov, artwork_original = _pipeline_provider(
        tmp_path,
        {"Ogre.txt": manual_body.encode("utf-8")},
    )
    g = _group("Ogre")
    res = enrich_group(
        g,
        nfo_dir=tmp_path / "nfo",
        scans=_scans(g, tmp_path),
        artwork_original_dir=artwork_original,
        artwork_processed_dir=tmp_path / "artwork-processed",
        local_media_provider=prov,
    )
    assert len(res.manual_sources) == 1
    ms = res.manual_sources[0]

    # Pipeline conversion (mirrors pipeline.py's extra-sources union).
    extra = [
        RtfmSource(
            path=Path(ms["path"]),
            root=Path(ms["root"]),
            category=CATEGORY_MANUALS,
            stem=g.title,
        )
    ]
    cfg = RtfmConfig(enabled=True, manuals_roots=())
    result = build_rtfm_for_group(
        g,
        cfg=cfg,
        rtfm_dir=tmp_path / "rtfm",
        sources=list(extra),
        basename="Ogre",
    )
    assert result.written is True, f"expected emitted .rtfm, got: {result.review_reason}"
    assert result.rtfm_path is not None and result.rtfm_path.is_file()
    text = result.rtfm_path.read_text(encoding="utf-8")
    assert "Insert the Ogre disk and boot the machine." in text
    # Provenance keeps the REAL source filename, not the synthetic stem.
    assert result.sources and result.sources[0].filename == "Ogre.txt"


def test_large_manual_source_skipped_by_rtfm_cap_not_artwork_cap(
    tmp_path: Path,
) -> None:
    """A manual exceeding RTFM's own 8 MB source cap is skipped by the RTFM
    builder with an explicit note (its own DoS guard), never by the image
    safety cap and never as an artwork error."""
    big_txt = b"A" * (9 * 1024 * 1024)
    mroot = tmp_path / "manuals"
    mroot.mkdir()
    src_path = mroot / "Ogre.txt"
    src_path.write_bytes(big_txt)
    cfg = RtfmConfig(enabled=True, manuals_roots=())
    g = _group("Ogre")
    result = build_rtfm_for_group(
        g,
        cfg=cfg,
        rtfm_dir=tmp_path / "rtfm",
        sources=[RtfmSource(path=src_path, root=mroot, category=CATEGORY_MANUALS, stem=g.title)],
        basename="Ogre",
    )
    assert any("byte cap" in n for n in result.notes), result.notes
    assert result.written is False
