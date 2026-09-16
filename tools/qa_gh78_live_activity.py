"""GH-78 Windows real-GUI live-activity qualification driver (QA-only).

Runs the REAL production live-activity chain on Windows:
real MainWindow constructor (real Diagnostics tab, real _diag widget),
real GuiState, real PipelineWorker (QThread) wired exactly as _on_run does
(worker.activity -> _on_activity -> _append_diag), with the online provider
CLASSES mocked at their construction boundary so the run is network-free.

Gates:
  * online run: Diagnostics live log shows per-provider ATTEMPT + RESULT lines
    ("Trying playmatch identity resolver", "Playmatch resolved identity", ...)
  * every appended line is timestamped (production _append_diag behavior)
  * offline control run: NO provider attempt lines appear (offline unchanged)
  * report.json + screenshot uploaded; exit 0 iff all steps ok.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from amiga_adf_library_builder.gui.layout import PortablePaths  # noqa: E402
from amiga_adf_library_builder.gui.main_window import MainWindow  # noqa: E402
from amiga_adf_library_builder.gui.state import GuiState  # noqa: E402
from amiga_adf_library_builder.gui.worker import PipelineWorker  # noqa: E402

REPORT = {"steps": [], "errors": []}


def _step(name, ok, detail=""):
    REPORT["steps"].append({"step": name, "ok": bool(ok), "detail": str(detail)})
    print(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}", flush=True)
    return bool(ok)


def _fail(exc):
    REPORT["errors"].append(repr(exc))
    print("ERROR:", repr(exc), flush=True)


def build_corpus(base: Path) -> Path:
    root = base / "lib"
    orig = root / "original"
    orig.mkdir(parents=True, exist_ok=True)
    for n in (
        "Example - Space Tactics (Disk 1 of 2).adf",
        "Example - Space Tactics (Disk 2 of 2).adf",
    ):
        (orig / n).write_bytes(b"QA-GH78-SYNTHETIC-ADF")
    return root


def build_provider_cfg(base: Path) -> Path:
    cfg = base / "providers.toml"
    cfg.write_text("[ playmatch ]\nenabled = true\n\n[ hasheous ]\nenabled = true\n")
    return cfg


def mock_provider(found=True, provider_id="gh78-id"):
    r = MagicMock()
    r.found = found
    r.needs_manual_review = False
    r.match_method.value = "EXACT_HASH" if found else "NONE"
    r.confidence = 1.0 if found else 0.0
    r.provider_id = provider_id if found else None
    r.transport_error = None
    r.metadata = {"canonical_title": "Test"} if found else None
    r.external_ids = None
    r.relevance_category = None
    r.manual_review_reason = "qa review"
    r.manufacturer = "E.P.I." if found else None
    r.artwork_url = None
    r.artwork_source_url = None
    r.artwork_provider = "qa"
    m = MagicMock()
    m.resolve.return_value = r
    return m


def run_worker(app, state: GuiState, win: MainWindow, timeout_s=90.0):
    """Real production run path: PipelineWorker + activity->win._on_activity."""
    result = {"done": False, "finished": []}
    worker = PipelineWorker(state)
    worker.activity.connect(win._on_activity)  # exact production wiring (_on_run)
    worker.finished.connect(lambda *a: (result["finished"].append(a),
                                        result.__setitem__("done", True)))
    win._worker = worker
    worker.start()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if result["done"]:
            app.processEvents()
            break
        time.sleep(0.05)
    return worker, result


def main() -> int:
    base = Path(os.environ.get(
        "QA_GH78_BASE",
        str(Path(tempfile.gettempdir()) / "GH78 Live Activity Dir With Spaces")))
    if base.exists():
        import shutil
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    root = build_corpus(base)
    prov_cfg = build_provider_cfg(base)

    app = QApplication.instance() or QApplication([])
    # Real production MainWindow constructor (real Diagnostics tab/_diag).
    win = MainWindow(portable_paths=PortablePaths(base / "appdata"))
    win.resize(900, 680)
    win.show()
    app.processEvents()
    _step("mainwindow_constructs", bool(win.windowTitle()), win.windowTitle())
    _step("diag_widget_exists", hasattr(win, "_diag") and win._diag is not None,
          type(getattr(win, "_diag", None)).__name__)

    def diag_text():
        return win._diag.toPlainText()

    win._diag.clear()

    # ---- 1. ONLINE run: provider activity must reach the live log ----
    try:
        pm = mock_provider(True, "pm-gh78")
        hs = mock_provider(True, "hs-gh78")
        with (
            patch("amiga_adf_library_builder.playmatch.PlaymatchProvider", return_value=pm),
            patch("amiga_adf_library_builder.hasheous.HasheousProvider", return_value=hs),
        ):
            state = GuiState(
                library_root=str(root),
                online=True,
                provider_config_path=str(prov_cfg),
                run_mode="build",
            )
            worker, result = run_worker(app, state, win)
            text = diag_text()
            _step("online_run_finished", result["done"] and bool(result["finished"]),
                  f"done={result['done']} finished={len(result['finished'])}")
            fin = result["finished"][0] if result["finished"] else (None, "?", False, None)
            _step("online_run_no_error", str(fin[1]) == "", f"error={fin[1]!r}")
            _step("run_started_line", "Run started." in text, "")
            _step("playmatch_attempt_line",
                  "Trying playmatch identity resolver" in text, "")
            _step("playmatch_result_line",
                  "Playmatch resolved identity" in text
                  and "pm-gh78" in text, "")
            _step("hasheous_attempt_line",
                  "Trying hasheous identity resolver" in text, "")
            _step("hasheous_result_line",
                  "Hasheous resolved identity" in text and "hs-gh78" in text, "")
            # Production _append_diag timestamps every line: HH:MM:SS prefix.
            lines = [l for l in text.splitlines()
                     if "Trying playmatch" in l or "Playmatch resolved" in l]
            ts_ok = bool(lines) and all(
                re.match(r"^\d{2}:\d{2}:\d{2}", l) for l in lines)
            _step("lines_timestamped_by_production_append", ts_ok,
                  f"sample={lines[0][:40] if lines else '(none)'}")
    except Exception as exc:
        _fail(exc)
        _step("online_driver_exception", False, repr(exc))

    # ---- 2. OFFLINE control (no provider config): no provider lines ----
    try:
        win._diag.clear()
        state = GuiState(
            library_root=str(root),
            online=False,
            run_mode="build",
        )
        worker, result = run_worker(app, state, win)
        text = diag_text()
        _step("offline_run_finished", result["done"], f"done={result['done']}")
        _step("offline_no_provider_lines",
              "Trying playmatch" not in text and "Trying hasheous" not in text
              and "resolved identity" not in text,
              f"diag_lines={len(text.splitlines())}")
    except Exception as exc:
        _fail(exc)
        _step("offline_driver_exception", False, repr(exc))

    # ---- 2b. OFFLINE with provider configs (informational): the provider
    # attempt itself is pre-existing (not gated on `online` at BASE either);
    # the candidate now surfaces it truthfully with the transport-error reason.
    try:
        win._diag.clear()
        state = GuiState(
            library_root=str(root),
            online=False,
            provider_config_path=str(prov_cfg),
            run_mode="build",
        )
        worker, result = run_worker(app, state, win)
        text = diag_text()
        _step("offline_with_cfg_miss_reason_truthful",
              "Playmatch: no match (transport error" in text
              and "Hasheous: no match (transport error" in text,
              "offline-with-config miss lines carry the real transport-error reason")
    except Exception as exc:
        _fail(exc)
        _step("offline_with_cfg_exception", False, repr(exc))

    # ---- 3. Screenshot of the real window with the live log ----
    try:
        shots = base / "qa-gh78-live-activity"
        shots.mkdir(parents=True, exist_ok=True)
        p = shots / "gh78_diag.png"
        win.grab().save(str(p))
        _step("screenshot", p.exists() and p.stat().st_size > 0, str(p))
    except Exception as exc:
        _fail(exc)
        _step("screenshot", False, repr(exc))

    try:
        win.close()
    except Exception:
        pass

    out = base / "qa-gh78-live-activity"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(REPORT, indent=2))
    print("report:", out / "report.json", flush=True)
    all_ok = all(s["ok"] for s in REPORT["steps"]) and not REPORT["errors"]
    print("OVERALL:", "PASS" if all_ok else "FAIL", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
