# Gotek-facing NFO format and durable provenance (Gotek NFO contract)

This document defines the contract for the Gotek Touchscreen Interface display
`.nfo` files and where the detailed source / metadata / manual-approval
provenance is kept so that it survives outside the final SD-card layout.

## 1. Gotek-facing `.nfo` contract

Every Gotek-facing `.nfo` (generated for online-enriched exports, offline
exports, exporter fallback generation, and manually approved releases) follows
one identical contract:

```text
Title: <canonical title>
Blurb: <year> - <publisher> - <short description>
```

Rules:

1. **Line 1** is always `Title: <canonical title>`.
2. **Line 2** is always a labelled `Blurb:`.
3. The blurb is built only from available trusted metadata. Missing fields are
   omitted rather than invented; there are no empty ` - ` separators. If nothing
   is available, line 2 is a bare `Blurb:`.
4. The whole file is **≤ 512 UTF-8 bytes** (the Gotek firmware reads only the
   first 512 bytes of an NFO). The renderer truncates the blurb (never the
   `Title:` line) to stay within the budget.
5. The display NFO carries **no rich provenance**: no source hashes, no approval
   URLs, no metadata provider/source, no retrieval timestamp, no confidence/query,
   no duplicate enrichment-mode fields, no project banner or separator.

This matches the upstream Gotek "Labelled" NFO form documented in
`docs/upstream-gotek-requirements.md` §5.

## 2. Durable provenance — outside the NFO

All detailed provenance is preserved in two per-release sidecar files written
under `assets/nfo/` (the same directory as the `.nfo`, but **never copied into
the SD-card `/ADF` or `/DSK` output** by the exporter):

- `<release>.provenance.json` — machine-readable, schema
  `gotek-nfo-provenance/1`.
- `<release>.provenance.txt` — human-readable, mirrors the JSON content.

Both sidecars capture:

- original source filenames;
- SHA-256 hashes and sizes (per source image);
- manual-approval URLs and roles (`approved_sources`);
- metadata provider and source URL;
- retrieval timestamp;
- confidence and query;
- enrichment mode (`online` / `cache` / `offline` / `curated` / …).

### 2.1 JSON schema (`gotek-nfo-provenance/1`)

```json
{
  "schema": "gotek-nfo-provenance/1",
  "generated_at": "2026-08-05T00:00:00+00:00",
  "release_key": "examplespacetactics",
  "title": "Example Space Tactics",
  "year": null,
  "publisher": null,
  "edition": null,
  "group": null,
  "chipset": null,
  "language": null,
  "version": null,
  "alt_marker": null,
  "trainer": false,
  "disks": 4,
  "specials": 0,
  "description": null,
  "source_images": [
    {
      "filename": "Example - Space Tactics (Disk 1 of 4).adf",
      "format": "ADF",
      "sha256": "<hex>",
      "size": 901120
    }
  ],
  "approved_sources": [
    { "role": "metadata", "url": "https://www.lemonamiga.com/games/details.php?id=example" }
  ],
  "metadata_provenance": {
    "provider": "cache",
    "source_url": "https://…",
    "provider_id": "…",
    "retrieved_at": "…",
    "confidence": 0.92,
    "query": "Example Space Tactics",
    "artwork_url": "https://…",
    "artwork_source_url": "https://…",
    "artwork_provider": "lemon-amiga"
  },
  "enrichment_mode": "cache"
}
```

`approved_sources` contains only entries whose URL was actually supplied
(ratified "no guessed URLs" — §5 of
`docs/issue1-security-ratification.md`). When no URL is present for a role, that
role is simply absent.

## 3. Why sidecars and not the NFO

The Gotek firmware only displays the first 512 bytes of the NFO and reads only
`Title:` / `Blurb:`. Embedding hashes, URLs, and metadata provenance would
(a) overflow the 512-byte display budget and (b) be invisible on-device. Keeping
it in a structured sidecar preserves the preservation/audit trail (manual-approval
binding, SHA-256, collision protection, deterministic rerun) without bloating the
display file, and the exporter's copy step deliberately copies only
`<basename>.nfo` into staging, so nothing leaks into the final SD-card layout.

## 4. Rendering paths (one contract)

| Path | Code | NFO source |
|------|------|-----------|
| Online-enriched export | `enrich.enrich_group` | `nfo_render.render_gotek_nfo` |
| Offline / cached export | `enrich.enrich_group` | `nfo_render.render_gotek_nfo` |
| Manually approved release | `enrich.enrich_group` (with `approved_sources`) | `nfo_render.render_gotek_nfo` |
| Exporter fallback (no enrichment artifact) | `exporter._build_nfo` | `nfo_render.render_gotek_nfo` |

All four use the same `render_gotek_nfo` contract. The provenance sidecars are
written by `enrich.enrich_group`; the exporter fallback path has no metadata to
record, so it writes only the display NFO.
