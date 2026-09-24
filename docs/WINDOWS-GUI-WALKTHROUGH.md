# Amiga ADF Library Builder — Windows GUI Walkthrough

**Applies to:** Amiga ADF Library Builder v0.2.26<br>
**Platform:** Windows 10/11, 64-bit portable GUI<br>
**Guide date:** 2026-09-22

---

## 1. Purpose of this guide

This guide walks through the current Windows interface in the order an operator actually encounters it.

It covers:

- the main window;
- every major tab;
- Run / Export Settings;
- Preview & Curation;
- Unified Lookup;
- Match Review;
- Metadata Sources;
- Manual Lookup;
- common dialogs;
- recommended operator workflow.

This is a UI walkthrough, not a replacement for the full User Guide.

---

# 2. Main window overview

The main window is titled:

```text
Amiga ADF Library Builder
```

The current v0.2.26 tab bar contains:

```text
Library
Options
Providers
LaunchBox media
Preview & Curation
Diagnostics
Metadata Sources
Manual Lookup
```

Below the tab area is a persistent:

```text
Run / Export Settings
```

section followed by the main run controls.

The bottom-level controls include:

```text
Run
Cancel
Open log files…
Review N ambiguous matches
```

The review button changes to reflect the number of ambiguous local matches currently waiting for review.

---

# 3. Configuration profiles

The GUI supports saved configuration profiles.

Profile actions include:

- Save current profile
- Save As
- Load Profile

Use profiles when you regularly switch between different library or provider configurations.

Examples:

```text
Offline Library
Full Online Enrichment
LaunchBox Media
Final Gotek Export
Test Collection
```

Profiles store normal settings, not provider secrets.

Credentials are handled separately.

---

# 4. Library tab

The Library tab defines the core filesystem locations.

The visible path controls include:

```text
Library Root
Original Disks
ADF Library Export Folder
```

Each path row has a:

```text
Choose…
```

button.

---

## 4.1 Library Root

This is the anchor for the managed Amiga library.

Example:

```text
D:\AmigaLibrary
```

The application can derive several other paths beneath this root.

Recommended:

```text
D:\AmigaLibrary\
├── original\
├── catalog\
├── assets\
├── work\
├── output\
├── unknown\
├── reports\
└── logs\
```

---

## 4.2 Original Disks

This points to the preservation source collection.

Example:

```text
D:\AmigaLibrary\original
```

The application scans `.adf` and `.dsk` files here.

Treat this as read-only source material.

---

## 4.3 ADF Library Export Folder

This is the real output location — the folder where finished export/library files are written.

The interface describes it as the folder where the completed library is placed.

It should not point at your original disk directory.

---

## 4.4 ADF Library Export Folder

This is where final exported files are written when actual export mode is enabled.

Review this path carefully before every final export.

---

# 5. Options tab

The Options tab is divided into routine metadata controls, advanced controls, and local asset-matching controls.

---

## 5.1 Metadata section

### Use online metadata sources

Checkbox:

```text
Use online metadata sources
```

Enable this when you want the application to contact online providers.

Leave it off for an offline-only run.

---

### Refresh metadata even if cached

Checkbox:

```text
Refresh metadata even if cached
```

Enable this when you want the application to bypass a previously cached lookup and query sources again.

Useful when:

- a bad match was cached;
- a provider has changed;
- you enabled a new provider;
- a title previously returned no result;
- you are deliberately refreshing stale metadata.

Do not leave it enabled for every normal run unless you specifically want repeated network lookups.

---

### Include artwork

Checkbox:

```text
Include artwork
```

This controls whether the application searches for artwork during the run.

It is distinct from the export requirement:

```text
Require artwork before export
```

One controls lookup/processing; the other controls whether missing artwork blocks export.

---

### Include manuals (RTFM)

Checkbox:

```text
Include manuals (RTFM)
```

This controls whether manual/RTFM processing participates in the run.

If disabled, the run skips manual-building behavior.

---

# 6. Advanced options

The Advanced section includes persistent settings and artwork-conversion controls.

---

## 6.1 Remember these settings

Checkbox:

```text
Remember these settings
```

Use this if you want routine settings to persist between sessions.

Sensitive provider credentials are not stored here.

---

## 6.2 Progressive JPEG policy

The GUI exposes a progressive-JPEG conversion policy.

This controls how imported artwork is handled when progressive JPEGs are encountered.

