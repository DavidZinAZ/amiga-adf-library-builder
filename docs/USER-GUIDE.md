# Amiga ADF Library Builder — Full User Guide

**Applies to:** Amiga ADF Library Builder v0.2.26<br>
**Primary platform covered:** Windows 10/11 portable GUI<br>
**Also covered:** command-line operation for advanced users<br>
**Guide date:** 2026-09-22

---

## 1. What Amiga ADF Library Builder does

Amiga ADF Library Builder helps turn an unorganized collection of Amiga disk images into a reviewed, enriched, and exportable game library.

The application is preservation-first. Your original `.adf` and `.dsk` files are treated as source material and are not rewritten by the normal library workflow. The application scans them, identifies and groups releases, enriches them with metadata, artwork, and optional manuals, lets you review and correct the result, then prepares a Gotek-compatible library.

The Windows application is the recommended interface for normal users. The project also contains a command-line interface for automation, troubleshooting, development, and advanced operation.

The normal workflow is:

1. Choose a library root and source-disk folder.
2. Scan the original ADF/DSK collection.
3. Let the application identify games and releases.
4. Enrich the library from configured metadata sources.
5. Review uncertain or incorrect matches.
6. Curate release names, artwork, metadata, and manuals.
7. Optionally select one release per game with 1G1R.
8. Verify the intended export.
9. Export the finished library.

---

## 2. Important concepts

### Library root

The **Library Root** is the folder that owns the application's working library.

A typical layout is:

```text
<library_root>/
├── original/
├── catalog/
│   ├── metadata-cache/
│   └── metadata-curated/
├── assets/
│   ├── artwork-original/
│   ├── artwork-processed/
│   └── nfo/
├── unknown/
├── work/
│   └── staging/
├── output/
├── config/
│   └── manual-approvals/
├── reports/
└── logs/
```

The application may also use a separate cache directory.

### Original disks

`original/` contains your source `.adf` and `.dsk` files.

Treat this as your preservation copy. The application reads these files to identify, hash, group, and export releases. Do not use the application output as a replacement for your original collection.

### Release

A **release** is a particular version of a game represented by one or more disk images. Different cracks, editions, languages, chipset variants, trainers, or alternate dumps may be treated as separate releases.

### Game

A **game** is the canonical title above one or more releases.

The internal model distinguishes:

```text
Game
  -> Release / edition
     -> Disk images
```

This matters when using curation and 1G1R selection.

### Canonical identity

The application maintains a canonical identity for games and releases rather than relying only on the original filename.

Canonical identity is used to keep metadata, provider matches, manual choices, and curation decisions tied to the correct release even when display names change.

### Curation state

The Preview & Curation workspace tracks the state of releases.

Common states include:

- Pending
- Accepted
- Rejected
- Modified
- Needs Review
- Ghost

Additional filters let you find releases that are missing artwork, missing RTFM material, unmatched, or excluded.

### Ghost releases

A **Ghost** is an emptied release identity retained for history/state consistency after operations such as merge or move. It is not intended to be exported as a normal release.

### 1G1R

**1G1R** means **One Game, One Release**.

When enabled for Gotek export, the application chooses one release for each game rather than exporting every variant.

---

## 3. Getting the Windows application

The normal Windows user does **not** need Python.

The current release provides two Windows builds:

### Portable ZIP

```text
amiga-adf-gui-portable.zip
```

This is the recommended version for most users.

Extract the ZIP to a normal writable directory, then run:

```text
AmigaADFLibraryBuilder\AmigaADFLibraryBuilder.exe
```

### Single executable

```text
amiga-adf-gui.exe
```

This is a single-file portable build.

The portable ZIP is generally easier to inspect and troubleshoot because the application's supporting files remain visible in their directory structure.

---

## 4. First launch

When you start the Windows GUI, the main window contains these major tabs:

- **Library**
- **Options**
- **Providers**
- **LaunchBox media**
- **Preview & Curation**
- **Diagnostics**
- **Metadata Sources**
- **Manual Lookup**

Below the tabs is the **Run / Export Settings** area.

The exact controls available may depend on your configuration and whether a previous library state has already been loaded.

---

## 5. Configure the Library tab

The Library tab defines where the application reads and writes data.

The most important setting is the **Library Root**.

Other paths may include the original disk source, export work/staging folder, and export destination.

