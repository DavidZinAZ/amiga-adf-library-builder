# ISS-14: LaunchBox Games Database — Feasibility & Access Determination

**Status:** INFEASIBLE / BLOCKED  
**Date:** 2026-08-17  
**Author:** Case (software-engineer)  
**Related Issue:** GitHub #14

---

## Executive Summary

**Disposition: INFEASIBLE / BLOCKED**

There is no compliant third-party integration path for an optional LaunchBox Games Database online metadata+artwork provider. Implementation is blocked by:

1. **No official public API exists** — Confirmed by LaunchBox's own feedback portal.
2. **No documented terms permitting third-party automated access or redistribution** — The only publicly accessible artifact (Metadata.zip) lacks an explicit license for third-party automated use, caching, or image redistribution.
3. **Image access mechanism is undocumented for external use** — While image URLs can be constructed from Metadata.zip data, no terms authorize third-party automated retrieval or redistribution.

The provider MUST remain OFF by default with only a documented, non-functional config placeholder.

---

## Evidence Summary

### 1. No Official Public API

**Source:** LaunchBox Feedback Portal — "Public API Access for the LaunchBox Games Database"  
**URL:** https://feedback.launchbox-app.com/p/public-api-access-for-the-launchbox-games-database-globewithmeridianswrench  
**Key excerpt:** "Currently, there is no official API available for accessing the LaunchBox Games Database. This limits integration opportunities with community tools, external scrapers, frontend plugins, and content management systems."

**Status:** Open to community (as of 2026), not implemented, no timeline.

### 2. Only Available Access Method: Metadata.zip

**Source:** LaunchBox Community Forums — "Is there a public way to get images from the LaunchBox Games Database?"  
**URL:** https://forums.launchbox-app.com/topic/54163-is-there-a-public-way-to-get-images-from-the-launchbox-games-database/  
**Key excerpts:**
- "There is no API for LaunchBox, but the Games Database can be downloaded daily from https://gamesdb.launchbox-app.com/Metadata.zip, and contains a lot of game metadata, but no mention of images." — insanj (Mar 26, 2020)
- "This just simply isn't true lol. I don't know how you can look at the metadata package and conclude that, since it includes metadata for all of the images. It includes the image file names, which can be easily used to construct a URL." — Jason Carr, LaunchBox Founder (Mar 27, 2020)

**Source:** GitHub — Skyscraper Issue #132 "Support for Launchbox Games DB as scraper"  
**URL:** https://github.com/muldjord/skyscraper/issues/132  
**Key excerpts:**
- "There seems to be no API for scraping the LaunchBox. LaunchBox seems to just download a zipped metadata with xml files it then parses." — RolfVeinoeSorensen (Mar 20, 2019)
- "If used, I would have to set up an API myself, which is not something I am willing to do... But it would be textual data only, as I don't think they would allow the downloading of images. Either way, I would need permission to do this as well." — muldjord, Skyscraper maintainer (Mar 20, 2019)
- "Looked into this a bit more and I've concluded that this isn't really feasible to do... I would need permission to do this as well." — muldjord (Jul 16, 2019) — **Issue closed as infeasible**

### 3. Third-Party Automated Use / Redistribution Terms