For most users, the default is appropriate.

Change this only if you understand why a particular Gotek/display workflow needs a different conversion behavior.

---

# 7. Local Asset Matching

The Options tab contains confidence controls for matching local artwork and manuals.

The three key thresholds are:

```text
Auto-match
Review
Near-tie
```

---

## 7.1 Auto-match threshold

Above this confidence level, a candidate can be accepted automatically.

Higher values are safer but may create more review work.

---

## 7.2 Review threshold

Candidates above this lower threshold but below auto-match can be routed to review.

---

## 7.3 Near-tie difference

If the two top candidates are too close together, the application can force human review even if the top candidate otherwise appears strong.

This helps prevent accidental matches between similarly named games.

Unless you have a specific matching problem, leave the defaults alone.

---

# 8. Providers tab

The Providers tab renders one panel for each known online provider.

Every provider panel follows the same general layout.

Typical controls are:

```text
Enabled
provider-specific fields
Set credentials…
Check connection…
```

---

## 8.1 Enabled

Checkbox:

```text
Enabled
```

Turning this on makes the provider eligible for use once its required configuration is satisfied.

A provider can be visible in the GUI but still inactive.

---

## 8.2 Provider fields

Providers expose their own non-secret configuration fields.

Examples can include:

- API endpoint
- timeout
- cache TTL
- concurrency
- confidence threshold
- region preference
- download feature toggles

Tooltips explain each field.

---

## 8.3 Set credentials…

Button:

```text
Set credentials…
```

Use this for providers that require secrets.

The credential dialog masks secret values and stores them separately from normal GUI settings.

Do not paste secrets into ordinary provider text fields unless explicitly required by the provider design.

---

## 8.4 Check connection…

Button:

```text
Check connection…
```

Use this after configuring a provider to verify that the configuration is usable.

A successful status check does not guarantee that every game will produce a match, but it confirms the provider is reachable/configured.

---

# 9. Providers currently surfaced in v0.2.26

The provider registry currently includes:

```text
Playmatch
Hasheous
IGDB
ScreenScraper
RetroAchievements
Lemon Amiga
Hall of Light
```

---

## 9.1 Hall of Light

Panel name:

```text
Hall of Light
```

Characteristics:

- Amiga-specific metadata
- no credentials required
- metadata-only in the current provider adapter
- enabled by default in the registry

For Amiga collections, this is one of the most directly relevant sources.

---

## 9.2 Lemon Amiga

Panel name:

```text
Lemon Amiga
```

Characteristics:

- Amiga-specific metadata
- no credentials required
- metadata-only in the current GUI provider adapter
- disabled by default

Separate typed-document/manual workflows also use Lemon Amiga through Manual Lookup.

---

## 9.3 IGDB

Panel name:

```text
IGDB
```

Capabilities:

- metadata
- artwork
- online lookup

Requires Twitch OAuth credentials.

Disabled by default.

---

## 9.4 ScreenScraper

Panel name:

```text
ScreenScraper
```

Capabilities include:

- metadata
- artwork
- hash-aware lookup
- manual-related retrieval

Requires ScreenScraper developer credentials.

It also supports member credentials where applicable.

---

## 9.5 RetroAchievements

Panel name:

```text
RetroAchievements
```

Capabilities:

- metadata
- artwork
- hash-first identity support

Requires an API key.

Disabled by default.

---

## 9.6 Playmatch

Panel name:

```text
Playmatch
```

Purpose:

- ROM-hash identity resolution
- metadata correlation

It is disabled by default.

---

## 9.7 Hasheous

Panel name:

```text
Hasheous
```

Purpose:

- ROM-hash identity resolution
- metadata correlation

It is disabled by default.

---

# 10. LaunchBox media tab

The LaunchBox media tab configures local artwork and manual roots.

This is especially useful when you already maintain a LaunchBox collection.

The tab is divided into two main sections:

```text
Artwork roots (each root has one asset type)
Manuals / RTFM roots (PDF / TXT documents)
```

---

# 11. Artwork roots

The artwork table has columns:

```text
Folder
Asset type
```

Each row maps one folder to one media category.

Buttons include:

```text
Add folder…
Remove
Move Up
Move Down
```

---

## 11.1 Add folder…

Choose a local media directory.

Then assign the appropriate asset type.

Typical categories include:

```text
Screenshot - Game Title
Box - Front
Screenshot - Gameplay
```

