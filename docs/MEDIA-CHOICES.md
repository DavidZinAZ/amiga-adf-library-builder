# Reviewing Artwork and RTFM

**Applies to v0.3.13. Reviewed 2026-10-16.**

## Open and inspect choices

After a build, **Review choices** opens releases with multiple distinct matching images or manuals. **Decide later** leaves them for later review. You can reopen **Preview & Curation → Review media choices…** to inspect candidates or change saved choices.

Choose Artwork or RTFM. Other artwork categories are labelled separately where needed. Identical files are consolidated; their locations remain available. Preview images, dimensions, PDF pages, or text. **Enlarge image / PDF page…** and **Compare with saved choice…** help inspect details.

## Save and navigate

- **Use this** saves immediately. For RTFM, select the primary document and check optional reference cards, hints, or walkthroughs. Extras are combined only after explicit selection.
- **Preview generated RTFM** shows converted text for the current manual selection.
- **Browse…** adds a local file of your choice.
- In shared mode, **None of these** records an explicit omission. **Use automatic matching** removes the override.
- In per-disk mode, removing a custom choice returns the disk to shared inheritance.
- **Next / Previous** navigate purposes, disks, and releases. **Done / Close** finish review; already-saved choices remain saved.

Undo/redo also persists media-choice changes. Sources are never rewritten. A selected PDF is converted to text, not copied as PDF bytes into an RTFM file.

## Shared and per-disk media

**Same media for the whole release** is the default for artwork and RTFM independently. **Different media per disk** adds a disk selector. The saved-choice panel identifies a custom or inherited choice for that disk.

Disks without a custom choice inherit shared release media. Overrides persist across builds and follow disks moved or merged into another release. Switching back to shared mode retains overrides for later reuse but exports only shared companions.

Shared output uses `<release>.jpg` / `<release>.rtfm`. Overrides use the exact exported disk stem, such as `<release>-2.jpg` / `<release>-2.rtfm`. Re-exporting into the same staging run removes obsolete managed companions. Device game-list cover caching remains release-level; see [Export Format](gotek-export-format.md).

When several artwork purposes have saved choices, priority is title screen, front cover, gameplay screenshot, then other purposes. Unresolved alternatives are withheld and reported as awaiting review. Complete per-disk choices can resolve the purpose without a shared selection.

## Correct the destination release

**Move disks to another release…** selects disks and a destination, previews the result, and applies the same staged move/combine workflow available in curation.

**Assign media to another release…** associates the selected candidate with a different release and removes that selected source association. The original media file stays in place. Both actions support undo/redo.

## Matching behaviour

DAT matching uses compatible SHA-1/MD5/CRC hashes, file size, and each release's own disks, checking against the scan's SHA-256 identity. Normalized title matches are candidates, not equivalent to byte identity.

Manual matching preserves numbered parts and does not mix weaker rival documents into an accepted manual. Generic versionless artwork may match versioned releases; artwork explicitly marked for another version stays distinct. Recognised category folders prevent logos and unrelated image types from competing as front covers.

For source development, [the developer guide](DEVELOPER-GUIDE.md) describes the launcher in `tools/`; release users should use the published Windows build.
