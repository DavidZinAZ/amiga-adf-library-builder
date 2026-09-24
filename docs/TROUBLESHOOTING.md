# Amiga ADF Library Builder — Troubleshooting Guide

**Applies to:** Amiga ADF Library Builder v0.2.26<br>
**Guide date:** 2026-09-22

---

## 1. How to use this guide

Start with the symptom you see, not with the feature name.

For most problems, the fastest diagnostic path is:

```text
1. Confirm the Library Root and source paths
2. Check Diagnostics
3. Open saved logs
4. Check Preview & Curation state/filter
5. Check provider/local-media configuration
6. Check canonical identity and provenance
7. Retry only after you know what failed
```

Avoid repeatedly refreshing, rerunning, or lowering match thresholds without first identifying the failure.

---

# 2. First diagnostic checks

Before deeper troubleshooting, verify:

- you are running v0.2.26 or the intended version;
- the Library Root is correct;
- the Original Disks path exists;
- `.adf`/`.dsk` files are present;
- the application can write to its portable state directory;
- the application can write to its staging/output directories;
- final export is not accidentally pointed at the original source directory.

Then open:

```text
Diagnostics
```

and:

```text
Open log files…
```

---

# 3. Preview & Curation is empty

## Symptoms

- Preview & Curation shows zero releases.
- The source directory contains ADF files.
- The app appears to complete a run but nothing is listed.

## Check

1. Open **Library**.
2. Confirm the Library Root.
3. Confirm the Original Disks path.
4. Verify files actually exist there.
5. Switch Preview filter to:

```text
All
```

6. Clear the Preview search box.
7. Check Diagnostics for scan errors.
8. Confirm the run actually performed a Build/scan.

## Common causes

- wrong source directory;
- stale search text;
- active filter hides everything;
- scan failed before state population;
- library state was not loaded/generated;
- permissions or missing files.

## Recovery

Run a normal Build with final export disabled, then reopen Preview & Curation.

---

# 4. A release disappeared from Preview

## Symptoms

A release was visible, then appears to vanish.

## First check

Set:

```text
Filter: All
```

and clear the search box.

Then check:

```text
Ghost
Excluded
Rejected
```

filters.

## Common causes

- state filter changed;
- release was rejected;
- release was excluded from export;
- merge emptied the source release into Ghost;
- search text no longer matches;
- move/merge changed title/group metadata.

---

# 5. Filter appears broken

## Symptoms

- selecting Pending/Accepted/etc. seems to show the wrong releases;
- a release remains hidden after a state change.

## Recovery

1. switch to **All**;
2. clear search;
3. select the intended filter again;
4. click another row and return;
5. if still wrong, save state and reload Preview state.

If behavior persists, capture the Diagnostics/logs and exact filter/state combination.

---

# 6. Online lookup finds nothing

## Symptoms

Diagnostics may show messages equivalent to:

```text
No metadata found from online sources
```

## Check

- **Use online metadata sources** is enabled;
- the intended provider is enabled;
- required credentials are present;
- provider connection check succeeds;
- network access is available;
- the title/query is clean;
- the result is not being served from stale cache.

## Try

Use:

```text
Lookup…
```

then:

```text
Alternate search
```

with a clean canonical title.

Example:

```text
Hacker II: The Doomsday Papers
```

instead of a filename-heavy query such as:

```text
Hacker II The Doomsday Papers v1.0 cr XYZ
```

---

# 7. Known game exists online but does not match

## Symptoms

You can find the game manually on Hall of Light, Lemon Amiga, or another provider, but ADF Builder returns no match.

## Check

- sequel number;
- punctuation;
- subtitle;
- Roman numeral vs Arabic numeral;
- edition text;
- crack/version tokens;
- canonical title.

Use:

```text
Unified Lookup
Manual Lookup
Metadata Sources
Explain Match / Provenance
```

If the provider result exists but is rejected, inspect the relevance reason before forcing an override.

---

# 8. Wrong sequel is matched

## Example

```text
Game
```

is matched to:

```text
Game II
```

or vice versa.

## Do not

Do not simply Accept Match.

## Recovery

1. Open Unified Lookup.
2. Use Alternate Search with the exact title.
3. Compare candidates.
4. Check provider, confidence, and reason.
5. Open Manual Lookup.
6. Record the correct authoritative identity if verified.
7. Reject the wrong match.

This is exactly the kind of case where title similarity alone is unsafe.

---

# 9. Provider says Ready but still returns nothing

"Ready" means the provider is configured enough to attempt use.

It does **not** guarantee a match.

## Check

