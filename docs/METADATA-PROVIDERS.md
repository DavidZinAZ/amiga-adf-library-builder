# Amiga ADF Library Builder — Metadata & Provider Guide

**Applies to:** Amiga ADF Library Builder v0.2.26<br>
**Guide date:** 2026-09-22

---

## 1. Why this guide exists

ADF Builder has several different kinds of metadata input, and they are easy to confuse.

The application can use:

- live online providers;
- cached provider results;
- operator-curated metadata;
- indexed DAT/local metadata sources;
- local LaunchBox/media folders;
- ROM-hash identity resolvers;
- manual/document sources;
- canonical curation claims.

These are related, but they are not the same thing.

This guide explains what each source does, how they interact, when they are authoritative, and how to troubleshoot them.

---

# 2. The metadata model

ADF Builder does not simply ask one website for a game title and trust the first answer.

Instead, metadata is accumulated from several layers:

```text
Original filename / disk identity
        |
        v
Parsed release identity
        |
        v
Canonical Game / Release / Disk identity
        |
        +--> Curated/operator claims
        +--> Cached metadata
        +--> Online providers
        +--> DAT/indexed metadata sources
        +--> Local media / LaunchBox
        +--> Hash identity services
        +--> Manual/document sources
```

The purpose of the canonical layer is to make metadata stable even if:

- filenames are messy;
- providers disagree;
- artwork comes from a different source than text metadata;
- the user manually corrects a release;
- the same game has multiple versions.

---

# 3. Canonical identity

The canonical model distinguishes:

```text
Game
  -> Release
     -> Disk
```

This is important because metadata can belong at different levels.

Examples:

- the game title belongs to the Game;
- crack group or edition may belong to a Release;
- a disk number belongs to a Disk;
- artwork may be shared at Game level;
- a manual may apply to a Game or a specific Release;
- a hash belongs to a specific disk image.

The Manual Lookup tab exposes these levels directly.

---

# 4. MetadataRecord fields

Online metadata providers return a structured record with fields such as:

```text
canonical_title
description
year
developer
publisher
genres
platforms
source_url
artwork_url
artwork_source_url
artwork_provider
provider
provider_id
retrieved_at
confidence
query
relevance_category
relevance_confidence
relevance_evidence
```

Not every provider supplies every field.

A provider may be useful for identity but not artwork, or artwork but not manuals.

---

# 5. Curated metadata

Curated metadata is operator-maintained and authoritative.

Typical location:

```text
catalog\metadata-curated\
```

Curated records are loaded with high confidence and take precedence over ordinary provider results.

Use curated metadata when:

- a provider is consistently wrong;
- a rare Amiga release needs a known-good correction;
- title normalization cannot distinguish a special edition;
- you have verified metadata manually.

Curated metadata should be treated as deliberate human knowledge, not as a temporary cache.

---

# 6. Cached metadata

Typical location:

```text
catalog\metadata-cache\
```

Cached metadata stores provider results so the application does not need to repeat network calls every run.

Benefits:

- faster runs;
- less provider traffic;
- resilience when a provider is temporarily unavailable;
- reproducible metadata between runs.

If you want to bypass cached results, enable:

```text
Refresh metadata even if cached
```

or use the equivalent CLI option.

Do this intentionally rather than on every run.

---

# 7. Online relevance validation

ADF Builder does not automatically accept every online result.

Provider candidates can be classified as:

```text
accepted
review
rejected
```

The validation considers things such as:

- canonical title similarity;
- exact title identity;
- sequel-number differences;
- obvious non-game/biographical pages;
- title extensions;
- relevance evidence.

This helps prevent a result for:

```text
Hacker
```

from being incorrectly applied to:

```text
Hacker II
```

or vice versa.

A candidate that cannot be confidently accepted should be routed to review rather than silently cached as correct.

---

# 8. Live provider vs. Metadata Source

These are different concepts.

## Live Provider

Configured under:

```text
Providers
```

Examples:

```text
Hall of Light
Lemon Amiga
IGDB
ScreenScraper
RetroAchievements
Playmatch
Hasheous
```

A live provider may contact a remote API or website.

## Metadata Source

Configured under:

```text
Metadata Sources
```

Examples:

- TOSEC-style DAT
- No-Intro XML
- local indexed metadata folders

Metadata Sources are indexed local data, not live web providers.

---

# 9. Hall of Light

Provider name:

```text
Hall of Light
```

Provider ID:

```text
hall-of-light
```

Current behavior:

