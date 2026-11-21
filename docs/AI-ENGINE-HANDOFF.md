# AI-engine handoff

Resume here for ScreenScraper, Preview & Curation, and Windows packaging.
Use the [release checklist](RELEASE-CHECKLIST.md) for publication.

## Current behavior

- `screenscraper.py` looks up disk hashes and then searches by title. A clean
  title miss can retry without the release version and with the article at
  the front. Ambiguous matches go to review.
- `enrich.py` tries alternate advertised artwork if the preferred image
  fails. Offline builds reuse saved artwork masters.
- `pipeline.py` and `rtfm.py` reuse downloaded PDFs and existing manual
  sidecars. Preview combines current build output with saved curation choices,
  including explicit omissions.
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
- Follow up on unusable advertised PDFs and ambiguous title matches. The
  current logs do not distinguish all reasons a PDF was unusable.
- Public release builds use the local developer vault workflow. Keep developer
  credentials, the vault, personal disk images, and live logs out of Git.