---

## 11.2 Remove

Removes the selected folder mapping from the configuration.

This does not delete the source directory.

---

## 11.3 Move Up / Move Down

Changes the priority/order of configured media roots.

This can matter when multiple local sources contain possible matches.

---

# 12. Manuals / RTFM roots

This section configures local document folders.

Supported intent includes PDF/TXT manual material.

Buttons include:

```text
Add folder…
Remove
Move Up
Move Down
```

The source directories are treated as local reference libraries rather than destinations.

---

# 13. Check roots…

Button:

```text
Check roots…
```

Use this after configuring LaunchBox/media/manual roots.

The GUI validates the configured locations and displays diagnostics.

Use this before a large run to catch missing drives or mistyped paths.

---

# 14. Preview & Curation tab

This is the core human-review workspace.

It contains:

- top toolbar;
- filter/search bar;
- release table;
- release-detail pane;
- artwork preview;
- curation action log;
- action buttons;
- context menu.

---

# 15. Preview toolbar

Buttons:

```text
Load State…
Save State…
Export Changes…
Undo
Redo
```

---

## 15.1 Load State…

Loads a saved:

```text
.library_state.json
```

file into the curation workspace.

---

## 15.2 Save State…

Saves the current staged curation state.

Use this before major manual-edit sessions or experiments.

---

## 15.3 Export Changes…

Exports the curation decision log as a reusable JSON changes patch.

This is useful for preserving operator decisions separately from the live state.

---

## 15.4 Undo

Button:

```text
Undo
```

Shortcut:

```text
Ctrl+Z
```

Reverses the most recent curation action when supported.

---

## 15.5 Redo

Button:

```text
Redo
```

Shortcut:

```text
Ctrl+Y
```

Reapplies an action that was just undone.

---

# 16. Preview filters

The filter dropdown currently contains:

```text
All
Pending
Accepted
Rejected
Modified
Needs Review
Missing Artwork
Missing RTFM
Unmatched
Excluded
Ghost
```

---

## 16.1 All

Shows all current release rows.

Use this when you think a release has “disappeared” because of a filter.

---

## 16.2 Pending

Releases awaiting curation decision.

---

## 16.3 Accepted

Releases currently included for export.

---

## 16.4 Rejected

Releases deliberately excluded.

---

## 16.5 Modified

Releases where custom curation edits have been applied.

---

## 16.6 Needs Review

Releases requiring operator attention.

---

## 16.7 Missing Artwork

Shows releases without required/associated artwork.

---

## 16.8 Missing RTFM

Shows releases without associated manual/RTFM content.

---

## 16.9 Unmatched

Shows releases that lack a successful identity match.

---

## 16.10 Excluded

Shows releases excluded from export.

---

## 16.11 Ghost

Shows retained empty/historical release identities.

Ghost rows often result from merge/move operations.

---

# 17. Preview search box

The search box filters by:

- release key;
- title;
- group name.

Use the filter dropdown and search together for large libraries.

Example:

```text
Filter: Missing Artwork
Search: Lemmings
```

---

# 18. Release table

The current release table has seven columns.

The visible data includes:

```text
State
Release Key
Title
Edition
Group
ADFs
Confidence
```

The exact sizing can vary with the window.

---

# 19. Multi-select

The release table supports extended selection.

Use normal Windows selection patterns:

```text
Ctrl+Click
Shift+Click
```

This allows batch state changes such as Accept or Reject.

---

# 20. Main state buttons

Below/near the release list are buttons:

```text
Accept
Reject
Set Pending
Mark Modified
```

---

## 20.1 Accept

Marks selected release(s) as:

```text
Accepted
```

Accepted releases are eligible for export.

---

## 20.2 Reject

Marks selected release(s) as:

```text
Rejected
```

Rejected releases are excluded from export.

---

## 20.3 Set Pending

Returns selected release(s) to:

```text
Pending
```

Use this when you want to reconsider a prior decision.

---

## 20.4 Mark Modified

Marks the selected release as having operator-applied custom changes.

---

# 21. Release Detail pane

Selecting a release populates the right-hand detail pane.

Fields include:

```text
Release Key
Title
Edition
Group
State
Confidence
ADF count
Artwork
RTFM
Provenance
```

---

## 21.1 Edition

Edition is editable in the staged state.

Use this for things such as:

