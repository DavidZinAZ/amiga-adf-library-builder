# Data and Cache Layout

The repository contains code. Persistent library data lives outside the
repository and is selected at runtime through the portable path configuration
(portable path configuration). A single **library root** anchors every working directory; only
`cache_dir` defaults elsewhere (the XDG cache).

```text
<library_root>/
├── original/                 immutable ADF/DSK intake (read-only to the app)
├── catalog/
│   ├── metadata-cache/       resolved provider records
│   └── metadata-curated/     operator-maintained overrides
├── assets/
│   ├── artwork-original/     downloaded master artwork plus provenance
│   ├── artwork-processed/    generated JPG derivatives
│   └── nfo/                  generated release NFO files
│       ├── <release>.nfo              Gotek display NFO (Title:/Blurb:, ≤512 bytes)
│       ├── <release>.provenance.json  durable provenance (machine-readable)
│       └── <release>.provenance.txt   durable provenance (human-readable)
├── unknown/                  unresolved or quarantined groups
├── output/                   generated output
├── work/
│   └── staging/<run-id>/     reviewable ADF/DSK output
├── config/
│   └── manual-approvals/     manual-approval feature approval records (preserved byte-for-byte)
├── reports/                  run reports
└── logs/                     logs

<cache_dir>                   XDG cache (~/.cache/amiga-adf-library-builder by default)
```

`catalog/`, `assets/`, `review/`, and `rejected/` remain predictable internal
working directories under the library root. `original/` is never created or
written by the application — the operator supplies the read-only corpus.

The SD-card tree is a *separate, explicit publish step* owned by the operator
and is never written by the build
or export commands.

Do not commit `original/`, caches, downloaded artwork, staging output, or SD-card
content to Git.
