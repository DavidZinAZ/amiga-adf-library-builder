# Release Checklist

**Applies to v0.3.13. Reviewed 2026-10-16.**

Use this maintained checklist for current releases. Ticket-specific QA records live under [archive/qa](archive/README.md); they retain historical evidence and do not describe current control labels.

## Prepare

- Confirm the intended source commit and a clean, reviewable change set.
- Update `pyproject.toml` for an application release and add accurate changelog entries. Documentation-only commits need no new application tag.
- Update relevant guides and run `python tools/check_docs.py`.
- Run focused and relevant regression tests. Record failures, platform exclusions, and limitations explicitly.
- Verify no original disk collection, downloaded source media, vault, token, cache, or generated working library is staged in Git.

## Packaged Windows qualification

Use the actual ZIP/EXE on Windows, not only source/widget tests. Record artifact version, source commit, test date, screenshots where useful, and relevant redacted log lines. Mark unperformed checks as not tested.

- Launch the complete portable ZIP and single-file EXE; check displayed version and paths with spaces.
- Verify Library-Root auto-creation, original/export paths, and restored settings.
- Check fresh-install online providers are off and explicitly saved enablement survives restart. Confirm runtime provider diagnostics agree with settings.
- Add/rescan a DAT source; restart and confirm indexed source persistence and a known hash match.
- Build a synthetic collection; inspect responsive finalization, curation loading, and live progress.
- Verify text PDF/manual generation and scanned-PDF offline OCR, including cache reuse and a long-operation heartbeat.
- Review and save Artwork/RTFM choices, compare previews, navigate Next/Previous, and restart/rebuild to confirm persistence.
- Exercise shared and per-disk media, inherited choices, custom companion export, and scope changes on a repeat export.
- Move a disk with custom media, merge releases, assign media elsewhere, and verify undo/redo and persistence.
- Inspect NFO, artwork, and generated RTFM from Preview; verify state and provenance agree after lookup Apply.
- Confirm Build & Export is a tab with one Export action and no Check only/acknowledgement controls.
- Verify required-artwork and 1G1R behaviour, export paths, unresolved-choice summaries, and actual errors.
- Hash original ADF/DSK and media fixtures before/after; verify they were not modified.

## Publish and verify

- Create a version tag matching `pyproject.toml`; preserve existing tags.
- Wait for the Windows build and release jobs to succeed.
- Verify both `amiga-adf-gui-portable.zip` and `amiga-adf-gui.exe` exist on the intended GitHub release, from the intended commit.
- Report qualification coverage and limitations truthfully. CI import/launch checks do not establish every interactive or device-level behaviour.
- Inspect the exported tree before copying to hardware. Device display validation is separate; exact disk companions do not change the firmware's release-level game-list cover cache.
