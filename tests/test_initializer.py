from pathlib import Path

from amiga_adf_library_builder.initializer import ensure_managed_directories
from amiga_adf_library_builder.paths import PathConfig, resolve_config


def _cfg(root: Path) -> PathConfig:
    return resolve_config(library_root=str(root))[0]


def test_initializer_creates_expected_directories(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg = _cfg(data_root)
    created = ensure_managed_directories(cfg)

    # original_dir is the operator-supplied read-only corpus and must NOT be
    # created by the bootstrap; the managed dirs under library_root are.
    assert (data_root / "original") == cfg.original_dir
    assert cfg.original_dir not in created
    assert (cfg.library_root / "assets" / "artwork-original").is_dir()
    assert (cfg.library_root / "assets" / "artwork-processed").is_dir()
    assert (cfg.library_root / "assets" / "nfo").is_dir()
    assert cfg.staging_dir.is_dir()


def test_initializer_is_idempotent(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg = _cfg(data_root)
    ensure_managed_directories(cfg)
    assert ensure_managed_directories(cfg) == []


def test_initializer_preserves_original_content(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg = _cfg(data_root)
    original = cfg.original_dir
    original.mkdir(parents=True)
    evidence = original / "messy source name.adf"
    evidence.write_bytes(b"do not change")

    ensure_managed_directories(cfg)

    assert evidence.read_bytes() == b"do not change"
