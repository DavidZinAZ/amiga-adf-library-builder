# Reviewing images and manuals

For this source checkout, **Launch-Updated-GUI.cmd** opens the updated GUI using
the project's installed `.venv` environment. Downloaded release executables
remain separate from these local source changes.

After a run, the application offers **Review choices** when a release has
multiple different matching images or manuals. **Decide later** leaves them in
the review queue. You can open **Preview & Curation → Review media choices…**
at any time to inspect candidates or change a saved decision.

Choose a release, then a purpose such as **Box - Front**, **Screenshot - Game
Title**, or **manual**. Identical files are shown once, with the number of
copies and their locations retained. Covers and screenshots are reviewed
separately.

- Select a candidate to preview it. Images show dimensions and can be enlarged.
  PDFs support page navigation and enlarged page previews. Text documents show
  a readable excerpt. **Compare with saved choice…** opens a side-by-side view.
- **Use this** stages the selected candidate for that purpose.
- For manuals, select the primary document and check any optional additional
  documents, such as reference cards or hints. These are combined only after
  your explicit selection.
- **Preview generated RTFM** shows the converted output for the currently
  selected primary document and checked extras.
- **None of these** records an explicit omission. Later scans respect it.
- **Browse…** adds a file of your choice.
- **Use automatic matching** removes the saved override for that purpose.
- **Save choices** persists all staged decisions in the existing library
  curation state. **Keep current / decide later** discards unsaved changes.

Saved choices survive later runs and remain available for review. Undo and redo
also save changes to media choices. Source disks and media are never modified;
RTFM previews and converted export files are generated in managed working
directories. A selected PDF is converted to RTFM text before export.

Gotek exports one image per release. When several image purposes have saved
selections, the default priority is title screen, front cover, then gameplay
screenshot, followed by other purposes. Unresolved image alternatives are held
for review. Manuals awaiting a choice are also withheld from export.

## Matching corrections

DAT matching computes comparable SHA-1, MD5 and CRC hashes from each release's
own disk files, checks file size, and verifies that the bytes still match the
scan's SHA-256 identity. It never compares different hash algorithms or borrows
another release's disks. Normalized title lookup applies its limit after
identity matching, avoiding missing candidates because of punctuation or
spacing differences.

Manual matching preserves numbered part identities. A high-scoring manual
does not cause weaker rival documents to be included in its output. Generic,
versionless artwork can be reviewed for versioned releases; artwork explicitly
marked for another version stays distinct.
