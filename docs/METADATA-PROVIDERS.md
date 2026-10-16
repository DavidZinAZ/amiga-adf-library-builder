# Metadata, Providers, and Local Sources

**Applies to v0.3.13. Reviewed 2026-10-16.**

## Different source systems

| System | Role |
|---|---|
| Metadata Sources | Indexes local DAT identity records in `Library-Root/data/metadata_sources.db` |
| Local Media | Discovers local artwork and manual sources read-only |
| Providers | Configures generic online provider panels; currently Wikipedia and ScreenScraper |
| Manual Lookup | Searches/browses manual sources and applies selected results to curation |
| Canonical/curated state | Records identity claims and operator-authoritative decisions |
| Provider caches | Reuses lookup results; refresh does not erase curation authority |

Online access is off by default, and every online provider checkbox starts unchecked on a fresh GUI installation. Enable the providers you want and save settings. Previously saved explicit choices are restored. Options permission and individual provider enablement are separate controls.

## DAT identity

Add a DAT file or DAT folder on Metadata Sources. Rescan a source or reindex changed sources when its content changes. Removing the configured source removes its index association, not the original DAT file.

Disk-byte matching compares compatible SHA-1, MD5, or CRC values and file sizes using each release's own disks, while checking the scan's SHA-256 identity. A normalized title candidate is useful evidence but not a verified byte match. Numbered parts and distinct release versions must remain distinguishable.

The GUI, lookup, and pipeline should use the resolved Library-Root metadata-source database. Do not create a second index in the application data directory as a workaround.

## Local Media

Add artwork roots and manual roots separately. Recognised artwork category folders are detected automatically; the GUI no longer asks for an asset type per folder. Flat folder layouts retain a default/saved category internally for compatibility.

Folder ordering is meaningful, but different plausible assets can still require an operator decision. Duplicate bytes are consolidated for review. An indexed-image count is not a count of accepted covers.

For selected artwork purposes, the normal priority is title screen, front cover, gameplay screenshot, then other purposes. A logo or back-cover category should not become a front-cover alternative merely because an entire library folder was selected.

## Manual sources and RTFM

Local TXT/PDF manuals feed the RTFM builder. Native PDF text is attempted before offline OCR. Packaged builds include the extraction backends and RapidOCR model/runtime data; source installs need GUI/document extras. OCR results are content-addressed and reusable without reloading the OCR engine on a cache hit.

RTFM activity reports source discovery, matching, document text extraction, OCR pages, cache reuse, and a ten-second heartbeat during long operations. No source, explicit omission, awaiting review, and extraction/build error are separate outcomes.

A primary manual and optional extras are combined only after explicit selection. Weak rival documents are not silently mixed into an accepted manual. PDFs are converted to text before RTFM export.

Shared and per-disk artwork/RTFM choices, file reassignment, and undo are documented in [Media Choices](MEDIA-CHOICES.md).

## Online integrations

Wikipedia and ScreenScraper are the generic Providers-tab entries. Configure only fields the current app exposes. ScreenScraper availability depends on the configured access; a Ready/configured state is not proof that a relevant lookup will succeed.

The core also has provider-specific integration paths, including optional key-based sources. Those are distinct from the generic GUI registry. Consult configuration fields in the current source rather than assuming every core provider has a GUI panel. Manual source searching likewise has its own controls and permission.

Inspect diagnostics for disabled, not configured, blocked/challenged, transport failure, parse failure, and successful-no-result states. Online relevance checks are needed: a similar title or a result from another platform/version is not automatically authoritative.

## Credentials, cache, and provenance

Store credentials through the application's secret workflow, never in public settings, logs, issues, or Git. Back up the vault and retain its unlock password when upgrading.

Provider caches reduce repeat lookups. Refresh may query enabled providers again; it does not remove saved operator overrides. Canonical identity, curated metadata, media choices, and 1G1R selection have separate owners. Revert a decision through its owning workflow rather than deleting arbitrary databases.

NFO display text stays concise. Richer metadata/artwork/manual evidence belongs in provenance sidecars and curation history outside the Gotek export. See [NFO Provenance](gotek-nfo-provenance.md) and [State Ownership](STATE-OWNERSHIP-MAP.md).

## Source configuration

The CLI can use provider/local-media TOML configuration. [The example configuration](../config/example.toml) describes portable path and source settings. Internal legacy mapping keys are retained for saved-settings compatibility; current UI wording is Local Media.

For symptoms and recovery see [Troubleshooting](TROUBLESHOOTING.md). Historical provider implementation and feasibility documents are under [the archive](archive/README.md); they are dated records, not a list of currently enabled providers.
