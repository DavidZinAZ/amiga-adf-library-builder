# GH-141 Findings Catalog — Final (AR-001 … AR-012)

Repo: amiga-adf-library-builder @ `9d20b804158f4c5662dffd81bb0307bc2af978ca`
(main, v0.2.16). Every finding below was re-derived from source in the final
synthesis pass; citations are `file:line` under `src/amiga_adf_library_builder/`.

Provenance legend — each finding carries the outcome of the three-stage
adversarial process:
- **RECON** — Q-Branch evidence map
- **REVIEW** — initial independent architecture review (its attempt 2 already
  self-corrected one false positive: placeholder-URL divergence)
- **CHALLENGE** — technical challenge (attempt to disprove + 7 candidate
  findings CF-001…CF-007)
- **SYNTHESIS** — final reconciliation; new evidence produced during
  deduplication

Classifications: `ROOT CAUSE` (remediation target), `ARCH` (structural),
`MAINT` (hygiene/drift risk), `DOC` (documentation defect), `KEEP`
(intentional, sound — do not "fix"). Severity: High / Medium / Low
(user-visible or correctness impact of NOT fixing). Confidence: High where
the final pass re-verified every citation; Medium where the finding is
directional but a precise impact bound was not derived.

## Final dispositions at a glance

| ID | Class | Severity | Conf. | Challenge verdict | Final disposition |
|----|-------|----------|-------|-------------------|-------------------|
| AR-001 | ROOT CAUSE | High | High | PARTIALLY DISPROVEN | REVISED & kept — lifecycle is path-asymmetric append-only, not "write-once" |
| AR-002 | DOC | Low | High | CONFIRMED | kept |
| AR-003 | ARCH | High | High | CONFIRMED + corrected | 6 stores (manual approvals added) |
| AR-004 | ARCH | Medium | High | CONFIRMED + expanded | 5 versioned stores, 5 distinct idioms (manual_approvals stamp-only) |
| AR-005 | ARCH | Medium | High | CONFIRMED + quantified | legacy naming load-bearing in 7 modules (review said 5) |
| AR-006 | MAINT | Medium | High | CONFIRMED + expanded | 3rd private reach found in synthesis (canonical_naming→exporter) |
| AR-007 | KEEP | — | High | CONFIRMED | keep |
| AR-008 | ARCH | Medium | High | CONFIRMED, exact | 28 kw-only params (review "25+", challenge "28+" → exactly 28) |
| AR-009 | KEEP | — | High | CONFIRMED | keep |
| AR-010 | KEEP | — | High | CHALLENGE: KEEP | confirmed false positive twice; KEEP |
| AR-011 | KEEP | — | High | CONFIRMED | keep per-provider boundaries; dedupe shared helpers |
| AR-012 | ARCH | Medium | High | NEW (CF-002+CF-006 merged) | canonical.db never retires rows or claims |

Challenge candidate disposition: CF-001 → merged into AR-001 (it is the
mechanism AR-001 mischaracterized, not a separate defect). CF-003 → AR-001
mechanism (CLI backfill path, see below). CF-004 → AR-006 (count expanded:
5 named atomic writers). CF-005 → AR-004 (store/idiom count expanded).
CF-007 → AR-001 (contract asymmetry is the root cause itself).

---

### AR-001 — canonical.db lifecycle is path-asymmetric and append-only (ROOT CAUSE, High/High)

**Final claim.** There is one canonical.db with **three unequal writers and no
reconciliation**: (a) the GUI-path refresh is additive and never updates
existing release rows; (b) the CLI-path create is write-once AND invents
top-authority claims from scan data; (c) neither path ever retires rows or
claims (see AR-012). Region/language scoring and canonical export naming
therefore depend on *which path touched the store first, and in what order* —
not on current scan reality.

