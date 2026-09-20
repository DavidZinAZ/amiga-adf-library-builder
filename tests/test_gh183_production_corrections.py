"""GH-183 regressions through production discovery, persistence and export."""
from dataclasses import replace
from pathlib import Path
import json

import pytest

from amiga_adf_library_builder import metadata, enrich, rtfm
from amiga_adf_library_builder.canonical import CanonicalLibrary, Game, Release, Provenance, SourceAuthority
from amiga_adf_library_builder.exporter import export_all, _sanitize_component
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.manual_lookup import get_document_associations, document_to_rtfm_sources
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.paths import resolve_config
from amiga_adf_library_builder.pipeline import run_pipeline
from amiga_adf_library_builder.run_config import RunConfig


def library(tmp_path, titles=("Hacker", "Hot Rod")):
    root = tmp_path / "library"
    original = root / "original"
    original.mkdir(parents=True)
    for title in titles:
        (original / f"{title} (Disk 1 of 1).adf").write_bytes(b"disk fixture")
    manuals = tmp_path / "manuals"
    manuals.mkdir()
    config = tmp_path / "rtfm.toml"
    config.write_text('[rtfm]\nenabled = true\n[rtfm.local]\nmanuals = ' + json.dumps(str(manuals)) + '\n')
    cfg, _ = resolve_config(library_root=str(root), original_dir=str(original))
    run = RunConfig(rtfm_config_path=str(config), include_artwork=False,
                    export=True, upstream_task_closed=True, one_per_game=False, run_id="gh183")
    return root, manuals, cfg, run


def trace(result):
    return {row["title"]: row for row in result["rtfm"]["manual_trace"]["per_release"]}


def test_diagnostics_match_physical_output_and_idempotent_export(tmp_path, monkeypatch):
    root, manuals, cfg, run = library(tmp_path)
    (manuals / "Hacker.txt").write_text("Move with joystick. Press fire to enter the terminal.")
    monkeypatch.setattr(metadata, "_text_get", lambda *a, **k: pytest.fail("offline HTTP"))
    for _ in range(2):
        result = run_pipeline(cfg, run)
        rows = trace(result)
        assert set(rows) == {"Hacker", "Hot Rod"}
        hacker = rows["Hacker"]
        assert hacker["written"] is True
        assert hacker["sources_count"] == 1
        assert hacker["doc_types"]
        assert hacker["no_rtfm_reason"] == ""
        assert Path(hacker["output_path"]).is_file()
        assert hacker["rtfm_path"] == hacker["output_path"]
        assert hacker["persistence_status"] == "persisted"
        assert hacker["provenance_persisted"] is True
        assert hacker["export_status"] == "exported"
        assert Path(hacker["export_path"]).read_bytes() == Path(hacker["rtfm_path"]).read_bytes()
        missing = rows["Hot Rod"]
        assert missing["written"] is False
        assert missing["sources_count"] == 0
        assert "no matching manual source" in missing["no_rtfm_reason"]
        assert missing["output_path"] is None
        assert missing["export_path"] is None
        assert missing["persistence_status"] == "not-written"
        assert result["rtfm"]["manual_trace"]["export_status"] == "completed"


@pytest.mark.parametrize("mode,reason", [
    ("no-config", "no RTFM config"), ("disabled", "enabled=false"),
    ("deselected", "deselected"), ("failure", "broken fixture"),
])
def test_every_release_has_skip_or_failure_diagnostics(tmp_path, monkeypatch, mode, reason):
    root, manuals, cfg, run = library(tmp_path)
    if mode == "no-config":
        run = replace(run, rtfm_config_path=None)
    elif mode == "disabled":
        Path(run.rtfm_config_path).write_text('[rtfm]\nenabled = false\n')
    elif mode == "deselected":
        run = replace(run, include_manuals_rtfm=False)
    else:
        def fail(*args, **kwargs):
            raise ValueError("broken fixture")
        monkeypatch.setattr(rtfm, "build_rtfm_all", fail)
    rows = trace(run_pipeline(cfg, run))
    assert set(rows) == {"Hacker", "Hot Rod"}
    for row in rows.values():
        assert row["written"] is False
        assert reason in row["no_rtfm_reason"]
        assert row["output_path"] is None
        assert row["export_path"] is None


