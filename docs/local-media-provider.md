# local-media provider — Local-Media Artwork Provider (Base App)

**Status:** implemented (T6.1).  
**Scope:** offline, read-only, no signup / API key / network.

This document explains the `local_media` provider: what it does, the
guarantees it holds, how to configure it, and how to point it at a LaunchBox
install under Windows, Linux, mounted SMB/NFS, and WSL.

---

## 1. What it is

A generic, pluggable `LocalMediaProvider` with a `LaunchBoxAdapter` (and room
for other adapters later). It discovers box/screenshot artwork from operator-
configured local media libraries, copies the chosen source into the
application's **own** artwork cache, and records provenance that survives the
original library later moving or disappearing.

This is the core local-media provider *base-application* solution: freeware, no account, no
API key, fully usable offline. It replaces any paid/account-gated provider for
the base app.

## 2. Hard guarantees (audited by QA and security reviewers)

* **Read-only against the source library.** Nothing under any configured root
  is ever opened for writing, renamed, deleted, or reorganized. Source files
  are opened read-only (`"rb"`) and only ever copied INTO the application's own
  cache directory. A pre-flight proof (`assert_read_only_roots`) `os.stat`s each
  root to confirm it is visible to this process before discovery; the
  authoritative read-only behavior is the `"rb"` open of individual source images
  (the stat-only check only proves existence/visibility, not openability).
* **Offline.** No network imports, no DNS, no sockets. The provider and its
  tests import nothing from `socket` / `urllib` / `requests`. Tests prove this
  by monkeypatching `socket.socket` to raise and asserting the run still
  completes.
* **Stdlib-only discovery.** Discovery, checksum, copy, and provenance use only
  the Python standard library (`pathlib`, `hashlib`, `json`, `re`, `difflib`,
  `shutil`). `Pillow` is NOT required to discover/score/copy/prove provenance —
  only the existing `artwork.py` resize step touches Pillow, and that runs
  against the app-owned cache copy.
* **Exact category priority.** The priority is, and must remain, exactly
  `Screenshot - Game Title` → `Box - Front` → `Screenshot - Gameplay`. The
  current category is fully searched before the next is considered, and a
  higher-priority confident match always wins.
* **No false merges.** Sequels, similarly named games, editions, and unrelated
  titles must not cross-match. One approved image is reusable for variants of
  the same canonical game (cracks, trainers, alternate dumps, language/chipset
  variants, multi-disk releases) via canonical-title reuse.
* **Uncertain matches go to manual review.** Never silently accepted.

## 3. Matching order (per game)

1. exact canonical game title
2. exact original ROM/disk filename stem
3. normalized title (punctuation + separators removed)
4. canonical-title reuse across cracks/trainers/alt-dumps/language/chipset/
   multi-disk variants (release tags stripped from both sides)
5. carefully scored fuzzy match (guarded against false merges)
6. manual-review queue when confidence is below the approved threshold

Both the candidate's **filename stem** and its **parent folder name** are
considered at each tier, because LaunchBox names games at either level
(`.../Box - Front/Example Space Tactics/001.png` or
`.../Box - Front/Example Space Tactics.png`).

LaunchBox also uses **flat** layouts where the game is named only at the
*file* level with a trailing ordinal,
`.../Screenshot - Game Title/Bubble Bobble-01.png`, and **region-nested**
layouts where a region folder sits between the category and the file,
`.../Screenshot - Game Title/United States/Xenon.png`. In both cases there is
**no per-game folder**, so the game identity is derived from the (ordinal-
stripped) filename. Category and region folder names are never treated as game
titles (see Section 12).

## 4. Canonical-title reuse (variant handling)

A conservative, audited tag list is stripped from both the group title and the
candidate identity when computing the "canonical base": `cr`, `trainer`,
`alt`, chipset tokens (`aga`, `m3`, `ecs`, `ocs`, `rtg`), crack-group tokens
(`qtx`, `skr`), language tokens (`en`, `de`, `fr`, ...), and disk/side markers.
This lets

