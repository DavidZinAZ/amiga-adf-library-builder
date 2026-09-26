# ACTIVE-TASK — GH-192 REM-3 — Diagnostic/Observability Fixes

- Task: GH-192 REM-3 (from corrected Q-Branch research handoff)
- Mode: IMPLEMENT (authorized by task body)
- BASE: a444a4f81f66f7ea4d9d527d2f22af3d75f1b243 (origin/main, v0.2.33)
- Branch: dev/gh192-rem3
- Worktree: /home/dumbo/projects/amiga-adf-library-builder
- Status: IMPLEMENTING

## Tasks

### TASK A — _try_provider exception classification
Fix the _try_provider() exception classifier so transport/infrastructure failures are classified as request_error rather than falling through to parse_error. Use proper exception-type handling.

### TASK B — HOL/Lemon swallowed transport failures
Add bounded diagnostic visibility before returning None so DNS/connection/HTTP/timeout/provider failures can be distinguished from a clean no-match. Preserve the existing None return contract.

### TASK C — misleading not_configured outcome
Fix the diagnostic outcome used when an enabled/configured provider was actually called and returned no candidate. Do not label that state not_configured.

## Implementation Notes
- Primary change: src/amiga_adf_library_builder/metadata.py
- Test file: tests/test_gh192_rem3.py (new)
- Changes are bounded to diagnostic/observability only
- No functional matching/scoring behavior changes
