# State Ownership Map

**Current baseline:** Amiga ADF Library Builder v0.2.26<br>
**Reviewed:** 2026-09-22

This document is the current high-level ownership map. The detailed 2026-09-14
architecture-review snapshot is preserved at
[`archive/architecture-review/STATE-OWNERSHIP-MAP-2026-09-14.md`](archive/architecture-review/STATE-OWNERSHIP-MAP-2026-09-14.md).

The project intentionally does **not** treat one database as the source of truth
for every fact.

| State/fact | Owning subsystem/store | Notes |
|---|---|---|
| scan/parse/group observations | catalog / pipeline state | derived from read-only source files |
| persistent file identity | file identity DB | hashes and durable identity/carry-over |
| Preview & Curation staged state | library state | accepted/rejected/modified/review/Ghost and decision log |
| canonical Game/Release/Disk identity | `canonical.db` | canonical entities, claims, precedence |
| indexed DAT/local source entries | `metadata_sources.db` | owned by Metadata Source Manager |
| online metadata cache | metadata cache | provider result reuse; refreshable |
| curated metadata | metadata-curated | operator/project curated values; stronger than normal refresh |
| manual approval records | manual approvals store | explicit operator approval state |
| local-media review decisions | local-media review state | persistent ambiguous media/manual decisions |
| selection / 1G1R decisions | selection/operator-decision state | deterministic export selection |
| GUI preferences | `gui-settings.toml` | non-sensitive settings only |
| GUI credentials | `secrets.vault` / secret backend | never ordinary settings/logs |
| exported artifacts | staging/output | generated result, not canonical source identity |

## Ownership rules

1. The module/store that writes a durable fact owns it.
2. Other subsystems should read that owner rather than silently creating a
   competing copy.
3. Operator-authoritative curation must survive routine provider refresh.
4. Provider cache is replaceable; provenance and operator decisions are not.
5. A path-resolution change must not result in two active copies of
   `canonical.db`, `metadata_sources.db`, or equivalent durable identity state.
6. Schema changes require explicit migration/version handling.

For implementation detail, inspect the corresponding modules and invariant
tests in the current source tree.
