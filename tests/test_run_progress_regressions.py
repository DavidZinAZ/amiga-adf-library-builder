from pathlib import Path
from types import SimpleNamespace
import threading

from amiga_adf_library_builder.rtfm_progress import report, progress_scope
from amiga_adf_library_builder.rtfm import RtfmConfig, build_rtfm_all
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.selection import select_one_per_game


def test_heartbeat_reports_long_operation_and_scope_stops():
    lines = []
    heartbeat = threading.Event()
    def activity(line):
        lines.append(line)
        if "still working" in line:
            heartbeat.set()
    with progress_scope(activity, interval=.01):
        report("Example.pdf: OCR page 2 of 10")
        assert heartbeat.wait(2)
    count = len(lines)
    report("outside scope")
    assert len(lines) == count
    assert any("Example.pdf" in line and "still working" in line for line in lines)


def test_manual_progress_is_live_and_each_release_has_outcome(tmp_path):
    (tmp_path / "Space Quest.txt").write_text("CONTROLS\nPress FIRE to start.")
    groups = group_records([parse_filename("Space Quest.adf"), parse_filename("No Manual.adf")])
    lines = []
    results = build_rtfm_all(groups, cfg=RtfmConfig(enabled=True, manuals_roots=(str(tmp_path),)),
                             rtfm_dir=tmp_path / "out", activity=lines.append)
    assert len(results) == 2
    assert any("manual 1 of 2" in line for line in lines)
    assert any("manual 2 of 2" in line for line in lines)
    assert any("Space Quest.txt: reading text" in line for line in lines)
    assert any("Space Quest: built" in line for line in lines)
    assert any("No Manual: no matching manual" in line for line in lines)


def test_progress_failure_does_not_break_manual_generation(tmp_path):
    (tmp_path / "Space Quest.txt").write_text("Press FIRE to start.")
    groups = group_records([parse_filename("Space Quest.adf")])
    def broken(line):
        raise RuntimeError("display unavailable")
    result = build_rtfm_all(groups, cfg=RtfmConfig(enabled=True, manuals_roots=(str(tmp_path),)),
                            rtfm_dir=tmp_path / "out", activity=broken)
    assert result[0].written


def test_inline_versions_share_game_identity_and_numeric_latest_wins():
    groups = group_records([parse_filename(f"A-10 Tank Killer v{v}.adf") for v in ("1.0", "1.5", "1.12")])
    result = select_one_per_game(groups)
    assert len(result.selected) == 1
    assert result.selected[0].title.endswith("v1.12")
    assert len(result.rejected) == 2
    assert len({g.release_key for g in groups}) == 3  # Never merge disk releases.


def test_legacy_saved_version_selection_still_wins():
    groups = group_records([parse_filename(f"A-10 Tank Killer v{v}.adf") for v in ("1.0", "1.5")])
    preferred = next(g for g in groups if g.title.endswith("v1.0"))
    result = select_one_per_game(groups, decisions={preferred.release_key.split("|")[0]: preferred.release_key})
    assert result.selected == [preferred]


def test_pending_media_choices_are_not_reported_as_export_errors():
    from amiga_adf_library_builder.activity_log import render_run_summary
    lines = render_run_summary({"export": {"errors": [], "conflicts": [
        "Awaiting media choice: Space Quest — Box - Front"]}})
    assert "awaiting review" in lines[0]
    assert "problems" not in lines[0]
    assert any("Space Quest" in line for line in lines)


def test_post_run_refresh_schedules_review_and_completes(tmp_path, monkeypatch):
    from amiga_adf_library_builder.gui import main_window as module
    state = tmp_path / "state.json"
    state.write_text("{}")
    calls = []
    window = SimpleNamespace(
        _preview_widget=SimpleNamespace(load_state_file=lambda path: calls.append("load"),
                                         notify_media_review=lambda: None),
        _append_diag=calls.append,
        _refresh_manual_lookup_panel=lambda: calls.append("refresh"),
        _clear_live_log=lambda: calls.append("close-log"),
        _finish_run_to_idle=lambda: calls.append("idle"))
    monkeypatch.setattr(module.QTimer, "singleShot", lambda delay, callback: calls.append("schedule-review"))
    module.MainWindow._on_run_finalized(window, {"state_path": state})
    assert "schedule-review" in calls and "refresh" in calls and "idle" in calls
    assert not any("load skipped" in str(line) for line in calls)


def test_pdf_native_text_progress(tmp_path):
    import pymupdf
    from amiga_adf_library_builder.rtfm_docs import extract_pdf_text
    path = tmp_path / "Example.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "CONTROLS: Press FIRE to start the game and use the joystick to move.")
        doc.save(path)
    lines = []
    with progress_scope(lines.append):
        result = extract_pdf_text(path)
    assert not result.empty
    assert any("Example.pdf: checking text page 1 of 1" in line for line in lines)


def test_cached_ocr_logs_reuse_without_loading_engine(tmp_path, monkeypatch):
    from amiga_adf_library_builder import rtfm_ocr as ocr
    monkeypatch.setattr(ocr, "cache_key_for_file", lambda path: "digest")
    monkeypatch.setattr(ocr, "_read_cache", lambda *args: "Previously extracted manual text.")
    def unavailable():
        raise AssertionError("Cached text must not reload the OCR engine")
    monkeypatch.setattr(ocr, "ocr_backend", unavailable)
    lines = []
    with progress_scope(lines.append):
        result = ocr.ocr_pdf_document(tmp_path / "Example.pdf", cache_dir=tmp_path)
    assert result.cache == "hit"
    assert any("reused cached OCR text" in line for line in lines)


def test_ocr_reports_each_page(tmp_path, monkeypatch):
    import pymupdf
    from amiga_adf_library_builder import rtfm_ocr as ocr
    path = tmp_path / "Scanned.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=100, height=100)
        doc.new_page(width=100, height=100)
        doc.save(path)
    monkeypatch.setattr(ocr, "ocr_backend", lambda: "rapidocr")
    monkeypatch.setattr(ocr, "_ocr_ndarray", lambda *args: "OCR page text")
    lines = []
    with progress_scope(lines.append):
        result = ocr.ocr_pdf_document(path, use_cache=False)
    assert result.ok
    assert any("OCR page 1 of 2" in line for line in lines)
    assert any("OCR page 2 of 2" in line for line in lines)
