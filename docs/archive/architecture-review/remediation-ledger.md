# GH-141 — Architecture Remediation Ledger

**Baseline:** v0.2.17 / origin/main `a6734d4e608ffb25335a5e6c0c79d34e07fb3e3f`
**Ledger date:** 2026-09-14
**Program:** GH-141 architecture remediation (AR-001 … AR-012 + 2 non-numbered P5 actions)
**Post-remediation Windows baseline:** v0.2.17 (GH-155/PR156)

---

## 1. Disposition Summary

| ID | Class | Sev/Conf | Remediation | Final disposition |
|----|-------|----------|-------------|-------------------|
| AR-001 | ROOT CAUSE | High/High | GH-143 / PR144 | **REMEDIATED** — record-of-authority lifecycle with scoped release retirement; SEED=15 tier; CLI convergence |
| AR-002 | DOC | Low/High | GH-153 / PR154 (P5 Item 1) | **REMEDIATED** — docstring rewritten to describe create-if-absent behavior |
| AR-003 | ARCH | High/High | GH-145 / PR146 | **REMEDIATED** — state-ownership map published; cross-store invariant tests added |
| AR-004 | ARCH | Med/High | GH-145 / PR146 | **REMEDIATED** — version-marker hygiene (dead constant removed, manual_approvals schema validated) |
| AR-005 | ARCH | Med/High | GH-147 / PR148 | **REMEDIATED** — `release_basename` preserved as compatibility shim with DeprecationWarning; `canonical_release_name` is primary path |
| AR-006 | MAINT | Med/High | GH-151 / PR152 | **REMEDIATED** — `utils.py` created with 4 canonical functions; 19 call sites migrated across 17 modules |
| AR-007 | KEEP | —/High | — | **PROTECTED** — GUI↔CLI equivalence gate (`test_gui_equivalence.py`) preserved |
| AR-008 | ARCH | Med/High | GH-149 / PR150 | **REMEDIATED** — typed `RunConfig` frozen dataclass; pipeline signature refactored |
| AR-009 | KEEP | —/High | — | **PROTECTED** — staging discipline + SecretBackend ABC preserved |
| AR-010 | KEEP | —/High | — | **PROTECTED** — placeholder `.example` URLs confirmed deliberate governance |
| AR-011 | KEEP | —/High | — | **PROTECTED** — layered provider chain + conflict provenance preserved |
| AR-012 | ARCH | Med/High | GH-143 / PR144 | **REMEDIATED** — scoped release retirement (`retired` flag); claim accumulation bounded |
| P5-a | GUI/canonical persistence failure visibility | — | GH-153 / PR154 (P5 Item 2) | **REMEDIATED** — `logging.warning()` added to bare except handlers |
| P5-b | Manual approvals schema/version validation | — | GH-145 / PR146 | **REMEDIATED** — `from_dict` validates `schema_version`; loader skips mismatches |

---

## 2. Per-AR-ID Detail

### AR-001 — canonical.db lifecycle (ROOT CAUSE, High/High)

**Original finding.** canonical.db had three unequal writers, no update path for release columns, no retirement concept, and CLI seeding fabricated CURATION-authority claims from scan data.

**Remediation.** GH-143 / PR144 implemented record-of-authority with scoped release retirement (Alt C):
- New `SEED=15` authority tier between PARSER and DAT
- Release descriptor columns re-resolved from winning claim on each write
- Soft-delete retirement (`retired` column) for releases no longer in library
- CLI convergence: CLI passes `library_state_path` when available
- Forward-only schema migration v1 → v2 adding `retired` column

**Tests.** `tests/test_canonical_lifecycle.py` (23 tests): SEED tier, column re-resolution, retirement, CLI convergence, migration idempotency, `release_id` stability.

**Confidence.** High. Every mechanism `file:line`-verified in `canonical.py`, `pipeline.py`, `selection.py`, `canonical_naming.py`.

---

### AR-002 — `_ensure_canonical_library` docstring (DOC, Low/High)

**Original finding.** Docstring stated "Fresh CLI exports never create canonical.db" — false since GH-136-era change.

**Remediation.** GH-153 / PR154 (P5 Item 1) rewrote docstring to accurately describe create-if-absent behavior. No behavioral change.

**Tests.** Existing `test_1g1r_r1_r4.py` exercises the behavior.

**Confidence.** High. Direct reading of false invariant vs. actual code.

---

### AR-003 — Six persistent stores, no documented SSOT (ARCH, High/High)

**Original finding.** Six persistent stores encode overlapping identity facts with no documented single source of truth and no cross-store consistency tests.