```text
AGA
ECS
CD32 conversion
Budget
Demo
Special edition
```

only when they accurately describe the release.

---

## 21.2 Group

Group is also editable in the staged state.

This may represent crack/release group identity where applicable.

---

## 21.3 Confidence

Shows the current confidence associated with the release match.

Do not treat confidence as absolute truth.

Always inspect the actual candidate when titles are ambiguous.

---

# 22. Artwork Preview

The detail pane includes:

```text
Artwork Preview
```

This shows the artwork associated with the currently selected release.

Use it to catch obvious wrong-game and wrong-edition matches before export.

---

# 23. Curation Actions / Decision Log

The detail pane also includes:

```text
Curation Actions (Decision Log)
```

This records operator actions and state changes.

The decision log is important for:

- auditing manual changes;
- understanding why a release changed;
- undo/redo behavior;
- preserving curation history.

---

# 24. Additional release buttons

Buttons include:

```text
Add Note…
View ADF List…
Rename…
Lookup…
```

---

## 24.1 Add Note…

Opens a dialog for adding a curation note.

Use notes for useful human context such as:

```text
Verified against original box scan
Disk 2 belongs to alternate release
Preferred WHDLoad-equivalent crack
Manual manually associated
```

Keep notes factual and concise.

---

## 24.2 View ADF List…

Shows all disk-image files currently grouped into the selected release.

Use this before:

- moving disks;
- merging releases;
- accepting a multi-disk title;
- exporting an unfamiliar release.

---

## 24.3 Rename…

Changes the staged release name.

This does not mean your preservation source files are renamed.

---

## 24.4 Lookup…

Opens the unified lookup interface for the release.

---

# 25. Preview context menu

Right-clicking a release exposes many curation operations.

Current actions include:

```text
state changes
Rename…
Move to Folder…
Create Folder & Move…
Move ADF(s) to Release…
Merge Release Into…
Online Lookup…
Offline/Local Lookup…
Alternate Search…
Accept Match
Reject Match
Accept Metadata Only
Keep Current Filename
Select Artwork…
Select RTFM/Manual…
Exclude from Export
Restore to Export
Review…
Open Artwork
Open NFO
Open RTFM
Show Source
Explain Match / Provenance
```

This is one of the most powerful parts of the GUI.

---

# 26. Move to Folder…

Moves the staged release into a selected folder structure.

Use this for export organization, not for modifying preservation source files.

---

# 27. Create Folder & Move…

Creates a destination folder and places the staged release there.

Useful for manual library organization when the automatic destination is not what you want.

---

# 28. Move ADF(s) to Release…

This opens a dedicated dialog.

The dialog shows:

```text
Source
ADF files in source
Select ADF(s) to move
Select all
Destination Release
Search
Preview
```

Only checked ADF files are moved.

Recommended procedure:

1. inspect the source ADF list;
2. select only the incorrect disk(s);
3. search for the destination release;
4. verify the destination preview;
5. apply;
6. inspect both releases afterward.

---

# 29. Merge Release Into…

This opens a merge dialog.

The dialog shows:

```text
Source
ADF files in source
Destination Release
Search
Merge Summary
```

Use this only when two release records truly belong together.

After merging, inspect the source Ghost and destination contents.

Do not merge simply because two titles look similar.

---

# 30. Online Lookup…

Runs lookup using the online provider route.

Use this when the automatic match is missing or wrong.

---

# 31. Offline/Local Lookup…

Runs lookup using configured local sources.

This is useful for LaunchBox/local-media collections.

It should not require contacting online providers.

---

# 32. Alternate Search…

Allows a custom query instead of relying on the current release title.

This is useful when:

- punctuation confuses a provider;
- Roman numeral vs. Arabic numeral naming differs;
- subtitle order differs;
- original filename contains crack/version noise.

---

# 33. Accept Match

Accepts the current identified match.

Use only after verifying the candidate.

---

# 34. Reject Match

Rejects the current match.

Use this when the provider result is demonstrably wrong.

---

# 35. Accept Metadata Only

Accepts metadata without necessarily accepting all other candidate-linked assets/identity behavior.

Useful when the textual metadata is right but another part of the candidate package is not.

---

# 36. Keep Current Filename

Preserves the current filename choice even when other metadata changes.

---

# 37. Select Artwork…

Allows manual artwork selection for the release.

Use this when automatic artwork matching is wrong or absent.

---