### Recommended approach

Create a dedicated library directory, for example:

```text
D:\AmigaLibrary
```

Then keep your original disk collection beneath it:

```text
D:\AmigaLibrary\original
```

The application can derive most working paths from the library root.

### Export work folder

The export work folder is a scratch/staging location used while preparing output.

It is not your preservation source.

### Export destination

The export destination is where finished files are written when you perform an actual export.

Review this path carefully before enabling export.

---

## 6. Configure the Options tab

The Options tab contains routine and advanced behavior controls.

### Use online metadata sources

Enable this when you want the application to contact configured online metadata providers.

Online lookup is optional. The application is designed to retain useful offline behavior.

### Refresh metadata even if cached

Normally the application can reuse previously stored provider results.

Enable **Refresh metadata even if cached** when:

- you believe a prior lookup was wrong;
- a provider has changed;
- you enabled a new provider;
- a previously missing game should now be found;
- you want the application to re-query current sources.

Do not enable this merely because you are running the library again. Cached metadata exists specifically to prevent unnecessary provider traffic.

### Include artwork

When enabled, the application attempts to obtain artwork from approved local sources and configured providers.

Turning this off tells the run not to search for artwork.

This is different from **Require artwork before export**.

### Include manuals (RTFM)

When enabled, the application attempts to build or associate RTFM/manual content where sources are available.

RTFM support can use local manual roots and supported provider/manual workflows.

### Remember these settings

The application can persist selected non-sensitive GUI settings for reuse.

Provider credentials are handled separately and are not intended to be written into ordinary configuration fields.

### Local Asset Matching thresholds

These controls determine when local artwork/manual matches can be accepted automatically and when they should be sent for review.

The major concepts are:

- **Auto-match threshold** — high-confidence matches can be accepted automatically.
- **Review threshold** — lower-confidence candidates may be shown for review.
- **Near-tie difference** — two similarly scored candidates can force manual review rather than allowing an arbitrary winner.

If you are unsure, leave these at their defaults.

---

## 7. Providers

The **Providers** tab contains the application's online metadata/identity providers.

Each provider has a generic panel with:

- Enabled/disabled state
- Provider-specific settings
- Optional credentials
- Connection/status check

A provider being listed does not mean it must be enabled.

### Hall of Light

**Hall of Light** is an Amiga-specific metadata source.

In v0.2.26 it is enabled by default in the provider registry and does not require credentials.

It is particularly relevant for Amiga title/release identification.

### Lemon Amiga

**Lemon Amiga** is an Amiga-specific metadata source.

It does not require credentials and is disabled by default in the provider registry.

The current GUI provider describes it as metadata-only.

### IGDB

**IGDB** can provide metadata and artwork.

It requires Twitch OAuth credentials and is disabled by default.

Use **Set credentials...** rather than placing secrets in normal configuration fields.

### ScreenScraper

**ScreenScraper** can provide metadata, artwork, and manual-related data.

It supports several identification methods, including hash-first lookup and title/system search.

It requires ScreenScraper developer credentials. Member credentials may optionally be used depending on the account and service limits.

### RetroAchievements

**RetroAchievements** is an optional metadata/artwork provider with hash-oriented identity support.

It requires an API key and is disabled by default.

### Playmatch

**Playmatch** is an optional ROM-hash identity resolver.

It is disabled by default.

Its intended role is identity correlation rather than simply title scraping.

### Hasheous

**Hasheous** is an optional ROM-hash identity resolver.

It is disabled by default and supports a configured Hasheous-compatible endpoint.

### Provider safety

Do not enable every provider merely because it exists.

A sensible configuration is to begin with the Amiga-specific sources and only add credentialed providers when they provide something you actually need.

---

## 8. LaunchBox media

The **LaunchBox media** tab allows the application to use local image and manual collections.

This is useful when you already maintain a LaunchBox installation or compatible local media library.

The local-media path is designed to be read-only against the external source collection. Matching can use configured image/media directories and manual directories while copying selected material into the ADF Builder's own managed cache.

Typical image categories include:

- Screenshot - Game Title
- Box - Front
- Screenshot - Gameplay

The local-media matcher considers exact and normalized names, canonical titles, disk names, and conservative fuzzy matching.

Uncertain matches should be routed to review instead of silently accepted.

### Why local media is useful

