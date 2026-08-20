"""Independent Issue #15 acceptance: GUI and CLI drive the SAME core pipeline.

This is the acceptance gate's "equivalent GUI and CLI operations produce
equivalent application results" criterion. It does NOT merely compare kwargs
dicts (that is covered by ``test_gui_equivalence.py``); it actually *executes*
``pipeline.run_pipeline`` twice -- once via a constructed :class:`GuiState`
(``build_path_config_from_gui_state`` + ``build_pipeline_kwargs``, exactly what
``PipelineWorker`` uses) and once via the kwargs the CLI's ``build``/``export``
handlers pass -- and asserts the produced application artifacts are
byte-/semantically-equivalent.

The fixture library is synthetic and lives under ``tests/fixtures``; no private
corpus paths are used. Everything runs offline (no network, no providers).

Identity of the two runs is achieved by pointing both library roots at the SAME
read-only ``original/`` corpus, so the *inputs* to the core are identical; the
only difference is which library root the derived output/working dirs live
under. Outputs are compared deterministically (timestamps/run-ids stripped).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amiga_adf_library_builder.gui.state import (
    GuiState,
    build_path_config_from_gui_state,
    build_pipeline_kwargs,
)
from amiga_adf_library_builder.initializer import ensure_managed_directories
from amiga_adf_library_builder import cli, pipeline

_SHARED_ORIGINAL = (
    Path(__file__).resolve().parent / "fixtures" / "issue15-lib"
)


def _cli_build_kwargs(library_root: str, original_dir: str) -> dict:
    """Replicate exactly what ``cli._run_build`` passes to ``run_pipeline``."""
    parser = cli.build_parser()
    args = parser.parse_args(
        ["build", "--library-root", library_root, "--original-dir", original_dir]
    )
    cfg = cli._resolve_cfg(args)
    return {
        "cfg": cfg,
        "online": bool(args.online),
        "refresh_metadata": bool(args.refresh_metadata),
        "upstream_task_closed": bool(args.export_gate_acknowledged),
        "local_media_config_path": getattr(args, "config", None),
        "rtfm_config_path": getattr(args, "config", None),
        "playmatch_config_path": getattr(args, "playmatch_config", None)
        or getattr(args, "config", None),
        "hasheous_config_path": getattr(args, "hasheous_config", None)
        or getattr(args, "config", None),
    }


def _cli_export_kwargs(
    library_root: str, original_dir: str, *, verify_only: bool
) -> dict:
    """Replicate exactly what ``cli._run_export`` passes to ``run_pipeline``."""
    import argparse as _argparse

    parser = cli.build_parser()
    argv = [
        "export",
        "--library-root",
        library_root,
        "--original-dir",
        original_dir,
        "--export-gate-acknowledged",
    ]
    if verify_only:
        argv.append("--verify-only")
    args = parser.parse_args(argv)
    cfg = cli._resolve_cfg(args)
    return {
        "cfg": cfg,
        "online": bool(args.online),
        "refresh_metadata": bool(args.refresh_metadata),
        "require_artwork": bool(args.require_artwork),
        "upstream_task_closed": bool(args.export_gate_acknowledged),
        "export": True,
        "verify_only": bool(args.verify_only),
        "run_id": args.run_id,
        "verified_artwork_width": pipeline.VERIFIED_ARTWORK_WIDTH,
        "verified_artwork_height": pipeline.VERIFIED_ARTWORK_HEIGHT,
        "local_media_config_path": getattr(args, "config", None),
        "rtfm_config_path": getattr(args, "config", None),
        "playmatch_config_path": getattr(args, "playmatch_config", None)
        or getattr(args, "config", None),
        "hasheous_config_path": getattr(args, "hasheous_config", None)
        or getattr(args, "config", None),
    }


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _groups_semantic(path: Path) -> set[tuple]:
    """Release-group identity set, ignoring run_id + written_at timestamps."""
    rows = _read_jsonl(path)
    norm = set()
    for r in rows:
        key = r.get("release_key")
        title = r.get("title")
        norm.add((key, title))
    return norm


def _nfo_tree(nfo_dir: Path) -> dict[str, str]:
    """Map '<basename>.nfo' -> text content (deterministic Gotek-facing output)."""
    out: dict[str, str] = {}
    if not nfo_dir.is_dir():
        return out
    for p in sorted(nfo_dir.iterdir()):
        if p.suffix == ".nfo":
            out[p.name] = p.read_text(encoding="utf-8")
    return out


def test_equiv_build_produces_identical_artifacts(tmp_path: Path):
    """GUI build path and CLI build path yield identical deterministic output."""
    assert _SHARED_ORIGINAL.is_dir(), "fixture missing: run the QA fixture setup"

    gui_root = tmp_path / "lib_gui"
    cli_root = tmp_path / "lib_cli"
    gui_root.mkdir()
    cli_root.mkdir()
    original = str(_SHARED_ORIGINAL)

    # --- GUI path (exactly what PipelineWorker._run does) --------------------
    gui_state = GuiState(
        library_root=str(gui_root),
        original_dir=original,
        run_mode="build",
    )
    gui_cfg = build_path_config_from_gui_state(gui_state)
    gui_kwargs = build_pipeline_kwargs(gui_state, gui_cfg)
    ensure_managed_directories(gui_cfg)
    gui_result = pipeline.run_pipeline(**gui_kwargs)

    # --- CLI path (exactly what cli._run_build does) --------------------------
    cli_kwargs = _cli_build_kwargs(str(cli_root), original)
    ensure_managed_directories(cli_kwargs["cfg"])
    cli_result = pipeline.run_pipeline(**cli_kwargs)

    # 1) The pipeline invocation inputs are equivalent. The GUI's
    #    build_pipeline_kwargs always sets export/verify_only/require_artwork
    #    (to their run_pipeline defaults: False) plus include_artwork /
    #    include_manuals_rtfm (to their run_pipeline defaults: True, GH-24) and
    #    verified_artwork_width/height (CLI-equivalent defaults for the exporter
    #    gate; harmless on build); the CLI build handler omits all seven and
    #    relies on those same defaults. Both resolve to the identical effective
    #    call. Prove the GUI adds exactly those seven default-only keys and that
    #    every CLI-passed key matches.
    gui_only_keys = set(gui_kwargs) - set(cli_kwargs)
    assert gui_only_keys == {
        "export", "verify_only", "require_artwork",
        "include_artwork", "include_manuals_rtfm",
        "verified_artwork_width", "verified_artwork_height",
    }
    assert gui_kwargs["export"] is False
    assert gui_kwargs["verify_only"] is False
    assert gui_kwargs["require_artwork"] is False
    assert gui_kwargs["include_artwork"] is True
    assert gui_kwargs["include_manuals_rtfm"] is True
    for k in cli_kwargs:
        if k == "cfg":
            # The PathConfig legitimately differs by library root; only the
            # original/ corpus (the shared read-only input) must be identical.
            assert gui_kwargs["cfg"].original_dir == cli_kwargs["cfg"].original_dir
            continue
        assert gui_kwargs[k] == cli_kwargs[k], f"pipeline kwarg {k} differs"

    # 2) Deterministic catalog outputs match byte-for-byte *after stripping
    #    the run-wall-clock timestamp* (the only field that legitimately
    #    differs between two runs). Filenames / sha256 / paths / sizes are
    #    identical -- that is the real equivalence signal.
    def _strip_scanned(jsonl_text: str) -> str:
        import json as _json

        rows = [ _json.loads(ln) for ln in jsonl_text.splitlines() if ln.strip() ]
        for r in rows:
            r.pop("scanned_at", None)
        return _json.dumps(rows, sort_keys=True, indent=2)

    gui_scan = _strip_scanned((gui_cfg.catalog_dir / "scan.jsonl").read_text(encoding="utf-8"))
    cli_scan = _strip_scanned((cli_kwargs["cfg"].catalog_dir / "scan.jsonl").read_text(encoding="utf-8"))
    assert gui_scan == cli_scan, "scan.jsonl differs between GUI and CLI paths"

    gui_parse = (gui_cfg.catalog_dir / "parse.jsonl").read_text(encoding="utf-8")
    cli_parse = (cli_kwargs["cfg"].catalog_dir / "parse.jsonl").read_text(encoding="utf-8")
    assert gui_parse == cli_parse, "parse.jsonl differs between GUI and CLI paths"

    # 3) Grouping identity (release keys/titles) matches, ignoring run_id/time.
    assert _groups_semantic(gui_cfg.catalog_dir / "groups.jsonl") == _groups_semantic(
        cli_kwargs["cfg"].catalog_dir / "groups.jsonl"
    )

    # 4) The Gotek-facing NFO files are byte-identical.
    assert _nfo_tree(gui_cfg.nfo_dir) == _nfo_tree(cli_kwargs["cfg"].nfo_dir)

    # 5) Top-level result semantics agree.
    for k in (
        "files_scanned",
        "records_parsed",
        "groups",
        "original_preserved",
        "review_routed",
        "unknown_routed",
    ):
        assert gui_result[k] == cli_result[k], f"result[{k}] differs"
    assert gui_result["files_scanned"] == 3
    assert gui_result["groups"] == 2
    assert gui_result["original_preserved"] is True


def test_equiv_export_produces_identical_staging(tmp_path: Path):
    """GUI export path and CLI export path yield identical staging content."""
    assert _SHARED_ORIGINAL.is_dir(), "fixture missing: run the QA fixture setup"

    gui_root = tmp_path / "lib_gui"
    cli_root = tmp_path / "lib_cli"
    gui_root.mkdir()
    cli_root.mkdir()
    original = str(_SHARED_ORIGINAL)

    # --- GUI path -------------------------------------------------------------
    gui_state = GuiState(
        library_root=str(gui_root),
        original_dir=original,
        run_mode="export",
        export_gate_acknowledged=True,
    )
    gui_cfg = build_path_config_from_gui_state(gui_state)
    gui_kwargs = build_pipeline_kwargs(gui_state, gui_cfg)
    ensure_managed_directories(gui_cfg)
    gui_result = pipeline.run_pipeline(**gui_kwargs)

    # --- CLI path -------------------------------------------------------------
    cli_kwargs = _cli_export_kwargs(str(cli_root), original, verify_only=False)
    ensure_managed_directories(cli_kwargs["cfg"])
    cli_result = pipeline.run_pipeline(**cli_kwargs)

    # The gate must be OPEN (operator-acknowledged) and the export must run.
    assert gui_result["export_gate_open"] is True
    assert cli_result["export_gate_open"] is True
    assert "export" in gui_result and "export" in cli_result

    # releases_exported is a count (not path-stamped).
    g_exp = gui_result["export"]
    c_exp = cli_result["export"]
    for k in ("releases_exported",):
        assert g_exp[k] == c_exp[k], f"export[{k}] differs"
    assert g_exp["releases_exported"] == 2

    # The folders_written / files_written lists hold absolute run-id-stamped
    # paths; compare their basenames (the actual application result, not the
    # temp root).
    gui_folders = sorted(Path(p).name for p in g_exp["folders_written"])
    cli_folders = sorted(Path(p).name for p in c_exp["folders_written"])
    assert gui_folders == cli_folders, "exported folder names differ"
    gui_files = sorted(Path(p).name for p in g_exp["files_written"])
    cli_files = sorted(Path(p).name for p in c_exp["files_written"])
    assert gui_files == cli_files, "exported file names differ"

    # Compare the staging trees RELATIVE to their run-owned roots (ignore
    # the run-id in the path; compare folder names + file contents).
    gui_stage = gui_result["export"]["staging_root"]
    cli_stage = cli_result["export"]["staging_root"]

    def _rel_tree(root: str) -> dict[str, str]:
        base = Path(root)
        out: dict[str, str] = {}
        for p in sorted(base.rglob("*")):
            if p.is_file():
                rel = p.relative_to(base).as_posix()
                out[rel] = p.read_bytes().decode("utf-8", errors="replace")
        return out

    gui_tree = _rel_tree(gui_stage)
    cli_tree = _rel_tree(cli_stage)
    # Folder names are the release basenames (run-id-independent); file names
    # under them are deterministic. Normalise by dropping the top-level run-id
    # directory segment so the comparison is structural, not path-stamped.
    gui_norm = {_strip_run_id(k): v for k, v in gui_tree.items()}
    cli_norm = {_strip_run_id(k): v for k, v in cli_tree.items()}
    assert gui_norm == cli_norm, "staging content differs between GUI and CLI export"

    # The folders_written list holds absolute run-id-stamped paths; compare
    # their basenames (the actual application result, not the temp root).
    gui_folders = sorted(Path(p).name for p in g_exp["folders_written"])
    cli_folders = sorted(Path(p).name for p in c_exp["folders_written"])
    assert gui_folders == cli_folders, "exported folder names differ"


def _strip_run_id(rel_path: str) -> str:
    """Drop the leading run-id directory segment from a staging-relative path.

    Staging layout is ``<run_id>/<release_basename>/...``. The run_id is
    generated per-process and is not part of the application result.
    """
    parts = rel_path.split("/")
    if len(parts) > 1:
        return "/".join(parts[1:])
    return rel_path


def test_gui_app_imports_and_constructs_offscreen(tmp_path: Path):
    """The PyInstaller hook target imports and the GUI constructs headless.

    Linux-side smoke gate (the real windowed launch is qualified on
    windows-latest). Mirrors the build-time sanity gate in
    ``.github/workflows/build-windows.yml``.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from amiga_adf_library_builder.gui.app import GuiApp

    app = GuiApp(base_dir=str(tmp_path / "app-base"))
    try:
        assert app.window() is not None
    finally:
        from PySide6.QtWidgets import QApplication

        if QApplication.instance():
            QApplication.instance().quit()
