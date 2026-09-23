# Amiga ADF Library Builder Documentation

**Current documentation baseline: v0.2.26 — 2026-09-22**

Everything in the main list below is intended to describe the current
application. Ticket-era, migration, research, and superseded implementation
documents have been moved under [`archive/`](archive/).

## User documentation

- [Quick Start](QUICKSTART.md) — fastest Windows-first path to a first build.
- [Full User Guide](USER-GUIDE.md) — complete end-user workflow.
- [Installation Guide](INSTALLATION.md) — Windows installation, upgrades, source/developer install.
- [Windows GUI Walkthrough](WINDOWS-GUI-WALKTHROUGH.md) — current tabs, controls, lookup, curation, and export behavior.
- [Metadata & Provider Guide](METADATA-PROVIDERS.md) — online providers, local sources, canonical identity, artwork, manuals, caching, provenance.
- [Troubleshooting Guide](TROUBLESHOOTING.md) — symptom-first diagnostics and recovery.

## Developer / technical documentation

- [Developer Guide](DEVELOPER-GUIDE.md) — development setup, architecture, testing, packaging, contribution/release workflow.
- [Architecture](ARCHITECTURE.md) — current high-level shared-core architecture.
- [State Ownership Map](STATE-OWNERSHIP-MAP.md) — current durable-state ownership model.
- [Data and State Layout](DATA-LAYOUT.md) — current library/application state layout.
- [Command Reference](COMMANDS.md) — CLI reference.
- [Windows Build Reference](BUILD-Windows.md) — current Windows packaging/qualification path.
- [Gotek Export Format](gotek-export-format.md) — stable Gotek-facing layout reference.
- [Gotek NFO Provenance](gotek-nfo-provenance.md) — stable NFO/provenance reference.
- [Test Data Policy](test-corpus.md) — repository test-corpus policy.

## Documentation maintenance

- [Documentation Audit — 2026-09-22](DOCUMENTATION-AUDIT-2026-09-22.md)
- [Historical / research archive](archive/)

When current behavior changes, update the corresponding current guide and
`CHANGELOG.md` with the code change. Historical ticket documents should remain
in the archive rather than being presented as current product documentation.