- enabled by default;
- no credentials required;
- Amiga-specific metadata lookup;
- relevance validated;
- can parse Hall of Light game pages;
- participates in the main lookup pipeline.

Hall of Light is one of the strongest Amiga-specific sources in the base configuration.

The current GUI adapter describes it as metadata-only, while the lower metadata layer can also discover artwork from approved Hall of Light pages when the artwork workflow uses those pages.

That distinction matters: the provider panel's advertised capability is not necessarily the entire artwork-discovery machinery available elsewhere in the core.

---

# 10. Lemon Amiga

Provider name:

```text
Lemon Amiga
```

Provider ID:

```text
lemon-amiga
```

Current behavior:

- disabled by default;
- no credentials required;
- Amiga-specific metadata lookup;
- metadata-only in the GUI provider adapter;
- also used by typed-document/manual workflows.

Lemon Amiga can expose game metadata and typed documents such as:

- manuals;
- instructions;
- cheats/hints;
- other document links.

The Manual Lookup tab contains a dedicated:

```text
Typed-document search (Lemon Amiga)
```

workflow.

---

# 11. IGDB

Provider name:

```text
IGDB
```

Current capabilities:

- metadata;
- artwork;
- online title lookup.

Requirements:

- Twitch OAuth client ID;
- Twitch OAuth client secret.

The provider is disabled by default.

Credentials should be stored using the GUI's:

```text
Set credentials…
```

workflow or supported environment variables.

Do not put client secrets into public configuration files.

---

# 12. ScreenScraper

Provider name:

```text
ScreenScraper
```

Capabilities include:

- metadata;
- artwork;
- manual-related retrieval;
- hash-first identification;
- provider-ID reuse;
- title + Amiga system search.

Lookup precedence inside the ScreenScraper provider is designed around:

```text
1. ROM hash
2. cached provider ID
3. title/system search
```

This is desirable because an exact ROM hash is generally stronger evidence than a fuzzy title string.

Credentials can include:

```text
developer ID
developer password
software name
optional member ID
optional member password
```

The provider also supports:

- region preference;
- metadata download toggle;
- artwork download toggle;
- manual download toggle;
- cache TTL;
- rate-limit handling.

---

# 13. RetroAchievements

Provider name:

```text
RetroAchievements
```

Capabilities:

- hash-first identity;
- metadata;
- artwork.

The provider hashes the relevant disk locally and compares it against the public RetroAchievements hash set.

An exact hash match is treated as high-confidence identity evidence.

Requires:

```text
RetroAchievements API key
```

Disabled by default.

---

# 14. Playmatch

Provider name:

```text
Playmatch
```

Purpose:

- ROM-hash identity correlation;
- metadata correlation.

The intended privacy model is to transmit only limited public identity material such as:

```text
SHA-256
canonical title
```

rather than local filesystem paths or collection structure.

Disabled by default.

Playmatch is useful when you operate a compatible resolver service.

---

# 15. Hasheous

Provider name:

```text
Hasheous
```

Purpose:

- ROM-hash identity;
- external correlation IDs;
- metadata correlation.

It is disabled by default.

The bundled configuration expects a configured compatible endpoint rather than assuming one universal public endpoint.

No credential is required for the supported subset in the current design.

---

# 16. MobyGames

MobyGames exists in the core metadata/provider architecture and has dedicated documentation/configuration, but it is not currently surfaced in the same generic GUI provider registry shown in v0.2.26.

It can provide:

- metadata;
- cover/screenshot artwork;
- Amiga platform filtering.

Requirements:

```text
MOBYGAMES_API_KEY
```

Typical configuration:

```toml
[mobygames]
enabled = true
api_key_env = "MOBYGAMES_API_KEY"
```

The API key is expected through an environment variable rather than written directly into the config.

---

# 17. Wikipedia

Wikipedia is part of the lower-level metadata fallback machinery.

It is not presented as one of the main configurable GUI provider panels.

It can supply:

- canonical page title;
- textual metadata;
- artwork when appropriate.

ADF Builder applies relevance validation before accepting online results.

Wikipedia should be viewed as a fallback/general source rather than the primary authority for exact Amiga release identity.

---

# 18. RAWG

RAWG is also present in the lower-level online metadata architecture.

It requires:

```text
RAWG_API_KEY
```

It can provide:

- metadata;
- artwork;
- game platform information.

Like Wikipedia, RAWG is not the central Amiga-specific source.

---

# 19. Local media / LaunchBox

Local media is not a live online provider.

It is configured under:

```text
LaunchBox media
```

and/or the local-media configuration.

It is:

- offline;
- read-only against source libraries;
- no account required;
- no API key required.