**Remediation.** GH-145 / PR146:
- Published `docs/STATE-OWNERSHIP-MAP.md` with full per-fact ownership table
- Added `tests/test_state_ownership_invariants.py` with 6 cross-store invariant tests
- Each shared fact (sha256, release_key, curation_state, title, region/language/publisher/edition, disk_number, approval match, game_id, size) assigned authoritative owner

**Tests.** `test_state_ownership_invariants.py`: sha256 consistency, release_key consistency, curation_state projection, metadata_source schema enforcement, manual_approvals schema validation, no-store-owns-everything registry.

**Confidence.** High for ownership map; Medium for invariant tests (new, not yet run against production data).

---

### AR-004 — Five versioned stores, five idioms (ARCH, Med/High)

**Original finding.** Five versioned stores with five different migration idioms; two decorative (dead constant, stamp-only).

**Remediation.** GH-145 / PR146:
- `metadata_source.py:197` dead `SCHEMA_VERSION = constant removed
- `manual_approvals.py` `from_dict` now validates `schema_version` stamp on load
- `library_state.py` strict-reject preserved (migration path deferred as R4)
- `file_identity.py` and `canonical.py` real migrations preserved as reference idioms

**Tests.** `test_state_ownership_invariants.py:264-294` — `TestManualApprovalsSchemaVersionValidated` (3 sub-tests).

**Confidence.** High.

---

### AR-005 — Dual naming regime (ARCH, Med/High)

**Original finding.** `release_basename` remained load-bearing across 7 production modules; `canonical_release_name` was not the operative default.

**Remediation.** GH-147 / PR148 (Alt 3 — Preserve with deprecation markers):
- `release_basename()` emits `DeprecationWarning` on every call
- All fallback call sites in `canonical_naming.py`, `exporter.py`, `pipeline.py` emit context-specific warnings
- `canonical_release_name` is the primary path
- Function preserved as compatibility shim

**Tests.** `test_collision.py` (2 new tests), `test_canonical_naming.py` (updated). 214+ tests pass.

**Confidence.** High. Exhaustive 7-module dependency map.

---

### AR-006 — Cross-module duplication (MAINT, Med/High)

**Original finding.** 7× `_now_iso`/`utc_now`, 6× `_sha256_file`, 4× `_write_json_atomic`, 2× `_sha256_bytes`, 2× `_slugify_title` — scattered across modules with no shared utility layer.

**Remediation.** GH-151 / PR152 (Alt D — new `utils.py`):
- Created `src/amiga_adf_library_builder/utils.py` with `now_iso`, `sha256_file`, `sha256_bytes`, `write_json_atomic`
- Migrated 19 call sites across 17 modules
- `canonical_naming._slugify_title` deleted, routed to `canonical.slugify_title`
- Preserved: sanitize family (different contracts), `_build_provenance*` variants (different output types), `metadata_source._sha256_file` (Optional wrapper), `file_identity` static methods (deferred Phase 2)

**Tests.** 254+ tests pass, 0 failures.

**Confidence.** High. Byte-identical verification of all consolidated copies.

---

### AR-007 — GUI↔CLI equivalence gate (KEEP, High)

**Original finding.** `test_gui_equivalence.py` (297 L / 10 tests) enforces GUI↔CLI parity — strongest invariant in the repo.

**Remediation.** None. **PROTECTED.** Do not disturb during any remediation cycle.

**Tests.** `test_gui_equivalence.py` — 10 tests, all pass.

---

### AR-008 — `run_pipeline` parameter drift (ARCH, Med/High)

**Original finding.** 28 keyword-only parameters; each feature ticket forced 3-site changes guarded only by the equivalence test.

**Remediation.** GH-149 / PR150:
- Created `src/amiga_adf_library_builder/run_config.py` with frozen `RunConfig` dataclass
- Pipeline signature refactored to `run_pipeline(cfg: PathConfig, run: RunConfig)`
- `from_legacy_kwargs()` classmethod + `**kwargs` escape hatch with DeprecationWarning
- `__post_init__` runtime validation for artwork dimensions
- 14 invariants identified and preserved

**Tests.** `test_run_config.py` (17 tests), `test_1g1r_semantic_blockers.py` (18 tests), `test_issue33_launchbox_mappings.py` (21 non-GUI tests), `test_1g1r_r1_r4.py` (6 tests). 62 focused tests pass.

**Confidence.** High. All 21 run_pipeline references measured directly.

---

### AR-009 — Staging discipline and secrets (KEEP, High)

**Original finding.** Staged export dir + `gui/secrets.py` SecretBackend ABC + RedactingFilter are sound.

**Remediation.** None. **PROTECTED.**

---

### AR-010 — Placeholder provider base URLs (KEEP, High)

**Original finding.** `.example` defaults are byte-identical between GUI and provider modules; deliberate disabled-by-default governance (issue #12).

**Remediation.** None. **PROTECTED.** False positive retired after three independent source checks.

---

### AR-011 — Layered provider chain (KEEP, High)

**Original finding.** playmatch→hasheous→igdb→screenscraper→retroachievements with confidence-based override and recorded conflicts is correct design.

**Remediation.** None. **PROTECTED.** Structural duplication is AR-006's helper problem, not the chain's.

---

### AR-012 — canonical.db never retires rows or claims (ARCH, Med/High)

**Original finding.** `field_claim` uses `INSERT OR IGNORE` with no DELETE/retirement path; releases accumulate forever.

**Remediation.** GH-143 / PR144 (jointly with AR-001):
- `retired` column added to `release` table (schema v2)
- `retire_releases(active_release_ids)` method marks releases not in current library
- Read paths (`releases_for_game`, `release_row`, `claims_for`) filter retired by default
- `include_retired` flag available for audit queries

**Tests.** `test_canonical_lifecycle.py`: retire_releases, releases_for_game excludes retired, claims_for excludes retired, release_row returns None for retired, schema v1→v2 migration.

**Confidence.** High.

---

## 3. Non-Numbered P5 Actions

### P5-a — GUI/canonical persistence failure visibility

**Original finding.** `_persist_canonical_library` call site at `pipeline.py:928-935` used bare `except Exception: pass`, silently swallowing all canonical.db write failures.

**Remediation.** GH-153 / PR154 (P5 Item 2):
- Added `logging.warning("canonical.db persistence failed (non-fatal): %s", exc)` at call site
- Added `logging.warning("canonical.db write failed (non-fatal): %s", exc)` inside `_persist_canonical_library`
- Best-effort semantics preserved (staged state file remains curation authority)

**Tests.** `tests/test_p5_canonical_persist_failure_logging.py` (2 tests).

**Confidence.** High.

---

### P5-b — Manual approvals schema/version validation

**Original finding.** `manual_approvals.py` `SCHEMA_VERSION` stamp was written but never validated on load.

**Remediation.** GH-145 / PR146:
- `ApprovalRecord.from_dict` raises `ValueError` on `schema_version` mismatch
- `_read_json_records` catches `ValueError` and skips malformed records
- Backward compatible: records without `schema_version` accepted

**Tests.** `test_state_ownership_invariants.py:264-294` — 3 sub-tests (mismatch rejected, valid accepted, missing version accepted).

**Confidence.** High.

---

## 4. v0.2.17 Qualified Post-Remediation Windows Baseline

**Release:** v0.2.17 (GH-155 / PR156)
**Base:** `4bf5d9cd9c6f7f8110ee9ea67d394c1407b85cfb`
**Candidate:** `26b816cb26cb2127211708d629f79ec3a8ee2bf2`
**Convention:** Same 4-file pattern as prior release commit `4d53b09`:
- `pyproject.toml`
- `CHANGELOG.md`
- `AmigaADFGui.spec`
- `tests/test_version_identity.py`

**Auto-generated files regenerated:**
- `src/amiga_adf_library_builder/_frozen_version.py`
- `tools/_version_info.txt`

**Tests:** 9 version identity tests pass.

**Expected Windows assets:**
- `amiga-adf-gui-portable.zip`
- `amiga-adf-gui-onefile`
- `amiga-adf-gui.exe`

**Workflows:** `.github/workflows/build-windows.yml`, `.github/workflows/qa-windows-real-exec.yml`

---

## 5. Stale Claim Corrections

### GH-153 closeout wording correction

**Stale claim.** GH-153 original closeout stated AR-001/AR-012 remained open after P5 implementation.

**Correction.** AR-001 and AR-012 were **already remediated** by GH-143 / PR144 (merged at `2cac193`). GH-153 addressed only the two NEEDS-FIX P5 items (AR-002 docstring, GUI canonical persistence failure visibility) and verified manual-approvals schema validation as SATISFIED-AS-IS from GH-145. AR-001/AR-012 are **CLOSED** via GH-143.

---

## 6. Remediation Commit Lineage

| Ticket | PR | Base | Candidate | Merge |
|--------|----|------|-----------|-------|
| GH-143 | PR144 | `9d20b80` | `2cac1936702a632b1d27ebebb3eebce620affd5a` | `2cac193` |
| GH-145 | PR146 | `2cac193` | `0f12d08ec5fd6e500d8376eb5414f350dfe75d59` | `0f12d08` |
| GH-147 | PR148 | `0f12d08` | `2b3cbc6c8df7190c3c751a0acc40fb06d4b1ecda` | `e3363f9` |
| GH-149 | PR150 | `e3363f9` | `3f602595da7775ea63ecda8b84a2902b251f17a7` | `351ff36` |
| GH-151 | PR152 | `351ff36` | `60d0a80091c702f8e753e534d3184d610055c10f` | `20da806` |
| GH-153 | PR154 | `20da806` | `b308d4d111139acf1b263cc8e25b205ab4f28927` | `b308d4d` |
| GH-155 | PR156 | `4bf5d9c` | `26b816cb26cb2127211708d629f79ec3a8ee2bf2` | `a6734d4` |

---

## 7. Architectural Invariants Preserved

1. **GUI↔CLI equivalence** — `test_gui_equivalence.py` (297 L / 10 tests) — AR-007 KEEP
2. **Curation survives provider refresh** — `test_canonical_model.py:258-274` — GH-143 design contract
3. **Conflict preservation** — `test_canonical_model.py` — claims accumulate, never silently overwritten
4. **Staging discipline** — `work/staging/<run-id>/` + manifest; separate Phase-10 SD publish — AR-009 KEEP
5. **Secrets design** — `gui/secrets.py` SecretBackend ABC + RedactingFilter — AR-009 KEEP
6. **Provider chain boundaries** — playmatch→hasheous→igdb→screenscraper→retroachievements — AR-011 KEEP
7. **Placeholder URL governance** — `.example` defaults consistent across GUI/provider modules — AR-010 KEEP
8. **Cancellation/activity hooks** — cooperative `cancel_event` + `activity` callback — preserved across all remediations
9. **Export gate** — `exporter_guard.export_gate_open` — untouched by remediation
10. **Windows frozen builds** — `_frozen_version.py` regenerated per release convention — GH-155

---

## 8. Intentionally Retained Behaviors

| Behavior | Reason |
|----------|--------|
| `release_basename()` function | Compatibility shim across 7 modules; deprecated but functional |
| `library_state` strict-reject (no migration) | Deferred R4; known contained risk |
| `file_identity` static methods | Deferred Phase 2; behavioral no-op but low priority |
| 27+ inline `datetime` calls | Deferred Phase 2; not part of AR-006 high-confidence set |
| Sanitize family (6 independent implementations) | Different contracts per module; consolidation would blur ownership |
| `_build_provenance*` variants | Different output types, different scopes |
| `metadata_source._sha256_file` Optional wrapper | Preserves Optional[str] return type distinct from sha256_file |
| `manual_approvals.utc_now` / `metadata.utc_now` | Public aliases retained for API compatibility |

---

## 9. Test Evidence Summary

| Suite | Tests | Status |
|-------|-------|--------|
| `test_canonical_lifecycle.py` | 23 | All pass |
| `test_canonical_model.py` | existing | All pass |
| `test_canonical_naming.py` | existing | All pass |
| `test_canonical_naming_production.py` | existing | All pass |
| `test_state_ownership_invariants.py` | 6+ | All pass |
| `test_run_config.py` | 17 | All pass |
| `test_1g1r_semantic_blockers.py` | 18 | All pass |
| `test_issue33_launchbox_mappings.py` | 21 non-GUI | All pass |
| `test_1g1r_r1_r4.py` | 6 | All pass |
| `test_gui_issue24_metadata_selection.py` | 20 | All pass |
| `test_p5_canonical_persist_failure_logging.py` | 2 | All pass |
| `test_collision.py` | 2 new | All pass |
| `test_gui_equivalence.py` | 10 | All pass |
| `test_version_identity.py` | 9 | All pass |
| **Total focused regression** | **254+** | **All pass** |

---

## 10. Sources & Durable Artifact Locations

- `docs/architecture-review/findings.md` — GH-141 findings catalog (AR-001 … AR-012)
- `docs/architecture-review/final-review.md` — GH-141 final verdict + P1-P5 priorities
- `docs/architecture-review/architecture-truth-map.md` — 6-store truth map
- `docs/STATE-OWNERSHIP-MAP.md` — authoritative per-fact ownership table (GH-145)
- `GH141-QBRANCH-RECON.md` — original evidence map
- `GH143-QBRANCH-CANONICAL-LIFECYCLE-DESIGN.md` — AR-001/AR-012 design
- `GH145-QBRANCH-STATE-OWNERSHIP-DESIGN.md` — AR-003/AR-004 design
- `GH147-QBRANCH-LEGACY-NAMING-MAP.md` — AR-005 design
- `GH149-QBRANCH-RUNCONFIG-MAP.md` — AR-008 design
- `GH151-QBRANCH-DUP-MAP.md` — AR-006 design
- `GH153-QBRANCH-P5-CURRENT-STATE-MAP.md` — P5 current-state measurement

---

*End of GH-141 Remediation Ledger.*
