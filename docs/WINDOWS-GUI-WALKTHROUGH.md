# Windows GUI Walkthrough

**Applies to v0.3.13. Reviewed 2026-10-16.**

## Workspace map

| Tab | Purpose |
|---|---|
| Library | Original disk folder and export destination; Library-Root is created automatically beside the app |
| Build & Export | Build/Export buttons, requirements, 1G1R, progress, cancellation, and log access |
| Options | Online permission, refresh, artwork/manual inclusion, matching and related options |
| Providers | Explicit provider enablement, settings, credentials, and connection checks |
| Local Media | Artwork and manual folders, ordering, and root diagnostics |
| Preview & Curation | Inspect and correct releases, review media, combine/move disks, undo/redo |
| Diagnostics | Activity and provider/configuration outcomes |
| Metadata Sources | Add, rescan, reindex, or remove indexed DAT sources |
| Manual Lookup | Search/browse manual sources and apply a selected result |

Build & Export is a tab, not a permanent bottom panel. There is no Check only or second export acknowledgement checkbox in the GUI.

## First build

1. Choose **Original disks (read-only)** and **ADF Library Export Folder** on Library.
2. Add available artwork and manuals on Local Media, then add DAT sources if available.
3. Leave online access off, or explicitly enable/configure desired providers. No online provider is selected on a fresh install.
4. In Build & Export, click **Build library**.
5. Follow Diagnostics or open the log folder. RTFM activity identifies files, OCR pages, cached text, and long-operation heartbeats.
6. Inspect the resulting Preview & Curation state.

The app stores settings separately from Library-Root working data. Missing folder paths are retained so they can be corrected; a disconnected drive should not silently erase saved mappings.

## Local Media

Add artwork and manual folders separately. The artwork asset-type column is no longer a user control: recognised category subfolders are detected automatically, preventing logos or back covers from being treated as front-cover alternatives.

Move Up / Move Down changes source order. Check roots reports missing folders and source counts. Removing a configured root removes a mapping, not the source directory.

## Preview & Curation

Inspect a selected release's disks and metadata before accepting it. Use **Combine with another release…** to select a destination and all or some disks. The dialog previews the resulting disk list. The action changes the staged library; source ADF/DSK files stay unchanged.

Approved entries use muted green. Ghost entries retain history after all disks have moved away. Use undo/redo for supported curation changes and preserve state backups for recovery.

## Candidate review

The review prompt opens when matching finds different alternatives. Review can also be opened later from Preview & Curation.

- **Artwork / RTFM:** choose the media purpose. Other artwork categories remain distinguishable.
- **Use this:** saves the primary candidate and selected RTFM extras immediately.
- **Next / Previous:** move through purposes, disks in per-disk mode, and the review list.
- **Same media for the whole release:** default shared choice.
- **Different media per disk:** choose a disk and save a custom override. The saved-choice panel identifies custom versus inherited media.
- **Move disks to another release…:** opens the disk/destination picker during review.
- **Assign media to another release…:** reassigns the selected candidate's association without moving the media file.
- **Compare with saved choice… / Enlarge image / PDF page…:** inspect candidates before selecting.
- **Preview generated RTFM:** inspect conversion of the selected manual and extras.
- **Done / Close:** finish or close review. Choices already saved remain saved.

[Media Choices](MEDIA-CHOICES.md) explains omissions, inheritance, and export behaviour.

## Lookup and sources

The Providers tab configures online integrations; Metadata Sources indexes local DAT records. These are separate systems. Manual Lookup searches/browses manual sources and applies results to staged state.

Inspect provider diagnostics before assuming an empty result means a title does not exist. Disabled, unconfigured, blocked, failed, and successful-no-result outcomes need different corrections. Refresh does not erase operator-authoritative choices.

## Export

Open Build & Export, confirm the displayed destination and requirements, then click **Export library**. The application writes the final files into a run-owned staging folder. Review the summary for missing required artwork, choices awaiting review, and actual errors.

Exported disk filenames determine per-disk JPEG/RTFM companion names. Unassigned disks use shared media. Copy inspected output to your device as a separate step; the app does not write source originals or automatically publish to an SD card.

See [User Guide](USER-GUIDE.md), [Gotek Export Format](gotek-export-format.md), and [Troubleshooting](TROUBLESHOOTING.md).
