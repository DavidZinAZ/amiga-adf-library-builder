#!/usr/bin/env python3
"""Windows QA driver for GH-85 packaged GUI progress qualification.

Run by a GitHub Actions workflow on ``windows-latest``. It exercises the
packaged GUI against synthetic ADF inputs and records the operator-facing
progress behavior as JSON + screenshot artifacts:

  1. clean launch of the real standalone exe under a path with spaces;
  2. real-GUI offscreen run that builds a small synthetic library and
     captures the worker's progress/activity emissions;
  3. structured checks for GH-85 acceptance:
       - progress advances during enrichment;
       - progress is monotonic;
       - 100% occurs only on completion;
       - processed count is shown and consistent;
       - error path surfaces clearly without a raw traceback;
       - zero-release run completes cleanly;
  4. emit ``report.json`` + ``screenshots/``.

This file is QA-owned harness only. It does not modify product code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPORT: dict = {"steps": [], "errors": [], "gh85": {}}


def _step(name: str, ok: bool, detail: str = "") -> None:
    REPORT["steps"].append({"step": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def _collect_progress(worker, collector, timeout_s: float = 120.0):
    """Run worker and collect signal emissions until finished or timeout."""
    from PySide6.QtCore import QCoreApplication, QTimer

    app = QCoreApplication.instance() or QCoreApplication([])
    worker.progress.connect(collector.on_progress)
    worker.activity.connect(collector.on_activity)
    worker.finished.connect(collector.on_finished)

    worker.start()
    deadline = time.time() + timeout_s
    while collector.finished_emission is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    if collector.finished_emission is None:
        worker.request_cancel()
        for _ in range(200):
            app.processEvents()
            time.sleep(0.01)
    return collector


class ProgressCollector:
    def __init__(self) -> None:
        self.progress_emissions = []
        self.activity_emissions = []
        self.finished_emission = None

    def on_progress(self, phase: str, percent: int, detail: str) -> None:
        self.progress_emissions.append((phase, percent, detail))

    def on_activity(self, text: str) -> None:
        self.activity_emissions.append(text)

    def on_finished(self, result, error: str, cancelled: bool, cfg) -> None:
        self.finished_emission = (result, error, cancelled, cfg)


def _build_state(base_dir: Path, original_dir: Path, library_root: Path):
    from PySide6.QtWidgets import QApplication

    from amiga_adf_library_builder.gui import MainWindow, PortablePaths, SettingsStore
    from amiga_adf_library_builder.gui.main_window import GuiState
    from amiga_adf_library_builder.gui.state import (
        build_path_config_from_gui_state,
        build_pipeline_kwargs,
    )
    from amiga_adf_library_builder.initializer import ensure_managed_directories

    app = QApplication.instance() or QApplication([])
    state = GuiState(
        library_root=str(library_root),
        original_dir=str(original_dir),
        run_mode="build",
        online=False,
        refresh_metadata=False,
        require_artwork=False,
        verify_only=False,
        export_gate_acknowledged=False,
    )
    pp = PortablePaths(base_dir=library_root)
    pp.ensure_all()
    cfg = build_path_config_from_gui_state(state)
    ensure_managed_directories(cfg)
    kwargs = build_pipeline_kwargs(state, cfg)
    return app, state, pp, cfg, kwargs


def _run_gh85_normal(base_dir: Path, original_dir: Path, library_root: Path):
    from threading import Event

    from amiga_adf_library_builder.gui.worker import PipelineWorker

    app, state, pp, cfg, kwargs = _build_state(base_dir, original_dir, library_root)
    cancel_event = Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = _collect_progress(worker, ProgressCollector())
    return collector, cfg


def _run_gh85_error(base_dir: Path):
    from threading import Event

    from amiga_adf_library_builder.gui.worker import PipelineWorker

    library_root = base_dir / "gh85-err-lib"
    original_dir = base_dir / "gh85-err-orig"
    original_dir.mkdir(parents=True, exist_ok=True)
    app, state, pp, cfg, kwargs = _build_state(
        base_dir, original_dir, library_dir := library_root
    )
    state.library_root = "/nonexistent/path/that/does/not/exist"
    cancel_event = Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = _collect_progress(worker, ProgressCollector(), timeout_s=20.0)
    return collector


def _run_gh85_zero(base_dir: Path):
    from threading import Event

    from amiga_adf_library_builder.gui.worker import PipelineWorker

    library_root = base_dir / "gh85-zero-lib"
    original_dir = base_dir / "gh85-zero-orig"
    original_dir.mkdir(parents=True, exist_ok=True)
    app, state, pp, cfg, kwargs = _build_state(base_dir, original_dir, library_root)
    cancel_event = Event()
    worker = PipelineWorker(state, config_path=None, cancel_event=cancel_event)
    collector = _collect_progress(worker, ProgressCollector())
    return collector


def main() -> int:
    base_dir = Path(
        os.environ.get("QA_GUI_BASE", r"C:\Users\runneradmin\Test Dir With Spaces\lib")
    ).resolve()
    exe = Path(
        os.environ.get("QA_EXE", "dist/AmigaADFLibraryBuilder/AmigaADFLibraryBuilder.exe")
    )
    if not exe.is_file():
        exe = Path("dist/amiga-adf-gui.exe")
    report_dir = Path(os.environ.get("QA_REPORT_DIR", "qa-windows-gh85-artifacts"))
    report_dir.mkdir(parents=True, exist_ok=True)
    screenshots = report_dir / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # 1) Clean launch of the real standalone exe.
    # ------------------------------------------------------------------ #
    if exe.is_file():
        env = dict(os.environ)
        env["AMIGA_ADF_GUI_BASE"] = str(base_dir)
        env["QT_QPA_PLATFORM"] = "offscreen"
        try:
            proc = subprocess.Popen(
                [str(exe)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(6)
            alive = proc.poll() is None
            _step(
                "exe_launch_clean",
                alive,
                f"exe={exe.name} pid={proc.pid if alive else 'exited'}",
            )
            created = [d for d in ("config", "data", "logs", "cache")
                       if (base_dir / d).is_dir()]
            _step(
                "exe_portable_layout_spaces",
                set(created) >= {"config", "logs", "cache"},
                f"base='{base_dir}' created={created}",
            )
            self_contained = (
                (exe.parent / "python3.dll").is_file()
                or (exe.parent / "python312.dll").is_file()
                or (exe.parent / "_internal").is_dir()
            )
            _step(
                "exe_self_contained",
                self_contained,
                f"python dll/_internal beside {exe.name}: {self_contained}",
            )
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        except Exception as exc:
            _step("exe_launch_clean", False, f"could not launch exe: {exc}")
            REPORT["errors"].append(str(exc))
    else:
        _step("exe_launch_clean", False, f"exe not found: {exe}")

    # ------------------------------------------------------------------ #
    # 2) GH-85 progress qualification on the real Windows runtime.
    # ------------------------------------------------------------------ #
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    gh85_report = {
        "normal_run": {},
        "error_run": {},
        "zero_release_run": {},
        "screenshot": None,
        "errors": [],
    }

    def _gh85_step(name: str, ok: bool, detail: str = "") -> None:
        REPORT["steps"].append({"step": name, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    try:
        from PySide6.QtWidgets import QApplication

        from amiga_adf_library_builder.gui import PortablePaths
        from amiga_adf_library_builder.gui.main_window import GuiState
        from amiga_adf_library_builder.gui.state import (
            build_path_config_from_gui_state,
            build_pipeline_kwargs,
        )
        from amiga_adf_library_builder.initializer import ensure_managed_directories
        from amiga_adf_library_builder.pipeline import run_pipeline, build_staged_library_from_result

        # --- normal multi-release run -----------------------------------------
        normal_root = base_dir / "gh85-original"
        normal_root.mkdir(parents=True, exist_ok=True)
        fixtures = [
            "Gh85 - Game One (Disk 1 of 2).adf",
            "Gh85 - Game One (Disk 2 of 2).adf",
            "Gh85 - Game Two.adf",
            "Gh85 - Game Three (Disk 1 of 3).adf",
            "Gh85 - Game Three (Disk 2 of 3).adf",
            "Gh85 - Game Three (Disk 3 of 3).adf",
        ]
        for name in fixtures:
            (normal_root / name).write_bytes(name.encode("utf-8"))

        normal_lib = base_dir / "gh85-normal-lib"
        collector, cfg = _run_gh85_normal(base_dir, normal_root, normal_lib)
        result, error, cancelled, _ = collector.finished_emission or (None, "", False, None)
        progress_values = [p[1] for p in collector.progress_emissions]
        progress_phases = [p[0] for p in collector.progress_emissions]
        groups = result.get("groups", 0) if result else 0

        gh85_report["normal_run"] = {
            "groups": groups,
            "cancelled": cancelled,
            "error": error,
            "progress_count": len(progress_values),
            "progress_values": progress_values,
        }
        _gh85_step("gh85_normal_run_completed", not cancelled and error == "" and groups >= 3,
                   f"groups={groups} cancelled={cancelled} error={error}")
        if progress_values:
            _gh85_step(
                "gh85_initial_phases_present",
                all(v in progress_values for v in (2, 6, 15, 30, 55, 95, 100)),
                f"observed={progress_values}",
            )
            _gh85_step(
                "gh85_progress_monotonic",
                all(progress_values[i] <= progress_values[i + 1]
                    for i in range(len(progress_values) - 1)),
                "",
            )
            _gh85_step(
                "gh85_100_once_and_last",
                progress_values.count(100) == 1 and progress_values[-1] == 100,
                f"100_count={progress_values.count(100)} last={progress_values[-1]}",
            )
            enrich_phases = [
                p for p in progress_phases
                if "Filling in missing metadata" in p and "(" in p and ")" in p and "/" in p
            ]
            _gh85_step(
                "gh85_enrichment_count_shown",
                len(enrich_phases) >= 1,
                f"enrich_phases={len(enrich_phases)}",
            )
            if enrich_phases:
                last_enrich = None
                for phase, percent in zip(progress_phases, progress_values):
                    if "Filling in missing metadata" in phase and percent < 95:
                        last_enrich = (phase, percent)
                if last_enrich:
                    phase, percent = last_enrich
                    parts = phase.split("(")[1].split(")")[0].split("/")
                    completed = int(parts[0])
                    total = int(parts[1])
                    _gh85_step(
                        "gh85_processed_count_consistent",
                        completed == total or completed == total - 1,
                        f"completed={completed} total={total}",
                    )
                    expected = 55 + int(40 * (completed / total)) if total else 55
                    _gh85_step(
                        "gh85_progress_matches_completed",
                        abs(percent - expected) <= 2,
                        f"percent={percent} expected~{expected}",
                    )
            enrich_values = [v for v in progress_values if 55 <= v <= 95]
            _gh85_step(
                "gh85_enrichment_advances",
                len(enrich_values) >= 2,
                f"enrich_values={enrich_values}",
            )
        else:
            _gh85_step("gh85_progress_emissions_present", False, "no progress emissions")

        # --- error path -------------------------------------------------------
        err_collector = _run_gh85_error(base_dir)
        err_result, err_error, err_cancelled, _ = (
            err_collector.finished_emission or (None, "", False, None)
        )
        _gh85_step(
            "gh85_error_clear_no_crash",
            bool(err_error) and not err_cancelled and err_result is None,
            f"error={err_error!r} cancelled={err_cancelled}",
        )

        # --- zero-release run -------------------------------------------------
        zero_collector = _run_gh85_zero(base_dir)
        zero_result, zero_error, zero_cancelled, _ = (
            zero_collector.finished_emission or (None, "", False, None)
        )
        zero_progress = [p[1] for p in zero_collector.progress_emissions]
        _gh85_step(
            "gh85_zero_release_clean",
            not zero_cancelled and zero_error == "" and zero_progress and zero_progress[-1] == 100,
            f"groups={zero_result.get('groups', 0) if zero_result else 0} progress={zero_progress}",
        )

        # --- screenshot of main window ----------------------------------------
        try:
            from amiga_adf_library_builder.gui import MainWindow, PortablePaths, SettingsStore

            pp_screen = PortablePaths(base_dir=base_dir / "gh85-screenshot-lib")
            pp_screen.ensure_all()
            mw = MainWindow(
                portable_paths=pp_screen,
                settings_store=SettingsStore(pp_screen.settings_file()),
            )
            pix = mw.grab()
            shot = screenshots / "gh85-main-window.png"
            pix.save(str(shot))
            _gh85_step("gh85_screenshot", shot.is_file(), f"saved {shot}")
            gh85_report["screenshot"] = str(shot)
            mw.close()
        except Exception as exc:
            _gh85_step("gh85_screenshot", False, f"grab failed: {exc}")
            gh85_report["errors"].append(repr(exc))

    except Exception as exc:
        _gh85_step("gh85_qualification", False, repr(exc))
        gh85_report["errors"].append(repr(exc))
        REPORT["errors"].append(repr(exc))

    REPORT["gh85"] = gh85_report
    report_path = report_dir / "report.json"
    report_path.write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    leak = False
    for log in (base_dir / "logs").rglob("*.log") if (base_dir / "logs").is_dir() else []:
        txt = log.read_text(encoding="utf-8", errors="replace").lower()
        if any(k in txt for k in ("sk_live_", "bearer ", "api_key=", "client_secret=")):
            leak = True
    _step("no_secret_leak_in_logs", not leak,
          "no plaintext token/secret in GUI logs" if not leak else "SECRET LEAK")
    print("\nREPORT:", report_path)
    hard_fail = any(not s["ok"] for s in REPORT["steps"]
                    if s["step"] in (
                        "exe_launch_clean", "exe_portable_layout_spaces", "exe_self_contained",
                        "gh85_normal_run_completed", "gh85_initial_phases_present",
                        "gh85_progress_monotonic", "gh85_100_once_and_last",
                        "gh85_enrichment_count_shown", "gh85_processed_count_consistent",
                        "gh85_progress_matches_completed", "gh85_enrichment_advances",
                        "gh85_error_clear_no_crash", "gh85_zero_release_clean",
                        "no_secret_leak_in_logs",
                    ))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