# 38. Select RTFM/Manual…

Allows manual association of an RTFM/manual source.

This is useful when a document exists but automatic association cannot prove the match.

---

# 39. Exclude from Export

Marks the release as excluded from final export.

The source remains in the library state.

---

# 40. Restore to Export

Reverses export exclusion.

---

# 41. Review…

Opens the release review dialog.

The dialog explains:

```text
why review is required
current state
staged candidate
```

The operator can then accept or reject the staged release.

---

# 42. Open Artwork

Opens the associated artwork file when available.

---

# 43. Open NFO

Opens the generated NFO when available.

---

# 44. Open RTFM

Opens the generated/associated RTFM material when available.

---

# 45. Show Source

Shows the source location for the selected release/material.

Useful for tracing where a match came from.

---

# 46. Explain Match / Provenance

Opens a match explanation dialog.

Use this when you want to know:

- which provider supplied a value;
- why a candidate was selected;
- what source identity was used;
- how a value reached the current release.

This is especially valuable when debugging incorrect automatic matches.

---

# 47. Unified Lookup dialog

The lookup dialog has three tabs:

```text
Lookup
Candidates
Detail
```

---

# 48. Unified Lookup — Lookup tab

The top section identifies the selected release.

It shows information such as:

```text
filename
title
hashes
size
disk identity
provenance
```

There is a search box and:

```text
Search
```

button.

---

# 49. Lookup modes

The Source / Mode group includes:

```text
Online (Hall of Light, curated, cache)
Offline (local media / LaunchBox)
Alternate search (custom query)
```

The actual provider chain is displayed underneath.

---

## 49.1 Online

Use the configured online lookup route.

---

## 49.2 Offline

Use local-media/LaunchBox sources without online-provider access.

---

## 49.3 Alternate search

Allows a custom query string.

---

# 50. Unified Lookup — Candidates tab

The candidate table includes columns:

```text
Confidence
Match Type
Provider
Why
Source State
Status
```

There is also:

```text
Candidate Artwork
```

preview.

This tab is where you compare possible matches.

---

# 51. Candidate evaluation

Before choosing a result, compare:

- canonical title;
- confidence;
- provider;
- match method;
- explanation;
- artwork;
- sequel number;
- edition;
- source status.

A high score is not a substitute for checking identity.

---

# 52. Unified Lookup — Detail tab

The Detail tab shows:

```text
Pre-apply (current)
Post-apply (proposed)
Provenance Check
```

Buttons include:

```text
Apply to release
Compare selected
Dismiss
```

---

## 52.1 Compare selected

Use this before applying an uncertain match.

It lets you see what the candidate would change.

---

## 52.2 Apply to release

Applies the chosen candidate to the staged release.

It should not directly write the final export.

---

## 52.3 Dismiss

Closes/ignores the lookup without applying a candidate.

---

# 53. Match Review window

The Match Review window handles ambiguous local artwork/manual matches.

The window includes:

```text
Release
Candidates
Artwork Preview
```

and navigation controls.

Buttons include:

```text
Select Candidate
No Match
Browse…
Previous
Apply & Next
Close
```

---

## 53.1 Select Candidate

Selects the highlighted candidate for the release.

---

## 53.2 No Match

Records that none of the presented candidates is correct.

This is better than choosing a wrong match just to clear the queue.

---

## 53.3 Browse…

Lets you browse manually for a different local file.

---

## 53.4 Previous

Moves to the previous ambiguous item.

---

## 53.5 Apply & Next

Applies the current decision and advances to the next review item.

This is efficient for processing a long review queue.

---

# 54. Diagnostics tab

The Diagnostics tab contains the live activity log.

The GUI explicitly notes that sensitive values are hidden/redacted.

Controls include:

```text
Show live processing log
Clear
Jump to top
Follow Live: On
```

---

## 54.1 Show live processing log

Enables/disables display of live processing messages.

---

## 54.2 Clear

Clears the currently displayed diagnostics view.

It does not necessarily delete saved log files.

---

## 54.3 Jump to top

Moves to the first line of the visible log.

---

## 54.4 Follow Live

When enabled, the log view follows new entries as they arrive.

Toggle it off when you need to inspect older messages without the window jumping downward.

---

# 55. Open log files…

The main window includes:

```text
Open log files…
```

Use this when the on-screen log is not enough.

Saved logs are useful for:

