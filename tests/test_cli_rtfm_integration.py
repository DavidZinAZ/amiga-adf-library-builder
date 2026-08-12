"""End-to-end CLI integration test: the `build`/`export` handlers must forward
the resolved config path to ``pipeline.run_pipeline(rtfm_config_path=...)`` so an
enabled ``[rtfm]`` table actually builds ``.rtfm`` sidecars.

This is the regression guard for the CLI wiring defect where both handlers
passed ``local_media_config_path`` but never ``rtfm_config_path``, leaving the
``run_pipeline`` RTFM branch dead when invoked from the CLI.

Fully synthetic: NO maintainer-private corpus, names, hashes, or machine state.
All game titles/contents are synthetic (e.g. "Synthetic Quest III"). Config paths
are derived from the pytest ``tmp_path`` fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amiga_adf_library_builder.cli import main


def _write_config(tmp_path: Path) -> tuple[Path, Path]:
    """Create a synthetic library_root + an enabled [rtfm] config.

    Returns (config_toml_path, library_root). The [rtfm] table points its
    instructions discovery root at a synthetic source dir containing a manual
    for "Synthetic Quest III". No real corpus / host paths are used.
    """
    library_root = tmp_path / "library"
    original_dir = library_root / "original"
    original_dir.mkdir(parents=True)

    # Synthetic single-disk release -> one release group.
    (original_dir / "Synthetic Quest III (Disk 1 of 1).adf").write_bytes(b"\x00" * 16)

    instructions_root = tmp_path / "instructions"
    instructions_root.mkdir()
    (instructions_root / "Synthetic Quest III.txt").write_bytes(
        b"Fire: button 1\nMove with joypad\n"
    )

    config_toml = tmp_path / "rtfm.toml"
    config_toml.write_text(
        'library_root = "{lr}"\n'
        "\n"
        "[rtfm]\n"
        "enabled = true\n"
        'template = "controls-first"\n'
        "\n"
        "[rtfm.local]\n"
        'instructions = "{ins}"\n'
        "\n".format(lr=library_root, ins=instructions_root)
    )
    return config_toml, library_root


def test_cli_build_enables_rtfm_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    config_toml, library_root = _write_config(tmp_path)

    rc = main(["build", "--config", str(config_toml), "--json"])
    assert rc == 0

    result = json.loads(capsys.readouterr().out)
    # The CLI must have forwarded the config path as rtfm_config_path.
    assert result["rtfm"]["configured"] is True
    assert len(result["rtfm"]["built"]) >= 1

    rtfm_path = Path(result["rtfm"]["built"][0])
    # The .rtfm artifact must exist on disk under assets/rtfm.
    assert rtfm_path.is_file()
    assert rtfm_path.parent.name == "rtfm"
    assert rtfm_path.parent.parent.name == "assets"
    assert rtfm_path.parent.parent.parent == library_root.resolve()


def test_cli_export_enables_rtfm_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    config_toml, library_root = _write_config(tmp_path)

    # Gate is acknowledged; RTFM building is independent of the export gate, so
    # the .rtfm artifact must be produced regardless of export-stage outcome.
    rc = main(
        [
            "export",
            "--config",
            str(config_toml),
            "--export-gate-acknowledged",
            "--json",
        ]
    )
    # 0 = clean export, 4 = export conflicts/errors (still a valid run result).
    assert rc in (0, 4)

    result = json.loads(capsys.readouterr().out)
    assert result["rtfm"]["configured"] is True
    assert len(result["rtfm"]["built"]) >= 1

    rtfm_path = Path(result["rtfm"]["built"][0])
    assert rtfm_path.is_file()


def test_cli_without_rtfm_table_builds_nothing(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Guard against regression in the opposite direction: a config without an
    enabled [rtfm] table must NOT build any .rtfm (behavior unchanged)."""
    library_root = tmp_path / "library"
    original_dir = library_root / "original"
    original_dir.mkdir(parents=True)
    (original_dir / "Synthetic Quest III (Disk 1 of 1).adf").write_bytes(b"\x00" * 16)

    config_toml = tmp_path / "plain.toml"
    config_toml.write_text('library_root = "{lr}"\n'.format(lr=library_root))

    rc = main(["build", "--config", str(config_toml), "--json"])
    assert rc == 0

    result = json.loads(capsys.readouterr().out)
    # rtfm_config_path is forwarded (configured True), but with no [rtfm] table
    # the config is disabled so no .rtfm is built. This guards that the wiring
    # change does NOT cause spurious .rtfm generation when RTFM is not enabled.
    assert result["rtfm"]["configured"] is True
    assert result["rtfm"]["built"] == []