@pytest.mark.parametrize("options,expected", [
    ({"export": False}, "not-requested"),
    ({"upstream_task_closed": False}, "blocked"),
    ({"verify_only": True}, "verify-only"),
])
def test_generation_does_not_claim_export(tmp_path, options, expected):
    root, manuals, cfg, run = library(tmp_path, ("Hacker",))
    (manuals / "Hacker.txt").write_text("Use the terminal to open the locked door.")
    row = trace(run_pipeline(cfg, replace(run, **options)))["Hacker"]
    assert row["written"] is True, row["no_rtfm_reason"]
    assert row["export_status"] == expected
    assert row["export_path"] is None


@pytest.mark.parametrize("title,expected", [
    ("Hacker", "Hacker"),
    ("Hacker II: The Doomsday Papers", "Hacker II_ The Doomsday Papers"),
    ("Hot Rod", "Hot Rod"),
])
def test_physical_export_uses_display_title_without_qualifiers(tmp_path, title, expected):
    original = tmp_path / "original"
    original.mkdir()
    name = "Input (Disk 1 of 1).adf"
    (original / name).write_bytes(b"original disk")
    group = group_records([parse_filename(name)])[0]
    group.title = title
    group.edition = "US"
    group.version = "v1.0"
    group.group = "source"
    with CanonicalLibrary(tmp_path / "curation" / "canonical.db") as canon:
        canon.upsert_game(Game(game_id="stable-id"))
        canon.upsert_release(Release(release_id=group.release_key, game_id="stable-id", region="US"))
        prov = Provenance(source="operator", authority=SourceAuthority.CURATION)
        canon.claim_field("game", "stable-id", "title", title, prov)
        canon.claim_field("release", group.release_key, "region", "US", prov)
    result = export_all([group], staging_dir=tmp_path / "staging", run_id="run",
                        upstream_task_closed=True, verified_artwork_width=150,
                        verified_artwork_height=150, original_dir=original, library_root=tmp_path)
    assert result.errors == result.conflicts == []
    folder = Path(result.folders_written[0])
    assert folder.name == expected
    assert (folder / f"{expected}.adf").read_bytes() == b"original disk"
    assert title in (folder / f"{expected}.nfo").read_text()


@pytest.mark.parametrize("title,expected", [
    ('A<>:"/\\|?*B', 'A_________B'), ("Hot Rod. ", "Hot Rod"),
    ("CON", "_CON"), ("nul.txt", "_nul.txt"), ("LPT1", "_LPT1"),
    ("Hacker's + Friends!", "Hacker's + Friends!"), ("A\x01B", "A_B"),
])
def test_windows_components(title, expected):
    assert _sanitize_component(title) == expected


