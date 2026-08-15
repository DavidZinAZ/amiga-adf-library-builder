"""build_path_config_from_gui_state equivalence tests (Issue #15).

These prove the GUI builds the SAME :class:`PathConfig` and the SAME
``run_pipeline`` keyword arguments as the CLI would from the analogous flags:

  --library-root            library_root
  --original-dir           original_dir
  --staging-dir            staging_dir
  --output-dir             output_dir
  --online                 online
  --refresh-metadata       refresh_metadata
  --require-artwork        require_artwork
  --verify-only            verify_only
  --export-gate-acknowledged  upstream_task_closed

The GUI does NOT reimplement the pipeline; same inputs must yield identical
core behavior as the CLI. We verify the path resolution matches ``resolve_config``
directly and the pipeline kwargs match what ``cli.py`` passes for ``build``/``export``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amiga_adf_library_builder.gui.state import (
    GuiState,
    build_path_config_from_gui_state,
    build_pipeline_kwargs,
)
from amiga_adf_library_builder.paths import resolve_config


def _write_config(tmp_path: Path, **kw) -> str:
    from amiga_adf_library_builder.paths import write_config_file

    p = tmp_path / "config.toml"
    write_config_file(p, **kw)
    return str(p)


def test_library_root_required_raises():
    with pytest.raises(Exception):
        build_path_config_from_gui_state(GuiState())


def test_path_config_matches_resolve_config(tmp_path: Path):
    root = tmp_path / "lib"
    root.mkdir()
    state = GuiState(
        library_root=str(root),
        original_dir=str(root / "original"),
        staging_dir=str(root / "work" / "staging"),
        output_dir=str(root / "output"),
    )
    cfg = build_path_config_from_gui_state(state)

    # Independent reference: the same inputs to resolve_config (the CLI path).
    ref, _ = resolve_config(
        library_root=str(root),
        original_dir=str(root / "original"),
        staging_dir=str(root / "work" / "staging"),
        output_dir=str(root / "output"),
    )
    assert cfg.library_root == ref.library_root
    assert cfg.original_dir == ref.original_dir
    assert cfg.staging_dir == ref.staging_dir
    assert cfg.output_dir == ref.output_dir


def test_derived_paths_when_not_set(tmp_path: Path):
    root = tmp_path / "lib"
    root.mkdir()
    cfg = build_path_config_from_gui_state(GuiState(library_root=str(root)))
    # Mirrors the CLI default derivation under the library root.
    assert cfg.original_dir == (root / "original").resolve()
    assert cfg.staging_dir == (root / "work" / "staging").resolve()
    assert cfg.output_dir == (root / "output").resolve()


@pytest.mark.parametrize(
    "flag,kwarg,value",
    [
        ("online", "online", True),
        ("refresh_metadata", "refresh_metadata", True),
        ("require_artwork", "require_artwork", True),
        ("verify_only", "verify_only", True),
        ("export_gate_acknowledged", "upstream_task_closed", True),
    ],
)
def test_pipeline_kwargs_flag_mapping(tmp_path: Path, flag, kwarg, value):
    root = tmp_path / "lib"
    root.mkdir()
    state = GuiState(library_root=str(root))
    setattr(state, flag, value)
    cfg = build_path_config_from_gui_state(state)
    kwargs = build_pipeline_kwargs(state, cfg)
    assert kwargs[kwarg] is value


def test_pipeline_kwargs_export_mode(tmp_path: Path):
    root = tmp_path / "lib"
    root.mkdir()
    state = GuiState(library_root=str(root), run_mode="export")
    cfg = build_path_config_from_gui_state(state)
    kwargs = build_pipeline_kwargs(state, cfg)
    assert kwargs["export"] is True

    state2 = GuiState(library_root=str(root), run_mode="build")
    cfg2 = build_path_config_from_gui_state(state2)
    kwargs2 = build_pipeline_kwargs(state2, cfg2)
    assert kwargs2["export"] is False


def test_pipeline_kwargs_provider_config_path(tmp_path: Path):
    root = tmp_path / "lib"
    root.mkdir()
    cfg_file = _write_config(tmp_path, library_root=str(root))
    state = GuiState(library_root=str(root), provider_config_path=cfg_file)
    cfg = build_path_config_from_gui_state(state)
    kwargs = build_pipeline_kwargs(state, cfg)
    # The GUI passes the provider config file to every optional provider, exactly
    # like the CLI passes ``--config`` to playmatch/hasheous/rtfm/local_media.
    assert kwargs["playmatch_config_path"] == cfg_file
    assert kwargs["hasheous_config_path"] == cfg_file
    assert kwargs["rtfm_config_path"] == cfg_file
    assert kwargs["local_media_config_path"] == cfg_file


def test_gui_vs_cli_build_invocation_match(tmp_path: Path):
    """The GUI kwargs must equal the kwargs ``cli.py`` passes to run_pipeline for ``build``."""
    import argparse

    from amiga_adf_library_builder import cli

    root = tmp_path / "lib"
    root.mkdir()
    cfg_file = _write_config(tmp_path, library_root=str(root))

    # Simulate the CLI 'build' command with the accepted flags.
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "build",
            "--config", cfg_file,
            "--online",
            "--refresh-metadata",
            "--export-gate-acknowledged",
        ]
    )
    ref_cfg = cli._resolve_cfg(args)
    # Reconstruct the kwargs the CLI would pass (mirrors cli._run_build).
    cli_kwargs = dict(
        cfg=ref_cfg,
        online=bool(args.online),
        refresh_metadata=bool(args.refresh_metadata),
        require_artwork=False,
        upstream_task_closed=bool(args.export_gate_acknowledged),
        export=False,
        verify_only=False,
        local_media_config_path=getattr(args, "config", None),
        rtfm_config_path=getattr(args, "config", None),
        playmatch_config_path=getattr(args, "playmatch_config", None) or getattr(args, "config", None),
        hasheous_config_path=getattr(args, "hasheous_config", None) or getattr(args, "config", None),
    )

    # Equivalent GUI state.
    state = GuiState(
        library_root=str(root),
        online=True,
        refresh_metadata=True,
        export_gate_acknowledged=True,
        run_mode="build",
        provider_config_path=cfg_file,
    )
    gui_cfg = build_path_config_from_gui_state(state, config_path=cfg_file)
    gui_kwargs = build_pipeline_kwargs(state, gui_cfg, config_path=cfg_file)

    # Compare the meaningful fields; config_path routing must match.
    assert gui_kwargs["online"] == cli_kwargs["online"]
    assert gui_kwargs["refresh_metadata"] == cli_kwargs["refresh_metadata"]
    assert gui_kwargs["upstream_task_closed"] == cli_kwargs["upstream_task_closed"]
    assert gui_kwargs["export"] == cli_kwargs["export"]
    assert gui_kwargs["playmatch_config_path"] == cli_kwargs["playmatch_config_path"]
    assert gui_kwargs["hasheous_config_path"] == cli_kwargs["hasheous_config_path"]
    assert gui_kwargs["cfg"].library_root == cli_kwargs["cfg"].library_root
