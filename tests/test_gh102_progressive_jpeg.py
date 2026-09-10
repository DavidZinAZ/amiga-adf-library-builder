"""Tests for GH-102 progressive-JPEG detection, conversion, and exporter wiring.

Verifies:
  * is_jpeg_progressive_from_bytes detects SOF2 markers (not extension).
  * progressive_to_baseline converts and reports action; baseline passes through.
  * exporter.export_release applies the policy correctly:
      - "always"  -> progressive source -> baseline output
      - "never"   -> progressive source -> progressive output (pass-through)
      - "baseline" source is never converted under any policy.
  * Per-image prompt callback is called ONLY for progressive sources.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

import pytest

from amiga_adf_library_builder import artwork
from amiga_adf_library_builder import exporter
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup


# ---------------------------------------------------------------------------
# Fixtures: synthetic JPEG streams with known SOF markers.
# ---------------------------------------------------------------------------


def _make_jpeg_bytes(*, progressive: bool, w: int = 16, h: int = 16, quality: int = 75) -> bytes:
    """Build a minimal valid JPEG with the requested SOF marker using PIL."""
    pytest.importorskip("PIL")
    from PIL import Image

    im = Image.new("RGB", (w, h), (0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, progressive=progressive)
    return buf.getvalue()


@pytest.fixture
def baseline_jpeg(tmp_path: Path) -> Path:
    p = tmp_path / "baseline.jpg"
    p.write_bytes(_make_jpeg_bytes(progressive=False))
    return p


@pytest.fixture
def progressive_jpeg(tmp_path: Path) -> Path:
    p = tmp_path / "progressive.jpg"
    p.write_bytes(_make_jpeg_bytes(progressive=True))
    return p


def _make_group(title: str = "Test Game", release_key: str = "test_game_1") -> ReleaseGroup:
    return ReleaseGroup(
        release_key=release_key,
        title=title,
        edition=None,
        group=None,
        chipset=None,
        language=None,
        version=None,
        alt_marker=None,
        ext="adf",
        records=[ParsedRecord(source_filename="Game.adf", ext="adf")],
        disks=[ParsedRecord(source_filename="Game.adf", ext="adf")],
        specials=[],
        has_main_disk=True,
        quarantine_reason=None,
    )


@pytest.fixture
def fake_group() -> ReleaseGroup:
    return _make_group()


def _artwork_dir_with(tmp_path: Path, src_jpeg: Path, name: str = "TestGame.jpg") -> tuple[Path, Path]:
    """Create artwork_original/ with a JPEG and original_dir/ with a Game.adf.

    Returns (artwork_original_dir, original_dir).
    """
    artwork_d = tmp_path / "artwork_original"
    artwork_d.mkdir()
    dest = artwork_d / name
    dest.write_bytes(src_jpeg.read_bytes())

    original_d = tmp_path / "original"
    original_d.mkdir()
    (original_d / "Game.adf").write_bytes(b"FAKE_ADF_CONTENT")
    return artwork_d, original_d


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


def test_is_jpeg_progressive_detects_progressive(progressive_jpeg: Path) -> None:
    assert artwork.is_jpeg_progressive_from_bytes(progressive_jpeg.read_bytes()) is True


def test_is_jpeg_progressive_detects_baseline(baseline_jpeg: Path) -> None:
    assert artwork.is_jpeg_progressive_from_bytes(baseline_jpeg.read_bytes()) is False


def test_is_jpeg_progressive_rejects_non_jpeg(tmp_path: Path) -> None:
    p = tmp_path / "not.jpg"
    p.write_bytes(b"not a jpeg at all")
    assert artwork.is_jpeg_progressive_from_bytes(p.read_bytes()) is False


def test_is_jpeg_progressive_empty_input() -> None:
    assert artwork.is_jpeg_progressive_from_bytes(b"") is False
    assert artwork.is_jpeg_progressive_from_bytes(b"\xff\xd8") is False


# ---------------------------------------------------------------------------
# Conversion tests
# ---------------------------------------------------------------------------


def test_progressive_to_baseline_converts(progressive_jpeg: Path) -> None:
    data, action = artwork.progressive_to_baseline(progressive_jpeg.read_bytes())
    assert action == "converted"
    assert artwork.is_jpeg_progressive_from_bytes(data) is False


def test_progressive_to_baseline_passthrough_baseline(baseline_jpeg: Path) -> None:
    data, action = artwork.progressive_to_baseline(baseline_jpeg.read_bytes())
    assert action == "baseline"
    assert data == baseline_jpeg.read_bytes()


def test_progressive_to_baseline_error_returns_original(tmp_path: Path) -> None:
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xc2" + b"\x00" * 20)
    data, action = artwork.progressive_to_baseline(bad.read_bytes(), on_error_return_original=True)
    assert action == "failed"
    assert data == bad.read_bytes()


# ---------------------------------------------------------------------------
# Exporter integration tests
# ---------------------------------------------------------------------------


def test_exporter_always_converts_progressive_to_baseline(
    tmp_path: Path, progressive_jpeg: Path, fake_group: ReleaseGroup
) -> None:
    artwork_original, original_dir = _artwork_dir_with(tmp_path, progressive_jpeg, "TestGame.jpg")
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    written, unchanged, conflicts = exporter.export_release(
        fake_group,
        staging_root,
        original_dir=original_dir,
        artwork_original_dir=artwork_original,
        convert_progressive_jpeg="always",
    )
    assert not conflicts
    jpgs = [n for n in written if n.endswith(".jpg")]
    assert len(jpgs) == 1, f"expected 1 jpg written, got {jpgs}"
    out_bytes = Path(jpgs[0]).read_bytes()
    assert artwork.is_jpeg_progressive_from_bytes(out_bytes) is False, (
        "'always' must produce baseline output from progressive source"
    )


def test_exporter_never_keeps_progressive(
    tmp_path: Path, progressive_jpeg: Path, fake_group: ReleaseGroup
) -> None:
    artwork_original, original_dir = _artwork_dir_with(tmp_path, progressive_jpeg, "TestGame.jpg")
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    written, _, conflicts = exporter.export_release(
        fake_group,
        staging_root,
        original_dir=original_dir,
        artwork_original_dir=artwork_original,
        convert_progressive_jpeg="never",
    )
    assert not conflicts
    jpgs = [n for n in written if n.endswith(".jpg")]
    assert len(jpgs) == 1
    out_bytes = Path(jpgs[0]).read_bytes()
    assert artwork.is_jpeg_progressive_from_bytes(out_bytes) is True, (
        "'never' must keep progressive source as progressive"
    )


def test_exporter_baseline_source_never_converted_under_always(
    tmp_path: Path, baseline_jpeg: Path, fake_group: ReleaseGroup
) -> None:
    artwork_original, original_dir = _artwork_dir_with(tmp_path, baseline_jpeg, "TestGame.jpg")
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    written, _, conflicts = exporter.export_release(
        fake_group,
        staging_root,
        original_dir=original_dir,
        artwork_original_dir=artwork_original,
        convert_progressive_jpeg="always",
    )
    assert not conflicts
    jpgs = [n for n in written if n.endswith(".jpg")]
    assert len(jpgs) == 1
    out_bytes = Path(jpgs[0]).read_bytes()
    assert artwork.is_jpeg_progressive_from_bytes(out_bytes) is False, (
        "baseline source must remain baseline regardless of policy"
    )


def test_exporter_prompt_callback_called_for_progressive(
    tmp_path: Path, progressive_jpeg: Path, fake_group: ReleaseGroup
) -> None:
    artwork_original, original_dir = _artwork_dir_with(tmp_path, progressive_jpeg, "TestGame.jpg")
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    calls: list[tuple[str, str]] = []

    def prompt(basename: str, title: str) -> bool:
        calls.append((basename, title))
        return True

    written, _, conflicts = exporter.export_release(
        fake_group,
        staging_root,
        original_dir=original_dir,
        artwork_original_dir=artwork_original,
        convert_progressive_jpeg="prompt",
        progressive_prompt_callback=prompt,
    )
    assert not conflicts
    assert len(calls) == 1, f"expected exactly 1 prompt call for 1 progressive source, got {calls}"
    jpgs = [n for n in written if n.endswith(".jpg")]
    out_bytes = Path(jpgs[0]).read_bytes()
    assert artwork.is_jpeg_progressive_from_bytes(out_bytes) is False, (
        "prompt+yes must convert progressive -> baseline"
    )


def test_exporter_prompt_callback_NOT_called_for_baseline(
    tmp_path: Path, baseline_jpeg: Path, fake_group: ReleaseGroup
) -> None:
    artwork_original, original_dir = _artwork_dir_with(tmp_path, baseline_jpeg, "TestGame.jpg")
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    calls: list[tuple[str, str]] = []

    def prompt(basename: str, title: str) -> bool:
        calls.append((basename, title))
        return True

    written, _, conflicts = exporter.export_release(
        fake_group,
        staging_root,
        original_dir=original_dir,
        artwork_original_dir=artwork_original,
        convert_progressive_jpeg="prompt",
        progressive_prompt_callback=prompt,
    )
    assert not conflicts
    assert calls == [], f"baseline source must NOT trigger prompt, got {calls}"
    jpgs = [n for n in written if n.endswith(".jpg")]
    assert len(jpgs) == 1
    out_bytes = Path(jpgs[0]).read_bytes()
    assert artwork.is_jpeg_progressive_from_bytes(out_bytes) is False


def test_exporter_prompt_callback_operator_says_no_keeps_progressive(
    tmp_path: Path, progressive_jpeg: Path, fake_group: ReleaseGroup
) -> None:
    artwork_original, original_dir = _artwork_dir_with(tmp_path, progressive_jpeg, "TestGame.jpg")
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    def prompt(basename: str, title: str) -> bool:
        return False

    written, _, conflicts = exporter.export_release(
        fake_group,
        staging_root,
        original_dir=original_dir,
        artwork_original_dir=artwork_original,
        convert_progressive_jpeg="prompt",
        progressive_prompt_callback=prompt,
    )
    assert not conflicts
    jpgs = [n for n in written if n.endswith(".jpg")]
    out_bytes = Path(jpgs[0]).read_bytes()
    assert artwork.is_jpeg_progressive_from_bytes(out_bytes) is True, (
        "prompt+no must keep progressive source as progressive"
    )


def test_exporter_prompt_without_callback_falls_back_to_never(
    tmp_path: Path, progressive_jpeg: Path, fake_group: ReleaseGroup
) -> None:
    artwork_original, original_dir = _artwork_dir_with(tmp_path, progressive_jpeg, "TestGame.jpg")
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    written, _, conflicts = exporter.export_release(
        fake_group,
        staging_root,
        original_dir=original_dir,
        artwork_original_dir=artwork_original,
        convert_progressive_jpeg="prompt",
        progressive_prompt_callback=None,
    )
    assert not conflicts
    jpgs = [n for n in written if n.endswith(".jpg")]
    out_bytes = Path(jpgs[0]).read_bytes()
    assert artwork.is_jpeg_progressive_from_bytes(out_bytes) is True, (
        "prompt policy without a callback must fall back to 'never' (keep progressive)"
    )


def test_exporter_non_jpeg_source_never_prompted_or_converted(
    tmp_path: Path, fake_group: ReleaseGroup
) -> None:
    """A .png source must never be detected as progressive nor trigger prompt."""
    pytest.importorskip("PIL")
    from PIL import Image

    d = tmp_path / "artwork_original"
    d.mkdir()
    png = d / "TestGame.png"
    Image.new("RGB", (16, 16), (0, 0, 0)).save(str(png), "PNG")

    original_d = tmp_path / "original"
    original_d.mkdir()
    (original_d / "Game.adf").write_bytes(b"FAKE_ADF_CONTENT")

    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    calls: list[tuple[str, str]] = []

    def prompt(basename: str, title: str) -> bool:
        calls.append((basename, title))
        return True

    written, _, conflicts = exporter.export_release(
        fake_group,
        staging_root,
        original_dir=original_d,
        artwork_original_dir=d,
        convert_progressive_jpeg="always",
        progressive_prompt_callback=prompt,
    )
    assert not conflicts
    assert calls == [], "non-jpeg source must never trigger prompt"
    jpgs = [n for n in written if n.endswith(".jpg")]
    assert len(jpgs) == 1
    out_bytes = Path(jpgs[0]).read_bytes()
    assert artwork.is_jpeg_progressive_from_bytes(out_bytes) is False
