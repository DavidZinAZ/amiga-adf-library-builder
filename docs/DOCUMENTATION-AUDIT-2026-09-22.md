# Documentation Audit — 2026-09-22

**Baseline:** Amiga ADF Library Builder v0.2.26

## Summary

The documentation set was audited against the v0.2.26 release and current Windows GUI source.

The primary issues found were:

- root README still described the project first as a command-line tool even though the Windows GUI is now a primary released interface;
- the old Quick Start began with Python/developer prerequisites, which was misleading for normal Windows users because the release is portable and requires no Python installation;
- user-facing, developer, research, QA, and ticket-era documents were mixed together without a clear documentation entry point;
- several permanent documents still contained issue/ticket-era framing;
- troubleshooting coverage was too small for the current GUI/provider/curation surface;
- current workflows such as Preview & Curation, Manual Lookup, Metadata Sources, provider configuration, 1G1R, artwork, RTFM, and export gating needed consolidated user-facing documentation.

## v0.2.26 baseline verified

The documentation refresh targets v0.2.26 and its current GUI tabs:

- Library
- Options
- Providers
- LaunchBox media
- Preview & Curation
- Diagnostics
- Metadata Sources
- Manual Lookup

The refresh also documents the current Windows portable release model, canonical identity workflow, manual/RTFM lifecycle, metadata-source database consistency, provider configuration, local media, curation persistence, move/merge behavior, and export safety controls.

## Remediation

The refreshed documentation set consists of:

- `docs/README.md`
- `docs/QUICKSTART.md`
- `docs/USER-GUIDE.md`
- `docs/INSTALLATION.md`
- `docs/WINDOWS-GUI-WALKTHROUGH.md`
- `docs/METADATA-PROVIDERS.md`
- `docs/TROUBLESHOOTING.md`
- `docs/DEVELOPER-GUIDE.md`

The root README's product-facing section was updated while preserving the existing credits, project-team, AI-assisted-development, security, and contribution material.

Historical/internal architecture, migration, provider-research, Gotek-format, test-corpus, and planning documents are retained rather than automatically deleted.

## Documentation rules going forward

- Describe released behavior, not planned ticket intent, in user-facing guides.
- Keep Windows end-user setup separate from developer/Python setup.
- Update user-facing guides and `CHANGELOG.md` together with behavior changes.
- Preserve technical provenance and historical architecture material where it remains useful.
- Do not claim a feature works merely because an issue or implementation ticket says it was fixed; documentation should reflect qualified current behavior.