- provider enabled;
- online mode enabled;
- provider supports the title/platform;
- credentials valid;
- quota/rate limit;
- clean query;
- Diagnostics;
- cache state.

Try a known-good title before concluding the provider itself is broken.

---

# 10. Provider credentials do not work

## Check

- use **Set credentials…**;
- do not place secrets in ordinary provider fields;
- verify account/API credentials with the provider;
- use **Check connection…**;
- inspect Diagnostics for authentication failures.

## Upgrade-related issue

If credentials worked in an older portable folder, confirm:

```text
config\secrets.vault
```

was intentionally preserved or re-created.

---

# 11. Online lookup keeps returning old data

## Symptom

You changed provider settings or fixed identity, but the same metadata returns.

## Try

Enable:

```text
Refresh metadata even if cached
```

Then rerun lookup.

## If nothing changes

A higher-authority value may be winning.

Open:

```text
Manual Lookup
```

and inspect:

```text
Canonical values, provenance and precedence
```

Look for:

- manual override;
- curated record;
- authoritative identity;
- another stronger claim.

---

# 12. Refresh does not override a manual correction

This is expected.

Manual/operator curation is intended to survive normal provider refresh.

If the manual value is no longer correct, use:

```text
Revert Override
```

rather than repeatedly refreshing providers.

---

# 13. Metadata is right but the displayed canonical value is wrong

Open:

```text
Manual Lookup
```

Inspect the claims for the field.

Possible cause:

```text
provider says A
manual override says B
```

The canonical system may correctly be honoring B.

If B is obsolete, revert that override.

---

# 14. Artwork is missing

## Check

- **Include artwork** enabled;
- local media folders exist;
- correct asset type assigned;
- provider artwork capability enabled;
- provider credentials;
- artwork candidate not sent to review;
- release identity is correct.

Use filter:

```text
Missing Artwork
```

Then inspect:

```text
Artwork Preview
Explain Match / Provenance
Diagnostics
```

---

# 15. Artwork path exists but preview says not found

The Preview pane can explicitly report:

```text
Artwork not found: <path>
```

## Causes

- file moved;
- drive unavailable;
- stale state points to an old cached file;
- local media root changed;
- application state restored from another machine.

## Recovery

Re-run artwork lookup or manually select artwork again.

---

# 16. Artwork preview says unreadable

Possible causes:

- corrupt image;
- unsupported image;
- partial download;
- file extension does not match content.

Try opening the source image outside the app.

If invalid, replace/re-fetch it.

---

# 17. Wrong artwork is selected

Do not lower thresholds.

Use:

```text
Match Review
Select Artwork…
Explain Match / Provenance
```

Check whether the wrong image came from:

- an incorrect canonical identity;
- a fuzzy local-media candidate;
- root ordering;
- a provider result.

If multiple local roots contain the same category, remember:

```text
first configured root wins same-type conflicts
```

Use Move Up / Move Down in LaunchBox media.

---

# 18. Local LaunchBox artwork is not found

## Check

- folder mapped under **LaunchBox media**;
- asset type correct;
- path exists;
- **Check roots…**;
- local matching thresholds;
- title normalization;
- region/subfolder layout;
- review queue.

## Also check

The source library is read-only. The app should not reorganize LaunchBox folders.

---

# 19. Ambiguous artwork/manual match

## Symptom

Main window shows:

```text
Review N ambiguous matches
```

## Correct action

Open Match Review.

For each item:

- inspect candidate;
- Select Candidate;
- choose No Match;
- Browse for a correct file.

Do not reduce review thresholds just to empty the queue.

---

# 20. Missing RTFM/manual

Use filter:

```text
Missing RTFM
```

## Check

- **Include manuals (RTFM)** enabled;
- local manual root configured;
- manual path exists;
- document format supported;
- canonical identity correct;
- typed document exists;
- provider/manual source enabled.

Open:

```text
Manual Lookup
```

and use:

```text
Typed-document search (Lemon Amiga)
```

if appropriate.

---

# 21. Lemon Amiga manual exists but app does not attach it

## Recovery

1. Open Manual Lookup.
2. Choose Game / Release.
3. Search Typed Docs.
4. Select the correct document.
5. Apply Selection.
6. Return to Preview & Curation.
7. Re-check Missing RTFM.
8. Open RTFM if available.

If the wrong sequel appears, correct canonical identity first.

---

# 22. Local PDF/TXT manual is ignored

## Check

- folder listed under Manuals / RTFM roots;
- **Check roots…** shows it;
- root ordering;
- file readable;
- supported extension;
- title matches canonical identity.

