# GH-141 — Architecture Review — Final Verdict

**Repo:** amiga-adf-library-builder · **Commit reviewed:**
`9d20b804158f4c5662dffd81bb0307bc2af978ca` (main, v0.2.16, clean tree,
in sync with origin/main at synthesis time)
**Date:** 2026-09-14 · **Companion documents:** `architecture-truth-map.md`,
`findings.md` (same directory)

## Verdict

The architecture is **sound in its deliberate boundaries and unsound in its
undocumented ones**. The pipeline/GUI separation, staging discipline, secrets
design, provider chain, and the GUI↔CLI equivalence test gate are correct and
should be preserved (AR-007/009/010/011 = KEEP). The central defect is not
any single bug but a **missing design decision**: `canonical.db` — which
1G1R scoring and canonical export naming both trust — has three unequal
writers, no update path for the columns the scorer reads, no deletion or
retirement concept, and an automated seeding path that writes scan data under
the top "operator/curation" authority (AR-001, AR-012). Around it sit two
structural debts: state without a documented owner map (AR-003/004) and an
interface that accretes parameters where behavior should live (AR-008, with
AR-005/AR-006 as its visible symptoms).

No finding in this review is a shipping blocker at 9d20b80; none was created
as a GitHub issue, per the review contract. Remediation is deliberately left
to a follow-on planning cycle.

## Process and adjudication model

Four passes, each adversarial to the previous:

1. **Recon** produced evidence maps (STATE-1…4, naming/provider/version
   inventories).
2. **Independent review** re-derived everything at source, corrected recon
   (STATE-2 mechanism was wrong; one atomic-write KEEP claim was overstated),
   and self-caught one false positive in its own second pass (placeholder-URL
   "divergence", now AR-010 KEEP).
3. **Technical challenge** re-derived AR-001…AR-011 again from source and
   partially disproved the headline mechanism of AR-001 (a refresh path —
   `_persist_canonical_library`, pipeline.py:906/934 — runs on every GUI
   build; "write-once" was only true of the CLI `_ensure` guard), while
   proposing seven candidate additions CF-001…CF-007.
4. **Final synthesis** (this document) re-verified every disputed citation
   against source, reclassified all seven candidates (none survived as
   standalone duplicates; two merged into new AR-012; the rest folded into
   AR-001/003/004/006 where they belong by root cause), and added
   synthesis-only findings: CLI seeding fabricates CURATION-authority
   "operator" claims from scan data (canonical.py:645-654 ×
   pipeline.py:1021-1038); neither CLI call site passes `library_state_path`
   (cli.py:541, :571); a third private cross-module reach
   (canonical_naming.py:42 → exporter._sanitize_component);
   manual_approvals' `schema_version` stamp is never checked on load.

Disagreements resolved:
- **AR-001** — challenge sustained on the stated mechanism; the *risk*
  stands in stronger revised form. Severity High retained.
- **Store count** — six, not five (manual approvals JSON is a sixth
  persistent store; CF-005 sustained).
- **Version idioms** — five stores carry version markers, five different
  semantics: two real migrations, one strict-reject, one dead constant, one
  stamp-only (expanded beyond both predecessor counts).
- **`run_pipeline` parameters** — exactly 28 keyword-only (review "25+",
  challenge "28+").
- **sha256 / atomic-writer / slugify censuses** — 7 / 5 named + 13 inline /
  7 defs · 6 independent (final AST-verified numbers).
- **AR-010** — unanimous KEEP after three independent source checks; recorded
  as a retired false positive so it cannot resurrect.

## Final findings index