**Verified mechanisms.**
- GUI: every completed run calls
  `build_staged_library_from_result` (gui/main_window.py:2336-2337) →
  `_persist_canonical_library` (pipeline.py:906) →
  `migrate_staged_library` → `upsert_release`/`claim_field`. This refutes the
  initial review's "nothing re-imports into an existing canonical.db" and its
  "write-once and never refreshed" headline.
- But `upsert_release` is `INSERT OR IGNORE` on `release` (canonical.py:512-521):
  the edition/region/language/publisher **columns are first-write-wins and
  never revisited** (only `disk` uses `INSERT OR REPLACE`, canonical.py:533-543;
  only `field_claim` rows grow). So a refresh exists, yet it can never correct
  the row-level attributes the 1G1R scorer reads
  (selection.py `_score_region`/`_score_language` via `canon.resolve_field`).
  The review's downstream consequences (stale region/language scoring,
  provenance non-determinism) survive; its stated mechanism did not.
- CLI: `_ensure_canonical_library` (pipeline.py:992) is guarded by
  `if db_path.is_file(): return` — write-once applies **only here**. When it
  does create (fresh library, `one_per_game`, pipeline.py:514-516), it builds
  the store from **post-curation** `groups` (after `_apply_curation`,
  pipeline.py:507) — but with every entry forced to
  `curation_state=PENDING` (pipeline.py:1036), so operator ACCEPTED/REJECTED
  state is silently dropped at seed time. Worse: it claims `title` and
  `release_key` with `SourceAuthority.CURATION` / source `"operator"` via
  `migrate_staged_library` (canonical.py:645-654 + pipeline.py:1021-1038) —
  scan-derived values enter the DB wearing the **highest local authority** and
  the identity of a human decision. That store then blocks any later
  provider-sourced correction (curation always wins resolution) and
  `has_curation_claim` will report operator approval that never happened.
  Synthesis caught this by following the CLI seed through authority resolution;
  it is the strongest form of the recon's STATE-2 divergence.
- CLI never supplies `library_state_path` at all (cli.py:541, cli.py:571 —
  `_apply_curation` no-ops, pipeline.py:78-79), so a "GUI curated → CLI
  exports" workflow only aligns naming/scoring with reality if some prior GUI
  run already seeded the rows the CLI reads.
- Failure policy diverges per path: GUI persistence is best-effort and
  swallows all errors (pipeline.py:909-912, 958-959); CLI `_load`/`_ensure`
  return None on open/seed failure → silent fallback to legacy naming
  (exporter.py:64,72) and neutral 20.0 region/language scores.

**Why root cause.** All AR-001-shaped symptoms — divergent GUI/CLI canonical
views, unfixable stale attributes, "which run created it" provenance — trace
to this single missing design decision: there is no defined refresh/conflict
contract for canonical.db.

**Recommendation direction (not implementation).** Decide the intended
contract (canonical.db as derived cache vs. curated record of authority),
then: make `release` updates overwrite non-authority-bearing columns from
resolved claims (or document + test first-write-wins); never seed
CURATION-tier claims from automated scan data (introduce a distinct
`seed`/`scanner` authority below curation); pass `library_state_path` from
CLI if CLI is meant to see curation; make GUI persistence failures visible.

### AR-002 — `_ensure_canonical_library` docstring contradicts its own behavior (DOC, Low/High)

**Claim (confirmed by challenge).** pipeline.py:998-999: "Fresh CLI exports
never create canonical.db (only the GUI staged-library path does)" — false as
of the GH-136-era change: pipeline.py:512-516 calls this helper on the
ordinary CLI export path whenever `_load_canonical_library` returns None.
The "20.0 neutral scoring" problem it describes is solved by this function.
Only the docstring remains true of the pre-change code.
**Remediation:** rewrite to describe create-if-absent + post-curation seeding
(and, if AR-001 lands, the authority of seeded claims). Low severity, high
value: a false invariant statement in the lifecycle root-cause module is
actively misleading to the next reviewer.

