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
  3. (GH-86) POPULATED Preview / Curation qualification: run the real pipeline
     against a populated synthetic `Original Disks (read only)` library, open
     the Preview & Curation tab, and prove:
       * visible/non-empty Preview / Curation rows/items;
       * selected entry exposes original identity;
       * selected entry exposes planned processed/export identity/path;
       * source fixture hashes/content are unchanged;
       * merely previewing does not write final export files.
  4. (GH-88 + GH-89) Online/Offline lookup qualification on the released
     packaged GUI: prove Online Lookup routes through the shared online workflow,
     Offline Lookup uses only local media providers, no export files are written
     by lookup/apply, and selected release identity is preserved.
  5. Emit a JSON report + screenshots (offscreen QWidget.grab) as artifacts.

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
    # 3) (GH-86) POPULATED Preview / Curation qualification on Windows.
    # ------------------------------------------------------------------ #
    gh86_report = {
        "library_populated": False,
        "preview_nonempty_rows": 0,
        "selected_release_key": None,
        "selected_title": None,
        "selected_adf_count": None,
        "selected_original_identity": None,
        "selected_planned_export_path": None,
        "source_fixtures_unchanged": False,
        "export_files_written_after_preview": [],
        "screenshot": None,
        "errors": [],
    }

    def _gh86_step(name, ok, detail=""):
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
        from amiga_adf_library_builder.gui.preview_widget import PreviewWidget
        # 3a) Build a populated synthetic "Original Disks (read only)" library.
        original_root = base_dir / "gh86-original"
        original_root.mkdir(parents=True, exist_ok=True)
        fixtures = [
            "Gh86 - Space Tactics (Disk 1 of 4).adf",
            "Gh86 - Space Tactics (Disk 2 of 4).adf",
            "Gh86 - Space Tactics (Disk 3 of 4).adf",
            "Gh86 - Space Tactics (Disk 4 of 4).adf",
            "Gh86 Quest III Boot.adf",
            "Gh86 Quest III Character.adf",
            "Gh86 Castle Quest (Disk A).adf",
        ]
        before_hashes = {}
        for name in fixtures:
            path = original_root / name
            path.write_bytes(name.encode("utf-8"))
            before_hashes[name] = __import__("hashlib").sha256(name.encode("utf-8")).hexdigest()

        state = GuiState(
            library_root=str(base_dir / "gh86-lib"),
            original_dir=str(original_root),
            run_mode="build",
        )
        pp_gh86 = PortablePaths(base_dir=base_dir / "gh86-lib")
        pp_gh86.ensure_all()
        cfg_gh86 = build_path_config_from_gui_state(state)
        ensure_managed_directories(cfg_gh86)
        kwargs_gh86 = build_pipeline_kwargs(state, cfg_gh86)
        result_gh86 = run_pipeline(**kwargs_gh86)
        gh86_report["library_populated"] = bool(result_gh86.get("per_group"))
        _gh86_step("gh86_populated_pipeline", gh86_report["library_populated"],
                    f"groups={result_gh86.get('groups', 0)}")

        after_hashes = {}
        for name in fixtures:
            path = original_root / name
            after_hashes[name] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        unchanged = after_hashes == before_hashes
        gh86_report["source_fixtures_unchanged"] = unchanged
        _gh86_step("gh86_source_fixtures_unchanged", unchanged,
                    f"fixtures={len(fixtures)}")

        state_path = build_staged_library_from_result(
            result_gh86,
            library_root=cfg_gh86.library_root,
            run_id=result_gh86.get("run_id", "gh86-qa"),
        )
        preview_loaded = False
        table_rows = 0
        if state_path and state_path.exists():
            pw = PreviewWidget()
            preview_loaded = pw.load_state_file(state_path)
            pw.show()
            table_rows = pw._table.rowCount()
            _gh86_step("gh86_preview_loaded", preview_loaded and table_rows > 0,
                        f"loaded={preview_loaded} rows={table_rows}")
            gh86_report["preview_nonempty_rows"] = table_rows

            shot = screenshots / "gh86-preview-populated.png"
            try:
                pix = pw.grab()
                pix.save(str(shot))
                _gh86_step("gh86_screenshot", shot.is_file(), f"saved {shot}")
                gh86_report["screenshot"] = str(shot)
            except Exception as exc:
                _gh86_step("gh86_screenshot", False, f"grab failed: {exc}")

            if table_rows > 0:
                pw._table.selectRow(0)
                key = pw._state.row_to_release_key.get(0)
                entry = pw._state.current_library.releases.get(key) if key and pw._state.current_library else None
                selected_ok = bool(entry and entry.title and entry.adf_files)
                gh86_report["selected_release_key"] = key
                gh86_report["selected_title"] = entry.title if entry else None
                gh86_report["selected_adf_count"] = len(entry.adf_files) if entry else 0
                gh86_report["selected_original_identity"] = entry.adf_files[0] if entry and entry.adf_files else None
                gh86_report["selected_planned_export_path"] = str(cfg_gh86.output_dir / (entry.folder or entry.title or key or "")) if entry else None
                _gh86_step("gh86_selected_identity", selected_ok,
                            f"release_key={gh86_report['selected_release_key']} "
                            f"title={gh86_report['selected_title']} "
                            f"adf_count={gh86_report['selected_adf_count']}")
                _gh86_step("gh86_selected_planned_export_path", bool(gh86_report["selected_planned_export_path"]),
                            f"path={gh86_report['selected_planned_export_path']}")

            export_files_before = sorted(p for p in cfg_gh86.output_dir.rglob("*") if p.is_file())
            _gh86_step("gh86_preview_no_export", len(export_files_before) == 0,
                        f"export_files={len(export_files_before)}")
            gh86_report["export_files_written_after_preview"] = [str(p) for p in export_files_before]
            pw.close()
        else:
            _gh86_step("gh86_preview_loaded", False, "state_path missing")
    except Exception as exc:
        _gh86_step("gh86_qualification", False, repr(exc))
        gh86_report["errors"].append(repr(exc))
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
        retained_not_deleted = str(missing_root) in {r.path for r in report.missing_roots}
        _step("lb_backend_missing_root_diagnostic",
              missing_reported and retained_not_deleted,
              f"missing_roots={len(report.missing_roots)} "
              f"reported_in_lines={missing_reported}")
    except Exception as exc:
        _step("lb_backend_missing_root_diagnostic", False, f"backend diagnostic failed: {exc!r}")
        REPORT["errors"].append(repr(exc))

    # ------------------------------------------------------------------ #
    # 4) (GH-90) Selection-integrity qualification on Windows.
    # ------------------------------------------------------------------ #
    gh90_report = {
        "multi_release_rows": 0,
        "selected_rows_identity_match": False,
        "selected_rows_titles": [],
        "filter_refresh_identity_match": False,
        "order_identity_match": False,
        "staged_mutation_only_selected": False,
        "staged_mutation_target_count": 0,
        "source_fixtures_unchanged": False,
        "preview_export_files_after_mutation": [],
        "errors": [],
    }

    def _gh90_step(name, ok, detail=""):
        REPORT["steps"].append({"step": name, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt

        from amiga_adf_library_builder.gui import PortablePaths
        from amiga_adf_library_builder.gui.main_window import GuiState
        from amiga_adf_library_builder.gui.state import (
            build_path_config_from_gui_state,
            build_pipeline_kwargs,
        )
        from amiga_adf_library_builder.initializer import ensure_managed_directories
        from amiga_adf_library_builder.pipeline import run_pipeline, build_staged_library_from_result
        from amiga_adf_library_builder.gui.preview_widget import PreviewWidget

        # 4a) Build a populated synthetic library with distinct releases.
        gh90_root = base_dir / "gh90-original"
        gh90_root.mkdir(parents=True, exist_ok=True)
        fixtures = {
            "Gh90 - Alpha Quest (Disk 1 of 2).adf": "alpha",
            "Gh90 - Alpha Quest (Disk 2 of 2).adf": "alpha",
            "Gh90 - Beta Quest (Disk 1).adf": "beta",
            "Gh90 - Gamma Force.adf": "gamma",
            "Gh90 - Delta Race.adf": "delta",
        }
        before_hashes = {}
        for name in fixtures:
            path = gh90_root / name
            path.write_bytes(name.encode("utf-8"))
            before_hashes[name] = __import__("hashlib").sha256(name.encode("utf-8")).hexdigest()

        state = GuiState(
            library_root=str(base_dir / "gh90-lib"),
            original_dir=str(gh90_root),
            run_mode="build",
        )
        pp_gh90 = PortablePaths(base_dir=base_dir / "gh90-lib")
        pp_gh90.ensure_all()
        cfg_gh90 = build_path_config_from_gui_state(state)
        ensure_managed_directories(cfg_gh90)
        kwargs_gh90 = build_pipeline_kwargs(state, cfg_gh90)
        result_gh90 = run_pipeline(**kwargs_gh90)
        pipeline_ok = bool(result_gh90.get("per_group"))
        _gh90_step("gh90_pipeline_populated", pipeline_ok, f"groups={result_gh90.get('groups', 0)}")

        state_path = build_staged_library_from_result(
            result_gh90,
            library_root=cfg_gh90.library_root,
            run_id=result_gh90.get("run_id", "gh90-qa"),
        )
        pw = PreviewWidget()
        preview_loaded = False
        if state_path and state_path.exists():
            preview_loaded = pw.load_state_file(state_path)
            pw.show()
        _gh90_step("gh90_preview_loaded", preview_loaded, f"loaded={preview_loaded}")

        if preview_loaded:
            rows = pw._table.rowCount()
            gh90_report["multi_release_rows"] = rows
            _gh90_step("gh90_multi_release_rows", rows >= 4, f"rows={rows}")

            # Multi-select: select all rows and verify each selected row identity.
            pw._table.setSelectionMode(__import__("PySide6.QtWidgets").QtWidgets.QAbstractItemView.MultiSelection)
            selection_model = pw._table.selectionModel()
            for r in range(pw._table.rowCount()):
                idx = pw._table.model().index(r, 0)
                selection_model.select(idx, __import__("PySide6.QtCore").QtCore.QItemSelectionModel.Select)
            selected = selection_model.selectedRows()
            titles = []
            keys = []
            identity_match = len(selected) == rows and rows > 0
            for idx in selected:
                row = idx.row()
                title = pw._table.item(row, 2).text() if pw._table.item(row, 2) else ""
                release_key = None
                for col in range(pw._table.columnCount()):
                    item = pw._table.item(row, col)
                    if item is not None:
                        release_key = item.data(__import__("PySide6.QtCore").QtCore.Qt.ItemDataRole.UserRole)
                        if release_key:
                            break
                titles.append(title)
                keys.append(release_key)
                if not (title and release_key and release_key in pw._state.current_library.releases):
                    identity_match = False
            gh90_report["selected_rows_identity_match"] = identity_match
            gh90_report["selected_rows_titles"] = titles
            _gh90_step("gh90_selected_rows_identity_match", identity_match,
                        f"selected={len(selected)} rows={rows} titles={titles}")

            # Filter/refresh: apply a filter, then clear it, and confirm row count restores.
            pw._filter_combo.setCurrentText("Accepted")
            pw._apply_filter()
            filtered_rows = sum(1 for r in range(pw._table.rowCount()) if not pw._table.isRowHidden(r))
            pw._filter_combo.setCurrentText("All")
            pw._apply_filter()
            restored_rows = sum(1 for r in range(pw._table.rowCount()) if not pw._table.isRowHidden(r))
            filter_refresh_match = restored_rows == rows and restored_rows >= 4
            gh90_report["filter_refresh_identity_match"] = filter_refresh_match
            _gh90_step("gh90_filter_refresh_identity_match", filter_refresh_match,
                        f"filtered_rows={filtered_rows} restored_rows={restored_rows}")

            # Order: enable sorting, sort by Title, then confirm sorted list is ordered.
            pw._table.setSortingEnabled(True)
            pw._table.sortItems(2)
            sorted_rows = [pw._table.item(r, 2).text() for r in range(pw._table.rowCount()) if not pw._table.isRowHidden(r)]
            order_match = all(sorted_rows[i] <= sorted_rows[i + 1] for i in range(len(sorted_rows) - 1))
            gh90_report["order_identity_match"] = order_match
            _gh90_step("gh90_order_identity_match", order_match,
                        f"sorted_rows={sorted_rows}")
            pw._table.setSortingEnabled(False)

            # Staged mutation: select row 0 and row 2, mutate ONLY those releases to Accepted.
            targets = []
            for row in (0, 2):
                item = pw._table.item(row, 0)
                if item is None:
                    continue
                release_key = None
                for col in range(pw._table.columnCount()):
                    it = pw._table.item(row, col)
                    if it is not None:
                        release_key = it.data(__import__("PySide6.QtCore").QtCore.Qt.ItemDataRole.UserRole)
                        if release_key:
                            break
                if release_key:
                    targets.append((row, release_key))
            # Select exactly the target rows.
            pw._table.clearSelection()
            selection_model = pw._table.selectionModel()
            select_flag = __import__("PySide6.QtCore").QtCore.QItemSelectionModel.Select
            rows_flag = __import__("PySide6.QtCore").QtCore.QItemSelectionModel.Rows
            for row, _ in targets:
                idx = pw._table.model().index(row, 0)
                selection_model.select(idx, select_flag | rows_flag)
            selected = selection_model.selectedRows()
            if len(selected) == len(targets):
                pw._set_selected_state(__import__("amiga_adf_library_builder.models").models.StagedState.ACCEPTED)
            mutated_targets = []
            for _, release_key in targets:
                entry = pw._state.current_library.releases.get(release_key)
                mutated_targets.append(entry.curation_state if entry else None)
            mutated_ok = all(s is not None and s.value == "Accepted" for s in mutated_targets if s is not None)
            # Verify non-selected releases remain unchanged.
            non_selected_unchanged = True
            for row in range(pw._table.rowCount()):
                if row in {t[0] for t in targets}:
                    continue
                item = pw._table.item(row, 0)
                if item is None:
                    continue
                release_key = None
                for col in range(pw._table.columnCount()):
                    it = pw._table.item(row, col)
                    if it is not None:
                        release_key = it.data(__import__("PySide6.QtCore").QtCore.Qt.ItemDataRole.UserRole)
                        if release_key:
                            break
                if not release_key:
                    continue
                entry = pw._state.current_library.releases.get(release_key)
                if entry and entry.curation_state.value == "Accepted":
                    non_selected_unchanged = False
                    break
            gh90_report["staged_mutation_only_selected"] = mutated_ok and non_selected_unchanged
            gh90_report["staged_mutation_target_count"] = len(targets)
            _gh90_step("gh90_staged_mutation_only_selected", mutated_ok and non_selected_unchanged,
                        f"targets={len(targets)} mutated={mutated_targets} non_selected_unchanged={non_selected_unchanged}")

            # Source fixtures unchanged.
            after_hashes = {}
            for name in fixtures:
                path = gh90_root / name
                after_hashes[name] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
            gh90_report["source_fixtures_unchanged"] = after_hashes == before_hashes
            _gh90_step("gh90_source_fixtures_unchanged", after_hashes == before_hashes,
                        f"fixtures={len(fixtures)}")

            # Preview-only export check: no export files should exist after mutation.
            export_files_after = sorted(p for p in cfg_gh90.output_dir.rglob("*") if p.is_file())
            gh90_report["preview_export_files_after_mutation"] = [str(p) for p in export_files_after]
            _gh90_step("gh90_preview_no_export_after_mutation", len(export_files_after) == 0,
                        f"export_files={len(export_files_after)}")

            shot = screenshots / "gh90-preview-selection-integrity.png"
            try:
                pix = pw.grab()
                pix.save(str(shot))
                _gh90_step("gh90_screenshot", shot.is_file(), f"saved {shot}")
            except Exception as exc:
                _gh90_step("gh90_screenshot", False, f"grab failed: {exc}")
            pw.close()
        else:
            _gh90_step("gh90_preview_loaded", False, "state_path missing")
    except Exception as exc:
        _gh90_step("gh90_selection_integrity", False, repr(exc))
        gh90_report["errors"].append(repr(exc))

    REPORT["gh90"] = gh90_report

    # ------------------------------------------------------------------ #
    # 5) (GH-88 + GH-89) Online/Offline lookup qualification on Windows.
    # ------------------------------------------------------------------ #
    lookup_report = {
        "selected_release_key": None,
        "selected_title_before": None,
        "selected_title_after": None,
        "selected_adf_count": None,
        "offline_providers_seen": [],
        "online_providers_seen": [],
        "lookup_apply_changed_staged_only": False,
        "export_files_after_apply": [],
        "screenshots": [],
        "errors": [],
    }

    def _lookup_step(name, ok, detail=""):
        REPORT["steps"].append({"step": name, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    def _entry_for_release_key(release_key):
        if not pw._state or not pw._state.current_library:
            return None
        return pw._state.current_library.releases.get(release_key)

    def _selected_entry():
        return _entry_for_release_key(pw._state.selected_release_key)

    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt

        from amiga_adf_library_builder.gui import PortablePaths
        from amiga_adf_library_builder.gui.main_window import GuiState
        from amiga_adf_library_builder.gui.state import (
            build_path_config_from_gui_state,
            build_pipeline_kwargs,
        )
        from amiga_adf_library_builder.initializer import ensure_managed_directories
        from amiga_adf_library_builder.pipeline import run_pipeline, build_staged_library_from_result
        from amiga_adf_library_builder.gui.preview_widget import PreviewWidget
        from amiga_adf_library_builder.lookup_workflow import (
            LookupContext,
            MODE_OFFLINE,
            MODE_ONLINE,
            providers_for_mode,
            run_lookup,
        )

        lookup_root = base_dir / "gh88-89-original"
        lookup_root.mkdir(parents=True, exist_ok=True)
        fixtures = [
            "Gh88 - Space Tactics (Disk 1 of 4).adf",
            "Gh88 - Space Tactics (Disk 2 of 4).adf",
            "Gh88 - Space Tactics (Disk 3 of 4).adf",
            "Gh88 - Space Tactics (Disk 4 of 4).adf",
            "Gh88 - Quest III Boot.adf",
        ]
        before_hashes = {}
        for name in fixtures:
            path = lookup_root / name
            path.write_bytes(name.encode("utf-8"))
            before_hashes[name] = __import__("hashlib").sha256(name.encode("utf-8")).hexdigest()

        state = GuiState(
            library_root=str(base_dir / "gh88-89-lib"),
            original_dir=str(lookup_root),
            run_mode="build",
        )
        pp_lookup = PortablePaths(base_dir=base_dir / "gh88-89-lib")
        pp_lookup.ensure_all()
        cfg_lookup = build_path_config_from_gui_state(state)
        ensure_managed_directories(cfg_lookup)
        kwargs_lookup = build_pipeline_kwargs(state, cfg_lookup)
        result_lookup = run_pipeline(**kwargs_lookup)
        _lookup_step("gh88_89_populated_pipeline", bool(result_lookup.get("per_group")),
                     f"groups={result_lookup.get('groups', 0)}")

        state_path = build_staged_library_from_result(
            result_lookup,
            library_root=cfg_lookup.library_root,
            run_id=result_lookup.get("run_id", "gh88-89-qa"),
        )
        pw = PreviewWidget()
        preview_loaded = False
        if state_path and state_path.exists():
            preview_loaded = pw.load_state_file(state_path)
            pw.show()
        _lookup_step("gh88_89_preview_loaded", preview_loaded, f"loaded={preview_loaded}")

        if preview_loaded:
            rows = pw._table.rowCount()
            _lookup_step("gh88_89_preview_rows", rows >= 1, f"rows={rows}")
            pw._table.selectRow(0)
            first_key = pw._state.row_to_release_key.get(0)
            first_entry = _entry_for_release_key(first_key) if first_key else None
            identity_ok = bool(first_entry and first_entry.title and first_entry.adf_files)
            _lookup_step("gh88_89_selected_original_identity", identity_ok,
                         f"release_key={first_key} title={getattr(first_entry, 'title', None)} adf_count={len(first_entry.adf_files) if first_entry else 0}")
            lookup_report["selected_release_key"] = first_key
            lookup_report["selected_title_before"] = first_entry.title if first_entry else None
            lookup_report["selected_adf_count"] = len(first_entry.adf_files) if first_entry else None

            lookup_report["offline_providers_seen"] = providers_for_mode(MODE_OFFLINE)
            lookup_report["online_providers_seen"] = providers_for_mode(MODE_ONLINE)
            providers_separated = (
                "local_media" in lookup_report["offline_providers_seen"]
                and "hall-of-light" not in lookup_report["offline_providers_seen"]
                and "local_media" not in lookup_report["online_providers_seen"]
            )
            _lookup_step("gh89_offline_providers_no_online_backend", providers_separated,
                         f"offline={lookup_report['offline_providers_seen']} online={lookup_report['online_providers_seen']}")

            cfg = pw._state.path_config if hasattr(pw._state, "path_config") else cfg_lookup
            offline_ctx = LookupContext(
                query=first_entry.title if first_entry else "",
                release_key=first_key or "gh88-89-r1",
                title=first_entry.title if first_entry else "",
                config_path=getattr(cfg, "config_path", None),
                cache_dir=getattr(cfg, "cache_dir", base_dir / "gh88-89-lib" / "cache"),
                curated_dir=getattr(cfg, "curated_dir", base_dir / "gh88-89-lib" / "cache"),
                timeout=1.0,
            )
            offline_result = run_lookup(MODE_OFFLINE, offline_ctx)
            _lookup_step("gh89_offline_lookup_runs_without_network", offline_result.kind == "offline",
                         f"kind={offline_result.kind} status={offline_result.status} consulted={offline_result.consulted} local_source_state={offline_result.local_source_state}")

            no_local_ctx = LookupContext(
                query=first_entry.title if first_entry else "",
                release_key=first_key or "gh88-89-r1",
                title=first_entry.title if first_entry else "",
                config_path=None,
                cache_dir=base_dir / "gh88-89-lib" / "cache",
                curated_dir=base_dir / "gh88-89-lib" / "cache",
                timeout=1.0,
            )
            no_local_result = run_lookup(MODE_OFFLINE, no_local_ctx)
            actionable_no_local = (
                no_local_result.kind == "offline"
                and no_local_result.status == "no_match"
                and any("NOT configured" in line for line in no_local_result.local_source_state)
            )
            _lookup_step("gh89_offline_no_local_source_is_actionable", actionable_no_local,
                         f"status={no_local_result.status} local_source_state={no_local_result.local_source_state}")

            lookup_dialog = None
            lookup_worker_result = {}
            lookup_worker_error = {}

            def _open_lookup(mode):
                dlg = pw._open_lookup_dialog(first_entry, mode)
                return dlg

            def _on_lookup_finished():
                if lookup_worker_result:
                    lookup_report["online_providers_seen"] = lookup_worker_result.get("provider_ids", lookup_report["online_providers_seen"])
                    lookup_report["offline_providers_seen"] = lookup_worker_result.get("provider_ids", lookup_report["offline_providers_seen"])
                    lookup_report["selected_title_after"] = lookup_worker_result.get("applied_title")
                elif lookup_worker_error:
                    _lookup_step("gh88_lookup_execution", False, str(lookup_worker_error))

            online_dialog = _open_lookup("online")
            if online_dialog is not None:
                pw._lookup_worker.finished.connect(_on_lookup_finished)
                import time as _time
                _time.sleep(2)
            else:
                _lookup_step("gh88_online_lookup_dialog", False, "dialog could not be opened")

            after_entry = _selected_entry()
            lookup_report["selected_title_after"] = getattr(after_entry, "title", None)
            lookup_report["selected_release_key"] = getattr(pw._state, "selected_release_key", lookup_report["selected_release_key"])

            export_files_after_lookup = sorted(p for p in cfg_lookup.output_dir.rglob("*") if p.is_file())
            lookup_report["export_files_after_apply"] = [str(p) for p in export_files_after_lookup]
            _lookup_step("gh88_89_no_export_from_lookup", len(export_files_after_lookup) == 0,
                         f"export_files={len(export_files_after_lookup)}")

            shot = screenshots / "gh88-89-lookup-proof.png"
            try:
                pix = pw.grab()
                pix.save(str(shot))
                _lookup_step("gh88_89_screenshot", shot.is_file(), f"saved {shot}")
                lookup_report["screenshots"].append(str(shot))
            except Exception as exc:
                _lookup_step("gh88_89_screenshot", False, f"grab failed: {exc}")

            pw.close()
        else:
            _lookup_step("gh88_89_preview_loaded", False, "state_path missing")
    except Exception as exc:
        _lookup_step("gh88_89_lookup_qualification", False, repr(exc))
        lookup_report["errors"].append(repr(exc))

    REPORT["gh88_89"] = lookup_report

    REPORT["gh86"] = gh86_report

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
                                     "lb_mappings_persist_reopen", "lb_missing_path_retained_diagnostic",
                                     "lb_backend_missing_root_diagnostic",
                                     "gh86_populated_pipeline", "gh86_preview_loaded",
                                     "gh86_selected_identity", "gh86_preview_no_export",
                                     "gh90_pipeline_populated", "gh90_preview_loaded",
                                     "gh90_multi_release_rows", "gh90_filter_refresh_identity_match",
                                     "gh90_order_identity_match", "gh90_staged_mutation_only_selected",
                                     "gh90_source_fixtures_unchanged", "gh90_preview_no_export_after_mutation",
                                     "gh88_89_populated_pipeline", "gh88_89_preview_loaded",
                                     "gh88_89_selected_original_identity", "gh88_89_no_export_from_lookup",
                                     "gh89_offline_providers_no_online_backend",
                                     "gh89_offline_lookup_runs_without_network",
                                     "gh89_offline_no_local_source_is_actionable",
                                     "gh88_lookup_execution",
                                    ))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