| ID | Class | Sev/Conf | One-line truth |
|----|-------|----------|----------------|
| AR-001 | ROOT CAUSE | High/High | canonical.db: GUI refreshes additively, CLI seeds write-once **as top authority**, release columns never update, failures diverge silently |
| AR-002 | DOC | Low/High | `_ensure_canonical_library` docstring states the opposite of current CLI behavior |
| AR-003 | ARCH | High/High | 6 persistent stores, overlapping identity facts, no documented SSOT, no cross-store consistency tests |
| AR-004 | ARCH | Med/High | 5 versioned stores / 5 idioms; one dead constant, one stamp-only field, one strict-reject with no migration |
| AR-005 | ARCH | Med/High | dual naming regime: `release_basename` deprecated in favor of `canonical_release_name`; preserved as compatibility shim; fallback instrumentation is KEEP |
| AR-006 | MAINT | Med/High | 5×now, 5+13 atomic writers, 7 sha256, 6 slugify impls, 3 private cross-module reaches, provider scaffolding ×6 |
| AR-007 | KEEP | —/High | test_gui_equivalence.py (297 L / 10 tests) enforces GUI↔CLI parity — strongest invariant |
| AR-008 | ARCH | Med/High | run_pipeline = 28 kw-only params; each feature is a 3-site change; the two CLI call sites already drift in coverage |
| AR-009 | KEEP | —/High | staging discipline + SecretBackend ABC sound |
| AR-010 | KEEP | —/High | `.example` placeholder URLs are deliberate, consistent, documented governance (issue #12) — false positive retired |
| AR-011 | KEEP | —/High | layered provider chain + recorded conflict provenance sound; dedupe helpers (AR-006), keep boundaries |
| AR-012 | ARCH | Med/High | canonical.db never deletes rows and never expires claims: monotonically accumulates every state the library ever had |

## Prioritized recommendations (for the follow-on remediation cycle — no issues created here)

1. **P1 — Define the canonical.db lifecycle contract** (AR-001 + AR-012,
   jointly; the only High/High ROOT CAUSE). Decide: derived-cache vs.
   record-of-authority. Consequent work items: never seed CURATION-tier
   claims from automated scan paths (add a lower `seed` authority); make
   `release` columns re-resolvable from claims; decide retirement policy
   (tombstone or scoped compaction against current membership); unify CLI
   and GUI seeding so provenance stops depending on which app ran first.
   Highest leverage: also unlocks a safe exit for AR-005.
2. **P2 — State ownership map + cross-store tests** (AR-003, AR-004). A
   page in docs/ naming each fact's authoritative store, plus one
   consistency test per shared fact; wire or delete the two decorative
   version markers; give library_state a migration path.
3. **P3 — RunConfig extraction** (AR-008). Move provider toggles and
   optional config paths behind one typed object at the pipeline boundary;
   keep cancel/activity as call args; update the equivalence gate with it.
   Do this **before** the next feature ticket adds param 29.
4. **P4 — Shared util module** (AR-006). now / atomic-json / sha256 /
   slug-sanitize as public helpers; eliminate the 3 private reaches. Pure
   hygiene; schedule after P1-P3 or alongside them opportunistically.
5. **P5 — Quick wins** (AR-002 docstring fix; make GUI canonical-persist
   failure visible rather than swallowed at pipeline.py:909-912;
   manual_approvals load-time schema check).
   Explicit non-work: leave AR-007/009/010/011 alone.

## Hotspots for future ticket planning

- `gui/main_window.py` (2,729 L) + `gui/preview_widget.py` (2,687 L):
  recurring collision surface — decompose before further tickets touch them.
- `pipeline.py:776-1044` (staged build / canonical persist / ensure): the
  P1 work concentrates here + `canonical.py` write surface.
- `run_pipeline` signature: three-site change fan-out until P3 lands.
- Provider modules (~5,988 L across 6): boundary is KEEP; only helper
  dedupe (P4) applies.

## Review limits

Static verification only — no runtime GUI session was executed at
9d20b80; every claim is `file:line` citable in the companion documents.
Severity/confidence are reviewer judgment. exporter_guard.py,
diagnostics.py internals, provider auth flows, and the CI matrix were out of
scope for every pass. Per contract, this review created no remediation GitHub
issues; the durable outputs here are the input to the publication ticket.
