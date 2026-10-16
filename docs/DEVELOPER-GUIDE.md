# Developer Guide

**Applies to v0.3.13. Reviewed 2026-10-16.**

## Setup

The core supports Python 3.11+; the Windows packaging workflow uses Python 3.12. Install the complete development environment for GUI/PDF/OCR tests:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[gui,dev]"
```

On Linux/macOS, activate a venv and install `.[gui,dev]`. `.[artwork,dev]` is sufficient only for core/artwork work; add `rtfm-docs` for document extraction. Packaged Windows builds include RapidOCR and its model data, so scanned-PDF OCR does not depend on a system Tesseract binary.

The installed `amiga-adf-gui` entry point starts the source GUI. Windows source checkouts can also use `tools/Launch-Updated-GUI.cmd`, which expects a root `.venv` and runs with the repository as app base. Runtime data created by source launches must remain untracked.

## Repository layout

| Path | Purpose |
|---|---|
| `src/amiga_adf_library_builder/` | Shared core and GUI |
| `tests/` | Unit, integration, widget, and regression coverage |
| `docs/` | Current user and engineering references |
| `docs/archive/` | Dated QA, task, audit, research, and design records |
| `config/example.toml` | Portable CLI/configuration example |
| `tools/` | Build, QA, and source-launch utilities |
| `probes/` | Diagnostic/reproduction helpers |
| `.github/workflows/` | Windows build/release and QA automation |
| `pyproject.toml` | Package metadata, dependencies, canonical version |
| `AmigaADFGui.spec` | Generated Windows packaging spec |

Keep root Markdown for project overview, changelog, contribution, conduct, and security policies. New ticket-specific records belong in the archive; maintained procedures belong in current docs. Do not add an ACTIVE-TASK file as a second issue tracker.

## Shared core and state

The CLI and GUI use the same pipeline. Presentation belongs under `gui/`; deterministic matching, rendering, selection, and export belong in core modules.

Important modules include `pipeline.py`, `parser.py`, `grouper.py`, `metadata.py`, `metadata_source.py`, `local_media.py`, `enrich.py`, `selection.py`, `media_review.py`, `rtfm.py`, `rtfm_docs.py`, `rtfm_ocr.py`, `exporter.py`, and `models.py`.

`gui/state.py` translates settings into core calls. `gui/worker.py` performs background work, while `gui/main_window.py`, `preview_widget.py`, and `media_choice_dialog.py` own presentation and curation interaction. Post-run finalization must keep the event loop responsive.

[State Ownership](STATE-OWNERSHIP-MAP.md) identifies the authority for each durable fact. The GUI's Library-Root is app-relative; CLI path overrides are separate. Do not create competing canonical or metadata-source databases.

## Media review contracts

`StagedReleaseEntry.media_choices` stores shared choices. A missing purpose means automatic matching; an empty list is a shared omission. `media_scopes` selects shared or per-disk mode separately for artwork and manual. `disk_media_choices` is keyed by original disk filename and then purpose.

Keep save/load, carry-over, undo/redo, move/merge payloads, pending-review classification, requirement checks, and export consistent when these fields change. Unassigned disks inherit shared media. File reassignment changes associations without writing original sources. Switching to shared mode preserves saved overrides and removes obsolete managed disk companions on repeat export.

The exporter maps original disk identity to the final ordinal filename before naming companions. Do not derive a disk companion name from an arbitrary source filename. See [Media Choices](MEDIA-CHOICES.md) and [Export Format](gotek-export-format.md).

## Providers and network access

The GUI registry currently exposes Wikipedia and ScreenScraper. Other integrations in the core/configuration are not automatically generic Providers-tab panels. GUI provider controls start unchecked; explicit saved enablement is restored. Registry constructor defaults alone do not describe GUI startup behaviour.

Network access must follow configured permission and shared URL/SSRF, pacing, caching, credential, and redaction policies. Do not bypass provider challenges. Wikipedia pacing is applied through `wikipedia_config.apply_effective_policy()` and the shared gate.

## Tests

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q
```

Run focused tests for changed behaviour, then relevant integration coverage. Platform-dependent symlink and permission tests require the relevant OS privileges; record exclusions rather than reporting them as passed. Widget/unit tests do not replace packaged Windows qualification or hardware checks.

Useful workflow regressions include `test_disk_media_workflow.py`, `test_gui_media_choices.py`, `test_media_review_matching.py`, `test_run_progress_regressions.py`, `test_exporter.py`, `test_gui_issue192_finalization_freeze.py`, `test_version_identity.py`, and `test_pdf_backend_packaging.py`.

Tests should use temporary synthetic fixtures. Keep personal ADF/DSK collections, downloaded media, credentials, caches, and generated libraries out of Git. See [Test Data Policy](test-corpus.md).

## Packaging and publication

`pyproject.toml` is the version authority. `tools/build_windows.py` generates the spec/bootstrap/version resources; ignored generated bootstrap files are not manually maintained sources. The CI builds Windows onedir and onefile artifacts, checks runtime dependencies and imports, and publishes tag assets when the tag matches the package version.

A documentation-only cleanup does not require a new application version/tag. A main push currently triggers Windows CI even when no application behaviour changed. Do not rewrite existing release tags to attach documentation edits.

Follow [Windows Build Reference](BUILD-Windows.md), [Release Checklist](RELEASE-CHECKLIST.md), and [Contributing](../CONTRIBUTING.md).

## Documentation maintenance

Update the authoritative guide with each behaviour change. Keep the index and cross-links current, and run `python tools/check_docs.py`. Historical records describe what was known or required at their recorded date; they are not current operating instructions.