Typical local source categories:

```text
Screenshot - Game Title
Box - Front
Screenshot - Gameplay
```

The matcher can use:

- exact canonical title;
- exact disk filename stem;
- normalized title;
- canonical reuse;
- conservative fuzzy matching.

Uncertain candidates go to review.

---

# 20. Local-media match priority

A typical priority order is:

```text
1. Screenshot - Game Title
2. Box - Front
3. Screenshot - Gameplay
```

Within those categories, exact and canonical matches should outrank fuzzy ones.

The matcher includes sequel guards so that:

```text
Game
Game 2
```

are not silently treated as the same title.

---

# 21. Local manual roots

LaunchBox/manual configuration can also include manual directories.

Examples:

```text
D:\LaunchBox\Manuals
D:\AmigaManuals
```

The source library remains read-only.

Selected documents are associated with canonical game/release identity rather than merely copied blindly.

---

# 22. RTFM

RTFM is the manual/document processing layer.

It can use:

- local text/manual sources;
- PDF/manual sources where dependencies are available;
- provider document associations;
- typed-document lookup;
- RetroKit/manual sources.

The normal GUI option is:

```text
Include manuals (RTFM)
```

The RTFM subsystem is designed to degrade safely if optional document extraction packages are unavailable.

---

# 23. RetroKit / Archive.org manuals

The configuration also contains an optional manual-only provider:

```text
retrokit_manuals
```

This is not a general metadata or artwork provider.

Its purpose is to find and retrieve a specific manual artifact from the RetroKit/Archive.org dataset and feed it into the RTFM workflow.

It is disabled by default.

No credentials are required.

---

# 24. Typed-document search

Manual Lookup includes:

```text
Typed-document search (Lemon Amiga)
```

This is a specialized document workflow rather than ordinary game metadata search.

Typical fields include:

```text
Provider/Source
Doc Type
Title
Status
URL
```

Use this when a document is known to exist but automatic association did not occur.

---

# 25. DAT / indexed metadata sources

The Metadata Sources tab supports local indexed metadata.

Actions include:

```text
Add DAT
Add Folder
Rescan
Reindex Changed
Remove
```

Supported intent includes:

- TOSEC-style DAT files;
- No-Intro XML;
- folders containing compatible DAT files.

The index is stored in a metadata-source database.

v0.2.26 specifically fixed path consistency so the GUI and lookup workflows use the same metadata-source database.

---

# 26. Metadata-source database

The portable GUI has a metadata-source database such as:

```text
data\metadata_sources.db
```

When a Library Root is configured, the library-resolved metadata source database may be preferred as the authoritative path.

The important rule is:

```text
all lookup workflows should refer to the same authoritative database
```

rather than separate stale copies.

---

# 27. Canonical claims and precedence

Canonical fields can have multiple competing claims.

Example:

```text
Title = "Hacker II"
```

may come from:

```text
parsed filename
Hall of Light
Lemon Amiga
DAT source
manual override
```

The canonical layer tracks:

- field;
- value;
- source;
- authority;
- provenance;
- whether a claim wins;
- whether it is manual.

Manual Lookup lets you inspect this directly.

---

# 28. Manual override

Use:

```text
Set / Override
```

to create an operator claim.

Operator claims are intended to outrank ordinary provider refreshes.

Use this only after verifying the value.

---

# 29. Revert Override

Use:

```text
Revert Override
```

to remove the operator claim.

Automated claims remain available.

This is preferable to deleting provider data manually.

---

# 30. Record as Authoritative Identity

Use:

```text
Record as Authoritative Identity
```

when you have verified the correct canonical game/release identity.

This is stronger than merely selecting a candidate for one run.

It makes the operator's decision part of the canonical database.

---

# 31. Unified Lookup modes

Unified Lookup exposes three main modes:

```text
Online
Offline
Alternate search
```

---

## 31.1 Online

Uses the online provider chain plus curated/cache state as configured.

---

## 31.2 Offline

Uses local sources such as:

```text
LaunchBox
local media
indexed metadata
```

without contacting online providers.

---

## 31.3 Alternate search

Uses a custom query.

This is useful when the original parsed title is noisy.

Example:

Original:

```text
Hacker II The Doomsday Papers v1.0 cr XYZ
```

Alternate search:

```text
Hacker II: The Doomsday Papers
```

---

# 32. Candidate fields

Unified Lookup candidates include:

```text
Confidence
Match Type
Provider
Why
Source State
Status
```

Use all of these, not only confidence.

---

# 33. Provider credentials

Credentialed providers must keep secrets separate from ordinary config.

