"""GH-65 — Pillow/PIL runtime-packaging regression coverage.

Background: the packaged Windows app hard-failed artwork processing with
``Artwork processing blocked: Pillow unavailable: No module named 'PIL'``
because Pillow was only an optional extra and was absent from the shipped
build's environment (and not pinned as a hidden import in the PyInstaller
spec).

These tests fail if the packaging/runtime contract regresses:

  * the `gui` extra must declare pillow (the build lane installs `.[gui]`);
  * the PyInstaller spec (committed + regenerated) must carry the PIL hidden
    imports so the frozen bundle keeps PIL importable;
  * the representative artwork-processing path must succeed end-to-end when
    PIL is importable (and raise the documented blocked error when it is not).

All checks are offline and deterministic.
"""
from __future__ import annotations

import importlib
import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
SPEC_FILE = REPO_ROOT / "AmigaADFGui.spec"
BUILD_DRIVER = REPO_ROOT / "tools" / "build_windows.py"
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-windows.yml"


def _gui_extra() -> list[str]:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    extras = data.get("project", {}).get("optional-dependencies", {})
    return list(extras.get("gui", []))


def _pillow_reqs(extra: list[str]) -> list[str]:
    # PEP 508 requirement form: "pillow", "pillow>=10", "Pillow==10.0", ...
    return [r for r in extra if re.match(r"^\s*pillow([\s><=!~]|$)", r, re.IGNORECASE)]


@pytest.mark.parametrize("entry", ["PIL", "PIL.Image"])
def test_spec_pins_pil_hidden_imports(entry: str) -> None:
    """The committed spec must force PIL into the frozen bundle (GH-65)."""
    assert SPEC_FILE.is_file(), f"missing PyInstaller spec: {SPEC_FILE}"
    text = SPEC_FILE.read_text(encoding="utf-8")
    match = re.search(r"^HIDDEN_IMPORTS\s*=\s*\[(.*)\]\s*$", text, re.MULTILINE)
    assert match, "HIDDEN_IMPORTS list not found in AmigaADFGui.spec"
    assert f"'{entry}'" in match.group(1), (
        f"{entry} missing from committed spec HIDDEN_IMPORTS (GH-65)"
    )


def test_build_driver_pins_pil_hidden_imports() -> None:
    """The spec driver must emit the PIL hidden imports (keeps spec in sync)."""
    assert BUILD_DRIVER.is_file(), f"missing build driver: {BUILD_DRIVER}"
    text = BUILD_DRIVER.read_text(encoding="utf-8")
    assert re.search(r"PILLOW_HIDDEN_IMPORTS\s*=\s*\[", text), (
        "tools/build_windows.py must define PILLOW_HIDDEN_IMPORTS (GH-65)"
    )
    for entry in ("PIL", "PIL.Image"):
        assert entry in text, f"{entry} not referenced in build driver (GH-65)"


def test_gui_extra_declares_pillow() -> None:
    """The `gui` extra must declare pillow so the build env installs it."""
    extra = _gui_extra()
    assert extra, "pyproject.toml [project.optional-dependencies] gui is empty"
    reqs = _pillow_reqs(extra)
    assert reqs, (
        f"pillow>=10 must be a declared member of the `gui` extra; got {extra} (GH-65)"
    )


def test_build_workflow_verifies_pil_import() -> None:
    """The shipped-build workflow must verify PIL imports before packaging."""
    assert BUILD_WORKFLOW.is_file(), f"missing workflow: {BUILD_WORKFLOW}"
    text = BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert "import PIL" in text or "from PIL import Image" in text, (
        "build-windows.yml must verify the PIL import in the build environment (GH-65)"
    )


def test_artwork_processing_succeeds_with_pil(tmp_path: Path) -> None:
    """Representative artwork path: PIL present -> deterministic JPEG out."""
    from PIL import Image

    from amiga_adf_library_builder.artwork import process_artwork_bytes

    src = tmp_path / "master.png"
    im = Image.new("RGB", (300, 240), (120, 60, 200))
    im.save(src, "PNG")

    data = process_artwork_bytes(src)
    assert data[:2] == b"\xff\xd8", "processed artwork must be JPEG"
    assert 0 < len(data) <= 500_000, "processed artwork must respect the 500 KB cap"

    out = tmp_path / "processed.jpg"
    out.write_bytes(data)
    with Image.open(out) as im2:
        w, h = im2.size
        assert w <= 2000 and h <= 2000


def test_artwork_processing_blocks_without_pil(monkeypatch, tmp_path: Path) -> None:
    """PIL absent -> documented blocked error (never silently fakes output)."""
    from PIL import Image

    src = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (10, 20, 30)).save(src, "PNG")

    # Simulate the packaged-app failure condition: PIL not importable.
    real_pil = sys.modules.pop("PIL", None)
    real_image = sys.modules.pop("PIL.Image", None)
    try:
        import builtins

        real_import = builtins.__import__

        def _blocked(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ModuleNotFoundError("No module named 'PIL'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked)
        for mod in ("PIL", "PIL.Image"):
            monkeypatch.setitem(sys.modules, mod, None)  # force reimport path

        from amiga_adf_library_builder.artwork import process_artwork_bytes

        with pytest.raises(RuntimeError) as exc_info:
            process_artwork_bytes(src)
        assert "Pillow unavailable" in str(exc_info.value)
    finally:
        # Restore real modules so other tests are unaffected.
        if real_pil is not None:
            sys.modules["PIL"] = real_pil
        if real_image is not None:
            sys.modules["PIL.Image"] = real_image
