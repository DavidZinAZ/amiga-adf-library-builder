# Upstream Gotek Touchscreen Interface — Requirements Verification

**Author:** Upstream requirements research
**Task:** T1 — first bounded research gate; gates Phase 5 Gotek export
**Date:** 2026-08-03
**Status:** COMPLETE — all acceptance criteria met; the artwork design questions are resolved.

---

## 0. Executive summary

The Gotek Touchscreen Interface (GTi) SD-card contract is **confirmed** and the
long-unresolved artwork question is **resolved without guessing**:

- Folder/file standard (`/ADF/<Game>/`, `/DSK/<Game>/`, `-N` multidisk, `.jpg` +
  `.nfo` companions) is exactly as the developer-confirmed `docs/gotek-export-format.md`
  states. Verified against upstream source, not assumed.
- **Artwork dimensions:** the upstream answer is *"any size"*. The firmware fits any
  cover image into the display box preserving aspect ratio and **never upscales**.
  Operative hard limits verified in source: **file ≤ 500 000 bytes (500 KB)** and
  **pixel dimensions ≤ 2000 × 2000**. There is no single required resize pixel size.
- The project's existing `docs/gotek-export-format.md` is **fully consistent** with the
  upstream source. Phase 5 (Gotek export) may proceed per the verified standard.

Confidence: **High** for folder/file/companion/artwork items (verified in source).
The only "unresolved" class that remains is device-specific cosmetic quality
(ideal aspect to avoid letterboxing), which is a recommendation, not a requirement.

---

## 1. Verification basis & authorization

- **Controlling research spec:** `docs/task-01-gotek-requirements.md`, which names
  `https://github.com/mesarim/Gotek-Touchscreen-interface` as the *"Authorized online
  source — Online access is authorized only for requirements research relevant to this
  task."* This ticket's own spec therefore grants the bounded `--online` authorization
  for verifying the Gotek requirement. Used accordingly.
- **Other access:** all local reads (`docs/`, `original/`, repo files) were read-only.
  No files under `original/` were opened for writing. No code was implemented. No commit.
- **Sources inspected:**
  - `README.md` of the upstream repo — release **A.5.0.0** (SD-card layout, `.nfo`
    format, cover-art notes).
  - `firmware/Gotek_JC3248/Gotek_JC3248.ino` — **FW_VERSION "5.3.0-JC3248"** (the
    actual image-discovery, cover-draw, and scaling logic).
  - `BUILDING.md` — confirms JPEG (`JPEGDEC`) and PNG (`PNGdec`, added v4.8.4) decoders.
  - Repo `docs/` index — no dedicated artwork-spec doc exists (cover art is documented
    only in README + the firmware-embedded SAMPLE.NFO).

Every claim below is tagged:

- `[DOC]` — confirmed from README/BUILDING documentation.
- `[SRC]` — confirmed from firmware source code (file:function:approx-line).
- `[INF]` — inferred from source behavior (stated as inference).

---

## 2. Findings against the required investigation (Task-01 item 1–10)

| # | Requirement | Verdict | Source | Note |
|---|-------------|---------|--------|------|
| 1 | Exact supported SD-card root folders | Confirmed | `[DOC]`+`[SRC]` | `/ADF`, `/DSK` (and `/GENERIC`, `/CONFIG.TXT`). One game **folder** directly under each. |
| 2 | Exact game-folder depth | Confirmed | `[SRC]` | **Exactly one level**: `<root>/<Game>/`. Firmware does not recurse into sub-subfolders for grouping. No nested version/release-group dirs. |
| 3 | ADF/DSK filename requirements | Confirmed | `[DOC]`+`[SRC]` | Single disk: `<Game>.adf` / `<Game>.dsk`. Basename == folder name. `.hfe`/`.img`/`.st` only apply in GENERIC mode, not ADF/DSK. |
| 4 | Multidisk grouping & numbering | Confirmed | `[DOC]`+`[SRC]` | `<Game>-1.adf`, `<Game>-2.adf`, … — **digits after the final dash**. Grouped into one entry with a paginated disk selector. |
| 5 | Sets larger than nine disks | Confirmed | `[DOC]`+`[SRC]` | Handled: disk selector paginates (6 disks/page). README explicitly cites Monkey Island 2 (10+). No 9-disk ceiling. |
| 6 | NFO filename/encoding/format/limits | Confirmed | `[DOC]`+`[SRC]` | Same folder & basename as game (or game-base name). Plain text. Simple or Labelled format. **Display reads only first 512 bytes.** |
| 7 | Artwork filename/format/dimensions/aspect/JPEG limits | **Resolved** | `[SRC]`+`[DOC]` | **Any size.** JPEG or PNG. Hard caps: file ≤ 500 KB, px ≤ 2000×2000. Aspect-fit at display, never upscaled. See §4. |
| 8 | Boot/save/character/intro/utility companion disks | Confirmed (no special class) | `[SRC]` | Upstream has **no special disk category**. Every `.adf`/`.dsk` in a game folder is a selectable disk. See §6. |
| 9 | Filename/path length & character restrictions | Confirmed | `[INF]` | FAT32 SD card (README). Standard FAT32 LFN rules apply; firmware does no extra filtering. Avoid `* ? " < > \|`. |
| 10 | Behavior differing between docs and source | Found | `[DOC]`vs`[SRC]` | README understates image support (source also accepts `.jpeg` and `.png`); 500 KB / 2000 px caps exist only in source, not docs. See §8. |