Local media has several advantages:

- works offline;
- avoids repeated network requests;
- can use artwork you already curated;
- preserves your external media collection;
- can provide better matches than generic online services for obscure Amiga releases.

---

## 9. Run / Export Settings

The controls below the tabs determine what the next run will do.

### Build the library

**Build the library (scan, organize, prepare)** runs the library pipeline without performing the final export.

Use this for normal scanning, matching, metadata work, and curation preparation.

### Export the library

**Export the library (writes the final files)** enables the final write path.

Use this only after reviewing your library and destination.

### I understand this run will write files

This is the explicit export safety acknowledgement.

A normal final export requires you to consciously enable the write operation.

### Check only — don't change files

Use **Check only** to validate the intended operation without writing final files.

This is strongly recommended before a significant export.

### Require artwork before export

When enabled, the export is blocked if an accepted release is missing required artwork.

This is different from **Include artwork**:

- **Include artwork** controls whether artwork is searched for.
- **Require artwork before export** controls whether missing artwork is allowed at export time.

### 1G1R: one release per game

Enable this if you want a Gotek library containing one selected release for each game.

Disable it if you want all accepted releases exported.

---

## 10. Running your first library build

A safe first run is:

1. Configure the Library Root.
2. Confirm the originals path.
3. Leave **Build the library** selected.
4. Leave **Export the library** off.
5. Enable **Use online metadata sources** only if desired.
6. Leave **Include artwork** on.
7. Leave **Include manuals (RTFM)** on if you have manual sources.
8. Click **Run**.

During the run, the application scans and organizes the collection and can enrich it according to your provider configuration.

Do not immediately export a large newly scanned library. Review it first.

---

## 11. Diagnostics

The **Diagnostics** tab and activity/status information are the first places to look when something does not behave as expected.

The main window also provides:

- current status;
- progress;
- **Cancel**;
- **Open log files...**;
- ambiguous-match review access when relevant.

Useful diagnostic questions include:

- Did the provider actually run?
- Did the title normalize to the expected game?
- Did the release enter Needs Review?
- Was metadata loaded from cache instead of refreshed?
- Was artwork found but rejected?
- Was the game associated with the wrong canonical identity?
- Did a manual source exist but fail association?
- Did the export gate prevent writing?

---

## 12. Preview & Curation

The **Preview & Curation** tab is the primary place to review what the application intends to do.

The workspace contains:

- toolbar actions;
- release filtering/search;
- a release table;
- a detail/preview pane;
- curation controls;
- state summary.

### Load State

**Load State...** loads a `.library_state.json` file into the workspace.

### Save State

**Save State...** saves the current curation state.

### Export Changes

**Export Changes...** creates a JSON changes/decision patch that can be reapplied.

### Undo / Redo

Curation actions support Undo and Redo.

Keyboard shortcuts include:

```text
Ctrl+Z   Undo
Ctrl+Y   Redo
```

### Filters

The current workspace supports filters including:

- All
- Pending
- Accepted
- Rejected
- Modified
- Needs Review
- Missing Artwork
- Missing RTFM
- Unmatched
- Excluded
- Ghost

Use these filters aggressively. They are much faster than manually scrolling a large collection.

---

## 13. Accepting and rejecting releases

Use the Preview & Curation controls to decide which releases belong in the finished library.

### Accepted

An accepted release is eligible for export, subject to other gates.

### Rejected

A rejected release is intentionally excluded.

### Needs Review

Needs Review means the application does not have enough confidence to silently decide.

Do not treat Needs Review as an error. It is a safety state.

Typical causes include:

- ambiguous title match;
- conflicting providers;
- uncertain local artwork match;
- incomplete disk set;
- unclear edition/release identity;
- near-tied candidates.

---

## 14. Moving ADFs between releases

The curation model supports moving selected disk images from one release to another.

Use this when the automatic grouping placed one or more disks into the wrong release.

Before moving disks:

1. Confirm the source release.
2. Confirm the destination release.
3. Select only the intended ADF files.
4. Check multi-disk numbering and titles.
5. Perform the move.
6. Review both source and destination afterwards.

The operation is designed to update staged curation state, not rewrite the preservation source files.

---

## 15. Merging releases

Use **Merge Release** when two release records really represent the same release and should be combined.

A merge should not be used merely because two games have similar titles.

