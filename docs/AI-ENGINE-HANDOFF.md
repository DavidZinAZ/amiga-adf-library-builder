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
- The previous live run built six RTFM sidecars. Hacker is a known positive
  check for both artwork and a manual. The Dark Queen of Krynn missed on the
  versioned DAT title. A live match and media availability after the retry
  remain unverified.
- Public release builds use the local developer vault workflow. Keep developer
  credentials, the vault, personal disk images, and live logs out of Git.