- provider errors;
- failed matching;
- export problems;
- missing artwork;
- manual association;
- bug reports.

---

# 56. Metadata Sources tab

This tab manages local/indexed metadata sources.

Toolbar buttons include:

```text
Add DAT
Add Folder
Rescan
Reindex Changed
Remove
```

---

# 57. Metadata Sources table

The table includes fields such as:

```text
Name
Type
Entries
Status
Enabled
Path
```

Use this tab for managed DAT-style source indexing rather than live online-provider configuration.

---

## 57.1 Add DAT

Button:

```text
Add DAT
```

Adds a DAT file to the local metadata index.

The tooltip specifically mentions TOSEC-style or No-Intro XML.

---

## 57.2 Add Folder

Adds a folder containing DAT files.

Useful when maintaining a collection of metadata datasets.

---

## 57.3 Rescan

Re-parses the selected source and rebuilds its entries.

Use this after changing the source file.

---

## 57.4 Reindex Changed

Reindexes sources whose content has changed since their last scan.

This is more targeted than rebuilding everything.

---

## 57.5 Remove

Removes the selected source from the index.

This removes the index registration, not necessarily the source file from disk.

---

# 58. Manual Lookup tab

The Manual Lookup tab provides the deepest canonical/provenance controls.

The top row contains entity selectors:

```text
Game
Release
Disk
Reload
```

The hierarchy lets you inspect canonical identity at different levels.

---

# 59. Game / Release / Disk selectors

Choose the game first.

Then choose a release.

Then optionally choose a disk.

This lets the GUI show claims relevant to the selected canonical entity.

---

# 60. Reload

Button:

```text
Reload
```

Reloads the canonical record picker from the canonical database.

Use this after a build or curation operation if the current list appears stale.

---

# 61. Canonical values, provenance and precedence

This section displays claims contributing to the current canonical value.

The claims table includes fields such as:

```text
Field
Value
Authority/source
record key
URL
winner/manual indicators
```

The exact displayed columns are generated from the canonical claim data.

The purpose is to answer:

```text
What value is currently canonical?
Where did it come from?
Why did it win?
Was it manually overridden?
```

---

# 62. Set / Override

Controls:

```text
Field
Value
Set / Override
```

Use this to create an operator curation claim.

Operator curation has high authority and is designed to survive ordinary provider refresh.

Use this only for information you have verified.

---

# 63. Revert Override

Button:

```text
Revert Override
```

Removes the manual override for the selected field.

Automated provider/source claims remain available and can become authoritative again.

---

# 64. Record as Authoritative Identity

Button:

```text
Record as Authoritative Identity
```

Use this when you have verified a specific identity and want the canonical database to record it as an explicit curation claim.

This is stronger than merely viewing a provider candidate.

---

# 65. Metadata source browser

Section:

```text
Metadata source browser (read-only DAT index)
```

Controls include:

```text
query box
Search Sources
Unified Lookup…
Use For Override
```

The results table displays candidate records from indexed sources.

---

## 65.1 Search Sources

Searches the local/indexed metadata database.

---

## 65.2 Unified Lookup…

Launches the broader lookup workflow from the Manual Lookup context.

---

## 65.3 Use For Override

Copies a selected source candidate into an override workflow.

Use this when a DAT/source entry is correct and should become the authoritative manual value.

---

# 66. Typed-document search

Manual Lookup also contains:

```text
Typed-document search (Lemon Amiga)
```

Controls include:

```text
search query
Search Typed Docs
Apply Selection
Refresh
```

The document table includes fields like:

```text
Provider/Source
Doc Type
Title
Status
Availability
URL
selection
```

This is particularly relevant to manual/RTFM association.

---

# 67. Search Typed Docs

Searches Lemon Amiga typed document records for the selected game/release context.

Use this when a manual or related document exists on Lemon Amiga but automatic association did not occur.

---

# 68. Apply Selection

Applies the selected typed document to the current canonical release workflow.

After applying, verify the result in Preview & Curation and the RTFM state.

---

# 69. Refresh

Refreshes the typed-document view.

---

# 70. Run / Export Settings

This section is always important because it determines whether the application is merely preparing the library or writing final output.

Main controls:

```text
Build the library (scan, organize, prepare)
Export the library (writes the final files)
I understand this run will write files
Check only — don't change files
Require artwork before export
1G1R: one release per game (Gotek export)
```

---

# 71. Build the library