After a merge:

- the destination receives the intended release content;
- the emptied source identity can remain as a Ghost;
- the curation/audit history preserves what happened.

Always inspect the resulting release before export.

---

## 16. Editing metadata

The Preview & Curation workspace supports metadata correction.

Fields such as release/edition/group information can be reviewed and, where supported, edited.

Manual corrections should be intentional because they can become persistent curation decisions used by later runs.

The goal is that once you fix a release, the application does not make you repeat the same work every time.

---

## 17. Unified Lookup

The Preview & Curation workflow includes a unified lookup experience for releases that need better identification.

The lookup UI separates:

- lookup/query;
- candidates;
- candidate detail;
- applying a selected result.

Lookup can be performed in online or offline/local modes depending on the configured sources.

The online view currently identifies Hall of Light and other configured source paths in the provider chain.

### Candidate review

Do not choose a candidate by title alone.

Review:

- confidence;
- match type;
- provider;
- explanation/reason;
- source state;
- artwork;
- canonical title;
- edition;
- release identity.

For sequels and similarly named games, verify the actual game identity carefully.

---

## 18. Manual Lookup

The **Manual Lookup** tab is a deeper identity/provenance workspace.

It lets you browse canonical entities and inspect claims from metadata sources.

The hierarchy includes:

```text
Game -> Release -> Disk
```

The panel provides views for canonical values, provenance, and precedence.

### Set / Override

Use **Set / Override** to record an operator curation value.

Operator curation is authoritative and is intended to survive provider refreshes.

### Revert Override

Use **Revert Override** to remove the manual claim for that field while keeping automated provider claims available.

### Record as Authoritative Identity

This records a selected identity as an authoritative curation claim in the canonical database.

Use this when you have verified the correct identity and want future processing to respect it.

### Metadata source browser

The Manual Lookup workspace also includes a read-only metadata-source browser.

This allows you to inspect source data rather than guessing what the provider returned.

---

## 19. Metadata Sources

The **Metadata Sources** tab manages indexed metadata sources used by canonical lookup workflows.

This area is distinct from the online provider toggle panels.

Think of the two concepts this way:

- **Providers** are live or configured sources/resolvers.
- **Metadata Sources** are managed/indexed metadata inputs available to lookup and canonical resolution.

In v0.2.26, the canonical metadata-source database path and lookup lifecycle were specifically corrected so the GUI workflows use the same source identity/path consistently.

---

## 20. Artwork

Artwork passes through a managed workflow.

Possible sources include:

- previously approved/cached artwork;
- LaunchBox/local media;
- configured online providers;
- manually selected sources.

The application keeps original/master artwork separate from processed export artwork.

Typical locations are:

```text
assets/artwork-original/
assets/artwork-processed/
```

Do not manually overwrite the preservation/master copy unless you intentionally understand the provenance consequences.

### Missing artwork

Use the **Missing Artwork** Preview filter.

If artwork should exist but does not:

1. Check whether **Include artwork** was enabled.
2. Check local LaunchBox/media mappings.
3. Check provider enablement.
4. Check provider credentials.
5. Review Diagnostics.
6. Use refresh if an earlier failed result is cached.
7. Inspect the release identity—bad identity frequently causes bad artwork lookup.

---

## 21. Manuals and RTFM

ADF Builder supports manual/RTFM workflows.

Manual material may come from:

- local manual folders;
- metadata/manual source associations;
- supported online manual providers;
- explicit/manual association workflows.

The application can associate source documents with canonical game/release identity and use them in the RTFM pipeline.

### Missing RTFM

Use the **Missing RTFM** filter.

If a manual exists somewhere but is not being attached:

1. Confirm **Include manuals (RTFM)** is enabled.
2. Confirm the correct game/release identity.
3. Inspect the Metadata Sources and Manual Lookup views.
4. Check local manual roots.
5. Check provider/manual settings.
6. Refresh or rerun lookup when necessary.
7. Verify that the document belongs to this exact game, not a sequel or similarly named title.

v0.2.26 includes fixes to real document association and Manual Lookup -> Preview -> Export lifecycle behavior, so the manual should be tied to canonical identity rather than merely a loose title string.

---

## 22. Match Review

When local artwork or manual matching is ambiguous, the main window can display a button such as:

```text
Review 3 ambiguous matches
```