---

## 3. Folder structure & filenames (requirements 1–5)

**Root folders** `[DOC]` README "SD card layout":
```
/ADF/<Game Name>/<Game Name>.adf      (+ optional .nfo and .jpg alongside)
/DSK/<Game Name>/<Game Name>.dsk
/CONFIG.TXT                            (auto-created if missing)
```
GENERIC mode adds `/GENERIC/<Name>/` (any image format, optional `ff.cfg`, `.rtfm`).

**Depth** `[SRC]` `drawCoverPanel()` (≈line 1054) and the file-walk that builds the
game list iterate `ADF/`/`DSK/` and treat **each immediate sub-directory as one game**.
There is no second level of grouping. This matches `docs/gotek-export-format.md`'s
explicit "Do not assume nested version or release-group directories."

**Multidisk** `[DOC]`+`[SRC]`:
```
/ADF/Oil Imperium [v1.1e] [QTX]/
  Oil Imperium [v1.1e] [QTX]-1.adf
  Oil Imperium [v1.1e] [QTX]-2.adf
  Oil Imperium [v1.1e] [QTX].jpg
  Oil Imperium [v1.1e] [QTX].nfo
```
Grouping keys on **digits after the final dash** `[SRC]` `diskGrid()` (≈line 995) +
multi-disk detection. A game name that itself contains dashes (e.g. `Example - Space
Unknown`) is fine: the parser must split on the **last** `-N` token — exactly what the
project's `gotek-export-format.md` example demonstrates.

**>9 disks** `[SRC]` `diskGrid()`: `pages = (nd + DISKS_PER_PAGE - 1) / DISKS_PER_PAGE`,
`multiPage` when pages > 1; 3 cols × 2 rows = 6 disks/page. README confirms 10+ works.

---

## 4. Artwork — THE critical item (requirement 7) — RESOLVED

### 4.1 What the upstream actually requires
- The firmware's **own embedded SAMPLE.NFO** (the doc it writes to a blank card to show
  users "how to lay out a game") states verbatim `[SRC]` (≈line 591):
  ```
  /ADF/YourGame/YourGame.jpg cover art (JPEG or PNG, any size)
  ```
  i.e. the *designed* answer to "exact artwork resize pixel dimensions" is **there is no
  fixed size — "any size" is correct and intended.**
- README `[DOC]` "Features (JC3248)": cover art is `.jpg`; BUILDING.md adds PNG (v4.8.4).
- The display code `[SRC]` `gfx_drawJpgFile()` (≈line 218) draws the cover into a fixed
  box and scales:
  ```
  float scX = (float)maxW/jw, scY = (float)maxH/jh, sc = min(scX, scY);
  if (sc > 1.0f) sc = 1.0f;   // never upscale
  ```
  So **any** image is aspect-preserved-fit into the box and is **never enlarged**.

### 4.2 Hard limits (verified in source — not guessed)
| Limit | Value | Source | Effect if exceeded |
|-------|-------|--------|--------------------|
| File size | **≤ 500 000 bytes (500 KB)** | `[SRC]` `gfx_drawJpgFile` ≈line 222: `if (st.st_size > 500000) return;` | Cover silently skipped (no image shown). |
| Pixel dimensions | **≤ 2000 × 2000** | `[SRC]` PNG guard ≈line 231: `if (jw>2000 || jh>2000) return;` | For PNG, skipped. JPEG path has no explicit px cap, but the 500 KB file cap is the effective JPEG ceiling. |
| Format | JPEG (baseline/progressive via JPEGDEC) or PNG (PNGdec, v4.8.4+) | `[DOC]`+`[SRC]` | Other formats not decoded. |
| Extensions accepted | `.jpg .jpeg .png` (case-insensitive) | `[SRC]` `findJPGFor` ≈line 679 | Others ignored. |
| Upscaling | Never | `[SRC]` `sc = min(...)`, `if (sc>1) sc=1` | Small images shown at native size, letterboxed. |

