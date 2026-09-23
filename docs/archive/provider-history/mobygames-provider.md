# Issue #1 — Optional MobyGames Metadata/Artwork Provider

**Status:** implemented (GH-1).  
**Profile:** software-engineer (Case).  
**Scope:** optional online provider, **disabled by default**, stdlib-only,
reuses the shared provider / security / cache architecture.

This document explains the `mobygames` provider: what it does, how to enable
it, where the API key comes from, where it sits in the metadata precedence
chain, how caching works, and what the app does with no configuration at all.

---

## 1. What it is

An OPTIONAL online metadata/artwork provider. When enabled and keyed,
`mobygames_lookup()` queries the MobyGames API (`https://api.mobygames.com/v1/games`)
by title, filters results to games released on an **Amiga platform**, ranks
matches by normalized title similarity, and returns a `MetadataRecord`
(`provider="mobygames"`) with canonical title, description, year, genres,
Amiga platforms, source URL, and — when present — cover/screenshot artwork.

Everything the provider does reuses existing shared machinery:

* **HTTP:** the same `_json_get()` urllib helper as RAWG/Wikipedia, with an
  injectable `opener` for tests. No new HTTP libraries; stdlib only.
* **Security:** every URL the provider emits is validated by the ratified
  `validate_source_url()` guard (scheme allowlist, no userinfo, no bare IP
  hosts, host allowlist). `mobygames.com` and `images.mobygames.com` were added
  to `HOST_ALLOWLIST` in `manual_approvals.py` and to
  `_ALLOWED_ARTWORK_PAGE_HOSTS` in `metadata.py` for this issue. A URL that
  fails validation is **dropped** (no artwork), never returned.
* **Caching / provenance:** the standard `save_cached()` / `load_cached()`
  JSON cache. Provenance fields (`provider`, `provider_id`, `source_url`,
  `artwork_provider`, `retrieved_at`) are populated on the record and survive
  cache round-trips.

## 2. Default state: off, keyless, offline-safe

* The `[mobygames]` table ships in `config/example.toml` with
  `enabled = false`.
* A missing config file, a missing `[mobygames]` table, or a failed config
  load all resolve to a **disabled** config — the pipeline degrades to
  default and the run is never broken by the provider.
* Even when `enabled = true`, the provider is a **no-op without an API key**:
  the key is read only from the environment variable named by `api_key_env`
  (default `MOBYGAMES_API_KEY`). No key in the environment → the provider is
  skipped and the chain proceeds to the next provider.
* No key, no config, no network: the base app behaves exactly as before
  (curated → cache → Wikipedia fallback). Local-only workflows are unaffected.
* **No credentials are ever committed.** The key exists only in the
  environment of the process doing the lookup. The shipped config contains
  only the *name* of the environment variable.

## 3. Setup

1. Copy the example config and enable the provider:

   ```toml
   [mobygames]
   enabled = true
   # api_key_env = "MOBYGAMES_API_KEY"   # default; override for a custom name
   # preferred_image_types = ["cover", "screenshot", "box"]
   # timeout = 20.0
   ```

2. Export the API key in the environment of the shell that runs the tool:

   ```sh
   export MOBYGAMES_API_KEY="your-key"
   ```

   The key is read at lookup time from `os.environ[mobygames_api_key_env]`,
   exactly like `RAWG_API_KEY`. Never put the key in a config file, log, or
   export.

3. Run with `--online` as usual. The provider is consulted only for online
   metadata lookups.

## 4. Precedence (deterministic, highest first)

`lookup_metadata()` resolves each title with this chain:

1. **curated** — operator-curated records in `metadata-curated/` (confidence
   forced to 1.0; may be supplemented only for missing fields);
2. **cache** — a previously saved record in `metadata-cache/` (skipped when
   `refresh` is set);
3. **keyed online providers** — RAWG (if `RAWG_API_KEY` is set), then
   **MobyGames** (if `mobygames` is enabled in config **and** the key env
   variable is set). Each is a silent no-op when gated off, so the chain
   simply proceeds. MobyGames is attempted only when RAWG produced no record;
4. **Wikipedia** — unkeyed fallback.

The first record found is cached (unless `refresh`) and returned with its
provider label (`curated`, `cache`, `rawg`, `mobygames`, `wikipedia`).
A provider failure (HTTP error, malformed payload, timeout) degrades to
`record = None` for that step — the chain continues and the run is never
broken. If every provider fails or finds nothing, the result is
`not-found` and enrichment proceeds offline.

