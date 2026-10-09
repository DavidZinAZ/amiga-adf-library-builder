"""Regression coverage: the packaged Windows GUI must be able to decode PDF
manuals.

Root cause this guards against: ``rtfm_docs`` imports PyMuPDF and pypdf LAZILY
inside functions, so PyInstaller static analysis never saw them. They were
omitted from the frozen bundle, leaving the packaged app with no PDF backend at
all -- every PDF manual (A-10 Tank Killer, Neuromancer, ...) routed for review
with a generic "all matched sources failed to decode", even though manual
matching itself worked.

This file fails if the packaging/runtime contract regresses:

  * the `gui` extra must declare pypdf + pymupdf (the build lane installs
    `.[gui]`);
  * the PyInstaller spec (committed + driver-rendered) must carry the PDF
    hidden imports AND collect the native MuPDF shared libraries -- a bundle
    with the Python modules but no libmupdf still cannot decode;
  * the build workflow must verify the PDF backend before packaging;
  * ``pdf_backend()`` must prove the backend is genuinely usable (an import
    alone is not proof in a frozen build);
  * a genuinely missing backend must produce an explicit, exact diagnostic and
    never a false success.

All checks are offline and deterministic.
"""

from __future__ import annotations

import builtins
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

REQUIRED_HIDDEN_IMPORTS = (
    "fitz",
    "pymupdf",
    "pymupdf.mupdf",
    "pypdf",
)

REQUIRED_PDF_REQS = ("pypdf", "pymupdf")


def _gui_extra() -> list[str]:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    extras = data.get("project", {}).get("optional-dependencies", {})
    return list(extras.get("gui", []))


def _spec_hidden_imports_text() -> str:
    text = SPEC_FILE.read_text(encoding="utf-8")
    match = re.search(r"^HIDDEN_IMPORTS\s*=\s*\[(.*)\]\s*$", text, re.MULTILINE)
    assert match, "HIDDEN_IMPORTS list not found in AmigaADFGui.spec"
    return match.group(1)


@pytest.mark.parametrize("entry", REQUIRED_HIDDEN_IMPORTS)
def test_committed_spec_pins_pdf_hidden_imports(entry: str) -> None:
    """The committed spec must freeze the PDF backend Python modules."""
    assert SPEC_FILE.is_file(), f"missing PyInstaller spec: {SPEC_FILE}"
    blob = _spec_hidden_imports_text()
    assert f"'{entry}'" in blob, (
        f"{entry} missing from committed spec HIDDEN_IMPORTS; the frozen GUI "
        f"cannot decode PDF manuals without it"
    )


def test_committed_spec_collects_native_mupdf_libraries() -> None:
    """The spec must collect MuPDF's native shared libraries as data files.

    Hidden imports alone are insufficient: PyMuPDF loads libmupdf /
    libmupdfcpp at runtime and PyInstaller's binary analysis does not follow
    that path. A bundle missing these fails at backend load even with the
    Python modules present.
    """
    text = SPEC_FILE.read_text(encoding="utf-8")
    assert "collect_data_files" in text, (
        "spec must import collect_data_files to bundle the native MuPDF libs"
    )
    assert re.search(r"PDF_NATIVE_DATAS\s*=", text), (
        "spec must define PDF_NATIVE_DATAS (native MuPDF library collection)"
    )
    assert "'pymupdf'" in text, "spec must collect native libs from 'pymupdf'"
    assert "datas=_collect_datas() + PDF_NATIVE_DATAS" in text, (
        "Analysis() must pass the collected native libs as datas"
    )


def test_build_driver_pins_pdf_hidden_imports() -> None:
    """The spec driver must emit the PDF hidden imports (keeps spec in sync)."""
    text = BUILD_DRIVER.read_text(encoding="utf-8")
    assert re.search(r"PDF_HIDDEN_IMPORTS\s*=\s*\[", text), (
        "tools/build_windows.py must define PDF_HIDDEN_IMPORTS"
    )
    for entry in REQUIRED_HIDDEN_IMPORTS:
        assert f'"{entry}"' in text, (
            f"{entry} not declared in the build driver's PDF_HIDDEN_IMPORTS"
        )
    assert re.search(r"PDF_PACKAGES_WITH_NATIVE_LIBS\s*=\s*\[", text), (
        "build driver must declare PDF_PACKAGES_WITH_NATIVE_LIBS"
    )


def test_driver_render_spec_emits_pdf_packaging() -> None:
    """render_spec(..., native_lib_packages=...) must emit the packaging hooks."""
    from tools.build_windows import render_spec

    spec = render_spec(
        target="onedir",
        name="AmigaADFLibraryBuilder",
        launcher_rel="app_launcher.py",
        pathex_rel=["src"],
        hidden_imports=["fitz"],
        console=False,
        application_version="0.3.7",
        native_lib_packages=["pymupdf"],
    )
    assert "PDF_NATIVE_DATAS" in spec
    assert "collect_data_files" in spec
    assert "datas=_collect_datas() + PDF_NATIVE_DATAS" in spec

    # Without the argument the spec stays backward-compatible.
    plain = render_spec(
        target="onedir",
        name="AmigaADFLibraryBuilder",
        launcher_rel="app_launcher.py",
        pathex_rel=["src"],
        hidden_imports=[],
        console=False,
        application_version="0.3.7",
    )
    assert "PDF_NATIVE_DATAS" not in plain
    assert "datas=_collect_datas()" in plain