### AR-003 — Six persistent stores encode overlapping identity facts with no documented SSOT (ARCH, High/High)

**Claim (confirmed, count corrected).** RECON/REVIEW said 5 stores; the
challenge's CF-005 implied a 6th. Synthesis confirms 6: catalog JSONL,
curation/library_state_<run>.json, curation/identity.db,
curation/canonical.db, the DAT index SQLite, and `config/manual-approvals/`
+ `config/local.manual-approvals/` JSON records. sha256 is authoritative in
identity.db and the DAT index; `release_key` in curation JSON,
canonical.db claims, and curation_memory; **approved titles/filenames** in
manual_approvals JSON *and* mirrored as curation claims in canonical.db
(manual_lookup.py:313-324, :355-369).
**Nuance retained from challenge:** granularity differs (file-level
identity.db vs release-level canonical.db), so blind consolidation is wrong.
The defect is that the overlap is undocumented and untested — no test asserts
which store wins when they disagree.
**Recommendation:** a short `docs/STATE-MODELS.md` ownership table + one
cross-store consistency test per shared fact. (Confirmed part of the gap:
`docs/ARCHITECTURE.md` contains zero occurrences of "canonical.db" — the
store the scorer and exporter trust is documented nowhere outside code.)

### AR-004 — Five versioned stores, five different migration idioms; two are decorative (ARCH, Medium/High)

**Verified census (expanded by challenge CF-005, confirmed by synthesis).**
1. file_identity.py:100-157 — `PRAGMA user_version`, deterministic real
   migrations. The reference idiom.
2. canonical.py:350-418 — `SCHEMA_VERSION=1` + `PRAGMA user_version` +
   forward-only `_migrate()`. Real.
3. library_state.py:99-100 — `schema_version=1` strict equality reject;
   **no migration function**; a v2 file is unreadable data-loss-by-error.
4. metadata_source.py:197 — `SCHEMA_VERSION = 1` declared once, never
   referenced by any migration/check logic (verified: 1 occurrence in file).
   Dead constant implying a guarantee that doesn't exist.
5. manual_approvals.py:60 → written into records at :167, but
   `ApprovalRecord.from_dict` (manual_approvals.py:187) never reads
   `schema_version`; mismatched records load silently. A **stamp-only**
   field that looks like a guard.
**Recommendation:** adopt the file_identity idiom (user_version) for SQLite
stores; add real read-side validation for library_state/manual_approvals;
delete or wire the metadata_source constant.

### AR-005 — Dual naming regime unresolved; legacy `release_basename` remains load-bearing in 7 modules (ARCH/MEDIUM, Medium/High)

**Claim (confirmed, count corrected 5→7).** The fallback itself is
well-instrumented and visible ("fallback: no canonical DB",
exporter.py:48-74) — KEEP that. But `naming.release_basename` remains the
operative default whenever canonical.db is absent/unloadable, and is imported
by exporter, pipeline, rtfm, enrich, retrokit, models **and
canonical_naming.py:43 itself**. canonical_naming also privately imports
`exporter._sanitize_component` (canonical_naming.py:42) and self-documents
its `_slugify_title` as a copy of `canonical.slugify_title`
(canonical_naming.py:482-486). GH-107 cannot be declared done while both
regimes produce names. AR-001 is a precondition for retiring the legacy path.

### AR-006 — Cross-module duplication census (MAINT, Medium/High)

**Verified census at 9d20b80 (challenge counts confirmed; synthesis adds a
third private reach).**
- Timestamps: 5 copies `_now`/`_now_iso` (canonical:45, catalog:23,
  file_identity:180, quarantine:21, scanner:18).