```
Example Space Tactics
Example Space Tactics M3
Example Space Tactics M3 cr QTX
Example Space Tactics M3 cr QTX alt a
```

all reuse the same LaunchBox `Example Space Tactics` folder — **without** collapsing
a genuine sequel (`Example Space Tactics 2`) onto the base game. The sequels guard is
enforced in scoring: a candidate that is a strict extension of the game title
(e.g. `examplespacetactics2`) scores `0.0` and never matches.

## 5. Caching + provenance

The selected source image is copied into the app cache
(`assets/artwork-original`, app-owned — never the LaunchBox root) **before** any
resize/export. A sidecar `<cached_image>.prov.json` records:

* `source_path` — original LaunchBox path
* `source_sha256` — checksum of the LaunchBox source
* `source_root` — the LaunchBox root it came from
* `category` — the image type selected
* `match_method` — exact_canonical / exact_disk_stem / normalized_title /
  canonical_reuse / fuzzy
* `confidence` — numeric match confidence
* `cached_path`, `cached_sha256`, `cached_at` — the app-owned copy

Because the copy and the provenance sidecar live in the app's own tree, the
provenance survives the original LaunchBox library later moving or
disappearing.

## 6. Diagnostics

Each resolution emits structured per-candidate diagnostics (mirroring the
structured logging structured-logging style) so QA and security reviewers can audit
every candidate evaluated and the chosen winner. In `enrich.py` the new
categories are `local_media` (hit), `local_media_miss` (no match), and
`local_media_review` (uncertain → manual review). Each `LocalMediaResult` also
carries a `candidates_evaluated` list with per-candidate method/score/path.

## 7. Provider order in the base app

1. existing approved local artwork cache (`assets/artwork-original`)
2. configured local-media libraries (this provider)
3. public-domain/CC0 local collections (future)
4. manual-review queue
5. optional external providers — only when separately installed + configured

The provider never hard-requires a network source.

## 8. Configuration

`config/example.toml`:

```toml
[local_media]
enabled = false
roots = ["/path/to/LaunchBox"]
platform_names = ["Commodore Amiga", "Amiga"]
preferred_image_types = ["Screenshot - Game Title", "Box - Front", "Screenshot - Gameplay"]
recursive = true
# confidence_threshold = 0.95
# Issue #33: per-folder mappings (GUI-editable; also persistable via the app).
# media_roots = [{ path = "/path/to/LaunchBox/Box Front", asset_type = "Box - Front" }]
# manual_roots = ["/path/to/manuals"]
```

* `enabled` — master switch.
* `roots` — one or more operator-configured LaunchBox root paths (legacy
  `Images/<Platform>/<Category>` tree discovery).