def test_collisions_are_case_insensitive_stable_and_preserve_disk_bytes(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    groups = []
    for i, title in enumerate(("Hacker", "hacker", "Hot:Rod", "Hot?Rod")):
        name = f"Input{i} (Disk 1 of 1).adf"
        (original / name).write_bytes(str(i).encode())
        group = group_records([parse_filename(name)])[0]
        group.title = title
        groups.append(group)
    observed = []
    for index, order in enumerate((groups, list(reversed(groups)))):
        result = export_all(order, staging_dir=tmp_path / "staging", run_id=str(index),
                            upstream_task_closed=True, verified_artwork_width=150,
                            verified_artwork_height=150, original_dir=original)
        assert result.errors == result.conflicts == []
        assert result.releases_exported == 4
        mapping = {}
        for folder in map(Path, result.folders_written):
            disk = next(folder.glob("*.adf"))
            mapping[disk.read_bytes()] = folder.name
        assert len({name.casefold() for name in mapping.values()}) == 4
        assert all("[" in name for name in mapping.values())
        observed.append(mapping)
    assert observed[0] == observed[1]


@pytest.mark.parametrize("kind,route", [("hints", "doc"), ("solution", "doc"), ("cheat", "cheat")])
def test_lemon_html_to_persisted_association_to_physical_rtfm(tmp_path, monkeypatch, kind, route):
    root, manuals, cfg, run = library(tmp_path, ("Hacker",))
    game_url = "https://www.lemonamiga.com/game/hacker"
    doc_url = f"https://www.lemonamiga.com/{route}/hacker/763"
    marker = f"Fixture {kind}: enter the terminal code BLUEBIRD to unlock the door."
    pages = {
        game_url: f'<html><h1>Hacker</h1><div class="docs"><a href="/{route}/hacker/763">{kind.title()}</a></div></html>',
        doc_url: f'<html><code>{marker}</code></html>',
    }
    requests = []
    def response(url, **kwargs):
        requests.append(url)
        assert url in pages, f"unexpected provider request {url}"
        return pages[url], url
    monkeypatch.setattr(metadata, "_text_get", response)
    # Isolate unrelated metadata providers; the document acquisition seam stays real.
    monkeypatch.setattr(enrich, "lookup_metadata", lambda *a, **k: (None, "fixture", []))
    result = run_pipeline(cfg, replace(run, online=True))
    assert requests == [game_url, doc_url]
    row = trace(result)["Hacker"]
    assert row["written"] is True, row["no_rtfm_reason"]
    assert row["doc_types"] == [kind]
    assert row["sources_count"] == 1
    assert row["provider_id_preserved"] is True
    assert marker in Path(row["rtfm_path"]).read_text()
    assert marker in Path(row["export_path"]).read_text()
    with CanonicalLibrary(root / "curation" / "canonical.db") as canon:
        claims = get_document_associations(canon, "game", "hacker")
        assert len(claims) == 1
        prov, content = claims[0]
        assert marker in content
        assert (prov.source, prov.record_key, prov.url) == ("lemon-amiga", kind, doc_url)
        sources = document_to_rtfm_sources("game", "hacker", canon, game_title="Hacker")
        assert len(sources) == 1
        assert marker in sources[0].content
    provenance = json.loads(Path(row["rtfm_path"] + ".provenance.json").read_text())
    assert provenance["sources"][0]["source_url"] == doc_url
    # Delete generated assets to prove restart rebuilds from persisted content,
    # rather than passing because a prior output happened to remain on disk.
    Path(row["rtfm_path"]).unlink()
    monkeypatch.setattr(metadata, "_text_get", lambda *a, **k: pytest.fail("offline acquisition"))
    restarted = trace(run_pipeline(cfg, replace(run, run_id="restart")))["Hacker"]
    assert restarted["written"] is True
    assert marker in Path(restarted["rtfm_path"]).read_text()
    assert marker in Path(restarted["export_path"]).read_text()


def test_pipeline_collision_names_keep_rtfm_preview_and_export_coherent(tmp_path):
    root, manuals, cfg, run = library(tmp_path, ("Hacker [cr AAA]", "Hacker [cr BBB]"))
    (manuals / "Hacker.txt").write_text("Use the joystick to select the terminal.")
    result = run_pipeline(cfg, run)
    rows = result["rtfm"]["manual_trace"]["per_release"]
    assert len(rows) == 2
    assert len({row["rtfm_path"] for row in rows}) == 2
    preview = {row["release_key"]: row for row in result["per_group"]}
    for row in rows:
        assert row["written"] is True, row["no_rtfm_reason"]
        assert row["export_status"] == "exported"
        assert Path(row["rtfm_path"]).read_bytes() == Path(row["export_path"]).read_bytes()
        assert Path(row["export_path"]).parent.name == preview[row["release_key"]]["folder"]
        assert Path(row["export_path"]).parent.name.startswith("Hacker [")


def test_failed_current_build_does_not_export_stale_rtfm(tmp_path):
    root, manuals, cfg, run = library(tmp_path, ("Hacker",))
    manual = manuals / "Hacker.txt"
    manual.write_text("Use the joystick to select the terminal.")
    first = trace(run_pipeline(cfg, run))["Hacker"]
    assert first["written"] is True
    manual.unlink()
    second = trace(run_pipeline(cfg, replace(run, run_id="without-manual")))["Hacker"]
    assert second["written"] is False
    assert second["export_path"] is None
    assert second["export_status"] == "not-exported"
    assert not list((cfg.staging_dir / "without-manual").rglob("*.rtfm"))


def test_provider_display_title_reaches_preview_nfo_and_export(tmp_path, monkeypatch):
    root, manuals, cfg, run = library(tmp_path, ("hacker",))
    monkeypatch.setattr(enrich, "lookup_metadata", lambda *a, **k: (
        metadata.MetadataRecord(canonical_title="Hacker"), "fixture", []))
    result = run_pipeline(cfg, replace(run, online=True, include_manuals_rtfm=False))
    assert len(result["per_group"]) == 1
    row = result["per_group"][0]
    assert row["title"] == row["folder"] == "Hacker"
    folder = cfg.staging_dir / run.run_id / "ADF" / "Hacker"
    assert folder.is_dir()
    assert (folder / "Hacker.nfo").read_text().startswith("Title: Hacker\n")