Open Match Review rather than lowering thresholds just to make the warning disappear.

For each ambiguous match, determine whether the candidate is actually correct.

Once an operator explicitly approves a match, the application can preserve that decision rather than repeatedly asking.

---

## 23. Provider credentials

Credentialed providers use dedicated secret handling.

Do not place API keys, passwords, or client secrets in:

- filenames;
- screenshots;
- issue reports;
- normal TOML values unless the provider explicitly requires an environment-variable name rather than the secret;
- exported diagnostic text.

Use the application's **Set credentials...** mechanism where available.

Examples of credentialed providers include:

- IGDB
- ScreenScraper
- RetroAchievements

Hall of Light and Lemon Amiga do not require credentials.

---

## 24. Configuration profiles

The GUI supports named configuration profiles.

Profile actions include saving the current settings, saving under a new profile name, and loading a saved profile.

Profiles are useful when you have different workflows, for example:

- full online enrichment;
- offline-only curation;
- LaunchBox-local-media workflow;
- test collection;
- final Gotek export.

Do not assume profiles contain provider secrets. Secret storage is separate.

---

## 25. Safe export procedure

For a mature library, use this sequence:

1. Run a normal Build.
2. Open Preview & Curation.
3. Resolve Needs Review entries.
4. Check Missing Artwork.
5. Check Missing RTFM.
6. Check Unmatched.
7. Review Modified releases.
8. Confirm merge/move results.
9. Confirm 1G1R behavior if enabled.
10. Select **Export the library**.
11. Enable **Check only — don't change files**.
12. Run the verification.
13. Review the destination and diagnostics.
14. Disable Check only.
15. Enable **I understand this run will write files**.
16. Perform the final export.

This sequence is intentionally cautious.

---

## 26. What the application should not do to originals

Normal application workflows should not:

- rename your preservation ADF source;
- rewrite ADF contents;
- delete source disks;
- move your source collection to the export tree;
- silently replace your external LaunchBox library;
- silently publish to arbitrary removable media.

If you believe a run modified an original source file, stop and inspect logs before continuing.

---

## 27. Troubleshooting quick reference

### Preview & Curation is empty

Check:

- Library Root
- Original Disks path
- whether `.adf`/`.dsk` files actually exist in the configured source
- Diagnostics
- whether a library state was generated/loaded

### Online metadata finds nothing

Check:

- **Use online metadata sources**
- provider Enabled state
- provider credentials
- network access
- title/canonical identity
- cached negative/old results
- **Refresh metadata even if cached**
- Diagnostics

### Known title matches wrong sequel

Do not apply the candidate.

Use Unified Lookup or Manual Lookup and verify the canonical game identity. Similar title text alone is not enough.

### Artwork is missing

Check Include Artwork, local media paths, provider state, credentials, candidate review, and release identity.

### Manuals are missing

Check Include manuals (RTFM), manual roots, Manual Lookup, Metadata Sources, provider settings, and canonical identity.

### Filter appears to hide releases

Switch to **All** first, then reapply the intended filter.

Remember that Ghost, Excluded, Missing Artwork, Missing RTFM, and Unmatched are separate filter categories.

### Export is blocked

Check:

- Export mode
- write acknowledgement
- Check only state
- Require artwork gate
- unresolved validation problems
- destination path
- Diagnostics

### Progress or run appears stuck

Open Diagnostics and logs before restarting.

If the GUI is still processing providers or local matching, there may be useful activity even when one display appears unchanged.

### Cancel

Use the GUI **Cancel** button and allow the application to exit its worker operation cleanly.

Avoid killing the process unless the application is truly unresponsive.

---

## 28. Command-line interface

Most Windows users can ignore this section.

The CLI remains useful for automation and advanced maintenance.

### Show help

```bash
amiga-adf-library-builder --help
```

### Initialize

```bash
amiga-adf-library-builder init --library-root /path/to/library
```

### Show resolved configuration

```bash
amiga-adf-library-builder config show
```

### Validate configuration

```bash
amiga-adf-library-builder config validate
```

### Scan

```bash
amiga-adf-library-builder scan --library-root /path/to/library
```

### Build

```bash
amiga-adf-library-builder build \
  --library-root /path/to/library \
  --online \
  --refresh-metadata \
  --export-gate-acknowledged \
  --json
```

### Export