- Atomic JSON writers: **5 named** — 4× `_write_json_atomic`
  (enrich:137, retrokit:511, rtfm:1429, screenscraper:1354) + 1×
  `_atomic_write_json` (manual_approvals:258, same semantics, different name —
  CF-004 confirmed) — plus **13** inline temp+os.replace sites outside those
  bodies (AST-verified: exporter:574, paths:740, selection:475, igdb:335/367,
  enrich:375, playmatch:272, pipeline:929, metadata:203,
  retroachievements:301, hasheous:360, retrokit:508, gui/state:220) incl.
  `build_staged_library_from_result` itself.
- sha256: **7** helpers — 6 private (`metadata_source:262`,
  `local_media:1861`, `exporter:178`, `manual_approvals:250`,
  `rtfm_docs:571`, `file_identity:163`) + public `scanner.sha256_of_file`
  (scanner.py:22).
- Slug/sanitize: **7 definitions across 4 modules, 6 independent
  implementations** (the 7th, canonical_naming._sanitize_component:490, is a
  documented delegation to exporter's — see private reaches below):
  naming._sanitize:15; canonical.slugify_title:49;
  canonical_naming._sanitize_token:150 + _slugify_title:482 +
  _sanitize_component:490→exporter; exporter._sanitize_component:90 +
  _sanitize_run_id:119. `_sanitize_token` preserves `.-[]()`; the exporter
  sanitizer does not — silent cross-phase filename drift if rules diverge.
- Provider scaffolding: 6 Config dataclasses, 5 `_cache_load`/`_cache_store`
  copies, per-provider error taxonomy, inline `__import__(..._version...)`
  User-Agent in 5 sites (enrich:355, screenscraper:674/708, metadata:26,
  retrokit:92).
- **Private cross-module reaches: 3** (recon+review said 2):
  selection.py:176→canonical_naming._slugify_title; manual_lookup.py:315→
  canonical._now_iso; **new in synthesis:** canonical_naming.py:42→
  exporter._sanitize_component.
**Recommendation:** a shared `util/` module (atomic-json, now, sha256, slug);
convert private reaches to public APIs. This is drift-risk today, not a
correctness bug — do not prioritize above AR-001/AR-003.

### AR-007 — GUI↔pipeline equivalence gate (KEEP, High confidence)

`tests/test_gui_equivalence.py` (297 lines, 10 tests — verified) asserts the
GUI builds identical PathConfig + run_pipeline kwargs. Strongest invariant in
the repo. Keep; AR-008 is its cost, not a reason to drop it.

### AR-008 — `run_pipeline` configuration-by-parameter drift (ARCH, Medium/High)

**Verified exactly: 28 keyword-only parameters** (AST count at 9d20b80;
review "25+", challenge "28+"). Each feature ticket adds one, forcing
three-site changes (pipeline.py signature, cli.py call sites ×2,
gui/state.py) guarded only by the equivalence test. The dual CLI call sites
(cli.py:541, :571) already diverge in coverage — the export site's
asymmetry (no `library_state_path`) is an instance of this drift causing an
AR-001-class behavioral split. Recommend a typed RunConfig object carrying
the provider toggles + optional paths; keep cancel/activity hooks as call
args.

### AR-009 — Staging discipline and secrets design (KEEP, High)

Staged export dir (`work/staging/<run-id>/` + manifest; separate gated
Phase-10 SD publish) and `gui/secrets.py` SecretBackend ABC + RedactingFilter
are sound. Do not disturb during state consolidation.

### AR-010 — Placeholder provider base URLs (KEEP; formally retired false positive, High)

`.example` defaults are byte-identical between GUI (gui/providers.py:136-137,
284-285) and modules (playmatch.py:74, hasheous.py:111), and deliberate:
hasheous.py:1-11 documents placeholder-as-disabled-by-default (issue #12
governance). Review's attempt 2 disproved it; the challenge independently
confirmed; synthesis re-verified the line pairs. This finding exists in the
record only to document its retirement.

### AR-011 — Layered provider chain with conflict provenance (KEEP, High)

playmatch→hasheous→igdb→screenscraper→retroachievements with
confidence-based override and recorded conflicts (enrich.py ~1089-1103) is
correct design. Structural duplication across ~5,988 lines of provider code
is AR-006's helper problem, not the chain's: dedupe helpers, keep boundaries.

### AR-012 — canonical.db accumulates claims and releases with no retirement path (ARCH, Medium/High) — NEW

**Verified.** `field_claim` uses `INSERT OR IGNORE` with a
(entity,field,source,record_key,value) PK (canonical.py:435-451 + schema at
:406) → **conflicting observations accumulate forever** (the store's own
docstrings concede this: "Append a claim. Never overwrites; conflicts
accumulate.", canonical.py:174); resolution sorts by
authority each read. No `DELETE FROM`, no delete/remove method exists
anywhere in canonical.py (grep-verified: 0 matches for DELETE/forget/remove
in the write surface). Consequences: releases that leave the corpus remain in
the store; a release that later re-enters under the same `release_id`
resurfaces its stale, first-written columns — which AR-001 proves are
never corrected — together with every old claim; and a wrong operator claim
can never be forgotten, only counter-weighted. Combined with AR-001's
never-update columns, canonical.db monotonically accumulates the union of
every state the library has ever been in.
Challenge CF-002+CF-006, merged as one finding (same root: no tombstone/
retirement concept in the canonical model).
**Recommendation:** define retirement (tombstone state or scoped compaction
against current library membership, run after successful GUI staged builds),
and record claim superseding rather than pure accumulation.

---

## Deduplication ledger (recon → review → challenge → final)

- RECON STATE-1 → **AR-003** (corrected: 6 stores).
- RECON STATE-2 → dissolved: mechanism was wrong in review *and* in challenge
  headline; final content lives in **AR-001** (3 writers + contract) — no
  standalone STATE-2 finding.
- RECON STATE-3 → **AR-004** (expanded to 5 stores, added stamp-only +
  dead-constant distinctions).
- RECON STATE-4 (atomic writes consistent "where present") → **partially
  superseded**: "where present" hid the 5-name/13-inline fragmentation →
  recorded in **AR-006**, so STATE-4 is NOT carried as a KEEP.
- RECON §5 "GUI defaults diverge from provider defaults" → **false positive**,
  retired as **AR-010 KEEP**.
- REVIEW AR-001 headline ("write-once, never refreshed") → **partially
  disproven** by challenge CF-001, re-verified and re-framed as
  path-asymmetric append-only.
- REVIEW AR-006 ("4 atomic writers, 2 private reaches") → expanded per
  synthesis census (5 named writers; **3** private reaches).
- CHALLENGE CF-001 → merged into AR-001. CF-002+CF-006 → **AR-012**.
  CF-003 → AR-001 mechanism (+ its authority-seeding consequence is the
  sharpest part of AR-001). CF-004 → AR-006. CF-005 → AR-004/AR-003.
  CF-007 → AR-001 (the two loaders' contracts: `_load_canonical_library`
  is pure-read with fallback contract; `_ensure_canonical_library` seeds +
  falls back to legacy on corruption — different failure semantics are part
  of the missing lifecycle contract, not a separate finding).
- **Synthesis-new:** CLI seeds CURATION-authority claims from scan data
  (AR-001, mechanism verified end-to-end); CLI has two run_pipeline call
  sites and passes no library_state (AR-001/AR-008); manual_approvals
  from_dict ignores its own schema_version stamp (AR-004); third private
  cross-module reach canonical_naming→exporter (AR-006).

## Residual uncertainties (honest bounds)

- No dynamic runtime reproduction (no GUI launched at 9d20b80); every claim
  above is static source verification. Severity assignments are judgment, not
  measurement.
- The exact user-visible harm frequency of AR-001 (e.g. a real stale-region
  mis-export) was not constructed; mechanism chain is fully cited instead.
- Provider auth flows, exporter_guard.py/diagnostics.py internals, CI matrix
  were explicitly out of scope for all three passes.
