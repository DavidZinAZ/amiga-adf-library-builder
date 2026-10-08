"""End-to-end regression: a local manual-root file must reach the exported sidecar.

Proves the FULL path with real pipeline + exporter code (no mocks of the
matching logic):

    manual_roots discovery -> local-media candidate index -> release match
    -> provider-cached manual -> RTFM builder receives matched manual
    -> ``<basename>.rtfm`` written under assets/rtfm
    -> exported staging folder carries the ``.rtfm`` sidecar.

Synthetic library, fully offline.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

from amiga_adf_library_builder.cli import main


def _run_build(config_toml: Path) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["build", "--config", str(config_toml), "--json"])
    assert rc == 0, f"build failed rc={rc}"
    return json.loads(buf.getvalue())


def test_manual_root_file_reaches_rtfm_sidecar(tmp_path: Path) -> None:
    """TXT manual from manual_roots -> matched -> .rtfm sidecar built."""
    library_root = tmp_path / "library"
    original_dir = library_root / "original"
    original_dir.mkdir(parents=True)
    (original_dir / "Ogre (Disk 1 of 1).adf").write_bytes(b"\x00" * 16)

    manuals_root = tmp_path / "manuals"
    manuals_root.mkdir()
    (manuals_root / "Ogre.txt").write_text(
        "GETTING STARTED\n\nInsert the Ogre disk and boot the machine.\n"
    )

    config_toml = tmp_path / "config.toml"
    config_toml.write_text(
        'library_root = "{lr}"\n'
        "\n"
        "[rtfm]\n"
        "enabled = true\n"
        "\n"
        "[rtfm.local]\n"
        'manuals = "{mr}"\n'
        "\n"
        "[local_media]\n"
        "enabled = true\n"
        "\n"
        "[[local_media.manual_roots]]\n"
        'path = "{mr}"\n'
        "\n".format(lr=library_root.resolve(), mr=manuals_root.resolve())
    )

    result = _run_build(config_toml)
    assert result["rtfm"]["configured"] is True
    assert len(result["rtfm"]["built"]) >= 1, "RTFM sidecar must be built"
    built_paths = [Path(path) for path in result["rtfm"]["built"]]
    provenance_paths = [
        Path(path) for path in result["rtfm"]["provenance_written"]
    ]
    actual_paths = sorted((library_root / "assets" / "rtfm").glob("*.rtfm"))
    assert len(built_paths) == len(actual_paths)
    assert all(path.is_file() for path in built_paths)
    assert len(provenance_paths) == 1
    assert actual_paths

    rtfm_path = built_paths[0]
    assert rtfm_path.is_file(), f"rtfm artifact missing: {rtfm_path}"
    text = rtfm_path.read_text(encoding="utf-8")
    assert "Insert the Ogre disk and boot the machine." in text, (
        "matched manual content must reach the .rtfm sidecar"
    )


def test_exported_staging_folder_carries_rtfm(tmp_path: Path) -> None:
    """The exported staging tree must carry the .rtfm sidecar next to .adf/.nfo."""
    library_root = tmp_path / "library"
    original_dir = library_root / "original"
    original_dir.mkdir(parents=True)
    (original_dir / "Ogre (Disk 1 of 1).adf").write_bytes(b"\x00" * 16)

    manuals_root = tmp_path / "manuals"
    manuals_root.mkdir()
    (manuals_root / "Ogre.txt").write_text(
        "GETTING STARTED\n\nInsert the Ogre disk and boot the machine.\n"
    )

    config_toml = tmp_path / "config.toml"
    config_toml.write_text(
        'library_root = "{lr}"\n'
        "\n"
        "[rtfm]\n"
        "enabled = true\n"
        "\n"
        "[rtfm.local]\n"
        'manuals = "{mr}"\n'
        "\n"
        "[local_media]\n"
        "enabled = true\n"
        "\n"
        "[[local_media.manual_roots]]\n"
        'path = "{mr}"\n'
        "\n".format(lr=library_root.resolve(), mr=manuals_root.resolve())
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main([
            "export", "--config", str(config_toml),
            "--export-gate-acknowledged", "--json",
        ])
    # 0 = clean export, 4 = export conflicts (still valid for this assertion).
    assert rc in (0, 4), f"export failed rc={rc}"
    staging = library_root / "work" / "staging"
    rtfm_files = list(staging.rglob("*.rtfm"))
    assert rtfm_files, "no .rtfm staged in the export tree"
    text = rtfm_files[0].read_text(encoding="utf-8")
    assert "Insert the Ogre disk and boot the machine." in text
