# AI-engine handoff

Resume here for ScreenScraper, Preview & Curation, and Windows packaging.
Use the [release checklist](RELEASE-CHECKLIST.md) for publication.

## Current behavior

- `screenscraper.py` looks up disk hashes and then searches by title. A
  missed or ambiguous search can retry with the release version removed or
  the title restored to standard article and subtitle order. A single
  normalized title match can be accepted. Ambiguous matches go to review.
- `enrich.py` tries alternate advertised artwork if the preferred image
  fails. Offline builds reuse saved artwork masters.
- `pipeline.py` and `rtfm.py` reuse downloaded PDFs and existing manual
  sidecars. ScreenScraper PDFs use the RTFM document size limit. The log
  identifies missing files, oversized downloads, and non-PDF responses.
  Preview combines current build output with saved curation choices,
  including explicit omissions. Accepting a release records curation only.
- Live run logs are under `Library-Root/logs`. The portable settings and
  encrypted credential vault live in `config` beside the executable.

## Verification and open questions

- The provider and pipeline regressions cover alternate artwork, offline
  media reuse, and refreshing blank prior Preview fields. The Windows package
  was also smoke-tested with an isolated portable base.
- With both online providers enabled, the live Windows build processed artwork
  for 20 of 25 releases, compared with 2 of 25 before the provider-order
  change. It rebuilt six RTFM sidecars from cached ScreenScraper PDFs.
- The Dark Queen of Krynn matched and downloaded artwork. ScreenScraper
  yielded no usable PDF. Jet Set Willy II succeeded with an alternate image
  after the preferred image failed validation.
- The Bard's Tale III fallback image hit a Wikimedia HTTP 429. A separate
  character-only disk group was quarantined. Defender and both Deluxe
  Pac-Man versions went to review for ambiguous ScreenScraper matches.
- For the next live check, look for matching artwork on Bard's Tale III,
  Defender of the Crown, and both Deluxe Pac-Man releases. Check the new PDF
  diagnostics if Dark Queen of Krynn still has no RTFM. Fixture tests cover
  the new title matching and PDF size behavior. These live outcomes are
  still unverified.
- Public release builds use the local developer vault workflow. Keep developer
  credentials, the vault, personal disk images, and live logs out of Git.