Examples:

### IGDB

```text
IGDB_CLIENT_ID
IGDB_CLIENT_SECRET
```

### ScreenScraper

```text
SCREENSCRAPER_DEV_ID
SCREENSCRAPER_DEV_PASSWORD
SCREENSCRAPER_SOFTNAME
SCREENSCRAPER_SSID
SCREENSCRAPER_SSPASSWORD
```

### RetroAchievements

```text
RETROACHIEVEMENTS_API_KEY
```

### MobyGames

```text
MOBYGAMES_API_KEY
```

### RAWG

```text
RAWG_API_KEY
```

The GUI's secret store is the preferred path for providers exposed through the GUI.

---

# 34. Secret storage

Portable GUI secret state may be stored under:

```text
config\secrets.vault
```

Do not:

- commit it;
- attach it to GitHub issues;
- send it with diagnostic archives;
- place it on public shares.

Logs are designed to redact secrets, but users should still inspect diagnostic bundles before sharing them.

---

# 35. Cache TTL

Several providers have a configurable:

```text
cache TTL
```

This controls how long provider results can be reused.

Short TTL:

- fresher data;
- more network calls.

Long TTL:

- faster;
- fewer requests;
- potentially older metadata.

For static retro-game metadata, long TTLs are usually reasonable.

---

# 36. Refreshing metadata

Use refresh when:

- a provider bug was fixed;
- a previously wrong title was cached;
- a provider has new data;
- you enabled a new source;
- canonical identity changed.

Do not refresh merely to "make sure" on every run.

Provider APIs may have quotas and rate limits.

---

# 37. Confidence

Confidence is evidence, not truth.

High confidence can still be wrong when:

- two games share similar names;
- a sequel number is lost;
- a regional subtitle differs;
- a crack filename hides the canonical title;
- provider data is incorrect.

Always visually verify suspicious titles.

---

# 38. Hash-first identity

Hash-based identity is generally stronger than title matching.

Providers such as:

```text
ScreenScraper
RetroAchievements
Playmatch
Hasheous
```

can use hashes or external hash correlation.

Advantages:

- independent of messy filenames;
- resilient to punctuation/title formatting;
- strong exact-match evidence.

Limitations:

- not every dump exists in every provider;
- modified/cracked disks can hash differently;
- provider hash databases can be incomplete.

---

# 39. Title-based identity

Title matching remains necessary because many Amiga collections contain:

- cracked releases;
- alternate dumps;
- trainers;
- custom versions;
- naming conventions absent from external hash databases.

ADF Builder therefore combines:

```text
hash evidence
canonical title
release markers
provider data
operator curation
```

rather than relying on one signal.

---

# 40. Artwork provenance

Artwork records can include:

```text
artwork_url
artwork_source_url
artwork_provider
```

The application preserves source/master artwork separately from processed derivatives.

Typical locations:

```text
assets\artwork-original\
assets\artwork-processed\
```

This lets you trace where an exported image came from.

---

# 41. Metadata provenance

Important metadata records preserve fields such as:

```text
provider
provider_id
source_url
retrieved_at
```

Do not remove these merely to make files look cleaner.

They are useful when investigating wrong matches.

---

# 42. NFO provenance

ADF Builder also preserves richer provenance separately from concise Gotek-facing NFO content.

This helps keep export-facing files small while retaining machine-readable/human-readable traceability.

---

# 43. Recommended provider strategy

For a typical Amiga user:

```text
1. Hall of Light
2. Lemon Amiga if desired
3. Local LaunchBox/media sources
4. ScreenScraper if credentials are available
5. IGDB as supplemental metadata/artwork
6. RetroAchievements for hash-supported identity
7. Specialized hash resolvers only if you operate/use them
```

Do not enable every source by default.

More providers can mean more disagreement and more review work.

---

# 44. Recommended metadata precedence mindset

Think in terms of evidence quality:

```text
verified operator decision
exact hash identity
trusted canonical/local DAT identity
Amiga-specific provider match
general online provider
fuzzy title candidate
```

The exact internal resolution rules can vary by workflow, but this is a useful operator mental model.

---

# 45. Troubleshooting: provider shows "Ready" but no results

Check:

- online lookup is enabled;
- the provider itself is enabled;
- credentials exist;
- query title is correct;
- provider supports Amiga;
- cached negative state;
- rate limits;
- Diagnostics log.

Try Alternate Search with a clean canonical title.

---

# 46. Troubleshooting: Hall of Light finds the wrong sequel

Use:

```text
Unified Lookup
Manual Lookup
Explain Match / Provenance
```