```bash
amiga-adf-library-builder export \
  --library-root /path/to/library \
  --online \
  --require-artwork \
  --export-gate-acknowledged \
  --run-id my-run \
  --json
```

### Verify without writing

```bash
amiga-adf-library-builder export \
  --library-root /path/to/library \
  --export-gate-acknowledged \
  --verify-only \
  --run-id my-run \
  --json
```

---

## 29. CLI configuration precedence

For command-line operation, configuration is resolved in this general order:

1. explicit CLI flags;
2. environment variables;
3. explicit config file;
4. per-user configuration;
5. system configuration;
6. built-in derived defaults after a library root is known.

This allows the same project to work across Windows, Linux, development environments, and portable deployments without hard-coded host paths.

---

## 30. Data that should be backed up

At minimum, back up:

- your original ADF/DSK collection;
- canonical/curation state;
- manual operator decisions;
- metadata-curated records;
- configuration;
- any irreplaceable local artwork or manuals you created yourself.

Caches and processed artwork can often be regenerated, but curated decisions may represent hours of manual work.

---

## 31. Data that is usually reproducible

Depending on your workflow, these can often be regenerated:

- processed artwork;
- downloaded provider cache;
- generated NFO files;
- staging trees;
- run reports;
- temporary work files.

Do not assume every cached provider result will remain available forever. External services can change, so preserve important curated choices.

---

## 32. Recommended workflow for a large collection

For a large collection, do not try to perfectly curate everything in one pass.

A practical sequence is:

### Pass 1 — Inventory

Run a build and confirm the application sees the whole collection.

### Pass 2 — Identity

Resolve obvious unmatched games and wrong canonical identities.

### Pass 3 — Grouping

Fix bad multi-disk grouping and move/merge errors.

### Pass 4 — Metadata

Resolve important missing or incorrect metadata.

### Pass 5 — Artwork

Work the Missing Artwork filter.

### Pass 6 — Manuals

Work the Missing RTFM filter.

### Pass 7 — Review queue

Resolve Needs Review and ambiguous matches.

### Pass 8 — 1G1R

Choose preferred releases if using one-game-one-release export.

### Pass 9 — Verification

Perform a check-only export.

### Pass 10 — Final export

Write the finished library.

---

## 33. Suggested habits

- Keep originals separate from output.
- Use Preview & Curation before export.
- Prefer explicit review over aggressive fuzzy matching.
- Save useful curation decisions.
- Use local media when you already have a good local collection.
- Refresh provider data only when necessary.
- Keep provider credentials out of ordinary files.
- Verify sequels carefully.
- Use Check only before final export.
- Preserve backups of manual curation state.

---

## 34. Current v0.2.26 notes

Version 0.2.26 includes important work around:

- unified canonical identity;
- metadata-source database path consistency;
- Hall of Light parsing;
- sequel rejection;
- real document/RTFM association;
- Manual Lookup persistence through Preview and Export;
- RTFM serialization/pipeline fixes;
- Windows release provenance.

If you are using an older portable executable, verify the version before troubleshooting behavior that has already been fixed.

---

## 35. Related documentation

This full User Guide is intended to become the main user-facing manual.

Additional project documentation should be organized into these companion guides:

1. **Installation Guide** — Windows download, portable layout, upgrades, first launch, developer install.
2. **Windows GUI Walkthrough** — every tab, control, dialog, and common workflow.
3. **Metadata & Provider Guide** — provider setup, credentials, precedence, caching, local media, canonical identity, manual sources.
4. **Troubleshooting Guide** — symptoms, diagnostics, recovery, logs, provider failures, matching problems.
5. **Developer Guide** — architecture, source layout, tests, packaging, data ownership, contribution workflow.

---

## 36. Final safety checklist

Before a final export, confirm all of the following:

- Correct Library Root
- Correct Original Disks path
- Correct Export Destination
- No unexpected Needs Review releases
- No unintended Ghost releases selected for output
- Important releases have the expected metadata
- Artwork is correct
- Manuals/RTFM are associated with the correct game
- Multi-disk games contain the right disks in the right release
- Merge and Move operations look correct
- 1G1R selection is correct if enabled
- Check-only verification passed
- You intentionally enabled the write acknowledgement

Once those conditions are satisfied, the library is ready for final export.

---

**End of Full User Guide — v0.2.26**
