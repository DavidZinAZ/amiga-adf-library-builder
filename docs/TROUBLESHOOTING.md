# Troubleshooting

## No JPG files are produced

Run with both:

```text
--online --require-artwork
```

Then inspect:

```bash
find <data-root>/catalog/metadata-cache -name '*.json' -print -exec python3 -m json.tool {} \;
find <data-root>/assets/artwork-original -type f -print
find <data-root>/assets/artwork-processed -type f -print
```

An empty `artwork_url` means no usable image source was resolved. Curated metadata under `catalog/metadata-curated` may supply an operator-approved source URL.

## Online lookup returns cached data

Use:

```text
--refresh-metadata
```

This refreshes provider metadata while preserving operator-curated overrides.

## A release is quarantined

Inspect the JSON under:

```text
<data-root>/unknown
```

The tool quarantines ambiguous or incomplete sets instead of guessing.

## Export reports a conflict

Use the same `--run-id` with `--verify-only` to inspect the existing staging tree. Choose a new run ID for a clean rebuild.

## Invalid run ID

Run IDs are sanitized to prevent path traversal. Use a simple value such as:

```text
enriched-20260804-210000
```

## Show installed version

```bash
.venv/bin/python -c 'import importlib.metadata as m; print(m.version("amiga-adf-library-builder"))'
```