Checkbox:

```text
Build the library (scan, organize, prepare)
```

Use this for normal:

- scanning;
- grouping;
- metadata;
- artwork processing;
- curation preparation.

It should not perform the final destination write.

---

# 72. Export the library

Checkbox:

```text
Export the library (writes the final files)
```

This is the primary final-write mode.

Only enable it after reviewing the library.

---

# 73. I understand this run will write files

Checkbox:

```text
I understand this run will write files
```

This is the explicit write acknowledgement.

It is a safety gate.

---

# 74. Check only — don't change files

Checkbox:

```text
Check only — don't change files
```

Runs verification without final writes.

Recommended before every important export.

---

# 75. Require artwork before export

Checkbox:

```text
Require artwork before export
```

When enabled, missing artwork can block export.

Use this when complete visual coverage is an explicit requirement.

---

# 76. 1G1R

Checkbox:

```text
1G1R: one release per game (Gotek export)
```

When enabled, export selection is limited to one preferred release per canonical game.

This is useful when you do not want every crack, version, language, and alternate dump on the Gotek.

---

# 77. Operator decisions file

The GUI exposes a path for a 1G1R operator-decisions JSON file.

This lets explicit operator release choices participate in selection.

Use this for reproducible preferred-release selection.

---

# 78. Selection manifest

The GUI also exposes a selection-manifest path.

This provides provenance for which release was selected during export.

---

# 79. ADF Library Export Folder preview

The GUI shows the resolved export destination in the Run / Export area.

Read it before clicking Run in export mode.

---

# 80. Export state summary

The GUI displays a pre-run export state summary.

Use it as the final sanity check for:

- mode;
- write acknowledgement;
- verify-only state;
- artwork requirement;
- output location.

---

# 81. Run button

Button:

```text
Run
```

Starts the currently configured build/export operation.

Before clicking it, verify the mode.

---

# 82. Cancel button

Button:

```text
Cancel
```

Requests cooperative cancellation of the active worker.

Use this rather than terminating the application whenever possible.

---

# 83. Recommended first-run GUI sequence

For a new collection:

1. Open **Library**.
2. Set Library Root.
3. Confirm Original Disks.
4. Open **Options**.
5. Decide whether online metadata is allowed.
6. Leave artwork enabled.
7. Enable manuals if desired.
8. Configure local matching only if needed.
9. Open **Providers**.
10. Enable only the providers you intend to use.
11. Configure **LaunchBox media** if you already have local artwork/manuals.
12. In Run / Export Settings, choose **Build the library**.
13. Keep final Export off.
14. Click **Run**.
15. Watch **Diagnostics**.
16. Open **Preview & Curation**.
17. Work through Needs Review.
18. Fix wrong grouping with Move/Merge.
19. Work Missing Artwork.
20. Work Missing RTFM.
21. Resolve unmatched titles.
22. Verify 1G1R selections if used.
23. Switch to Export.
24. Enable **Check only**.
25. Run verification.
26. Review output/destination.
27. Disable Check only.
28. Enable the write acknowledgement.
29. Run the final export.

---

# 84. Fast troubleshooting by tab

If something is wrong, start here:

### Wrong path / nothing found

```text
Library
```

### Provider not working

```text
Providers
Diagnostics
```

### Local artwork/manual not found

```text
LaunchBox media
Options
Match Review
```

### Wrong game identity

```text
Preview & Curation
Lookup…
Manual Lookup
```

### Missing DAT/local metadata

```text
Metadata Sources
```

### Missing manual

```text
Preview & Curation -> Missing RTFM
Manual Lookup -> Typed-document search
LaunchBox media -> Manuals / RTFM roots
```

### Wrong artwork

```text
Preview & Curation
Select Artwork…
Match Review
Explain Match / Provenance
```

### Export blocked

```text
Run / Export Settings
Diagnostics
```

---

# 85. GUI safety checklist before final export

Confirm:

```text
Library Root is correct
Original Disks path is correct
ADF Library Export Folder is correct
Needs Review queue is understood
Missing Artwork is acceptable or resolved
Missing RTFM is acceptable or resolved
Unmatched releases are understood
Ghost releases are not unintentionally exported
Move/Merge results are correct
1G1R selections are correct
Check-only verification succeeded
Write acknowledgement is intentional
```

Then perform the final export.

---

**End of Windows GUI Walkthrough — v0.2.26**