If document extraction is involved, optional RTFM dependencies may be required in a developer install.

---

# 23. Metadata Sources DAT file does not show results

Open:

```text
Metadata Sources
```

Check:

- source listed;
- source enabled;
- entry count > 0;
- status healthy;
- path exists.

Then try:

```text
Rescan
```

or:

```text
Reindex Changed
```

---

# 24. Metadata source path is stale

v0.2.26 includes fixes around metadata-source database path consistency.

If you upgraded from an older version and lookup still seems to use stale data:

1. confirm current Library Root;
2. inspect Metadata Sources;
3. rescan/reindex;
4. reload Manual Lookup;
5. verify source path and entry count.

---

# 25. Manual Lookup does not show recent changes

Click:

```text
Reload
```

This reloads canonical records.

If still stale, complete/re-run the Build that persists the state, then reload again.

---

# 26. Preview lookup applies metadata but not artwork

This can be expected.

The lookup application and artwork path are separate concerns.

Online candidate artwork URLs may be shown without immediately becoming a local artwork file path.

Use:

```text
Select Artwork…
```

or a normal enrichment/artwork run to materialize the artwork.

---

# 27. Release is stuck in Needs Review

Open:

```text
Review…
```

The dialog shows:

- review reason;
- current state;
- staged candidate.

Then choose an explicit decision.

Needs Review is not a dead-end state.

---

# 28. Review reason is unclear

Use:

```text
Explain Match / Provenance
```

and inspect the Curation Actions decision log.

The most recent review-relevant actions usually explain why the row entered review.

---

# 29. Accept button does not export immediately

Expected behavior.

Preview & Curation changes staged state.

Final files are written only through explicit Export mode.

This separation is intentional.

---

# 30. Rename does not rename originals

Expected behavior.

Rename changes staged/export identity.

The source ADF preservation files should remain unchanged.

---

# 31. Move ADF operation moved the wrong disks

Use Undo immediately if appropriate.

Then:

1. View ADF List;
2. select source release;
3. choose Move ADF(s) to Release;
4. check only intended files;
5. inspect destination preview.

The move dialog moves only explicitly checked ADFs.

---

# 32. Merge Release produced a Ghost row

Expected.

When a source release is emptied by merge, its identity can remain as Ghost for history/state integrity.

Use the Ghost filter to inspect it.

The destination should contain the merged disks.

---

# 33. Merge did not appear to update the table

Try:

- select another filter then return to All;
- select the destination row;
- inspect ADF count;
- inspect source under Ghost;
- save/reload state if the visual state appears stale.

If disk membership is wrong, use Undo.

---

# 34. Undo/Redo behaves unexpectedly after move/merge

Move and merge actions use stored payload state for recovery.

If Undo produces an unexpected result:

- stop further edits;
- save the state;
- inspect both source/destination ADF lists;
- capture logs and the decision log.

Avoid stacking more moves/merges on top of a state you no longer understand.

---

# 35. Progress appears stuck

## Check Diagnostics first

The UI may still be processing:

- providers;
- artwork;
- local matching;
- manuals;
- hashing;
- indexing.

If log entries continue, the run is active.

## If no activity

Look for:

- network timeout;
- provider rate limit;
- blocked filesystem;
- inaccessible network share.

---

# 36. Progress jumps to 100%

The progress bar is a high-level run indicator, not a precise per-file performance meter.

Use Diagnostics for actual activity.

If it reaches 100% but the UI never returns to idle, capture logs.

---

# 37. Cancel does not stop immediately

Cancel is cooperative.

The worker may need to finish the current bounded operation before stopping.

Use the GUI Cancel button first.

Do not kill the process unless it is truly unresponsive.

---

# 38. App appears frozen during cancel

Check:

```text
Diagnostics
```

If the UI cannot update at all, wait for the current provider/filesystem operation to return.

If the process is genuinely hung, collect logs after recovery.

---

# 39. Final export is blocked

Check:

```text
Export the library
I understand this run will write files
Check only
Require artwork before export
ADF Library Export Folder
```

Possible causes:

- write acknowledgement not checked;
- Check only still enabled;
- artwork requirement failed;
- invalid destination;
- validation failure.

The export-state summary should indicate the current gate state.

---

# 40. Export runs but writes nothing

Check whether:

```text
Check only — don't change files
```

is enabled.

Check-only validates without writing final files.

---

# 41. Export folder not set

Do not run export.

Correct it under Library / Run settings.

If a wrong export already occurred, inspect what was written before deleting anything.

Never point export at the original source directory.

---

# 42. Export conflict

