# Repository and Documentation Cleanup — 2026-10-16

Application baseline: v0.3.13. This is a documentation/repository maintenance record, not a new application release or a packaged/hardware QA verdict.

## Layout changes

- Root `QA_GATE_GH170.md` moved to `docs/archive/qa/QA_GATE_GH170.md`.
- Root `ACTIVE-TASK.md` moved to `docs/archive/tasks/GH-192-REM-3.md`; its original status is historical.
- `docs/DOCUMENTATION-AUDIT-2026-09-22.md` moved to `docs/archive/documentation/`.
- The source launcher moved to `tools/Launch-Updated-GUI.cmd`, with its repository-relative base corrected.
- Project overview, changelog, contribution/conduct/security policies, license, package metadata, and the build spec remain at root.

## Documentation changes

Current guides now describe the auto-created GUI Library-Root, Build & Export tab, one Export action, Local Media category recognition, offline provider defaults, immediate review saves, Next/Previous, disk moves/media reassignment, shared/per-disk media, and offline PDF OCR progress/cache reuse.

CLI verification and acknowledgement options remain documented separately. Historical reports retain their dated context. The current release checklist replaces ticket-specific gate wording as the maintained procedure. README draft text and obsolete setup claims were removed; project credits and AI-assisted-development disclosure were retained.

## Maintenance checks

`tools/check_docs.py` validates local Markdown link targets without network access. Git whitespace checks and example TOML parsing accompany this cleanup. No claim is made that external web links, interactive Windows QA, or hardware behaviour were requalified by a documentation-only change.

Portable GUI working state and vaults are explicitly ignored. Personal disk/media collections, caches, logs, credentials, and generated output are not part of the documentation commit.

Validation: 109 local Markdown link targets checked with none missing; example TOML parsed with online sources disabled; 67 configuration/version tests passed. A GitHub documentation-link workflow runs the same checker. These counts describe this cleanup run, not total project test coverage.

The existing tracked `tests/fixtures/issue15-lib/*.adf` files were inspected: they are 34–38 byte synthetic text markers, not disk-image collections. They remain as intentional test fixtures.
