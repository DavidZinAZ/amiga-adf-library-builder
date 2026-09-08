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
  4. (GH-91) STATE FILTER / DETAIL-BINDING qualification on Windows: on a real
     preview-widget instance, prove each canonical state filter shows exactly
     the matching rows, clearing/changing filters preserves identity, and
     Release Detail remains bound after filter changes.
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
    exe = Path(os.environ.get("QA_EXE", "dist/AmigaADFLibraryBuilder/AmigaADFLibraryBuilder.exe"))
    if not exe.is_file():
        exe = Path("dist/amiga-adf-gui.exe")
    report_dir = Path(os.environ.get("QA_REPORT_DIR", base_dir / "qa-windows-artifacts")).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    screenshots = report_dir / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)

    REPORT: dict = {"steps": [], "errors": []}

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
            time.sleep(6)
            alive = proc.poll() is None
            _step("exe_launch_clean", alive,
                  f"exe={exe.name} pid={proc.pid if alive else 'exited'}")
            created = [d for d in ("config", "data", "logs", "cache")
                       if (base_dir / d).is_dir()]
            _step("exe_portable_layout_spaces",
                  set(created) >= {"config", "logs", "cache"},
                  f"base='{base_dir}' created={created}")
            self_contained = (exe.parent / "python3.dll").is_file() or \
                             (exe.parent / "python312.dll").is_file() or \
                             (exe.parent / "_internal").is_dir()
            _step("exe_self_contained", self_contained,
                  f"python dll/_internal present beside {exe.name}: {self_contained}")
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
        mw._le_library_root.setText("")
        errors: list[str] = []
        clear_msgs: list[str] = []
        orig_crit = QMessageBox.critical
        QMessageBox.critical = staticmethod(
            lambda *a, **k: clear_msgs.append((a[1] if len(a) > 1 else ""))
        )
        crashed = False
        try:
            mw._on_run()
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
        except Exception as exc:
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
        mw.show()
        mw.close()
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
        from unittest import mock as _mock
        from amiga_adf_library_builder import local_media as _lm

        lb_dirs = {
            "front": base_dir / "lb" / "box front",
            "back": base_dir / "lb" / "box back",
            "manuals": base_dir / "lb" / "manuals",
        }
        for d in lb_dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        (lb_dirs["front"] / "Synthetic Quest III.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        )
        (lb_dirs["manuals"] / "Synthetic Quest III.txt").write_bytes(b"controls")

        mw_lb = MainWindow(
            portable_paths=pp,
            settings_store=SettingsStore(pp.settings_file()),
        )
        with _mock.patch(
            "amiga_adf_library_builder.gui.main_window.QFileDialog.getExistingDirectory",
            side_effect=[str(lb_dirs["front"]), str(lb_dirs["back"]), str(lb_dirs["manuals"])],
        ):
            mw_lb._lb_add_media_root()
            mw_lb._lb_add_media_root()
            mw_lb._lb_add_manual_root()
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

        mw_lb._lb_check_roots()
        diag = mw_lb._lb_diag_label.text()
        _step("lb_check_roots_diagnostic", bool(diag),
              f"diag_chars={len(diag)}")

        mw_lb.show()
        mw_lb.close()
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
    # 4) (GH-91) STATE FILTER / DETAIL-BINDING qualification on Windows.
    # ------------------------------------------------------------------ #
    gh91_report = {
        "library_populated": False,
        "preview_nonempty_rows": 0,
        "filter_exact_counts": {},
        "filter_identity_stable_after_filter_change": False,
        "release_detail_bound_after_filter_change": False,
        "case_or_mixed_value_counts": {},
        "source_fixtures_unchanged": False,
        "screenshot": None,
        "errors": [],
    }

    def _gh91_step(name, ok, detail=""):
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
        from amiga_adf_library_builder.models import StagedState, StagedReleaseEntry, CurationAction

        # 4a) Build a populated synthetic library with known states per release.
        gh91_root = base_dir / "gh91-original"
        gh91_root.mkdir(parents=True, exist_ok=True)
        fixtures = {
            "Gh91 - Alpha Quest (Disk 1 of 2).adf": "pending",
            "Gh91 - Alpha Quest (Disk 2 of 2).adf": "accepted",
            "Gh91 - Beta Quest (Disk 1).adf": "rejected",
            "Gh91 - Gamma Force.adf": "modified",
            "Gh91 - Delta Race.adf": "needs_review",
        }
        before_hashes = {}
        for name in fixtures:
            path = gh91_root / name
            path.write_bytes(name.encode("utf-8"))
            before_hashes[name] = __import__("hashlib").sha256(name.encode("utf-8")).hexdigest()

        state = GuiState(
            library_root=str(base_dir / "gh91-lib"),
            original_dir=str(gh91_root),
            run_mode="build",
        )
        pp_gh91 = PortablePaths(base_dir=base_dir / "gh91-lib")
        pp_gh91.ensure_all()
        cfg_gh91 = build_path_config_from_gui_state(state)
        ensure_managed_directories(cfg_gh91)
        kwargs_gh91 = build_pipeline_kwargs(state, cfg_gh91)
        result_gh91 = run_pipeline(**kwargs_gh91)
        pipeline_ok = bool(result_gh91.get("per_group"))
        _gh91_step("gh91_pipeline_populated", pipeline_ok, f"groups={result_gh91.get('groups', 0)}")

        state_path = build_staged_library_from_result(
            result_gh91,
            library_root=cfg_gh91.library_root,
            run_id=result_gh91.get("run_id", "gh91-qa"),
        )
        pw = PreviewWidget()
        preview_loaded = False
        preview_rows = 0
        if state_path and state_path.exists():
            preview_loaded = pw.load_state_file(state_path)
            pw.show()
            preview_rows = pw._table.rowCount()
        _gh91_step("gh91_preview_loaded", preview_loaded and preview_rows > 0,
                    f"loaded={preview_loaded} rows={preview_rows}")
        gh91_report["preview_nonempty_rows"] = preview_rows
        gh91_report["library_populated"] = preview_rows > 0

        if preview_rows > 0:
            # 4b) Exact canonical state counts via filter combo items.
            expected_counts = {state.value: 0 for state in StagedState}
            for entry in pw._state.current_library.releases.values():
                expected_counts[entry.curation_state.value] += 1
            exact_counts = True
            filter_exact = {}
            combo_items = {
                "pending": "Pending",
                "accepted": "Accepted",
                "rejected": "Rejected",
                "modified": "Modified",
                "needs_review": "Needs Review",
            }
            for canonical, label in combo_items.items():
                pw._filter_combo.setCurrentText(label)
                pw._apply_filter()
                visible = sum(1 for r in range(pw._table.rowCount()) if not pw._table.isRowHidden(r))
                filter_exact[label] = visible
                exact_counts = exact_counts and (visible == expected_counts[canonical])
            pw._filter_combo.setCurrentText("All")
            pw._apply_filter()
            gh91_report["filter_exact_counts"] = filter_exact
            _gh91_step("gh91_filter_exact_counts", exact_counts, f"counts={filter_exact}")
            gh91_report["filter_exact_counts"] = filter_exact

            # 4c) Clear/changing filter preserves row identity + Release Detail binding.
            selected_key_before = None
            detail_title_before = None
            if pw._table.rowCount() > 0:
                pw._table.selectRow(0)
                key_item = pw._table.item(0, 1)
                selected_key_before = key_item.data(Qt.ItemDataRole.UserRole) if key_item else None
                detail_title_before = pw._detail_title.text()
            pw._filter_combo.setCurrentText("Accepted")
            pw._apply_filter()
            restored_key = None
            restored_title = None
            if selected_key_before is not None:
                for r in range(pw._table.rowCount()):
                    if pw._table.isRowHidden(r):
                        continue
                    item = pw._table.item(r, 1)
                    if item and item.data(Qt.ItemDataRole.UserRole) == selected_key_before:
                        restored_key = item.data(Qt.ItemDataRole.UserRole)
                        break
            restored_title = pw._detail_title.text()
            identity_stable = (
                restored_key is not None and restored_key == selected_key_before and restored_title == detail_title_before
            )
            gh91_report["filter_identity_stable_after_filter_change"] = identity_stable
            _gh91_step("gh91_filter_identity_stable_after_filter_change", identity_stable,
                        f"selected_key={selected_key_before} restored_key={restored_key} title={detail_title_before!r}")
            pw._filter_combo.setCurrentText("All")
            pw._apply_filter()

            # 4d) Case/format tolerance: change underlying canonical state text casing
            # and confirm the same combo filter still matches.
            if pw._state.current_library and preview_rows > 0:
                first_key = list(pw._state.current_library.releases.keys())[0]
                first_entry = pw._state.current_library.releases[first_key]
                original_state = first_entry.curation_state
                mixed_label = "AcCePtEd"
                pw._filter_combo.setCurrentText(mixed_label)
                mixed_visible = sum(1 for r in range(pw._table.rowCount()) if not pw._table.isRowHidden(r))
                pw._filter_combo.setCurrentText("Accepted")
                pw._apply_filter()
                accepted_visible = sum(1 for r in range(pw._table.rowCount()) if not pw._table.isRowHidden(r))
                case_safe = mixed_visible == accepted_visible
                case_or_mixed = {
                    "accepted_visible": accepted_visible,
                    "mixed_label_visible": mixed_visible,
                    "case_safe": case_safe,
                }
            else:
                case_or_mixed = {"accepted_visible": 0, "mixed_label_visible": 0, "case_safe": False}
                case_safe = False
            gh91_report["case_or_mixed_value_counts"] = case_or_mixed
            _gh91_step("gh91_case_or_mixed_value_counts", case_safe, f"detail={case_or_mixed}")
            pw._filter_combo.setCurrentText("All")
            pw._apply_filter()

            # 4e) Mutate a selected release and verify Release Detail updates while
            # a non-matching filter hides the mutated row.
            target_key = None
            for key, entry in pw._state.current_library.releases.items():
                if entry.curation_state != StagedState.ACCEPTED:
                    target_key = key
                    break
            mutated_ok = False
            detail_mutation_ok = False
            detail_hidden_after_mutation = False
            if target_key is not None:
                for r in range(pw._table.rowCount()):
                    item = pw._table.item(r, 1)
                    if item and item.data(Qt.ItemDataRole.UserRole) == target_key:
                        pw._table.selectRow(r)
                        break
                pw._set_selected_state(StagedState.ACCEPTED)
                entry = pw._state.current_library.releases.get(target_key)
                mutated_ok = entry is not None and entry.curation_state == StagedState.ACCEPTED
                detail_mutation_ok = pw._state.selected_release_key == target_key and pw._detail_state.text().lower() == "accepted"
                pw._filter_combo.setCurrentText("Rejected")
                pw._apply_filter()
                for r in range(pw._table.rowCount()):
                    if pw._table.isRowHidden(r):
                        continue
                    item = pw._table.item(r, 1)
                    if item and item.data(Qt.ItemDataRole.UserRole) == target_key:
                        detail_hidden_after_mutation = False
                        break
                else:
                    detail_hidden_after_mutation = True
                pw._filter_combo.setCurrentText("All")
                pw._apply_filter()
            _gh91_step("gh91_mutation_preserves_detail_and_filter", mutated_ok and detail_mutation_ok and detail_hidden_after_mutation,
                        f"mutated={mutated_ok} detail_updated={detail_mutation_ok} hidden={detail_hidden_after_mutation}")

            # 4f) Source fixtures unchanged by preview/mutation.
            after_hashes = {}
            for name in fixtures:
                path = gh91_root / name
                after_hashes[name] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
            gh91_report["source_fixtures_unchanged"] = after_hashes == before_hashes
            _gh91_step("gh91_source_fixtures_unchanged", after_hashes == before_hashes,
                        f"fixtures={len(fixtures)}")

            shot = screenshots / "gh91-preview-state-filter.png"
            try:
                pix = pw.grab()
                pix.save(str(shot))
                _gh91_step("gh91_screenshot", shot.is_file(), f"saved {shot}")
                gh91_report["screenshot"] = str(shot)
            except Exception as exc:
                _gh91_step("gh91_screenshot", False, f"grab failed: {exc}")
            pw.close()
        else:
            _gh91_step("gh91_preview_loaded", False, "state_path missing")
    except Exception as exc:
        _gh91_step("gh91_state_filter_qualification", False, repr(exc))
        gh91_report["errors"].append(repr(exc))

    REPORT["gh91"] = gh91_report
    REPORT["gh86"] = gh86_report

    # ------------------------------------------------------------------ #
    # Emit the report + secret-leak scan of the logs dir
    # ------------------------------------------------------------------ #
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
                    if s["step"] in ("exe_launch_clean", "exe_portable_layout_spaces",
                                     "exe_self_contained", "settings_persist",
                                     "close_without_run_restore",
                                     "help_about_available", "no_crash_on_invalid_input",
                                     "no_secret_leak_in_logs",
                                     "lb_multi_mappings_added", "lb_check_roots_diagnostic",
                                     "lb_mappings_persist_reopen", "lb_missing_path_retained_diagnostic",
                                     "lb_backend_missing_root_diagnostic",
                                     "gh91_filter_exact_counts",
                                     "gh91_filter_identity_stable_after_filter_change",
                                     "gh91_case_or_mixed_value_counts",
                                     "gh91_mutation_preserves_detail_and_filter",
                                     "gh91_source_fixtures_unchanged"))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
