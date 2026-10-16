# User Guide

**Applies to v0.3.13. Reviewed 2026-10-16.**

## Install and protect your collection

Use the portable ZIP or standalone EXE from [GitHub Releases](https://github.com/DavidZinAZ/amiga-adf-library-builder/releases/latest). The application folder must be writable. The GUI creates `Library-Root` beside it; choose your original disk folder and export folder on **Library**.

Original ADF/DSK files and local source media are read-only input. Builds write catalogs, caches, generated assets, logs, and curation state. Exports write a run-owned staging tree. Back up original files and curation state independently; exported files are generated output.

See [Installation](INSTALLATION.md) and [Data Layout](DATA-LAYOUT.md).

## Prepare local sources

- **Local Media:** add artwork folders and manual folders independently. Category folders such as front covers, title screens, and gameplay screenshots are recognised automatically. Folder ordering establishes source priority; ambiguous distinct assets still need review.
- **Metadata Sources:** add DAT files or folders of DATs, then rescan or reindex changed sources. DAT identity matching uses compatible hashes and the current release's disks. A title candidate is weaker evidence than a byte match.
- **Options:** choose whether to include artwork and manuals, whether online access is allowed, and whether to refresh cached metadata.
- **Providers:** providers start unchecked. Configure and explicitly enable those you want; saved choices are restored. Online access and provider enablement are separate controls.

Local TXT/PDF manuals can produce RTFM sidecars. Packaged builds include PDF text extraction and offline OCR for scanned PDFs. Cold OCR can take minutes; page progress, cache reuse, and a ten-second heartbeat appear in the activity log.

## Build

Open **Build & Export** and click **Build library**. It scans, parses, groups, resolves metadata, and prepares assets without exporting the disk library. Check **Diagnostics** and the run log for outcomes and errors.

After completion, **Preview & Curation** loads the staged state and offers media review when different matching assets need a choice. Missing manuals, extraction failures, and choices awaiting review are reported separately.

## Curate releases

Select a release to inspect its disks, metadata, artwork, NFO, and RTFM. Use filters to locate incomplete, unmatched, or review-needed entries. Accept/reject decisions and corrections are saved in curation state.

**Combine with another release…** selects a destination and disks, previews the result, and defaults to all disks. Moving all disks merges the release; moving a subset leaves the remaining disks in the source. Empty source releases become Ghost entries for history. Original files do not move on disk. Undo/redo restores the association changes.

Do not merge unrelated release versions merely to reduce the number of rows. **1G1R: one release per game** chooses a release for export while preserving separate source disk sets. Saved operator decisions take precedence over ranking; conflicting saved choices need correction.

## Review artwork and RTFM

Open **Review media choices…** in Preview & Curation or accept the post-build review prompt.

1. Choose Artwork or RTFM and preview a candidate.
2. For RTFM, select a primary manual and check optional reference cards, hints, or other extras deliberately.
3. Click **Use this** to save immediately. Use **Compare with saved choice…** for a side-by-side comparison.
4. Use **Next** / **Previous** to continue. Closing the dialog retains choices already saved with Use this.

Shared release media is the default. **Different media per disk** adds a disk selector; custom choices are saved against the original disk identity. Unassigned disks inherit the shared choice. Custom choices move with their disks and survive subsequent builds. Changing back to shared mode retains overrides for later reuse.

**Move disks to another release…** opens the disk/destination picker inside review. **Assign media to another release…** associates the selected candidate with a different release, leaving the source file in place. Both support undo/redo.

See [Media Choices](MEDIA-CHOICES.md) for omission, automatic matching, PDF previews, and export details.

## Lookup and manual corrections

Preview lookup can use configured online providers or local sources. Inspect candidate evidence before applying it. **Manual Lookup** browses/searches sources and applies a selected result into the normal Preview → Export lifecycle. **Metadata Sources** manages indexed DAT identity data; it is distinct from the online Providers tab.

Provider cache, canonical identity, and operator decisions have different ownership. Refreshing a provider cache is not the same as removing a saved override. See [Metadata & Providers](METADATA-PROVIDERS.md).

## Export

On **Build & Export**, inspect the displayed destination, artwork requirement, and 1G1R settings. Click **Export library** once. The GUI has no separate write acknowledgement or Check only control.

Export writes a run-owned `ADF`/`DSK` tree containing disk files and available NFO, JPEG, and RTFM companions. Shared media uses the release basename; disk overrides use the exact exported disk stem. Undecided alternatives are withheld and named in the summary. Re-exporting to the same staging run removes obsolete managed companions when choices change.

Inspect the output before copying it to your device. The firmware lookup accepts exact disk companions with shared fallback; its game-list cover is cached at release level. See [Gotek Export Format](gotek-export-format.md).

## Recovery and automation

- Keep the full run log, application version, and relevant release names when reporting a problem.
- Back up `Library-Root`, portable settings, and the credential vault before upgrading.
- Keep personal collections, downloaded media, caches, and vaults out of Git.
- Use the CLI's `--verify-only` when an automation needs verification without staging writes. CLI acknowledgement flags remain supported.

See [Troubleshooting](TROUBLESHOOTING.md), [Commands](COMMANDS.md), and [State Ownership](STATE-OWNERSHIP-MAP.md).
