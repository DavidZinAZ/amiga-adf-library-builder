# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.2.x   | ✅ Yes     |
| < 0.2   | ❌ No      |

We support the latest released `0.2.x` line. Older releases are out of support
and will not receive security fixes.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.** Public disclosure before a fix is available
puts users at risk.

Report security issues privately by emailing:

> **security@amiga-adf-library-builder.example**

Replace `example` with the project's published domain once one is assigned. If a
dedicated security address is unavailable, use GitHub's private vulnerability
reporting feature on the repository ("Report a vulnerability" under
*Security → Advisories*) where enabled.

In your report, include:

- a description of the vulnerability and its impact;
- the affected version(s) and, if possible, the commit or tag;
- steps to reproduce, or a proof of concept;
- any suggested mitigation.

You will receive an acknowledgement within **5 business days**. We aim to provide
a substantive response and a remediation timeline within **30 days**, depending
on severity and complexity.

## Disclosure policy

We follow coordinated disclosure:

1. The reporter shares the issue privately.
2. We confirm, assess severity, and develop a fix.
3. We release a patched version and credit the reporter (with consent).
4. Public disclosure happens **after** a fixed version is available.

We will not pursue legal action against researchers acting in good faith who
follow this policy.

## Security model and known hardening

Amiga ADF Library Builder is a preservation-first, offline-by-default tool. Its
security posture is documented in-repo:

- **Offline by default.** Online metadata/artwork enrichment is opt-in via the
  `--online` flag. Nothing leaves the host unless the operator enables it.
- **Immutable sources.** Source disk images are never modified or overwritten.
- **Staging, not direct write.** `export` writes only to a new run-owned staging
  tree under `<library_root>/work/staging/<run-id>` so output is reviewable
  before publishing to a Gotek SD card.
- **SSRF guard.** Outbound HTTP(S) fetches (metadata, artwork discovery, artwork
  download) reject URLs that target non-public address space — loopback,
  link-local, RFC1918, and IPv6 ULA/private ranges (including IPv4-mapped IPv6).
  This is defense-in-depth against crafted curated/online records or approved-page
  redirects to internal hosts. See `src/amiga_adf_library_builder/metadata.py`
  (`guard_url`).
- **Manual-approval allowlist.** Special-only release approvals bind to an
  operator-reviewed URL allowlist with SHA-256 hashing and safe-fail behavior.
  Approval commands and record handling are documented in `docs/COMMANDS.md`.

## Scope of this policy

This policy covers the code and configuration in this repository. It does not
cover third-party services, the operator's own infrastructure, or the contents
of any private Amiga disk-image library the operator processes.