* `platform_names` — maps to the LaunchBox `Images/<Platform>` folders.
* `preferred_image_types` — the three categories in priority order.
* `recursive` — controls nested-folder descent.
* `confidence_threshold` — auto-accept floor (default 0.95).
* `media_roots` (Issue #33) — **new**: multiple image/media folders, each with
  an **explicit asset type** (e.g. `Box - Front`). See Section 13.
* `manual_roots` (Issue #33) — **new**: multiple folders of manual documents
  (`.pdf` / `.txt`). See Section 13.

The `[local_media]` table is surfaced by `paths.load_local_media_config`, which
preserves the existing config-precedence chain (explicit `--config` > env >
XDG > system).

## 9. Path support (Windows / Linux / WSL / SMB / NFS)

All paths go through `pathlib.Path` with no OS-specific assumptions. Examples:

### Linux (native LaunchBox root or extracted images)
```toml
roots = ["/mnt/data/LaunchBox"]
```

### Windows (native install)
```toml
roots = ["C:/Games/LaunchBox"]
```
`pathlib` normalizes backslashes; forward slashes also work.

### WSL mounting a Windows LaunchBox
```toml
roots = ["/mnt/c/Games/LaunchBox"]
```
WSL auto-mounts Windows drives under `/mnt/<letter>`. The provider only reads
them.

### Mounted SMB share
```toml
# Linux mount of //nas/launchbox at /mnt/launchbox
roots = ["/mnt/launchbox/LaunchBox"]
```
Mount the share read-only at the OS level for defense-in-depth; the provider is
read-only regardless.

### Mounted NFS share
```toml
# /etc/fstab: nas:/export/launchbox /mnt/launchbox nfs ro,...
roots = ["/mnt/launchbox/LaunchBox"]
```

### Multiple roots (e.g. a Windows LaunchBox + a curated SMB copy)
```toml
roots = [
    "/mnt/c/Games/LaunchBox",
    "/mnt/launchbox-curated/LaunchBox",
]
```

### Path resolution notes
* Symlinks are resolved by the OS; the provider never follows into a write path
  under a source root.
* Non-existent roots are skipped (never abort the run).
* Unreadable roots are caught and skipped; discovery continues with the rest.

## 10. Tests

`tests/test_local_media_provider.py` (and helpers) cover:

* exact priority order (Screenshot → Box → Gameplay; higher-priority confident
  match wins even when a lower-priority category also matches);
* recursive nested-folder discovery;
* exact / normalized / canonical-reuse / fuzzy / manual-review behavior;
* multi-disk, crack, trainer, alternate-dump, language, and chipset variants;
* assert no LaunchBox file is modified (checksum before/after);
* assert offline operation (monkeypatch `socket.socket` to raise, proving no
  network call);
* provenance completeness (all required fields present, cached copy exists).
* **local-media provider defect fix — flat and region-nested matching** (see Section 12):
  flat `<Category>/<Game>-NN.png` and region-nested
  `<Category>/<Region>/<file>` layouts resolve to the correct game identity
  from the ordinal-stripped filename; category/region names are never used as
  game titles; numbered/sequel titles are unaffected; canonical reuse across
  crack/trainer/alt/lang/chipset/multi-disk variants still works.

Run with:

```bash
python3 -m pytest tests/test_local_media_provider.py -v
```

## 11. Known constraints / follow-ups

* `Box - Front` is treated as a generic "front box art" category. LaunchBox
  also has `Box - Back`, `Clear Logo`, `Fanart`, etc.; only the three
  configured `preferred_image_types` are searched. Add more by extending
  `preferred_image_types`.
* The provider is stdlib-only for discovery; if a future adapter needs richer
  matching it must remain offline and read-only.
* External/key-gated providers (MobyGames/RAWG/IGDB) remain out of scope for the
  base app per the local-media provider research/qualification gate (operator approval
  required).

## 12. local-media provider defect: flat + region-nested filename matching

### Symptom

The provider discovered all 6,909 LaunchBox artwork files but failed to match
the **flat** and **region-nested** image layouts used by the real collection:

* `<root>/Images/Commodore Amiga/Screenshot - Game Title/Bubble Bobble-01.png`
* `<root>/Images/Commodore Amiga/Screenshot - Game Title/United States/<game image>.png`

The 66-group real run therefore remained at **15 artwork resized / 49 missing**
despite 6,909 valid candidates being indexed.

### Root cause

1. The adapter set `game_folder = entry.parent.name` for **every** discovered
   file. For flat files the immediate parent is the *category*
   (`Screenshot - Game Title`); for region-nested files it is the *region*
   (`United States`, `World`, `Europe`, ...). Those category/region names were
   then used as the candidate's game identity, so they could never equal a real
   game title.
2. The raw filename stem kept its LaunchBox ordinal (`Bubble Bobble-01` ->
   normalized `bubblebobble01`), which never equaled the group's normalized
   title (`bubblebobble`). The guarded fuzzy score (~0.93) routed the match to
   *manual review* instead of auto-accept.
3. `folder_chain_norm` included the category/region/platform names as candidate
   identities, broadening the pool of wrong "game titles."

### Fix (bounded; no provider redesign; `confidence_threshold` untouched)

* **Genuine per-game folder resolution** (`LaunchBoxAdapter._resolve_game_folder`):
  the immediate parent is treated as the game title only when it is neither the
  category nor a region folder; otherwise we ascend toward the category and use
  the nearest genuine folder. When no genuine folder exists (flat or
  region-only layout) it returns `None`, and `LocalMediaCandidate.folder_name`
  refuses to fall back to a category/region name. The game identity is then
  derived from the filename stem.
* **Safe ordinal stripping** (`_strip_launchbox_ordinal`): removes only a single
  trailing `-<1–3 digits>` from filename/folder identities. Verified to leave
  space-separated sequels (`Bubble Bobble 2`), integral titles (`1942`), and
  non-numeric suffixes (`Bubble Bobble-1x`) untouched.
* **Region-name guard** (`_is_region_name`): an audited list of LaunchBox region
  folder names is excluded from both the resolved game folder and the matching
  folder chain. Precise — only EXACT region names are excluded, so a game titled
  `European Soccer` (`europeansoccer` ≠ `europe`) is never mistaken for the
  region `Europe`.
* **Ordinal-aware scoring**: a new normalized tier (3b) matches the
  ordinal-stripped stem/folder/chain against the group title, and the fuzzy
  scorer also considers ordinal-stripped forms.

### Acceptance evidence (deterministic tests)

`tests/test_local_media_provider.py` gained tests pinned to each acceptance
criterion:

* `test_flat_category_file_matches_game_via_stem`
* `test_region_nested_file_matches_game_via_stem`
* `test_region_nested_with_ordinal_stem`
* `test_region_nested_game_folder_above_region`
* `test_region_name_never_used_as_game_title`
* `test_category_name_never_used_as_game_title`
* `test_genuine_per_game_folder_still_used`
* `test_numbered_game_title_ordinal_stripped_not_damaged`
* `test_sequel_title_ordinal_stripped_and_not_merged`
* `test_canonical_reuse_across_variants_issue9`
* `test_ordinal_strip_helper_is_safe`
* `test_is_region_name_detects_regions_not_games`

Full public test suite passes.

## 13. Issue #33 — configurable local LaunchBox folder mappings

Local, GUI-editable mappings between operator folders and LaunchBox media
types, plus manual-document roots. **LOCAL ONLY: no network egress, no
download/redistribution.** This complements (does not replace) the legacy
`roots` tree discovery.

### GUI (LaunchBox tab)

* **Image / media roots** — table of `(folder, asset type)` rows. *Add
  folder…* opens the native Browse picker; the asset type is chosen per row
  (default `Box - Front`, plus the LaunchBox categories from
  `LAUNCHBOX_IMAGE_CATEGORIES`). *Remove* deletes the selected mapping only —
  the folder itself is never touched.
* **Manual roots** — list of folders holding `.pdf` / `.txt` documents.
  *Add folder…* / *Remove* behave the same way.
* **Check roots…** — a **read-only** diagnostic that scans every configured
  root and reports, per root: scanned or missing, and the candidate image /
  manual-file count. **A missing/inaccessible root is retained in the config
  and reported in the diagnostics — it is never deleted.**

### Persistence

The two mappings persist across close/reopen via the settings store
(`launchbox_media_roots`, `launchbox_manual_roots`) and are restored on the
LaunchBox tab when the window reopens.

### Config / CLI

The same mappings are expressible in `[local_media]` as `media_roots`
(a table array of `{path, asset_type}`) and `manual_roots` (a string array),
and are surfaced by `LocalMediaConfig` / `scan_launchbox_roots` (diagnostics)
and the provider (candidate resolution).

### Tests

`tests/test_issue33_launchbox_mappings.py` covers: multi-root config with
distinct asset types; GUI add/remove/persist-restore of both mapping types;
the missing-path **retained-not-deleted** diagnostic; settings/preset
round-trip; and an offline assertion (socket monkeypatched).

`tools/qa_windows_real_exec.py` drives the real GUI LaunchBox tab on the
Windows runtime (offscreen): multi-mapping add with distinct asset types,
Check-roots diagnostic, persist-restore across close/reopen, and the
missing-path diagnostic — each a hard-fail step.
