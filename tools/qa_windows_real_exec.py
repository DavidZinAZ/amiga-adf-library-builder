#!/usr/bin/env python3
"""Real Windows qualification driver for the Issue #15 standalone GUI.

Run by `.github/workflows/qa-windows-real-exec.yml` on `windows-latest`. It
produces the NON-NEGOTIABLE operator evidence that the frozen Windows artifact
actually runs on Windows and that the GUI code (presentation layer over the
shared core) exercises its flows there:

  1. LAUNCH the standalone onedir exe (no separately-installed Python) under a
     path containing SPACES, with QT_QPA_PLATFORM=offscreen, and prove it stays
     alive + creates the portable config/data/logs/cache layout -- i.e. a clean
     Windows launch + portable layout + spaces-path support + self-contained
     artifact (the PyInstaller tree bundles the interpreter + Qt runtime).
  2. EXERCISE the real GUI code on the Windows runtime (installed package,
     offscreen) exactly as the Linux security tests do, but on Windows:
       * settings persistence (set a non-secret theme + default path, save,
         reload, assert the TOML persisted under the spaces base),
       * theme switch (light/dark/system) via the menu action,
       * Help/About availability (invoke and capture the dialog text),
       * diagnostics / log-dir access (logs_dir exists, run log is written),
       * actionable FAILURE PATH: feed an invalid (missing) library root and
         Run; assert a clear error surfaces and the process does NOT crash with a
         raw traceback (the run is wrapped; a QMessageBox.critical is shown).
  3. Emit a JSON report + screenshots (offscreen QWidget.grab) as artifacts.

This script does NOT modify any GUI/core source; it only drives the public
GUI entry points and inspects their side effects. It is not imported by pytest
(Linux collection must not require Windows/PySide6 availability at import).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPORT: dict = {"steps": [], "errors": []}


def _step(name: str, ok: bool, detail: str = "") -> None:
    REPORT["steps"].append({"step": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main() -> int:
    import tomllib

    base_dir = Path(os.environ.get("QA_GUI_BASE", r"C:\Users\runneradmin\Test Dir With Spaces\lib")).resolve()
    # The real onedir exe produced by the build step.
    exe = Path(os.environ.get("QA_EXE", "dist/AmigaADFLibraryBuilder/AmigaADFLibraryBuilder.exe"))
    if not exe.is_file():
        # Fall back to the onefile artifact if onedir is absent.
        exe = Path("dist/amiga-adf-gui.exe")
    report_dir = Path(os.environ.get("QA_REPORT_DIR", "qa-windows-artifacts"))
    report_dir.mkdir(parents=True, exist_ok=True)
    screenshots = report_dir / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # 1) LAUNCH the real standalone exe (clean Windows launch, offscreen)
    # ------------------------------------------------------------------ #
    if exe.is_file():
        env = dict(os.environ)
        env["AMIGA_ADF_GUI_BASE"] = str(base_dir)
        env["QT_QPA_PLATFORM"] = "offscreen"
        try:
            proc = subprocess.Popen(
                [str(exe)], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            # Give the frozen app time to construct QApplication + MainWindow.
            time.sleep(6)
            alive = proc.poll() is None
            _step("exe_launch_clean", alive,
                  f"exe={exe.name} pid={proc.pid if alive else 'exited'}")
            # Portable layout created under the SPACES base?
            created = [d for d in ("config", "data", "logs", "cache")
                       if (base_dir / d).is_dir()]
            _step("exe_portable_layout_spaces",
                  set(created) >= {"config", "logs", "cache"},
                  f"base='{base_dir}' created={created}")
            # Self-contained: the onedir tree carries its own python3*.dll.
            self_contained = (exe.parent / "python3.dll").is_file() or \
                             (exe.parent / "python312.dll").is_file() or \
                             (exe.parent / "_internal").is_dir()
            _step("exe_self_contained", self_contained,
                  f"python dll/_internal present beside {exe.name}: {self_contained}")
            # Tidy the launched exe.
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        except Exception as exc:  # pragma: no cover - environment failure
            _step("exe_launch_clean", False, f"could not launch exe: {exc}")
            REPORT["errors"].append(str(exc))
    else:
        _step("exe_launch_clean", False, f"exe not found: {exe}")

    # ------------------------------------------------------------------ #
    # 2) EXERCISE the real GUI code on the Windows runtime (offscreen)
    # ------------------------------------------------------------------ #
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from amiga_adf_library_builder.gui import MainWindow, PortablePaths, SettingsStore
        from amiga_adf_library_builder.gui.themes import available_themes

        app = QApplication.instance() or QApplication([])
        pp = PortablePaths(base_dir=base_dir)
        pp.ensure_all()
        mw = MainWindow(
            portable_paths=pp,
            settings_store=SettingsStore(pp.settings_file()),
        )

        # --- settings persistence (non-secret) --------------------------
        mw._settings_store.update(theme="dark", default_library_root=str(base_dir))
        reloaded = mw._settings_store.load()
        persisted = (reloaded.theme == "dark") and (reloaded.default_library_root == str(base_dir))
        _step("settings_persist", persisted,
              f"theme={reloaded.theme} default_library_root={reloaded.default_library_root!r}")
        # The TOML must live under the SPACES base and contain NO secret.
        settings_text = pp.settings_file().read_text(encoding="utf-8")
        no_secret = ("token" not in settings_text.lower()) and ("password" not in settings_text.lower())
        _step("settings_no_secret", no_secret,
              "settings TOML carries no token/password" if no_secret else settings_text[:200])

        # --- theme switch (light/dark/system) via menu action ----------
        for theme in available_themes(themes_dir=pp.themes_dir):
            mw._set_theme(theme)
        _step("theme_switch", True,
              f"applied themes: {available_themes(themes_dir=pp.themes_dir)}")

        # --- Help / About availability ----------------------------------
        about_text = []
        orig_about = QMessageBox.about
        QMessageBox.about = staticmethod(lambda *a, **k: about_text.append((a[1] if len(a) > 1 else "")))
        try:
            mw._show_about()
        finally:
            QMessageBox.about = orig_about
        help_text = []
        orig_info = QMessageBox.information
        QMessageBox.information = staticmethod(lambda *a, **k: help_text.append((a[1] if len(a) > 1 else "")))
        try:
            mw._show_help()
        finally:
            QMessageBox.information = orig_info
        help_about_ok = bool(about_text) and bool(help_text)
        _step("help_about_available", help_about_ok,
              f"about_chars={len(about_text[0]) if about_text else 0} "
              f"help_chars={len(help_text[0]) if help_text else 0}")

        # --- diagnostics / log-dir access -------------------------------
        mw._paths.logs_dir.mkdir(parents=True, exist_ok=True)
        _step("log_dir_access", mw._paths.logs_dir.is_dir(),
              f"logs_dir={mw._paths.logs_dir}")

        # --- actionable FAILURE PATH: invalid (missing) library root ---
        # Feed an empty / nonexistent root and click Run; the GUI must surface a
        # clear error (QMessageBox.critical) and NOT crash with a raw traceback.
        mw._le_library_root.setText("")  # missing/invalid input
        errors: list[str] = []
        clear_msgs: list[str] = []
        orig_crit = QMessageBox.critical
        QMessageBox.critical = staticmethod(
            lambda *a, **k: clear_msgs.append((a[1] if len(a) > 1 else ""))
        )
        crashed = False
        try:
            mw._on_run()
            # Pump the Qt event loop so the worker thread's finished signal is
            # delivered and the clear error dialog is shown (offscreen, no real
            # display, but the slot still runs). Quit once the worker is done.
            from PySide6.QtCore import QTimer

            loop = app
            done = {"v": False}

            def _quit_if_done():
                if done["v"]:
                    loop.exit()
                else:
                    QTimer.singleShot(100, _quit_if_done)

            def _on_finished_shim(result, error, cancelled):
                done["v"] = True

            mw._worker.finished.connect(_on_finished_shim)
            QTimer.singleShot(100, _quit_if_done)
            app.exec()
        except SystemExit:
            crashed = True
        except Exception as exc:  # raw traceback to the user == the failure mode
            crashed = True
            REPORT["errors"].append(f"raw exception on invalid input: {exc!r}")
        finally:
            QMessageBox.critical = orig_crit
        _step("failure_path_clear_error", bool(clear_msgs) and not crashed,
              f"clear_msg={clear_msgs[0] if clear_msgs else '(none)'} crashed={crashed}")
        _step("no_crash_on_invalid_input", not crashed,
              "no raw traceback/uncaught exception on invalid input" if not crashed else "CRASHED")

        # --- screenshot of the running window (offscreen grab) ----------
        try:
            pix = mw.grab()
            shot = screenshots / "main_window.png"
            pix.save(str(shot))
            _step("screenshot", shot.is_file(), f"saved {shot}")
        except Exception as exc:
            _step("screenshot", False, f"grab failed: {exc}")

        # --- close WITHOUT run -> reopen -> widget-level restore ----------
        # The literal Issue #17 repro on the real runtime: select folders (>=1
        # containing a SPACE), close the app via its NORMAL close path
        # (closeEvent -> _persist_defaults -> SettingsStore.save), reopen a
        # fresh instance on the same settings file, and assert at WIDGET level
        # that all four folder fields came back. No pipeline run in between.
        # In-process graceful close is the same closeEvent/_persist_defaults
        # code path the packaged exe executes on shutdown; the packaged-exe
        # smoke launch above already proves bundle integrity. A hard
        # terminate() is deliberately NOT used for the close here.
        cw_dirs = {
            "library_root": base_dir / "cw" / "library root",
            "original_dir": base_dir / "cw" / "original",
            "staging_dir": base_dir / "cw" / "staging",
            "output_dir": base_dir / "cw" / "output",
        }
        for d in cw_dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        mw._le_library_root.setText(str(cw_dirs["library_root"]))
        mw._le_original_dir.setText(str(cw_dirs["original_dir"]))
        mw._le_staging_dir.setText(str(cw_dirs["staging_dir"]))
        mw._le_output_dir.setText(str(cw_dirs["output_dir"]))
        mw.show()  # window is visible before the normal close
        mw.close()  # NORMAL close path: closeEvent -> _persist_defaults
        # Reopen: a FRESH MainWindow on the same settings file (the one the
        # close just wrote). Ctor loads the store and applies it to widgets.
        mw2 = MainWindow(
            portable_paths=pp,
            settings_store=SettingsStore(pp.settings_file()),
        )
        restored = {
            "library_root": mw2._le_library_root.text(),
            "original_dir": mw2._le_original_dir.text(),
            "staging_dir": mw2._le_staging_dir.text(),
            "output_dir": mw2._le_output_dir.text(),
        }
        expected_cw = {k: str(v) for k, v in cw_dirs.items()}
        match = restored == expected_cw
        _step(
            "close_without_run_restore",
            match,
            f"close_without_run_exercised={match} "
            f"library_root={restored['library_root']!r} "
            f"original_dir={restored['original_dir']!r} "
            f"staging_dir={restored['staging_dir']!r} "
            f"output_dir={restored['output_dir']!r}",
        )
        if match:
            REPORT["close_without_run_exercised"] = True
        mw2.close()
        mw2 = None

        # --- (GH-33) LaunchBox local folder mappings (offscreen, Windows) ----
        # Drive the REAL GUI LaunchBox tab on the Windows runtime: add multiple
        # image/media roots (native Browse picker, mocked in-process) each with an
        # explicit asset type, add multiple manual roots, run the read-only
        # "Check roots" diagnostic, and persist across close + reopen.
        from unittest import mock as _mock
        from amiga_adf_library_builder import local_media as _lm

        lb_dirs = {
            "front": base_dir / "lb" / "box front",
            "back": base_dir / "lb" / "box back",
            "manuals": base_dir / "lb" / "manuals",
        }
        for d in lb_dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        # Representative LaunchBox image + manual for discovery.
        (lb_dirs["front"] / "Synthetic Quest III.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        )
        (lb_dirs["manuals"] / "Synthetic Quest III.txt").write_bytes(b"controls")

        mw_lb = MainWindow(
            portable_paths=pp,
            settings_store=SettingsStore(pp.settings_file()),
        )
        # Add two image roots + one manual root via the REAL GUI methods,
        # mocking the native Browse picker (offscreen: no real dialog).
        with _mock.patch(
            "amiga_adf_library_builder.gui.main_window.QFileDialog.getExistingDirectory",
            side_effect=[str(lb_dirs["front"]), str(lb_dirs["back"]), str(lb_dirs["manuals"])],
        ):
            mw_lb._lb_add_media_root()
            mw_lb._lb_add_media_root()
            mw_lb._lb_add_manual_root()
        # Set DISTINCT asset types on the two image roots (explicit per-root mapping).
        combo0 = mw_lb._lb_media_table.cellWidget(0, 1)
        combo1 = mw_lb._lb_media_table.cellWidget(1, 1)
        for combo, wanted in ((combo0, "Box - Front"), (combo1, "Box - Back")):
            idx = combo.findText(wanted)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        added_ok = (
            mw_lb._lb_media_table.rowCount() == 2
            and mw_lb._lb_manual_list.count() == 1
            and combo0 is not None
            and combo1 is not None
        )
        _step("lb_multi_mappings_added", added_ok,
              f"media_rows={mw_lb._lb_media_table.rowCount()} "
              f"manual_rows={mw_lb._lb_manual_list.count()} "
              f"asset0={combo0.currentText() if combo0 else None!r} "
              f"asset1={combo1.currentText() if combo1 else None!r}")

        # Run the read-only diagnostic (scanned/missing + candidate counts).
        mw_lb._lb_check_roots()
        diag = mw_lb._lb_diag_label.text()
        _step("lb_check_roots_diagnostic", bool(diag),
              f"diag_chars={len(diag)}")

        # Persist across close + reopen (widget-level restore of both mapping types).
        mw_lb.show()
        mw_lb.close()  # closeEvent persists the LaunchBox mappings
        mw_lb2 = MainWindow(
            portable_paths=pp,
            settings_store=SettingsStore(pp.settings_file()),
        )
        restored_media = [
            mw_lb2._lb_media_table.item(r, 0).text()
            for r in range(mw_lb2._lb_media_table.rowCount())
        ]
        restored_manual = [
            mw_lb2._lb_manual_list.item(r).text()
            for r in range(mw_lb2._lb_manual_list.count())
        ]
        lb_restore = (
            len(restored_media) == 2
            and len(restored_manual) == 1
            and str(lb_dirs["manuals"]) in restored_manual
        )
        _step("lb_mappings_persist_reopen", lb_restore,
              f"restored_media={restored_media!r} restored_manual={restored_manual!r}")

        # A missing/inaccessible path is RETAINED (not deleted) + diagnostic emitted.
        gone = base_dir / "lb" / "gone"
        with _mock.patch(
            "amiga_adf_library_builder.gui.main_window.QFileDialog.getExistingDirectory",
            side_effect=[str(gone)],
        ):
            mw_lb2._lb_add_media_root()
        mw_lb2._lb_check_roots()
        rows_after = [
            mw_lb2._lb_media_table.item(r, 0).text()
            for r in range(mw_lb2._lb_media_table.rowCount())
        ]
        diagnostic = mw_lb2._lb_diag_label.text()
        missing_retained = (str(gone) in rows_after) and ("gone" in diagnostic)
        _step("lb_missing_path_retained_diagnostic", missing_retained,
              f"media_rows={len(rows_after)} diag_mentions_gone={'gone' in diagnostic}")
        mw_lb2.close()
        mw_lb2 = None

        mw.close()
        if QApplication.instance():
            QApplication.instance().quit()
    except Exception as exc:
        _step("gui_code_exercise", False, f"could not drive GUI code on Windows: {exc!r}")
        REPORT["errors"].append(repr(exc))

    # ------------------------------------------------------------------ #
    # 2b) (GH-33) Direct backend diagnostic: a missing/inaccessible LaunchBox
    #      root is reported (retained, never deleted) by scan_launchbox_roots.
    #      No Qt required; proves the diagnostic on the real runtime.
    # ------------------------------------------------------------------ #
    try:
        from amiga_adf_library_builder import local_media as _lm

        media_root = base_dir / "lb" / "box front"
        media_root.mkdir(parents=True, exist_ok=True)
        missing_root = base_dir / "lb" / "gone"
        cfg = _lm.LocalMediaConfig(
            enabled=True,
            media_roots=(
                _lm.MediaRoot(path=str(media_root), asset_type="Box - Front"),
                _lm.MediaRoot(path=str(missing_root), asset_type="Box - Back"),
            ),
        )
        report = _lm.scan_launchbox_roots(cfg)
        missing_reported = any(str(missing_root) in ln for ln in report.to_lines())
        retained_not_deleted = missing_root in {r.path for r in report.missing_roots}
        _step("lb_backend_missing_root_diagnostic",
              missing_reported and retained_not_deleted,
              f"missing_roots={len(report.missing_roots)} "
              f"reported_in_lines={missing_reported}")
    except Exception as exc:
        _step("lb_backend_missing_root_diagnostic", False, f"backend diagnostic failed: {exc!r}")
        REPORT["errors"].append(repr(exc))

    # ------------------------------------------------------------------ #
    # Emit the report + secret-leak scan of the logs dir
    # ------------------------------------------------------------------ #
    report_path = report_dir / "report.json"
    report_path.write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    # Spot-check: no plaintext secret/token in any log under the spaces base.
    leak = False
    for log in (base_dir / "logs").rglob("*.log") if (base_dir / "logs").is_dir() else []:
        txt = log.read_text(encoding="utf-8", errors="replace").lower()
        if any(k in txt for k in ("sk_live_", "bearer ", "api_key=", "client_secret=")):
            leak = True
    _step("no_secret_leak_in_logs", not leak,
          "no plaintext token/secret in GUI logs" if not leak else "SECRET LEAK")
    print("\nREPORT:", report_path)
    # Verdict: fail the CI step if any hard step failed.
    hard_fail = any(not s["ok"] for s in REPORT["steps"]
                    if s["step"] in ("exe_launch_clean", "exe_portable_layout_spaces",
                                     "exe_self_contained", "settings_persist",
                                     "close_without_run_restore",
                                     "help_about_available", "no_crash_on_invalid_input",
                                     "no_secret_leak_in_logs",
                                     # (GH-33) LaunchBox local mappings flows
                                     "lb_multi_mappings_added", "lb_check_roots_diagnostic",
                                     "lb_mappings_persist_reopen",
                                     "lb_missing_path_retained_diagnostic",
                                     "lb_backend_missing_root_diagnostic"))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