If an existing run/output conflicts, use a new run ID or verify-only mode in CLI workflows.

For CLI:

```bash
amiga-adf-library-builder export \
  --library-root /path/to/library \
  --export-gate-acknowledged \
  --verify-only \
  --run-id existing-id \
  --json
```

---

# 43. Require artwork blocks export

Expected if any accepted release lacks artwork.

Use:

```text
Missing Artwork
```

to locate blockers.

Either:

- supply artwork;
- reject/exclude the release;
- disable the artwork requirement if missing art is acceptable.

---

# 44. 1G1R selected the wrong release

Check:

- accepted/rejected state;
- operator decisions file;
- canonical Game grouping;
- edition/group metadata;
- selection manifest.

If releases that should be separate games are grouped together, fix canonical identity first.

---

# 45. 1G1R seems to remove releases

It intentionally selects one release per Game.

Disable:

```text
1G1R: one release per game
```

to export all accepted releases.

---

# 46. Release is quarantined

Ambiguous or incomplete groups may be quarantined instead of guessed.

Inspect the unknown/quarantine data and review the cause.

Typical reasons:

- incomplete multi-disk set;
- ambiguous title;
- special-only media;
- grouping uncertainty.

---

# 47. Original source files changed

This should not happen during normal staged workflows.

If you suspect it:

1. stop;
2. compare file hashes/backups;
3. inspect logs;
4. do not perform further writes;
5. confirm you are checking the original source path rather than exported copies.

---

# 48. App cannot save settings

The portable app needs write access to its base directory.

Move it to a writable path such as:

```text
C:\Tools\AmigaADFBuilder
```

Check that it can create:

```text
config
data
logs
cache
themes
```

---

# 49. Settings vanished after upgrading

Portable settings may live in the old app directory.

Inspect:

```text
config\gui-settings.toml
```

and intentionally migrate settings.

The Library Root itself should remain separate from the application directory.

---

# 50. Logs contain sensitive values

The app installs redaction for secrets, but always inspect files before sharing them externally.

Never share:

```text
secrets.vault
API keys
passwords
tokens
```

---

# 51. SmartScreen blocks the EXE

The release is unsigned.

Verify:

- official GitHub release;
- SHA-256 hash;
- expected version.

Do not bypass warnings for files from unknown mirrors.

---

# 52. Single-file EXE starts slowly

Expected.

PyInstaller onefile builds extract their runtime to a temporary location at startup.

Use the portable ZIP build for faster/more transparent startup.

---

# 53. Version seems wrong

For the portable release, verify the release tag/download source.

For developer installs:

```bash
python -c "import importlib.metadata as m; print(m.version('amiga-adf-library-builder'))"
```

v0.2.26 should report:

```text
0.2.26
```

---

# 54. Provider connection works in GUI but run does not use it

Check both:

```text
provider Enabled
Use online metadata sources
```

Also check whether that provider is wired into the specific workflow you are running.

Some lower-level providers may exist in core/config but not be exposed through the same GUI registry path.

---

# 55. Provider panel says credentials stored but requests fail

Possible causes:

- expired/revoked credentials;
- account quota;
- wrong endpoint;
- provider service outage;
- rate limit;
- mismatched developer/member account settings.

Use Diagnostics and provider-specific account documentation.

---

# 56. Rate limiting / HTTP failures

Do not repeatedly hammer Run.

Let the provider cooldown/rate-limit window clear.

Disable unnecessary providers.

Use cache when possible.

---

# 57. Network share is slow or intermittent

Symptoms may include:

- long scans;
- delayed artwork;
- timeout-like behavior;
- stale paths.

Check:

- share availability;
- permissions;
- latency;
- Windows drive mapping;
- server sleep/disconnect.

For large collections, local fixed storage is generally more predictable.

---

# 58. LaunchBox drive letter changed

If local media points to:

```text
E:\LaunchBox
```

and Windows now mounted it as:

```text
F:\LaunchBox
```

the app will not find the old path.

Update LaunchBox media roots and re-check them.

---

# 59. Local root precedence chooses the wrong file

The first configured root wins same-type conflicts.

Reorder with:

```text
Move Up
Move Down
```

Then rerun local matching.

---

# 60. Too many ambiguous matches

Do not immediately lower thresholds.

First ask why:

- poor filename normalization;
- duplicate media;
- overlapping roots;
- sequel-heavy collection;
- bad canonical identities.

Fix root cause before changing confidence bands.

---

# 61. Too few matches

Possible causes:

- auto/review thresholds too high;
- root not indexed;
- titles differ substantially;
- provider disabled;
- offline mode;
- missing credentials.

