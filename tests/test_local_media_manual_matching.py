"""Regression tests: local-media manual candidates (PDF/TXT from manual_roots).

Root cause being guarded (local-manual matching task):

  * ``LocalMediaProvider.discover()`` originally indexed ONLY image candidates
    from ``config.roots`` / ``config.media_roots``; manuals discovered from
    ``config.manual_roots`` never entered the candidate index used by
    ``resolve()`` / ``_select()`` -- so every release reported
    ``0 candidates evaluated`` despite 1381 scanned manual files.

Behavior under test:

  * manuals from ``manual_roots`` are indexed as ``Manual`` category
    candidates (PDF + TXT);
  * structural manual identity matching (NEVER substring containment):
    version suffix stripping, hyphen/underscore/separator normalization,
    article movement, roman<->arabic numerals;
  * must-NOT-match safeguards (``Defender`` vs ``Defender II`` etc.);
  * matched manual survives into the RTFM sidecar flow
    (``_compose_sections`` -> ``build_rtfm_for_group``);
  * image-only matching behavior is unchanged (no regression).

Fully synthetic: no maintainer corpus, no host paths beyond pytest tmp dirs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amiga_adf_library_builder import local_media as lm
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.rtfm import (
    RtfmConfig,
    build_rtfm_for_group,
    _compose_sections,
    discover_sources,
)


# --- helpers -----------------------------------------------------------------


def _group(title: str, **kw) -> ReleaseGroup:
    fn = kw.get("source_filename") or f"{title}.adf"
    rec = ParsedRecord(
        source_filename=fn,
        ext="adf",
        title=title,
    )
    return ReleaseGroup(
        release_key=kw.get("release_key") or f"{title.lower()}|",
        title=title,
        edition=None,
        group=None,
        chipset=None,
        language=None,
        version=kw.get("version"),
        alt_marker=None,
        ext="adf",
        records=[rec],
        disks=[rec],
    )


def _manual_provider(root: Path, manuals: dict[str, str], **cfg_kw) -> lm.LocalMediaProvider:
    """Provider whose ONLY sources are manual-root PDF/TXT files."""
    mroot = root / "manuals"
    mroot.mkdir(parents=True, exist_ok=True)
    for stem, body in manuals.items():
        p = mroot / stem
        p.write_bytes(body.encode("utf-8"))
    cfg = lm.LocalMediaConfig(
        enabled=True,
        manual_roots=(lm.ManualRoot(path=str(mroot)),),
        **cfg_kw,
    )
    prov = lm.LocalMediaProvider(cfg, root / "cache")
    prov.discover()
    return prov


# --- discovery ---------------------------------------------------------------


def test_manual_roots_indexed_into_candidate_index(tmp_path: Path) -> None:
    """PDF + TXT manuals from manual_roots enter the provider candidate index."""
    prov = _manual_provider(
        tmp_path,
        {"Neuromancer.pdf": "fake-pdf-bytes", "Ogre.txt": "Ogre manual text"},
    )
    manuals = [c for c in prov._index if c.category == "Manual"]
    assert len(manuals) == 2
    assert {c.path.suffix.lower() for c in manuals} == {".pdf", ".txt"}


def test_manual_candidates_reported_in_diagnostics(tmp_path: Path) -> None:
    """Diagnostics must not lie: a manual-provider-only run evaluates manuals."""
    prov = _manual_provider(tmp_path, {"Ogre.txt": "Ogre manual"})
    result = prov.resolve(_group("Ogre"))
    assert result.candidates_evaluated, "diagnostics must show manuals evaluated"
    assert any(d["category"] == "Manual" for d in result.candidates_evaluated)


# --- should-match (real-world cases) ----------------------------------------


@pytest.mark.parametrize(
    "title,stem",
    [
        ("A 10 Tank Killer v1.0", "A-10 Tank Killer.pdf"),
        ("A 10 Tank Killer v1.5", "A-10 Tank Killer.pdf"),
        ("Hacker II The Doomsday Papers v1.0", "Hacker II_ The Doomsday Papers.txt"),
        ("Dark Queen of Krynn, The v1.0", "The Dark Queen of Krynn.txt"),
        ("King of Chicago, The", "The King of Chicago.txt"),
        ("Neuromancer", "Neuromancer.pdf"),
        ("Ogre v1.06", "Ogre.txt"),
        ("Rocket Ranger", "Rocket Ranger.txt"),
        ("Stunt Car Racer", "Stunt Car Racer.txt"),
        ("UFO Enemy Unknown", "UFO_ Enemy Unknown.txt"),
        ("Ultima IV Quest of the Avatar", "Ultima IV_ Quest of the Avatar.txt"),
        ("Ultima V Warriors of Destiny", "Ultima V_ Warriors of Destiny.txt"),
        ("Ultima VI The False Prophet v1.12", "Ultima VI_ The False Prophet.txt"),
        ("Untouchables, The", "The Untouchables.txt"),
    ],
)
def test_manual_should_match(tmp_path: Path, title: str, stem: str) -> None:
    prov = _manual_provider(tmp_path, {stem: "CONTROLS\n\nFire: Space\n"})
    result = prov.resolve(_group(title))
    assert result.outcome == "auto_match", f"{title!r} -> {result.outcome} ({result.match_method})"
    assert result.found is True
    assert result.category == "Manual"
    # Manual-vs-artwork routing: a manual is NEVER copied into the artwork
    # cache; it is surfaced as a read-only manual source instead.
    assert result.cached_path is None
    assert result.manual_source is not None
    assert result.manual_source.name == stem


# --- must-NOT-match safeguards -----------------------------------------------


@pytest.mark.parametrize(
    "title,stem",
    [
        ("Defender", "Defender II.txt"),
        ("Defender", "Centurion_ Defender of Rome.txt"),
        ("Hacker", "Hacker II_ The Doomsday Papers.txt"),
    ],
)
def test_manual_must_not_match(tmp_path: Path, title: str, stem: str) -> None:
    """Short title must never match a longer unrelated title (no substring)."""
    prov = _manual_provider(tmp_path, {stem: "CONTROLS\n\nFire: Space\n"})
    result = prov.resolve(_group(title))
    assert result.outcome == "no_match", f"{title!r} wrongly matched {stem!r}"
    assert result.found is False
    assert result.cached_path is None


# --- RTFM / sidecar flow ------------------------------------------------------


def test_matched_txt_manual_reaches_rtfm_sidecar(tmp_path: Path) -> None:
    """A matched local TXT manual must flow into the RTFM builder output."""
    mroot = tmp_path / "manuals"
    mroot.mkdir()
    (mroot / "Ogre.txt").write_text("GETTING STARTED\n\nInsert disk 1 and boot.\n")
    cfg = RtfmConfig(enabled=True, manuals_roots=(str(mroot),))
    sources = discover_sources(cfg)
    assert len(sources) == 1
    result = build_rtfm_for_group(
        _group("Ogre"),
        cfg=cfg,
        rtfm_dir=tmp_path / "rtfm",
        sources=sources,
        basename="Ogre",
    )
    assert result.written is True, f"expected emitted .rtfm, got: {result.review_reason}"
    assert result.rtfm_path is not None and result.rtfm_path.is_file()
    text = result.rtfm_path.read_text(encoding="utf-8")
    assert "Insert disk 1 and boot." in text
    # Matched manual must be retained in provenance.
    assert result.sources and result.sources[0].filename == "Ogre.txt"


def test_matched_pdf_manual_reaches_rtfm_sidecar(tmp_path: Path) -> None:
    """A matched local PDF manual must reach the RTFM sidecar via extraction."""
    import fitz  # pymupdf (present in the test env; skip otherwise)

    mroot = tmp_path / "manuals"
    mroot.mkdir()
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        # > 32 non-whitespace chars: the extraction layer's native-text
        # minimum (native_text_min_chars=32) requires it, otherwise the
        # page is marked needs-OCR and routed for review.
        "GETTING STARTED\n\nBoot the Ogre disk. Fire with the joystick "
        "button to launch, and steer the tank across the battlefield.",
    )
    doc.save(str(mroot / "Ogre.pdf"))
    doc.close()

    cfg = RtfmConfig(enabled=True, manuals_roots=(str(mroot),))
    sources = discover_sources(cfg)
    assert len(sources) == 1 and sources[0].path.suffix.lower() == ".pdf"
    result = build_rtfm_for_group(
        _group("Ogre"),
        cfg=cfg,
        rtfm_dir=tmp_path / "rtfm",
        sources=sources,
        basename="Ogre",
    )
    assert result.written is True, f"expected emitted .rtfm, got: {result.review_reason}"
    assert result.rtfm_path is not None and result.rtfm_path.is_file()
    text = result.rtfm_path.read_text(encoding="utf-8")
    assert "Boot the Ogre disk." in text
    assert result.sources and result.sources[0].filename == "Ogre.pdf"


def test_manual_provider_match_feeds_compose_sections(tmp_path: Path) -> None:
    """Provider-level manual match and RTFM scoring agree on the same stem."""
    prov = _manual_provider(tmp_path, {"Neuromancer.txt": "Neuromancer manual"})
    result = prov.resolve(_group("Neuromancer"))
    assert result.outcome == "auto_match"
    mroot = tmp_path / "manuals"
    cfg = RtfmConfig(enabled=True, manuals_roots=(str(mroot),))
    sources = discover_sources(cfg)
    sections, prov_sources, order, skipped = _compose_sections(
        sources, _group("Neuromancer"), cfg=cfg
    )
    assert "Neuromancer manual" in "\n".join(str(v) for v in sections.values())


# --- image behavior unchanged -------------------------------------------------


def test_image_matching_unchanged_with_manual_roots_present(tmp_path: Path) -> None:
    """Adding manual_roots must not alter image discovery/selection."""
    root = tmp_path
    img = root / "img"
    (img / "Xenon").mkdir(parents=True)
    (img / "Xenon" / "cover.png").write_bytes(b"\x89PNG fake")
    cfg = lm.LocalMediaConfig(
        enabled=True,
        media_roots=(lm.MediaRoot(path=str(img), asset_type="Box - Front"),),
        manual_roots=(lm.ManualRoot(path=str(tmp_path / "manuals")),),
    )
    prov = lm.LocalMediaProvider(cfg, root / "cache")
    prov.discover()
    result = prov.resolve(_group("Xenon"))
    assert result.outcome == "auto_match"
    assert result.category == "Box - Front"
    assert result.match_method == lm.MatchMethod.EXACT_CANONICAL
    assert Path(result.cached_path).name == "cover.png"


def test_manual_loses_to_confident_image_match(tmp_path: Path) -> None:
    """A confident image match in preferred categories wins over the manual."""
    root = tmp_path
    img = root / "img"
    (img / "Xenon").mkdir(parents=True)
    (img / "Xenon" / "cover.png").write_bytes(b"\x89PNG fake")
    manuals = root / "manuals"
    manuals.mkdir()
    (manuals / "Xenon.txt").write_text("Xenon manual\n")
    cfg = lm.LocalMediaConfig(
        enabled=True,
        media_roots=(lm.MediaRoot(path=str(img), asset_type="Box - Front"),),
        manual_roots=(lm.ManualRoot(path=str(manuals)),),
    )
    prov = lm.LocalMediaProvider(cfg, root / "cache")
    prov.discover()
    result = prov.resolve(_group("Xenon"))
    assert result.outcome == "auto_match"
    assert result.category == "Box - Front"


def test_manual_version_suffix_not_stripped_mid_title(tmp_path: Path) -> None:
    """Only a TRAILING version token is stripped; mid-title tokens stay identity."""
    prov = _manual_provider(tmp_path, {"Ogre.txt": "Ogre manual\n"})
    # "Ogre v1.06" matches Ogre.txt (trailing version); but a title that merely
    # contains a version-like token mid-string must not collapse onto "Ogre".
    result = prov.resolve(_group("Ogre Battle v1.0"))
    assert result.outcome == "no_match", "versionless prefix must not match Ogre"
