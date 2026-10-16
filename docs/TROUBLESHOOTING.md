# Troubleshooting

**Applies to v0.3.13. Reviewed 2026-10-16.**

## Gather evidence

Record the app version, affected release/disk names, steps, and full run log. **Build & Export → Open log files…** opens the log directory. Diagnostics gives live activity; finalized logs contain per-release outcomes and a run summary. Remove credentials and private paths before sharing publicly.

## Export does not happen

**Build library** prepares working data; it does not export. Use **Export library** on Build & Export, then inspect the destination and summary. The GUI has no Check only or second acknowledgement checkbox. CLI automation still uses its explicit export acknowledgement and optional `--verify-only`.

Check required-artwork failures, quarantined releases, conflicting saved 1G1R decisions, destination access, and actual export errors. Media choices awaiting review are reported separately from write failures. The app stages output; copying it to an SD card is a separate step.

## RTFM processing takes a long time

Scanned PDFs need offline OCR. The first run can be slow; later runs may reuse content-addressed OCR text. Watch source filenames, OCR page numbers, cache messages, and ten-second heartbeats. A heartbeat indicates the operation is still active, not a percentage-complete estimate.

No matching manual, extraction/build failure, an explicit omission, and awaiting review are different outcomes. Check the source folders, filename identity/numbered parts, PDF extraction diagnostics, and saved choices. Packaged builds include PDF extraction and offline OCR; source installs need the appropriate extras.

## Artwork is missing or incorrect

Check Local Media roots and missing-drive diagnostics, then rebuild. Recognised category folders are detected automatically. Distinct matching alternatives need review; an indexed image count does not mean each release has accepted artwork.

Open media review and inspect purpose, evidence, and saved choices. A saved omission suppresses automatic export; remove the override or choose another image deliberately. Version-specific artwork should not be substituted across incompatible releases.

## Per-disk media looks shared

Select **Different media per disk**, choose the intended disk, and save with Use this. Disks without an override inherit shared media. Check exact exported disk-stem `.jpg`/`.rtfm` files. Returning to shared mode disables overrides without deleting saved decisions.

The firmware lookup supports disk-specific companions, but its game-list cover is cached at release level. A shared game-list cover alone does not prove the custom companion was not exported. See [Export Format](gotek-export-format.md).

## A disk or manual belongs elsewhere

Use **Move disks to another release…** in media review or **Combine with another release…** in curation. Choose the destination and disk subset, then inspect the preview. Empty sources become Ghost history entries.

For a candidate media file, use **Assign media to another release…**. It changes associations, not the original file location. Use undo/redo to reverse the decision.

## DAT results are missing or weak

Ensure Metadata Sources contains enabled, indexed DAT records; rescan or reindex changed sources. Verify the original disk files exist and have not changed since scanning. Hash identity is stronger than a normalized title candidate. A character/save-only disk set can remain quarantined even if its name resembles a known game.

The GUI and pipeline use the Library-Root metadata-source database. Do not fix an index problem by creating a second database elsewhere.

## Online providers return nothing

Online access is opt-in, and providers start unchecked. Check both Options permission and provider enablement/configuration. Saved explicit choices are restored. Inspect diagnostics for disabled, unconfigured, blocked/challenged, transport error, and no-result outcomes.

A connection check does not guarantee a relevant result for every game. Do not bypass a provider challenge with scraping workarounds. Refreshing cached metadata does not remove operator-authoritative choices. See [Metadata & Providers](METADATA-PROVIDERS.md).

## Settings or curation seem lost after upgrade

Confirm which executable and app base you launched. Portable settings and Library-Root are relative to that base; a fresh folder can look like a fresh installation. Restore your backed-up state to the intended base, then verify external source and export paths.

Do not delete curation or canonical databases to troubleshoot a replaceable cache. See [State Ownership](STATE-OWNERSHIP-MAP.md) and [Installation](INSTALLATION.md).

## A packaged build will not launch

Use the release ZIP's complete extracted folder, including `_internal`, or the standalone EXE. Place it in a writable directory. Confirm Windows security software has not quarantined a component. Avoid mixing dependency files from two releases.

For source builds, install `.[gui,dev]` and verify PDF/OCR imports; for packaged-build failures report the exact release asset and log. See [Windows Build Reference](BUILD-Windows.md).