## 5. Matching and ambiguity

* Search is title-based (`format=normal`, single request). The `platform`
  query parameter is intentionally NOT passed — it requires an integer
  platform ID, so Amiga filtering is done **client-side** on each result's
  `platforms` array.
* Only games with at least one `platform_name` containing "amiga" are
  considered.
* Among Amiga games, the best normalized-title-similarity match wins; ties
  break on the lower `game_id` (stable, no dict-order dependence).
* **Ambiguous or low-similarity matches are rejected** (floor 0.60) rather
  than silently chosen — mirroring the relevance floors used by the other
  providers. The chain then falls through to the next provider.
* `MetadataRecord` fields are populated deterministically from the response:
  `canonical_title`, `description` (tags stripped), `year` (earliest Amiga
  `first_release_date` prefix), `genres`, `platforms` (Amiga only),
  `source_url` (the game's `moby_url`, validated), `artwork_url`
  (Amiga-specific cover preferred, else first allowlisted screenshot),
  `artwork_provider="mobygames"`, `provider_id` (the `game_id`),
  `confidence` (the title-similarity ratio, capped at 1.0).

## 6. Caching

* A successful lookup is written via `save_cached()` to
  `<root>/metadata-cache/<cache_key(title)>.json` (atomic tmp-file rename).
* Repeat lookups for the same title hit the cache (`provider="cache"`) and
  perform **no** network I/O, even offline. `--refresh-metadata` forces a
  re-fetch and overwrites the cached record.
* A record resolved by MobyGames therefore keeps its
  `provider="mobygames"` / `provider_id` provenance across the cache
  round-trip — downstream export shows which source a title came from.

## 7. Offline behavior

* `enabled = false` (default): the provider is never attempted; identical
  behavior to a build without this feature.
* `enabled = true`, key missing: no-op; the chain proceeds to Wikipedia
  (or not-found) exactly as before.
* Network failure while enabled + keyed: the MobyGames step degrades to
  `None`, the chain continues, and a cached record (if any) is still served.
* With `--online` absent, `lookup_metadata()` is not called at all for the
  online path — cached and curated records remain available.

## 8. Security posture

* **SSRF guard.** All URL emission passes the ratified
  `validate_source_url()`: `http`/`https` only, no userinfo, no bare IP
  literals (loopback / link-local / private ranges are refused as IP hosts),
  and the host must be allowlisted. MobyGames hosts are allowlisted as
  `mobygames.com` (plus `www.`/`images.` subdomains).
* **No key leakage.** The key appears only in the API request URL (as the
  `api_key` query argument) sent over HTTPS to the API host; it is never
  written to config, logs, cache, or exports. Tests use synthetic keys only.
* **No execution of remote content.** The provider parses a JSON response;
  it does not execute, render, or follow remote instructions. Artwork URLs
  are validated strings; actual image download/resize happens only in the
  existing, already-audited artwork pipeline against allowlisted hosts.

## 9. Tests

`tests/test_mobygames_provider.py` (synthetic fixtures, injected fake
openers, no live network, no real key) covers:

* **matching** — synthetic payload → `MetadataRecord` populated with
  `provider="mobygames"`, `provider_id`, Amiga-only platforms, year, genres;
  single API fetch; Amiga-specific cover preferred; non-Amiga results and
  low-similarity matches rejected.
* **provider failure** — raised HTTP errors do not escape
  `lookup_metadata()`; the chain falls through to Wikipedia or
  `not-found` (base workflow continues).
* **caching** — a second lookup with `refresh=False` serves the cached record
  (`provider="cache"`) with zero additional provider fetches.
* **provenance** — `source_url`, `provider`, `provider_id`, and
  `artwork_provider` recorded and surviving the cache round-trip.
* **security-sensitive URL handling** — SSRF guard blocks loopback,
  link-local, private, and IPv6 literal hosts; only allowlisted MobyGames
  hosts pass for artwork; non-http(s) schemes refused; invalid artwork URLs
  dropped (fallback to a valid screenshot, or no artwork); the lookup only
  ever fetches the API endpoint.
* **disabled / offline / config** — disabled or keyless provider is a no-op
  (chain proceeds unchanged); custom `api_key_env` honored; config table
  parses from TOML with `enabled=false` default; the shipped
  `config/example.toml` is disabled and contains no key value.

Run: `python3 -m pytest tests/test_mobygames_provider.py -v`
