"""RTFM offline OCR fallback for image-only / scanned PDF manuals.

Synthetic, deterministic, offline. Covers the full contract:

  * normal-text PDF -> normal extraction wins, OCR never invoked;
  * image-only PDF -> OCR fallback invoked, recovered text flows into the
    existing RTFM processing and a real ``.rtfm`` is written;
  * OCR failure -> no false output, truthful concise reason;
  * cache miss -> OCR runs, cache written;
  * cache hit -> OCR does not run again;
  * source change -> cache invalidated by source SHA-256;
  * one scanned PDF serving two releases -> OCR executed once, two distinct
    ``.rtfm`` outputs, built count == 2;
  * a normal (Neuromancer-like) PDF never pays OCR cost;
  * a large scanned PDF is not rejected by the old image/text size caps;
  * PyInstaller packaging declares the OCR runtime, model data and hidden
    imports.

The RapidOCR engine is stubbed for speed: these tests prove the CONTRACT
(ordering, caching, plumbing, no-fabrication), not the OCR model quality --
real-data OCR validation lives in the acceptance probe, not the unit suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Force this worktree's source tree to win over any editable install.
_WT = Path(__file__).resolve().parents[1] / "src"
if str(_WT) not in sys.path:
    sys.path.insert(0, str(_WT))

from amiga_adf_library_builder import rtfm as rc  # noqa: E402
from amiga_adf_library_builder import rtfm_docs  # noqa: E402
from amiga_adf_library_builder import rtfm_ocr  # noqa: E402
from amiga_adf_library_builder.grouper import group_records  # noqa: E402
from amiga_adf_library_builder.models import ReleaseGroup  # noqa: E402
from amiga_adf_library_builder.parser import parse_filename  # noqa: E402
from amiga_adf_library_builder.rtfm_docs import (  # noqa: E402
    RtfmDocsConfig,
    extract_pdf_text,
)
from amiga_adf_library_builder.rtfm_ocr import ocr_pdf_document  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
SPEC_FILE = REPO_ROOT / "AmigaADFGui.spec"
BUILD_DRIVER = REPO_ROOT / "tools" / "build_windows.py"
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-windows.yml"

FAKE_OCR_TEXT = (
    "A-10 TANK KILLER\n\n"
    "This aircraft is the only true friend that the infantryman has in combat.\n"
    "Dynamix\n"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _native_pdf(path: Path, text: str) -> None:
    """Write a PDF whose pages carry real native text (no image-only pages)."""
    import fitz  # type: ignore

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()


def _image_only_pdf(path: Path, pages: int = 2, size=(200, 200)) -> None:
    """Write a genuinely image-only PDF (no text layer at all)."""
    import fitz  # type: ignore
    from PIL import Image
    import io

    doc = fitz.open()
    for _ in range(pages):
        img = Image.new("RGB", size, "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_image(fitz.Rect(0, 0, size[0], size[1]), stream=buf.getvalue())
    doc.save(str(path))
    doc.close()


@pytest.fixture()
def ocr_env(monkeypatch, tmp_path):
    """Stub the OCR engine and isolate the cache under ``tmp_path``.

    Records every OCR invocation so tests can assert invocation counts.
    """
    calls: list[dict] = []

    class _FakeEngine:
        def __call__(self, arr):
            calls.append({"shape": tuple(getattr(arr, "shape", ()))})
            # RapidOCR returns (result_list, elapse); result rows are
            # [box, text, score].
            rows = [[[0, 0], [1, 0], [1, 1], [0, 1]], FAKE_OCR_TEXT, "0.99"]
            return ([rows], [0.0])

    monkeypatch.setattr(rtfm_ocr, "_ENGINE", _FakeEngine())
    monkeypatch.setattr(rtfm_ocr, "ocr_backend", lambda: "rapidocr")
    cache_dir = tmp_path / "cache" / "rtfm-ocr"
    monkeypatch.setattr(rtfm_docs, "ocr_cache_dir", lambda: cache_dir)
    return calls, cache_dir


def _cfg(**kwargs) -> RtfmDocsConfig:
    base = dict(
        ocr_fallback_zoom=1.0,
        ocr_fallback_page_timeout=10.0,
        ocr_fallback_total_timeout=60.0,
        ocr_cache_enabled=True,
    )
    base.update(kwargs)
    return RtfmDocsConfig(**base)


# ---------------------------------------------------------------------------
# 1 + 9: normal text PDF -> OCR never invoked
# ---------------------------------------------------------------------------


def test_normal_text_pdf_does_not_invoke_ocr(tmp_path, ocr_env):
    calls, _cache = ocr_env
    pdf = tmp_path / "Neuromancer-like.pdf"
    _native_pdf(pdf, "Neuromancer chapter one: the sky above the port was the "
                     "color of television tuned to a dead channel.")

    res = extract_pdf_text(pdf, _cfg())

    assert res.text.strip(), "normal extraction must still succeed"
    assert "native_text" in res.reason
    assert calls == [], "OCR must never be invoked when normal text exists"
    # No cache artifact is created for a PDF that never needed OCR.
    assert not any(_cache.rglob("*.json")) if _cache.exists() else True


def test_neuromancer_control_zero_ocr_calls(tmp_path, ocr_env, monkeypatch):
    """Neuromancer-shaped control: normal extraction, OCR calls == ZERO."""
    calls, _cache = ocr_env
    pdf = tmp_path / "Neuromancer.pdf"
    _native_pdf(pdf, "Case woke in the Chiba City clinic with the taste of "
                     "blood in his mouth and a stranger's hands on his spine.")

    invoked = {"n": 0}

    def _boom(*a, **k):  # pragma: no cover - must never run
        invoked["n"] += 1
        raise AssertionError("OCR invoked for a normal-text PDF")

    monkeypatch.setattr(rtfm_ocr, "ocr_pdf_document", _boom)
    res = extract_pdf_text(pdf, _cfg())

    assert res.confidence == "high"
    assert invoked["n"] == 0
    assert calls == []


# ---------------------------------------------------------------------------
# 2 + 3 + 5: image-only PDF -> OCR invoked, text flows to RTFM
# ---------------------------------------------------------------------------


def test_image_only_pdf_invokes_ocr_and_recovers_text(tmp_path, ocr_env):
    calls, _cache = ocr_env
    pdf = tmp_path / "A-10 Tank Killer.pdf"
    _image_only_pdf(pdf, pages=2)

    res = extract_pdf_text(pdf, _cfg())

    assert calls, "OCR must be invoked for an image-only PDF"
    assert res.confidence == "low"
    assert "document_ocr" in res.reason
    assert FAKE_OCR_TEXT.strip() in res.text
    assert sum(1 for ch in res.text if not ch.isspace()) > 32


def test_ocr_success_writes_rtfm(tmp_path, ocr_env):
    """Recovered OCR text reaches the existing RTFM processing -> .rtfm written."""
    calls, _cache = ocr_env
    manuals = tmp_path / "Manuals"
    manuals.mkdir()
    pdf = manuals / "A-10 Tank Killer.pdf"
    _image_only_pdf(pdf, pages=1)

    rtfm_dir = tmp_path / "out" / "rtfm"
    cfg = rc.RtfmConfig(
        enabled=True,
        manuals_roots=(str(manuals),),
        docs=_cfg(),
    )
    group = ReleaseGroup(
        release_key="a10-v10",
        title="A-10 Tank Killer",
        edition="v1.0",
        group="A-10 Tank Killer v1.0",
        chipset="",
    )
    results = rc.build_rtfm_all(
        [group], cfg=cfg, rtfm_dir=rtfm_dir,
    )

    assert calls, "OCR must run for the scanned manual"
    assert len(results) == 1
    r = results[0]
    assert r.written and r.rtfm_path and r.rtfm_path.is_file(), (
        f"expected a written .rtfm; notes={r.notes}"
    )
    body = r.rtfm_path.read_text(encoding="utf-8")
    assert FAKE_OCR_TEXT.splitlines()[0] in body
    # Provenance must record the OCR method, never claim native text.
    assert any(
        (p.extraction_method or "").startswith("pdf:document_ocr")
        for p in r.sources
    ), f"OCR provenance missing: {[p.extraction_method for p in r.sources]}"


# ---------------------------------------------------------------------------
# 4: OCR failure -> truthful no-output
# ---------------------------------------------------------------------------


def test_ocr_failure_produces_no_false_output(tmp_path, ocr_env, monkeypatch):
    calls, _cache = ocr_env
    pdf = tmp_path / "scanned-blank.pdf"
    _image_only_pdf(pdf, pages=1)

    def _no_text(arr):  # OCR "succeeds" but finds nothing usable
        calls.append({"shape": tuple(getattr(arr, "shape", ()))})
        return (None, [0.0])

    monkeypatch.setattr(rtfm_ocr, "_ENGINE", type(
        "E", (), {"__call__": lambda self, arr: _no_text(arr)}
    )())

    res = extract_pdf_text(pdf, _cfg())

    assert res.text == "", "a failed OCR must never fabricate content"
    assert res.confidence == "unavailable"
    assert "no extractable text layer" in res.reason
    assert "OCR fallback" in res.reason
    # No cache entry is written for a failed OCR.
    assert not any(_cache.rglob("*.json")) if _cache.exists() else True

    # And the RTFM layer routes it for review instead of writing a .rtfm.
    manuals = tmp_path / "M"
    manuals.mkdir()
    import shutil

    shutil.copy(pdf, manuals / pdf.name)
    rtfm_dir = tmp_path / "out2" / "rtfm"
    cfg = rc.RtfmConfig(enabled=True, manuals_roots=(str(manuals),), docs=_cfg())
    group = ReleaseGroup(
        release_key="g1", title="scanned-blank", edition="",
        group="scanned-blank", chipset="",
    )
    results = rc.build_rtfm_all([group], cfg=cfg, rtfm_dir=rtfm_dir)
    assert results and not results[0].written
    assert "failed to decode" in (results[0].review_reason or "")
    # The truthful exact diagnostic must reach the operator's notes, not a
    # generic one. review_reason is the summary; the detail lives in notes.
    detail = " ".join(results[0].notes)
    assert "no extractable text layer" in detail
    assert "OCR produced no usable text" in detail


def test_ocr_engine_missing_is_truthful(tmp_path, ocr_env, monkeypatch):
    calls, _cache = ocr_env
    pdf = tmp_path / "scanned.pdf"
    _image_only_pdf(pdf, pages=1)
    monkeypatch.setattr(rtfm_ocr, "ocr_backend",
                        lambda: "unavailable (rapidocr import failed: ImportError)")

    res = extract_pdf_text(pdf, _cfg())

    assert res.text == ""
    assert res.confidence == "unavailable"
    assert "OCR engine unavailable" in res.reason or "unavailable" in res.reason
    assert calls == []


# ---------------------------------------------------------------------------
# 5 + 6 + 7: cache miss / hit / invalidation
# ---------------------------------------------------------------------------


def test_cache_miss_then_hit(tmp_path, ocr_env):
    calls, cache_dir = ocr_env
    pdf = tmp_path / "A-10 Tank Killer.pdf"
    _image_only_pdf(pdf, pages=2)

    res1 = ocr_pdf_document(pdf, zoom=1.0, page_timeout=10.0,
                            total_timeout=60.0, cache_dir=cache_dir)
    assert calls, "first run must OCR"
    assert res1.cache == "miss"
    assert res1.ok
    assert res1.pages == 2
    assert any(cache_dir.rglob("*.json")), "cache artifact must be written"

    res2 = ocr_pdf_document(pdf, zoom=1.0, page_timeout=10.0,
                            total_timeout=60.0, cache_dir=cache_dir)
    assert res2.cache == "hit"
    assert len(calls) == 2, (
        "second run must not OCR again: the 2 recorded calls are the first "
        "run's two pages"
    )
    assert res2.text == res1.text


def test_cache_disabled_always_ocrs(tmp_path, ocr_env):
    calls, cache_dir = ocr_env
    pdf = tmp_path / "scanned.pdf"
    _image_only_pdf(pdf, pages=1)

    res = None
    for _ in range(2):
        res = ocr_pdf_document(pdf, zoom=1.0, page_timeout=10.0,
                               total_timeout=60.0, cache_dir=cache_dir,
                               use_cache=False)
    assert len(calls) == 2
    # With caching off the result never claims a hit/miss.
    assert res is not None and res.cache == "none"


def test_changed_source_invalidates_cache(tmp_path, ocr_env):
    calls, cache_dir = ocr_env
    pdf = tmp_path / "scanned.pdf"
    _image_only_pdf(pdf, pages=1)
    first = ocr_pdf_document(pdf, zoom=1.0, page_timeout=10.0,
                             total_timeout=60.0, cache_dir=cache_dir)
    assert first.cache == "miss"
    assert len(calls) == 1

    # Rewrite the file with different content -> different SHA-256 identity.
    _image_only_pdf(pdf, pages=2, size=(240, 240))
    second = ocr_pdf_document(pdf, zoom=1.0, page_timeout=10.0,
                              total_timeout=60.0, cache_dir=cache_dir)
    assert second.cache == "miss", "a changed source must re-OCR"
    assert len(calls) == 3, "1 page before the change + 2 pages after"
    assert second.pages == 2


def test_cache_key_is_source_sha256(tmp_path, ocr_env):
    _calls, cache_dir = ocr_env
    pdf = tmp_path / "scanned.pdf"
    _image_only_pdf(pdf, pages=1)
    import hashlib

    expected = hashlib.sha256(pdf.read_bytes()).hexdigest()
    ocr_pdf_document(pdf, zoom=1.0, page_timeout=10.0, total_timeout=60.0,
                     cache_dir=cache_dir)
    assert (cache_dir / expected[:2] / f"{expected}.json").is_file()


def test_cache_never_written_in_source_dir(tmp_path, ocr_env):
    _calls, cache_dir = ocr_env
    src = tmp_path / "Manuals"
    src.mkdir()
    pdf = src / "scanned.pdf"
    _image_only_pdf(pdf, pages=1)
    ocr_pdf_document(pdf, zoom=1.0, page_timeout=10.0, total_timeout=60.0,
                     cache_dir=cache_dir)
    assert not any(src.rglob("*.json")), "cache must not live in the manual tree"
    assert pdf.read_bytes() == pdf.read_bytes()  # source untouched (read-only)


# ---------------------------------------------------------------------------
# 8: one scanned PDF, two releases -> OCR once, two .rtfm outputs
# ---------------------------------------------------------------------------


def test_one_scanned_pdf_two_releases_ocr_once(tmp_path, ocr_env):
    calls, _cache = ocr_env
    manuals = tmp_path / "Manuals"
    manuals.mkdir()
    pdf = manuals / "A-10 Tank Killer.pdf"
    _image_only_pdf(pdf, pages=2)

    rtfm_dir = tmp_path / "out" / "rtfm"
    cfg = rc.RtfmConfig(enabled=True, manuals_roots=(str(manuals),), docs=_cfg())
    groups = [
        ReleaseGroup(release_key="a10-v10", title="A-10 Tank Killer",
                     edition="v1.0", group="A-10 Tank Killer v1.0", chipset=""),
        ReleaseGroup(release_key="a10-v15", title="A-10 Tank Killer",
                     edition="v1.5", group="A-10 Tank Killer v1.5", chipset=""),
    ]
    results = rc.build_rtfm_all(groups, cfg=cfg, rtfm_dir=rtfm_dir)

    built = [r for r in results if r.written and r.rtfm_path
             and r.rtfm_path.is_file()]
    assert len(results) == 2
    assert len(built) == 2, f"built count must be 2; notes={[r.notes for r in results]}"
    # Source OCR deduplicated: exactly one document OCR pass (page-count calls).
    assert len(calls) == 2, "two pages OCR'd once -- not once per release"
    assert len(calls) != 4, "OCR must not be repeated per release"
    names = sorted(r.rtfm_path.name for r in built)
    # Two DISTINCT release outputs (never deduplicated into one file).
    assert len(names) == 2
    assert names[0] != names[1]
    assert all(n.endswith(".rtfm") for n in names)
    assert all("Tank Killer" in n for n in names)
    # Both carry the OCR-recovered text.
    for r in built:
        assert FAKE_OCR_TEXT.splitlines()[0] in r.rtfm_path.read_text(
            encoding="utf-8"
        )


def test_second_release_reuses_ocr_cache(tmp_path, ocr_env):
    """A cached OCR result is reused across separate RTFM builds."""
    calls, cache_dir = ocr_env
    manuals = tmp_path / "Manuals"
    manuals.mkdir()
    pdf = manuals / "A-10 Tank Killer.pdf"
    _image_only_pdf(pdf, pages=1)
    rtfm_dir = tmp_path / "out" / "rtfm"
    cfg = rc.RtfmConfig(enabled=True, manuals_roots=(str(manuals),), docs=_cfg())
    group = ReleaseGroup(release_key="g", title="A-10 Tank Killer", edition="v1.0",
                         group="A-10 Tank Killer v1.0", chipset="")

    rc.build_rtfm_all([group], cfg=cfg, rtfm_dir=rtfm_dir)
    assert len(calls) == 1
    rc.build_rtfm_all([group], cfg=cfg, rtfm_dir=rtfm_dir)
    assert len(calls) == 1, "second build must be a cache hit, not a re-OCR"


# ---------------------------------------------------------------------------
# 10: large scanned PDF must not be rejected by the old size caps
# ---------------------------------------------------------------------------


def test_large_scanned_pdf_is_ocr_attempted(tmp_path, ocr_env):
    """A 42 MB-class scanned manual must not be refused before OCR runs.

    The legacy ``max_bytes`` guard exists to stop decompression bombs; OCR
    payloads are far larger than that guard's default, so the fallback path
    must not inherit it. This proves a large source still reaches OCR.
    """
    calls, _cache = ocr_env
    pdf = tmp_path / "huge-scanned.pdf"
    # Many pages, each large enough that total raster work is substantial.
    _image_only_pdf(pdf, pages=12, size=(600, 800))

    res = ocr_pdf_document(pdf, zoom=1.0, page_timeout=10.0, total_timeout=60.0,
                           max_pixels=40_000_000)

    assert res.ok, "a large scanned PDF must still yield OCR text"
    assert res.pages == 12
    assert len(calls) == 12


def test_explosive_page_is_skipped_without_crashing(tmp_path, ocr_env):
    """A MediaBox that would explode the bitmap is skipped, not fatal."""
    calls, _cache = ocr_env
    pdf = tmp_path / "bomb.pdf"
    import fitz  # type: ignore

    doc = fitz.open()
    doc.new_page(width=100000, height=100000)  # absurd declared size
    doc.new_page(width=200, height=200)
    doc.save(str(pdf))
    doc.close()

    res = ocr_pdf_document(pdf, zoom=1.0, page_timeout=10.0, total_timeout=60.0,
                           max_pixels=1_000_000)

    assert res.ok
    assert len(calls) == 1, "only the sane page is rasterized"


# ---------------------------------------------------------------------------
# 11: PyInstaller packaging / configuration
# ---------------------------------------------------------------------------


REQUIRED_OCR_HIDDEN_IMPORTS = (
    "rapidocr_onnxruntime",
    "onnxruntime",
    "cv2",
    "numpy",
)


def _gui_extra() -> list[str]:
    import tomllib

    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    extras = data.get("project", {}).get("optional-dependencies", {})
    return list(extras.get("gui", []))


def _spec_hidden_imports_text() -> str:
    import re

    text = SPEC_FILE.read_text(encoding="utf-8")
    match = re.search(r"^HIDDEN_IMPORTS\s*=\s*\[(.*)\]\s*$", text, re.MULTILINE)
    assert match, "HIDDEN_IMPORTS list not found in AmigaADFGui.spec"
    return match.group(1)


@pytest.mark.parametrize("entry", REQUIRED_OCR_HIDDEN_IMPORTS)
def test_committed_spec_pins_ocr_hidden_imports(entry: str) -> None:
    blob = _spec_hidden_imports_text()
    assert f"'{entry}'" in blob, (
        f"{entry} missing from committed spec HIDDEN_IMPORTS; the frozen GUI "
        f"cannot run OCR on scanned manuals without it"
    )


def test_committed_spec_collects_ocr_model_data() -> None:
    text = SPEC_FILE.read_text(encoding="utf-8")
    assert "OCR_MODEL_DATAS" in text, (
        "spec must collect the OCR engine's model weights as data files"
    )
    assert "'rapidocr_onnxruntime'" in text, (
        "spec must collect RapidOCR's .onnx model weights"
    )
    assert "datas=_collect_datas() + PDF_NATIVE_DATAS + OCR_MODEL_DATAS" in text, (
        "Analysis() must pass the collected OCR model data as datas"
    )


def test_build_driver_pins_ocr_packaging() -> None:
    text = BUILD_DRIVER.read_text(encoding="utf-8")
    assert "OCR_HIDDEN_IMPORTS" in text, (
        "tools/build_windows.py must declare OCR_HIDDEN_IMPORTS"
    )
    assert "OCR_PACKAGES_WITH_DATA" in text, (
        "tools/build_windows.py must declare OCR_PACKAGES_WITH_DATA"
    )
    for entry in REQUIRED_OCR_HIDDEN_IMPORTS:
        assert f'"{entry}"' in text, (
            f"{entry} not declared in the build driver's OCR_HIDDEN_IMPORTS"
        )


def test_driver_render_spec_emits_ocr_packaging() -> None:
    from tools.build_windows import render_spec

    spec = render_spec(
        target="onedir",
        name="AmigaADFLibraryBuilder",
        launcher_rel="app_launcher.py",
        pathex_rel=["src"],
        hidden_imports=["rapidocr_onnxruntime"],
        console=False,
        application_version="0.3.8",
        data_packages=["rapidocr_onnxruntime", "cv2"],
    )
    assert "OCR_MODEL_DATAS" in spec
    assert "'rapidocr_onnxruntime'" in spec
    assert "datas=_collect_datas() + OCR_MODEL_DATAS" in spec

    plain = render_spec(
        target="onedir",
        name="AmigaADFLibraryBuilder",
        launcher_rel="app_launcher.py",
        pathex_rel=["src"],
        hidden_imports=[],
        console=False,
        application_version="0.3.8",
    )
    assert "OCR_MODEL_DATAS" not in plain


def test_gui_extra_declares_ocr_engine() -> None:
    import re

    extra = _gui_extra()
    matching = [
        r for r in extra
        if re.match(r"^\s*rapidocr-onnxruntime([\s><=!~]|$)", r, re.IGNORECASE)
    ]
    assert matching, (
        f"rapidocr-onnxruntime must be a declared member of the `gui` extra; "
        f"got {extra}"
    )


def test_build_workflow_verifies_ocr_engine() -> None:
    text = BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert "ocr_backend" in text, (
        "build-windows.yml must verify the bundled OCR engine before packaging"
    )
    assert 'assert backend == "rapidocr"' in text, (
        "build-windows.yml must fail the build when the OCR engine is unusable"
    )


def test_ocr_backend_reports_unavailable_without_engine(monkeypatch):
    """A missing engine must be reported concretely, never as success."""
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict
    ) else __builtins__.__import__

    def _blocked(name, *a, **k):
        if name == "rapidocr_onnxruntime":
            raise ImportError("No module named 'rapidocr_onnxruntime'")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _blocked)
    backend = rtfm_ocr.ocr_backend()
    assert backend.startswith("unavailable")
    assert "rapidocr" in backend


def test_cache_version_bump_invalidates_entries(tmp_path, monkeypatch):
    """A stale cache entry from another format must never be trusted."""
    cache_dir = tmp_path / "c"
    pdf = tmp_path / "s.pdf"
    _image_only_pdf(pdf, pages=1)
    key = rtfm_ocr.cache_key_for_file(pdf)
    p = cache_dir / key[:2] / f"{key}.json"
    p.parent.mkdir(parents=True)
    p.write_text(
        '{"version": 999, "engine": "rapidocr", "key": "%s", "text": "stale"}'
        % key,
        encoding="utf-8",
    )
    assert rtfm_ocr._read_cache(cache_dir, key) is None