Verify the exact canonical title and provider page.

Do not Accept Match until the sequel identity is correct.

---

# 47. Troubleshooting: Lemon Amiga manual exists but is not attached

Check:

```text
Manual Lookup
  -> Typed-document search (Lemon Amiga)
```

Search the clean title.

Inspect the typed-document results.

Select the correct manual.

Click:

```text
Apply Selection
```

Then verify:

```text
Preview & Curation
  -> Missing RTFM
```

---

# 48. Troubleshooting: local artwork is wrong

Check:

- configured folder;
- asset type;
- local matching thresholds;
- near-tie review;
- canonical identity;
- Match Review.

Do not simply lower the confidence threshold until the wrong image auto-matches.

---

# 49. Troubleshooting: DAT data does not appear

Check:

```text
Metadata Sources
```

Verify:

- source is enabled;
- path still exists;
- entry count is non-zero;
- status is healthy.

Then use:

```text
Rescan
```

or:

```text
Reindex Changed
```

If the source was indexed before a major v0.2.26 path fix, confirm the application is using the correct metadata-source database.

---

# 50. Troubleshooting: provider data is correct but canonical value is wrong

Open:

```text
Manual Lookup
```

Select the entity.

Inspect:

```text
Canonical values, provenance and precedence
```

Look for a higher-authority claim.

Possible causes:

- old manual override;
- curated metadata;
- stale authoritative identity;
- conflicting local source.

Use Revert Override only if the manual claim is no longer valid.

---

# 51. Troubleshooting: refresh does not change the result

Possible reasons:

- curated metadata outranks the provider;
- a manual claim is authoritative;
- canonical identity still points at the same game;
- provider returned the same result;
- another cache layer exists;
- the provider did not actually run.

Use:

```text
Explain Match / Provenance
```

and Manual Lookup rather than repeatedly refreshing.

---

# 52. Security model

Online providers are guarded against unsafe outbound targets.

The metadata layer includes protections against:

- loopback addresses;
- link-local addresses;
- private network addresses;
- unapproved artwork hosts;
- unsafe URL schemes.

Provider responses are treated as data, not executable instructions.

---

# 53. Offline mode

ADF Builder can operate without network access.

Offline workflows can still use:

- original filenames;
- canonical state;
- cached metadata;
- curated metadata;
- local DAT sources;
- LaunchBox artwork;
- local manuals;
- prior curation decisions.

This is useful for preservation environments and repeatable library builds.

---

# 54. Provider matrix

| Source | Type | Credentials | Metadata | Artwork | Manuals | Hash identity | Default |
|---|---|---|---|---|---|---|---|
| Hall of Light | Online Amiga provider | No | Yes | Core can discover | No | No | Enabled |
| Lemon Amiga | Online Amiga provider | No | Yes | Limited/core discovery | Typed docs | No | Disabled |
| IGDB | Online provider | Twitch OAuth | Yes | Yes | No | No | Disabled |
| ScreenScraper | Online provider | Developer credentials | Yes | Yes | Yes | Yes | Disabled |
| RetroAchievements | Online provider | API key | Yes | Yes | No | Yes | Disabled |
| Playmatch | Identity resolver | Optional token | Correlation | No | No | Yes | Disabled |
| Hasheous | Identity resolver | No for current subset | Correlation | No | No | Yes | Disabled |
| MobyGames | Core online provider | API key | Yes | Yes | No | No | Disabled |
| Wikipedia | Fallback | No | Yes | Possible | No | No | Fallback |
| RAWG | Fallback | API key | Yes | Yes | No | No | Optional |
| LaunchBox/local media | Local | No | Limited identity support | Yes | Yes | No | User configured |
| DAT sources | Local indexed | No | Yes | No | No | Depends on data | User configured |
| RetroKit manuals | Manual provider | No | No | No | Yes | No | Disabled |
| Curated records | Local authoritative | No | Yes | Can reference | Can reference | No | Authoritative when present |

---

# 55. Best practices

- Prefer canonical identity over raw filenames.
- Use hash evidence when available.
- Keep Amiga-specific providers near the front of your workflow.
- Treat manual overrides as durable decisions.
- Preserve provenance.
- Do not enable sources you do not need.
- Do not lower confidence thresholds merely to eliminate review work.
- Use typed-document lookup for missing manuals.
- Use Metadata Sources for DAT/local indexes, not the Providers tab.
- Use Refresh only when there is a reason.
- Keep credentials out of normal configuration files.

---

**End of Metadata & Provider Guide — v0.2.26**