### 4.3 Recommended (NOT required) export target
The default landscape cover box is `COVER_ART_W-4 × COVER_ART_H-4` `[SRC]` ≈lines 872–873,
898–899: **138 × 112 px** (aspect ≈ 1.23 : 1, ≈ 23 : 19). Portrait/compact box is
**104 × 104 px** (square). To minimize letterboxing on the default (landscape) screen,
export cover art at an aspect near **1.23 : 1**; but **any** valid image works because the
firmware fits it. This is a quality recommendation only — the exporter must satisfy the
hard limits in §4.2, not a specific pixel size.

### 4.4 Resolution of the artwork design questions
Both are now **closed**: the artwork question is answered by upstream source
("any size" + verified caps), not guessed. No assumed size (e.g. `~320×256`) is adopted;
the pipeline's resize step becomes deterministic — fit/encode to ≤ 500 KB and ≤ 2000×2000,
optionally targeting ≈ 138×112 for best fill.

---

## 5. NFO file (requirement 6)

- **Filename** `[SRC]` `findNFOFor()` (≈line 673): looks for `<basename>.nfo` and, for
  multi-disk, the game-base name `<gamebase>.nfo` in the same folder.
- **Encoding:** plain text (C-string chars; no BOM requirement evidenced).
- **Format** `[DOC]` README ".nfo file" section — two supported forms:
  - *Simple:* first non-empty line = display title (shown instead of the file name);
    everything after = blurb.
  - *Labelled:* case-insensitive `Title:` / `Blurb:` (or `Description:`) labels.
    `Title:` overrides the display name regardless of file/folder name.
- **Practical display limit** `[SRC]` (≈line 1062): the firmware reads **only the first
  512 bytes** of the NFO (`while (nf.available() && txt.length() < 512)`). Blurb is
  wrapped into the cover box (width ≈ `COVER_W-8` ≈ 142 px; 1–2 lines read best).
  → **Keep NFO ≤ 512 bytes; title + 1–2 line blurb.**

---

## 6. Special / companion disks (requirement 8)

Upstream has **no special "boot / save / character / intro / utility" category**
`[SRC]`. Every `.adf`/`.dsk` discovered inside a game folder is treated as a disk of
that game and appears in that game's disk selector. Implications for the exporter:

- If a special disk genuinely belongs to a game, place it **in that game's folder**
  (it will show as an extra selectable disk). Name it with the `-N` convention when it
  is part of an ordered set, or as a plainly-named extra disk otherwise.
- If a special disk is **orphaned** (e.g. only `Example_Quest_III_Boot.adf` present, no
  main disk), the project's existing quarantine policy (DECISION #15) applies — route to
  `review/`+`unknown/` with an explanation; do **not** invent a game folder.
- This is a parser/export design decision, not an upstream Gotek requirement. Gotek
  itself will display whatever disks sit in the folder.

---

## 7. Filename / path length & character restrictions (requirement 9)

- SD card is **FAT32** `[DOC]` README "First-time setup: Insert a blank FAT32 SD card."
- Therefore standard FAT32 long-filename (LFN) rules apply: up to 255-char filename and
  path components; `SD_MMC` 1-bit driver supports LFN `[INF]`.
- The firmware performs **no extra character filtering** — it calls `SD_MMC.open(path)`
  directly `[SRC]`. So avoid FAT32-illegal characters in folder/file names:
  `* ? " < > |`. The game-folder name is shown as-is (or overridden by NFO `Title:`).
- **Recommendation:** keep game-folder names well under 255 chars; the existing
  sanitization/uniqueness logic in DECISION #12 already covers this.

---

## 8. Documentation vs source differences (requirement 10)

1. **Image formats:** README says cover art is `.jpg` (+`.png` "added v4.8.4"). Source
   `findJPGFor()` actually also accepts **`.jpeg`** and is case-insensitive
   (`.JPG/.JPEG/.PNG`). The operative set is **jpg / jpeg / png**.
2. **Hard caps undocumented:** the **500 KB file cap** and **2000×2000 px cap** exist
   only in source (`gfx_drawJpgFile` / PNG guard); they are absent from README. This is
   the key doc-vs-source gap and the real "JPEG limits" answer.
