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


# ---------------------------------------------------------------------------
# (GH-76) GUI RTFM-config discovery fallback tests.
#
# When the packaged Windows GUI is launched by double-clicking, the operator
# has not selected a provider-config path, so ``state.provider_config_path``
# is empty and ``config_path`` is ``None``. The fix in ``build_pipeline_kwargs``
# makes the GUI fall back to the standard config-discovery chain so an enabled
# ``[rtfm]`` config is actually used — producing the same RTFM output as the
# CLI ``--config`` path.
# ---------------------------------------------------------------------------


def _write_rtfm_config(tmp_path: Path, library_root: str) -> Path:
    """Write a minimal enabled-[rtfm] config TOML and return its path."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'library_root = "{library_root}"\n'
        "\n"
        "[rtfm]\n"
        "enabled = true\n"
        'template = "controls-first"\n'
        "\n"
        "[rtfm.local]\n"
        'instructions = "{ins}"\n'.format(
            library_root=library_root,
            ins=str(tmp_path / "instructions"),
        )
    )
    return cfg


def test_gui_rtfm_config_discovery_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """GUI with no explicit provider_config_path still discovers a default
    config file and forwards it as ``rtfm_config_path`` (GH-76 fix)."""
    root = tmp_path / "lib"
    root.mkdir()

    # Place the config file where _discover_config_file (XDG path) will find it.
    xdg_base = tmp_path / "xdg_config"
    xdg_base.mkdir()
    cfg_file = xdg_base / "amiga-adf-library-builder" / "config.toml"
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text(f'library_root = "{root}"\n')

    # Point XDG_CONFIG_HOME at our temp dir so discovery finds our config.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_base))

    # No provider_config_path, no explicit config_path: the packaged-Windows
    # double-click scenario.
    state = GuiState(library_root=str(root))
    cfg = build_path_config_from_gui_state(state)
    kwargs = build_pipeline_kwargs(state, cfg)

    assert kwargs["rtfm_config_path"] is not None, (
        "GH-76: GUI must discover a default config when none is explicitly set"
    )
    assert kwargs["rtfm_config_path"] == str(cfg_file.resolve())
    # The provider config paths that use ``provider_cfg`` directly must match
    # the discovered config (playmatch/hasheous/retrokit use ``provider_cfg``;
    # local_media uses a separate resolver and is verified elsewhere).
    assert kwargs["playmatch_config_path"] == kwargs["rtfm_config_path"]
    assert kwargs["hasheous_config_path"] == kwargs["rtfm_config_path"]


def test_gui_rtfm_config_no_config_stays_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """With no discoverable config file, ``rtfm_config_path`` stays ``None``
    — existing skip semantics preserved (no spurious RTFM build)."""
    root = tmp_path / "lib"
    root.mkdir()

    # Point XDG at an empty dir so discovery finds nothing; env unset too.
    empty_xdg = tmp_path / "empty_xdg"
    empty_xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(empty_xdg))
    monkeypatch.delenv("AMIGA_ADF_CONFIG", raising=False)

    state = GuiState(library_root=str(root))
    cfg = build_path_config_from_gui_state(state)
    kwargs = build_pipeline_kwargs(state, cfg)

    assert kwargs["rtfm_config_path"] is None
    assert kwargs["playmatch_config_path"] is None
    assert kwargs["hasheous_config_path"] is None


def test_gui_rtfm_config_explicit_overrides_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An explicit ``provider_config_path`` must take precedence over the
    discovered default (GH-76: operator selection wins)."""
    root = tmp_path / "lib"
    root.mkdir()

    explicit_cfg = tmp_path / "explicit.toml"
    explicit_cfg.write_text(f'library_root = "{root}"\n')

    # Also seed a discoverable config to prove discovery is NOT used when
    # the operator has explicitly chosen one.
    xdg_base = tmp_path / "xdg_config"
    xdg_base.mkdir()
    discovered_cfg = xdg_base / "amiga-adf-library-builder" / "config.toml"
    discovered_cfg.parent.mkdir(parents=True)
    discovered_cfg.write_text(f'library_root = "{root}"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_base))

    state = GuiState(library_root=str(root), provider_config_path=str(explicit_cfg))
    cfg = build_path_config_from_gui_state(state)
    kwargs = build_pipeline_kwargs(state, cfg)

    assert kwargs["rtfm_config_path"] == str(explicit_cfg)