@pytest.mark.parametrize("req", REQUIRED_PDF_REQS)
def test_gui_extra_declares_pdf_backend(req: str) -> None:
    """The `gui` extra must declare the PDF backends so the build env installs
    them (the Windows build lane installs only `.[gui]`)."""
    extra = _gui_extra()
    assert extra, "pyproject.toml gui extra is empty"
    matching = [
        r for r in extra if re.match(rf"^\s*{re.escape(req)}([\s><=!~]|$)", r, re.IGNORECASE)
    ]
    assert matching, f"{req} must be a declared member of the `gui` extra; got {extra}"


def test_build_workflow_verifies_pdf_backend() -> None:
    """The shipped-build workflow must verify the PDF backend before packaging."""
    assert BUILD_WORKFLOW.is_file(), f"missing workflow: {BUILD_WORKFLOW}"
    text = BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert "pdf_backend" in text, (
        "build-windows.yml must verify the PDF backend before packaging"
    )
    assert "amiga_adf_library_builder.rtfm_docs" in text


# --- Runtime backend probe ---------------------------------------------------


def test_pdf_backend_reports_usable_backend() -> None:
    """With the real backends installed, pdf_backend() must name one."""
    from amiga_adf_library_builder.rtfm_docs import pdf_backend

    backend = pdf_backend()
    assert backend in ("pymupdf", "pypdf"), (
        f"expected a usable PDF backend, got {backend!r}"
    )


def test_pdf_backend_native_library_missing_is_not_usable(monkeypatch) -> None:
    """A frozen build missing MuPDF's native lib must NOT report 'pymupdf'.

    The import can succeed while the native backend cannot be used. The probe
    must exercise a real backend call, not just `import fitz`.
    """
    import amiga_adf_library_builder.rtfm_docs as rd

    monkeypatch.setattr(rd, "_have_pypdf", lambda: False)
    monkeypatch.setattr(rd, "_pypdf_backend", lambda: "unavailable")

    real_import = builtins.__import__

    class _Boom:
        def __getattr__(self, name):  # any fitz call explodes
            raise OSError("libmupdf.so.28.2: cannot open shared object file")

    def _broken_fitz(name, *args, **kwargs):
        if name in ("fitz", "pymupdf"):
            return _Boom()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _broken_fitz)

    backend = rd.pdf_backend()
    assert backend.startswith("unavailable"), (
        f"a native-library failure must not report a usable backend: {backend!r}"
    )
    assert "pymupdf" in backend and "OSError" in backend


def test_extract_pdf_missing_backend_names_both_and_never_fakes(
    monkeypatch, tmp_path,
) -> None:
    """No usable backend -> explicit diagnostic, no fabricated text, no write."""
    import amiga_adf_library_builder.rtfm_docs as rd

    pdf = tmp_path / "A-10 Tank Killer.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%EOF\n")

    monkeypatch.setattr(rd, "_pymupdf_backend", lambda: "unavailable")
    monkeypatch.setattr(rd, "_pypdf_backend", lambda: "unavailable")

    res = rd.extract_pdf_text(pdf)
    assert res.empty
    assert res.text == ""
    assert res.confidence == "unavailable"
    assert res.backend.startswith("unavailable")
    assert "pymupdf: unavailable" in res.reason
    assert "pypdf: unavailable" in res.reason


def test_extract_pdf_reports_backend_on_success(tmp_path) -> None:
    """A successful PDF extraction must record the backend that served it."""
    import fitz

    from amiga_adf_library_builder.rtfm_docs import extract_pdf_text

    pdf = tmp_path / "A-10 Tank Killer.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "CONTROLS\n\nFire the cannon with the joystick button.\n" * 4)
    doc.save(str(pdf))
    doc.close()

    res = extract_pdf_text(pdf)
    assert not res.empty
    assert res.text
    assert res.backend in ("pymupdf", "pypdf")
    assert res.confidence == "high"


def test_scanned_pdf_names_no_text_layer_not_decode_failure(tmp_path) -> None:
    """A text-less (scanned) PDF must report 'no extractable text layer',
    not a generic decode failure -- so a no-output outcome is auditable."""
    import amiga_adf_library_builder.rtfm_docs as rd

    # Simulate the environment's Tesseract being absent (the packaged app has
    # no OCR engine) so the page cannot be rescued by OCR.
    monkey_tesseract = lambda: False  # noqa: E731
    rd._tesseract_available = monkey_tesseract  # type: ignore[assignment]

    pdf = tmp_path / "Neuromancer.pdf"
    doc = fitz_doc_with_image_only_page(pdf)

    res = rd.extract_pdf_text(pdf)
    assert res.empty
    assert res.confidence == "unavailable"
    assert "no extractable text layer" in res.reason
    assert "decode" not in res.reason.lower()


def fitz_doc_with_image_only_page(path: Path):
    """A real, valid PDF whose single page carries no text layer."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    # A drawn rectangle with no text -> no extractable text layer.
    page.draw_rect(fitz.Rect(50, 50, 300, 400), color=(0, 0, 0), fill=(1, 1, 1))
    doc.save(str(path))
    doc.close()
    return doc