**Findings:**
- **No public Terms of Service, Terms of Use, or License page exists** for the Games Database at gamesdb.launchbox-app.com. Attempts to access `/terms`, `/tos`, `/privacy` return 404.
- **No explicit license** accompanies the Metadata.zip download.
- **Jason Carr (founder)** acknowledged "at least a couple other apps using the LaunchBox Games Database already" but did not specify terms, grant permission, or link to a license.
- **Skyscraper maintainer** explicitly stated: "I would need permission to do this as well" and closed the issue due to lack of permission and technical infeasibility.
- **LaunchBox Collective Master Terms & Conditions** (https://www.launchboxcollective.com/master-terms-and-conditions) govern **commercial studio/production services**, not the Games Database — different entity, different scope.

### 4. Image Access Mechanism

**Technical mechanism (per Jason Carr):**
- Metadata.zip contains XML with `<Game>` entries including `DatabaseID` and image metadata (file names, types, regions).
- Image URLs can be constructed from this data (pattern observed: `https://images.launchbox-app.com/games/images/{DatabaseID}/{filename}` or similar).
- **No documented base URL, URL schema, or authorization for external automated access.**

---

## Disposition Detail

| Criterion | Finding |
|-----------|---------|
| Official public REST API | **None exists** — confirmed by LaunchBox feedback portal (status: "Open To Community", not implemented) |
| Documented public data feed (Metadata.zip) | **Exists** at https://gamesdb.launchbox-app.com/Metadata.zip — daily update, XML format |
| Explicit license for third-party automated access to Metadata.zip | **None found** — no license file, no ToS page, no grant in forum posts |
| Explicit license for third-party image retrieval/redistribution | **None found** — Jason Carr confirmed technical feasibility but did not grant permission |
| Rate limits / auth requirements documented | **None** — no public documentation |
| Community precedent for standalone third-party tools | **None compliant** — Skyscraper declined; other apps referenced are LaunchBox plugins (in-app), not standalone integrations |

**Conclusion:** No compliant path exists. The only access method (Metadata.zip + constructed image URLs) lacks:
- An explicit license permitting third-party automated download, parsing, caching, or redistribution
- Documented terms for image retrieval (rate limits, attribution, caching, redistribution)
- Any authorization from LaunchBox for external software to use the database as a metadata/artwork source

---

## Recommendation

1. **Do not implement** any functional LaunchBox Games Database provider.
2. **Add a commented, disabled `[launchbox_gamesdb]` placeholder** in `config/example.toml` with a clear note that implementation is blocked by access terms.
3. **Document this finding** in this feasibility report for future reference.
4. **Revisit only if** LaunchBox publishes an official public API with explicit terms permitting third-party integration.

---

## Sources Index

| # | Source | Type | Date Accessed | Key Claim |
|---|--------|------|---------------|-----------|
| 1 | feedback.launchbox-app.com/p/public-api-access... | Official feedback portal | 2026-08-17 | No official API exists |
| 2 | forums.launchbox-app.com/topic/54163... | Official community forum | 2026-08-17 | Metadata.zip exists; images constructible; founder confirmation |
| 3 | github.com/muldjord/skyscraper/issues/132 | Third-party project issue | 2026-08-17 | No API; permission needed; deemed infeasible |
| 4 | thatdatascienceguy.medium.com/extracting-launchboxs... | Third-party analysis | 2026-08-17 | Metadata.zip structure, parsing approach (no license discussion) |
| 5 | gamesdb.launchbox-app.com/ | Games Database website | 2026-08-17 | No /terms, /tos, /privacy pages (404) |
| 6 | www.launchboxcollective.com/master-terms-and-conditions | Corporate ToS (unrelated) | 2026-08-17 | Covers studio services, not Games Database |

---

## Appendix: Metadata.zip Structure (for reference)

Per public community documentation (Medium analysis, Skyscraper issue):

```xml
<Game>
  <Name>Game Title</Name>
  <ReleaseDate>1993-01-01T00:00:00</ReleaseDate>
  <ReleaseYear>1993</ReleaseYear>
  <Overview>Description...</Overview>
  <MaxPlayers>1</MaxPlayers>
  <Cooperative>false</Cooperative>
  <VideoURL>https://www.youtube.com/watch?v=...</VideoURL>
  <DatabaseID>17687</DatabaseID>
  <CommunityRating>2.75</CommunityRating>
  <Platform>3DO Interactive Multiplayer</Platform>
  <ESRB>Not Rated</ESRB>
  <CommunityRatingCount>20</CommunityRatingCount>
  <Genres>Education</Genres>
  <Developer>The Software Toolworks</Developer>
  <Publisher>The Software Toolworks</Publisher>
  <!-- Image metadata also present per Jason Carr -->
</Game>
```

Image URL construction (per community observation, not official docs):
- Base: `https://images.launchbox-app.com/games/images/{DatabaseID}/`
- Filenames and types listed in Metadata.xml image associations

**This structure is documented for reference only — no license grants permission to use it.**

---

*End of feasibility report.*

---

## Recovery Verification Addendum — 2026-08-17 (Q Branch / technical-advisor)

This report was independently re-verified during a **standalone ISS-14 recovery**
(Kanban `t_ea77d472`), treating the original as evidence to confirm rather than a
conclusion to inherit. The original disposition — **INFEASIBLE / BLOCKED** —
is **CONFIRMED** and remains the correct terminal disposition.

### Independent checks performed

1. **No official public API (re-confirmed).** The LaunchBox Feedback Portal entry
   "Public API Access for the LaunchBox Games Database"
   (`feedback.launchbox-app.com/p/public-api-access-...`) is still an **OPEN**
   community feature request, explicitly **not implemented**. Critically, the
   portal's own text states the only supported third-party alternative is to
   *"access/parse the users' local `LaunchBox.Metadata.db` file"* or use the
   in-app `Unbroken.LaunchBox.Plugins.Data` namespace. That is a **LOCAL
   LaunchBox Desktop plugin model** — an in-app integration, **not** a standalone
   online provider. It therefore cannot satisfy ISS-14's requirement for an
   optional, standalone external metadata+artwork provider that must function
   without LaunchBox Desktop or network availability.

2. **No Games-Database-specific terms/license (re-confirmed).** No ToS, license,
   or acceptable-use page governs `gamesdb.launchbox-app.com` (no `/terms`,
   `/tos`, `/privacy` — 404). Only the general `launchbox-app.com` privacy policy
   and an unrelated `launchboxgames.com` notice exist. `Metadata.zip` ships with
   **no explicit license** for third-party automated access, caching, or image
   redistribution.

3. **Community precedent unchanged.** Skyscraper issue #132 remains **closed as
   infeasible**; the maintainer declined due to lack of permission and technical
   constraints.

### Conclusion

No compliant, supported, **standalone** third-party integration path exists that
meets ISS-14's acceptance criteria (Phase 0). The provider stays **OFF by
default** with a documented, non-functional config placeholder
(`[launchbox_gamesdb]`, disabled, in `config/example.toml`). Disposition:
**BLOCKED / INFEASIBLE.** Revisit only if LaunchBox publishes an official public
API accompanied by explicit third-party integration terms.

*Addendum end.*