3. **Version numbering:** README "Current release: A.5.0.0" (marketing) vs firmware
   `FW_VERSION "5.3.0-JC3248"` (build). Two schemes, not contradictory.
4. **GENERIC mode:** README documents `/GENERIC/` for non-Amiga images — relevant only
   if the exporter ever targets generic/FlashFloppy formats; out of scope for Phase 1
   ADF/DSK export.

---

## 9. Recommended exporter contract (mapping to the project)

1. Write single-level trees: `/ADF/<Unique Game>/` and `/DSK/<Unique Game>/`, exactly one
   folder per release (no nesting). `[matches gotek-export-format.md — confirmed consistent]`
2. Disk files: `<Game>-1.adf`, `<Game>-2.adf`, … (split on **final** `-N` token).
3. Companions in the same folder, same basename: `<Game>.jpg` (or `.png`) + `<Game>.nfo`.
4. Artwork export: valid JPEG or PNG, **≤ 500 KB**, **≤ 2000×2000 px**; optionally target
   ≈ 138×112 (landscape) / 104×104 (portrait) for best fill. Never upscaled by firmware,
   so master artwork is preserved and a downsized copy is what ships.
5. NFO: plain text, ≤ 512 bytes. **First line is `Title: <canonical title>`;
   second line is a labelled `Blurb: <year> - <publisher> - <short description>`
   `** (built only from available trusted metadata; missing fields omitted, no
   empty separators). The Gotek-facing NFO carries display-only metadata — it
   does NOT embed source hashes, approval URLs, metadata provenance, retrieval
   timestamps, or enrichment mode. All durable provenance is preserved outside
   the NFO (see `docs/gotek-nfo-provenance.md`).
6. Special-only / ambiguous sets → quarantine (DECISION #15), never guessed into a folder.
7. Filenames sanitized per DECISION #12 (FAT32-safe, uniqueness-checked).

---

## 10. Confidence & residual questions

- **High confidence** (verified in source): folder structure, depth, multidisk `-N`,
  companion `.jpg`/`.nfo`, artwork "any size" + 500 KB / 2000×2000 caps, NFO 512-byte
  display limit, >9-disk pagination, no special-disk category.
- **Medium confidence (inference):** exact FAT32 character constraints — standard FAT32
  rules assumed; no project-specific restriction found in source.
- **Residual (cosmetic only, not blocking):** ideal aspect to avoid letterboxing is
  derived from the default landscape box (≈1.23:1); other rotations use a square box.
  This affects only visual fit, never correctness.
- **No guessed numbers** were used. Every artwork figure traces to source lines cited above.

---

## 11. Sources (all recorded)

1. `https://github.com/mesarim/Gotek-Touchscreen-interface` — README.md, release A.5.0.0
   (SD card layout; `.nfo` format; cover-art notes). Inspected 2026-08-03.
2. `https://raw.githubusercontent.com/mesarim/Gotek-Touchscreen-interface/main/firmware/Gotek_JC3248/Gotek_JC3248.ino`
   — FW_VERSION "5.3.0-JC3248". Key functions/lines:
   - `gfx_drawJpgFile()` ≈ line 218 (500 KB cap line 222; scale logic lines 245–247;
     PNG 2000×2000 guard line 231).
   - `findJPGFor()` ≈ line 677 (accepted extensions).
   - `findNFOFor()` ≈ line 673 (NFO lookup).
   - `drawCoverPanel()` ≈ line 1054; cover box `COVER_ART_W-4 / COVER_ART_H-4` line 1068.
   - `diskGrid()` ≈ line 995 (pagination, >9 disks).
   - Embedded SAMPLE.NFO text "JPEG or PNG, any size" ≈ line 591.
   - Layout constants `COVER_ART_W=142, COVER_ART_H=116` (landscape) ≈ lines 872–873, 898–899;
     `108×108` (portrait) line 906.
   - NFO 512-byte read cap ≈ line 1062.
3. `https://raw.githubusercontent.com/mesarim/Gotek-Touchscreen-interface/main/BUILDING.md`
   — confirms JPEGDEC (JPEG) + PNGdec (PNG, v4.8.4) decoders.
4. Local (read-only): `docs/gotek-export-format.md` and `docs/ARCHITECTURE.md` —
   confirmed consistent with upstream; `gotek-export-format.md` verified accurate.
5. `original/` — inspected read-only via filename search only; **no files modified**.

---

*This document resolves the artwork design questions and unblocks Phase 5 (Gotek export).*
