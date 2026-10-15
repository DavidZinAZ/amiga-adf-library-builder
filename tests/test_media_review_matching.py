"""Regression cases for byte identity and competing media choices."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

from amiga_adf_library_builder.metadata_source import MetadataSourceManager
from amiga_adf_library_builder.media_review import deduplicate_candidates
from amiga_adf_library_builder import rtfm as r


def test_dat_matches_same_bytes_and_rejects_changed_intake(tmp_path):
    disk = tmp_path / "renamed.adf"
    disk.write_bytes(b"disk bytes")
    data = disk.read_bytes()
    dat = tmp_path / "games.dat"
    dat.write_text(f'<datafile><game name="Space Quest"><rom name="disk.adf" size="{len(data)}" sha1="{hashlib.sha1(data).hexdigest()}" md5="{hashlib.md5(data).hexdigest()}" /></game></datafile>')
    with MetadataSourceManager(tmp_path / "sources.db") as manager:
        manager.add_source(dat)
        identity = hashlib.sha256(data).hexdigest()
        assert manager.lookup_file(disk, expected_sha256=identity)[0].title == "Space Quest"
        assert manager.lookup_by_sha256(identity) == []
        disk.write_bytes(b"changed bytes")
        assert manager.lookup_file(disk, expected_sha256=identity) == []


def test_normalized_title_lookup_and_cache_invalidation(tmp_path):
    dat = tmp_path / "games.dat"
    dat.write_text('<datafile><game name="Space Quest"><rom name="disk.adf" /></game></datafile>')
    with MetadataSourceManager(tmp_path / "sources.db") as manager:
        source = manager.add_source(dat)
        assert manager.lookup_by_title("SpaceQuest")[0].title == "Space Quest"
        manager.set_enabled(source, False)
        assert manager.lookup_by_title("SpaceQuest") == []


def sources(tmp_path, names):
    result = []
    for index, name in enumerate(names):
        path = tmp_path / f"{name}.txt"
        path.write_text(f"DOCUMENT {index}: Press FIRE to begin. Move with joystick.")
        result.append(r.RtfmSource(path=path, root=tmp_path, category=r.CATEGORY_INSTRUCTIONS, stem=name))
    return result


def group(title="Space Quest"):
    return SimpleNamespace(title=title, release_key=title.lower(), records=[], quarantine_reason=None)


def test_exact_manual_does_not_include_weaker_rival(tmp_path):
    result = r.build_rtfm_for_group(group(), cfg=r.RtfmConfig(enabled=True),
        rtfm_dir=tmp_path / "out", sources=sources(tmp_path, ["Space Quest", "Space Quert"]))
    assert result.written
    text = result.rtfm_path.read_text()
    assert "DOCUMENT 0" in text and "DOCUMENT 1" not in text


def test_numbered_parts_are_distinct():
    assert not r._match_candidates("Space Part 2", "Space Part 3", "Space Part 3").matched


def test_multiple_distinct_manuals_wait_for_operator(tmp_path):
    result = r.build_rtfm_for_group(group(), cfg=r.RtfmConfig(enabled=True),
        rtfm_dir=tmp_path / "out", sources=sources(tmp_path, ["Space Quest", "Space-Quest"]))
    assert not result.written and result.routed_for_review
    assert len(result.media_candidates) == 2


def test_identical_copies_are_one_candidate_per_purpose(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("same bytes")
    b.write_bytes(a.read_bytes())
    candidates = deduplicate_candidates([
        {"path": str(a), "purpose": "manual", "score": .95},
        {"path": str(b), "purpose": "manual", "score": 1.0},
        {"path": str(a), "purpose": "reference", "score": 1.0},
    ])
    assert len(candidates) == 2
    manual = next(c for c in candidates if c["purpose"] == "manual")
    assert manual["path"] == str(b) and len(manual["locations"]) == 2


def test_enrichment_never_uses_another_releases_disk(tmp_path):
    from amiga_adf_library_builder.enrich import enrich_group
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.parser import parse_filename
    from amiga_adf_library_builder.scanner import scan_file
    own, rival = tmp_path / "Space Quest.adf", tmp_path / "Other Game.adf"
    own.write_bytes(b"unknown disk")
    rival.write_bytes(b"known rival")
    dat = tmp_path / "games.dat"
    dat.write_text(f'<datafile><game name="Other Game"><rom name="other.adf" sha1="{hashlib.sha1(rival.read_bytes()).hexdigest()}" /></game></datafile>')
    release = group_records([parse_filename(own.name)])[0]
    scans = {p.name: scan_file(p) for p in (own, rival)}
    with MetadataSourceManager(tmp_path / "sources.db") as manager:
        manager.add_source(dat)
        result = enrich_group(release, nfo_dir=tmp_path / "nfo", scans=scans,
            artwork_original_dir=tmp_path / "art", artwork_processed_dir=tmp_path / "processed",
            include_artwork=False, metadata_source_manager=manager)
    assert not any(item["matched"] for item in result.dat_results)
    assert release.title == "Space Quest"


def test_selected_pdf_is_converted_to_text_rtfm(tmp_path):
    import pymupdf
    from amiga_adf_library_builder.media_review import render_manual_choices
    path = tmp_path / "manual.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "CONTROLS\nPress FIRE to start the game. Move with the joystick.")
        pdf.save(path)
    payload = render_manual_choices(group(), [{"path": str(path)}], tmp_path / "out")
    assert not payload.startswith(b"%PDF")
    assert b"Press FIRE" in payload


def test_saved_none_prevents_automatic_manual_on_next_run(tmp_path):
    from amiga_adf_library_builder.models import StagedReleaseEntry, StagedLibrary
    from amiga_adf_library_builder.library_state import CurationStateManager
    release = group()
    entry = StagedReleaseEntry(release_key=release.release_key, title=release.title,
        edition=None, group=None, chipset=None, media_choices={"manual": []})
    state = tmp_path / "curation" / "library_state_test.json"
    state.parent.mkdir()
    CurationStateManager(state).save(StagedLibrary(releases={entry.release_key: entry}))
    sources(tmp_path, ["Space Quest"])
    results = r.build_rtfm_all([release], cfg=r.RtfmConfig(enabled=True, instructions_roots=(str(tmp_path),)),
        rtfm_dir=tmp_path / "out", library_root=tmp_path)
    assert not results[0].written
    assert "omitted" in results[0].notes[0]


def test_versioned_release_can_review_generic_artwork(tmp_path):
    from amiga_adf_library_builder.local_media import LocalMediaProvider, LocalMediaConfig, LocalMediaCandidate, MatchMethod
    provider = LocalMediaProvider(LocalMediaConfig(enabled=True), tmp_path / "cache")
    release = group("A-10 Tank Killer v1.5")
    identities = provider._group_identities(release)
    candidate = LocalMediaCandidate(path=tmp_path / "Box - Front" / "A-10 Tank Killer-01.png",
        root=tmp_path, category="Box - Front")
    method, score = provider._score(candidate, identities)
    assert method == MatchMethod.CANONICAL_REUSE and score >= .95
    rival = LocalMediaCandidate(path=tmp_path / "Box - Front" / "A-10 Tank Killer v1.0.png",
        root=tmp_path, category="Box - Front")
    assert provider._score(rival, identities)[1] < .95


def test_inline_versions_do_not_block_manual_matching_as_spelling_errors():
    from amiga_adf_library_builder.grouper import group_records
    from amiga_adf_library_builder.parser import parse_filename
    groups = group_records([parse_filename(f"Deluxe Pac Man v{v}.adf") for v in ("1.2", "1.4")])
    assert len(groups) == 2
    assert all(g.quarantine_reason is None for g in groups)