Inspect candidates before adjusting thresholds.

---

# 62. Candidate artwork is correct but metadata is wrong

Use:

```text
Accept Metadata Only
```

only when appropriate, or separately select artwork.

Do not force a single candidate package if only one component is correct.

---

# 63. Metadata is correct but filename should stay unchanged

Use:

```text
Keep Current Filename
```

after applying metadata.

---

# 64. Open Artwork / Open NFO / Open RTFM is missing

Those context-menu actions appear only when the corresponding file path exists.

If absent, the artifact may not yet be generated/associated.

---

# 65. "Show Source" points somewhere unexpected

Use:

```text
Explain Match / Provenance
```

to determine whether the source came from:

- local media;
- provider cache;
- curated metadata;
- DAT source;
- manual override.

---

# 66. Search Sources returns nothing

Check Metadata Sources first.

A source browser cannot return entries from an unindexed or empty DAT source.

---

# 67. Manual override cannot be applied

Check:

- Game/Release/Disk selected;
- valid field selected;
- non-empty value;
- canonical DB writable;
- current entity still exists.

Reload the entity picker if state changed recently.

---

# 68. Revert Override seems to do nothing

Possible reasons:

- no manual claim exists for that field;
- another authoritative source supplies the same value;
- UI has not reloaded.

Click Reload and inspect the claims table again.

---

# 69. Alternate search keeps using the old query

Close and reopen the lookup dialog, then confirm the query field.

If lookup is already running, wait for it to complete before launching another.

The Preview lookup worker prevents overlapping lookup jobs.

---

# 70. Lookup appears to do nothing when clicked repeatedly

The application avoids starting another lookup worker while one is already active.

Check the lookup dialog/status and Diagnostics.

---

# 71. Curation changes disappear after a new build

Current GUI state is designed to persist curation decisions and carry them over by release identity.

If decisions disappear:

- confirm same Library Root;
- confirm same canonical release identity;
- confirm state file/location;
- inspect whether the release key changed;
- check logs for state load/save errors.

---

# 72. A release key changed after identity correction

This can affect state carry-over.

Use canonical identity tools and inspect the decision log.

If necessary, reapply the curation once under the corrected identity.

---

# 73. NFO exists but contains wrong source

Open:

```text
Explain Match / Provenance
```

Check canonical metadata source.

Correct the authoritative claim before regenerating export.

---

# 74. RTFM serialization/export error

v0.2.26 includes fixes for RTFM serialization and pipeline failures.

If you still see an RTFM serialization error:

- verify version;
- capture the exact stack/log;
- identify the release and manual source;
- do not assume the old bug is still the cause.

---

# 75. Hall of Light parsing looks wrong

v0.2.26 includes Hall of Light parsing and sequel-rejection fixes.

Confirm you are truly running v0.2.26 before filing a regression.

Capture:

- query;
- result URL;
- canonical title;
- provider reason;
- Diagnostics log.

---

# 76. Manual Lookup applies selection but Preview does not reflect it

v0.2.26 specifically addresses the Manual Lookup -> Preview -> Export lifecycle.

Try:

1. Reload Manual Lookup;
2. apply again only if needed;
3. return to Preview;
4. select another row and back;
5. rerun Build if the persisted canonical state needs regeneration.

If still stale, capture the canonical entity ID and release key.

---

# 77. Diagnostics view is moving too fast

Toggle:

```text
Follow Live: On
```

off.

Then inspect older lines.

Use Jump to top when necessary.

---

# 78. Diagnostics was cleared accidentally

The Clear button clears the visible display.

Use:

```text
Open log files…
```

to inspect saved logs.

---

# 79. What to collect for a bug report

Include:

```text
ADF Builder version
Windows version
exact symptom
steps to reproduce
release title/key
provider involved
whether online/offline
relevant Diagnostics lines
relevant saved log excerpt
filter/state
screenshot if useful
```

Do not include:

```text
API keys
passwords
secrets.vault
private credentials
```

---

# 80. Recovery hierarchy

When something goes wrong, prefer this order:

```text
1. Inspect
2. Undo if safe
3. Reload UI/state
4. Re-run lookup
5. Re-run Build
6. Reindex local source
7. Refresh online metadata
8. Restore from backup/state
```

Avoid destructive cleanup as the first response.

---

# 81. Final emergency rule

If you are unsure whether an operation is touching original source files:

```text
STOP
```

Verify paths and hashes before continuing.

ADF Builder is designed around preserving originals, and the safest response to unexpected filesystem behavior is to inspect first.

---

**End of Troubleshooting Guide — v0.2.26**
