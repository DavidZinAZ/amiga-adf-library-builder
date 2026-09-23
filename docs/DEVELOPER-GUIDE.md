# Amiga ADF Library Builder — Developer Guide

**Applies to:** Amiga ADF Library Builder v0.2.26<br>
**Guide date:** 2026-09-22

---

## 1. Development principles

Amiga ADF Library Builder is a preservation-first Python application with a shared core used by both the CLI and Windows GUI.

The highest-level engineering constraints are:

- original ADF/DSK sources are read-only;
- GUI and CLI use the same core pipeline;
- behavior should be deterministic and testable;
- network use is opt-in;
- ambiguous identity is reviewed rather than guessed;
- state ownership is explicit;
- secrets never belong in ordinary settings or logs;
- export writes are gated;
- release artifacts must identify the exact application version.

---

# 2. Repository layout

A typical source checkout contains:

```text
amiga-adf-library-builder/
├── src/
│   └── amiga_adf_library_builder/
├── tests/
├── docs/
├── config/
├── tools/
├── .github/
│   └── workflows/
├── pyproject.toml
├── AmigaADFGui.spec
├── app_launcher.py
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
└── SECURITY.md
```

---

# 3. Python/package baseline

The project requires:

```text
Python >= 3.11
```

The Windows packaging pipeline uses:

```text
Python 3.12
```

The core package intentionally has:

```text
dependencies = []
```

Normal optional extras include:

```text
dev
artwork
rtfm-docs
gui
```

---

# 4. Development environment

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Upgrade pip:

```bash
python -m pip install --upgrade pip
```

Install development and artwork support:

```bash
python -m pip install -e ".[dev,artwork]"
```

For GUI development:

```bash
python -m pip install -e ".[gui]"
```

For RTFM/PDF extraction:

```bash
python -m pip install -e ".[rtfm-docs]"
```

---

# 5. Main console entry points

CLI:

```text
amiga-adf-library-builder
```

Configured in `pyproject.toml` as:

```text
amiga_adf_library_builder.cli:main
```

Developer GUI:

```text
amiga-adf-gui
```

Configured as:

```text
amiga_adf_library_builder.gui.app:main
```

The frozen Windows application uses the `gui.app:run` path through the packaging launcher.

---

# 6. Core/GUI boundary

The Windows GUI is a presentation layer over the shared core.

The intended flow is:

```text
CLI
  -> parse arguments
  -> resolve PathConfig
  -> run_pipeline()

GUI
  -> collect widget state
  -> build PathConfig
  -> build pipeline kwargs
  -> run_pipeline()
```

Do not reimplement scanning, parsing, grouping, enrichment, validation, or export logic in GUI widgets.

If behavior belongs to both CLI and GUI, it belongs in the shared core.

---

# 7. Important GUI bridge modules

The GUI/core bridge lives primarily in:

```text
gui/state.py
gui/worker.py
gui/main_window.py
```

`gui/state.py` maps GUI settings into shared path/pipeline configuration.

`gui/worker.py` runs the pipeline on a worker thread.

`gui/main_window.py` owns presentation, user interaction, and run orchestration.

Tests should protect equivalence between GUI and CLI inputs.

---

# 8. Main core modules

Important modules include:

```text
scanner.py
parser.py
grouper.py
pipeline.py
models.py
paths.py
catalog.py
enrich.py
metadata.py
artwork.py
nfo_render.py
exporter.py
exporter_guard.py
quarantine.py
library_state.py
canonical.py
canonical_naming.py
file_identity.py
lookup_workflow.py
metadata_source.py
manual_lookup.py
rtfm.py
rtfm_docs.py
selection.py
```

Provider-specific modules include:

```text
igdb.py
screenscraper.py
retroachievements.py
retrokit.py
playmatch.py
hasheous.py
local_media.py
```

---

# 9. Pipeline stages

Conceptually, the pipeline performs:

```text
Scan
 -> Parse
 -> Group
 -> Enrich
 -> Curate/reconcile
 -> Validate
 -> Stage/export
 -> Quarantine unresolved material
```

Original files are never normal write targets.

---

# 10. Scan stage

The scanner performs a read-only walk of the original collection.

Typical recorded facts include:

```text
path
filename
size
SHA-256
```

Hashing is important for:

- preservation verification;
- release identity;
- provider correlation;
- later provenance.

---

# 11. Parse stage

The parser attempts to derive structured release information from source naming.

Typical parsed fields include:

```text
title
disk number
disk count
edition
chipset
language
version
release/crack group
trainer
alternate marker
special-disk role
```

Parsing should remain deterministic.

Do not introduce provider/network behavior into filename parsing.

---

# 12. Group stage

Grouping assembles related disks into releases.

The grouping layer must avoid mixing incompatible releases.

Multi-disk ordering and release variants belong here or in the canonical model, not in ad-hoc GUI code.

---

# 13. Canonical model

The canonical hierarchy is broadly:

```text
Game
 -> Release
    -> Disk
```

The canonical layer exists because raw filenames are not sufficient identity.

A release may have:

```text
canonical title
edition
release group
variant
disk membership
provider correlations
curation claims
```

---

# 14. Canonical claims

The canonical database can hold competing claims for a fact.

Example:

```text
field: title

claim A:
  value: Hacker II
  source: Hall of Light

claim B:
  value: Hacker 2
  source: DAT

claim C:
  value: Hacker II: The Doomsday Papers
  source: operator curation
```

Resolution uses authority/precedence rather than whichever result arrived last.

Manual/operator curation is deliberately strong.

---

# 15. State ownership

The project explicitly avoids one universal database being called the source of truth for everything.

Different stores own different facts.

Consult:

```text
docs/STATE-OWNERSHIP-MAP.md
```

before adding new cross-store behavior.

The architectural rule is:

```text
the store that writes/owns a fact is authoritative for that fact
```

Other readers should not silently become competing owners.

---

# 16. Major persistent stores

Depending on feature and configuration, state may exist in:

```text
canonical.db
metadata_sources.db
metadata cache
curated metadata
library state
file identity state
manual approval records
provider caches
selection/operator-decision files
GUI settings
GUI secret vault
```

Do not duplicate the same fact into a new store without defining ownership.

---

# 17. Library state / curation state

Preview & Curation works against staged library state.

Common states include:

```text
pending
accepted
rejected
modified
needs_review
ghost
```

Curation actions are recorded in a decision log.

Examples:

```text
rename
metadata edit
move
merge
review resolved
accept/reject
note
lookup apply
```

Undo/redo should use stored structured action payloads where possible rather than parsing human-readable strings.

---

# 18. Ghost state

A Ghost release is an identity retained after content has been moved/merged away.

Do not treat Ghost as an ordinary exportable release.

Ghost exists to preserve state/history consistency.

---

# 19. Lookup workflow

Lookup behavior belongs in:

```text
lookup_workflow.py
```

rather than being independently implemented in Preview dialogs.

The GUI provides modes such as:

```text
online
offline/local
alternate/custom query
```

Provider selection should be determined centrally.

---

# 20. Online relevance validation

Online provider results are not trusted solely because a provider returned them.

The metadata layer validates candidate relevance.

Typical classifications are:

```text
accepted
review
rejected
```

Validation should protect against:

- sequel collisions;
- biographies/non-game pages;
- weak title similarity;
- misleading title extensions.

Do not weaken these gates merely to increase match rate.

---

# 21. Metadata caching

Provider results are persisted to reduce repeated network traffic.

Core helpers include concepts equivalent to:

```text
load_cached()
save_cached()
load_curated()
```

Refresh behavior must bypass provider cache deliberately while preserving operator-curated metadata.

---

# 22. Curated metadata

Curated records are authoritative operator/project data.

They should not be overwritten by ordinary provider refresh.

If a migration changes curated record format, preserve user edits.

---

# 23. Metadata Sources

Local DAT/indexed sources use `metadata_source.py` and a SQLite database.

The GUI manages them through:

```text
Add DAT
Add Folder
Rescan
Reindex Changed
Remove
```

Current supported intent includes TOSEC-style and No-Intro-style XML/DAT inputs.

Schema migrations should use an explicit version mechanism such as SQLite `PRAGMA user_version`.

---

# 24. Metadata source path consistency

All consumers must resolve the same authoritative `metadata_sources.db`.

v0.2.26 contains fixes specifically around DB-path consistency.

When adding a new lookup path, do not construct an independent fallback DB path if a resolved library path already exists.

---

# 25. Provider abstraction

GUI providers implement a generic abstraction in:

```text
gui/providers.py
```

A provider describes:

```text
id
name
description
enabled state
authentication requirement
fields
capabilities
status
connection test
credential behavior
typed config generation
```

The GUI renders providers generically.

Avoid per-provider widget implementations unless there is a compelling architectural reason.

---

# 26. Current GUI provider registry

Current generic providers include:

```text
Playmatch
Hasheous
IGDB
ScreenScraper
RetroAchievements
Lemon Amiga
Hall of Light
```

Other lower-level metadata providers may exist in core without appearing in this same registry.

Do not assume "exists in metadata.py" means "is exposed in Providers tab."

---

# 27. Adding a provider

A new provider should define:

1. configuration model;
2. enable/disable default;
3. authentication requirements;
4. capabilities;
5. bounded network behavior;
6. relevance/matching rules;
7. cache semantics;
8. provenance fields;
9. tests with fake/injected network responses;
10. GUI adapter if it should be user-configurable there.

Do not require a network call at import time.

---

# 28. Provider capabilities

Generic capabilities include concepts such as:

```text
online lookup
hash resolution
metadata
artwork
export
```

Declare only capabilities actually supported.

---

# 29. Network behavior

Tests should not require live network access.

Inject openers/clients or synthetic responses.

Network-facing code should be:

- bounded by timeouts;
- bounded by response size;
- rate-limit aware;
- safe against SSRF;
- deterministic under failure.

Provider failure should degrade cleanly where possible.

---

# 30. SSRF / URL safety

Remote URLs are validated before use.

Security checks include:

- permitted schemes;
- host allowlists where appropriate;
- rejection of loopback;
- rejection of link-local;
- rejection of private ranges;
- rejection of bare unsafe IP targets.

Do not bypass the shared URL validation layer in provider-specific code.

---

# 31. Secrets architecture

Settings and secrets are separate.

Non-sensitive GUI configuration:

```text
gui/settings.py
```

Secrets:

```text
gui/secrets.py
```

A normal GUI settings export must not include credentials.

---

# 32. Portable vault

The GUI can use an AES-GCM portable vault.

The master password is not stored.

A lost master password means the vault contents are unrecoverable.

Tests should verify:

- encrypt/decrypt;
- wrong-password failure;
- lock state;
- no plaintext secret leakage.

---

# 33. Secret redaction

Logging uses redaction filters.

Redaction should cover:

- known exact secret strings;
- sensitive `key=value` pairs;
- authorization/bearer values.

Do not log full provider request URLs when they may contain query-string credentials.

---

# 34. GUI portable paths

`gui/layout.py::PortablePaths` owns app-relative GUI state:

```text
<base>/config
<base>/data
<base>/logs
<base>/cache
<base>/themes
```

Frozen builds normally use the executable's directory as base.

Advanced override:

```text
AMIGA_ADF_GUI_BASE
```

No hard-coded user/home path should be required for the portable GUI.

---

# 35. Library paths

The Amiga library root is separate from the GUI's portable application base.

Typical library roles include:

```text
original_dir
staging_dir
output_dir
quarantine_dir
approvals_dir
reports_dir
logs_dir
cache_dir
```

Path safety checks must prevent writable roles from resolving inside the preservation source tree.

---

# 36. Artwork

Artwork processing lives in shared core modules.

The Windows GUI includes Pillow because artwork is part of expected packaged behavior.

Artwork architecture distinguishes:

```text
source/master artwork
processed export artwork
provenance
```

Never overwrite source artwork merely to satisfy an export format.

---

# 37. Progressive JPEG handling

Progressive JPEG policy is a user-facing option.

Any conversion must preserve provenance and remain deterministic.

Tests should cover:

- baseline input;
- progressive input;
- policy modes;
- failure behavior.

---

# 38. RTFM/manual architecture

Manual handling involves:

```text
rtfm.py
rtfm_docs.py
manual_lookup.py
provider document associations
local manual roots
RetroKit/manual provider
```

RTFM behavior should degrade clearly when optional document libraries are absent.

Do not fabricate manual text when extraction fails.

---

# 39. Optional RTFM dependencies

The `rtfm-docs` extra currently includes:

```text
pypdf
pymupdf
pillow
pytesseract
```

System Tesseract may also be required for OCR.

The core should fail with an actionable unavailable message instead of crashing unexpectedly.

---

# 40. Local media provider

`local_media.py` is designed to be:

- offline;
- read-only against source libraries;
- deterministic;
- conservative around fuzzy matches.

Matching may consider:

```text
canonical title
original disk stem
normalized title
variant-stripped title
fuzzy similarity
```

Uncertain results belong in review.

---

# 41. Local media precedence

When multiple configured roots contain the same media type, root ordering is meaningful.

The first root may win same-type conflicts.

The GUI exposes Move Up / Move Down to modify precedence.

Preserve this deterministic order in core behavior.

---

# 42. Match Review

Ambiguous local matches should produce a durable review item.

Operator decisions should be persisted so the same ambiguous match does not need to be resolved on every run.

---

# 43. Export safety

Export behavior is gated.

Conceptual requirements include:

```text
explicit export mode
explicit write acknowledgement
optional verify-only/check-only
optional artwork requirement
validated destination
```

A Build should not silently become a final Export.

---

# 44. Staging

Export writes to managed staging before publication.

Typical path:

```text
work/staging/<run-id>
```

Rollback should be scoped to the current run-owned staging path.

Avoid broad deletes against shared output roots.

---

# 45. Gotek layout

The export layer produces a flattened Gotek-compatible structure.

Conceptually:

```text
ADF/
  Game Name/
    Game Name-1.adf
    Game Name-2.adf
    Game Name.jpg
    Game Name.nfo
```

and similarly for DSK.

Internal identity can be richer than the output folder structure.

---

# 46. 1G1R

One-Game-One-Release selection is handled by selection logic, not by throwing away alternate releases.

Inputs can include:

```text
canonical game grouping
accepted/rejected state
operator decisions
selection manifest
```

Selection output should be reproducible and provenance-preserving.

---

# 47. NFO rendering

`nfo_render.py` owns Gotek-facing NFO output.

Keep concise display content separate from richer provenance sidecars.

Do not force all internal metadata into a constrained Gotek-facing NFO.

---

# 48. Provenance

Preserve enough information to answer:

```text
Where did this value come from?
Which provider supplied it?
Which provider record ID?
When was it retrieved?
Was it manually overridden?
Where did this artwork/manual come from?
```

Provenance is a first-class feature, not debugging clutter.

---

# 49. Diagnostics/activity logging

`activity_log.py` and GUI diagnostics expose run activity.

User-facing logs should:

- be readable;
- not expose secrets;
- identify provider and phase;
- include actionable failure context.

Avoid huge unstructured dumps when concise structured messages are possible.

---

# 50. Cooperative cancellation

The GUI worker uses cooperative cancellation rather than forced thread termination.

Core phases should check a cancellation event at safe boundaries.

Never terminate a worker in a way that can leave an export half-mutated without recovery information.

---

# 51. GUI thread rules

Long operations belong off the UI thread.

Qt widgets must be mutated from the GUI thread.

Worker signals should communicate:

```text
progress
status
completion
error
```

Do not block the event loop with network or large filesystem operations.

---

# 52. Tests

Run the full test suite:

```bash
python -m pytest
```

The repository's tests are intended to run without live network dependency.

---

# 53. Test categories

Important test areas include:

- parser;
- grouping;
- pipeline;
- path safety;
- GUI/core equivalence;
- provider adapters;
- provider matching;
- GUI settings;
- secret vault/redaction;
- canonical identity;
- state ownership;
- metadata source schema;
- local media matching;
- RTFM;
- move/merge;
- undo/redo;
- export gates;
- version identity;
- Windows packaging assumptions.

---

# 54. Provider tests

Provider tests should use synthetic responses.

Test:

```text
successful match
clean miss
ambiguous candidate
timeout
malformed response
oversized response
authentication failure
unsafe URL
cache hit
refresh
provenance
```

Avoid calling a live external service from CI.

---

# 55. Regression tests

Every user-visible bug fix should include a focused regression test.

A useful regression test should fail for the original bug, not merely exercise nearby code.

Examples:

- filter state survives redraw;
- move only selected ADFs;
- merge redraw/source Ghost behavior;
- sequel candidate rejected;
- manual selection survives Preview -> Export;
- metadata DB path is unified;
- packaged version matches tag.

---

# 56. State-ownership invariant tests

State ownership is machine-checkable.

Consult:

```text
tests/test_state_ownership_invariants.py
```

before refactoring shared state.

A refactor that causes two stores to silently own the same fact is architectural regression even if unit tests otherwise pass.

---

# 57. Version source

The canonical application version is defined in:

```text
pyproject.toml
```

Example:

```toml
version = "0.2.26"
```

Development runtime reads from this canonical version path.

Frozen builds receive a build-time version module.

Avoid hard-coded copies in runtime source.

---

# 58. Windows spec version

`AmigaADFGui.spec` includes an application version used during packaging.

Release qualification/tests must ensure this agrees with the canonical project version.

Do not casually hand-edit generated packaging files.

The spec explicitly states it is generated by:

```text
tools/build_windows.py
```

---

# 59. Windows packaging

Build locally on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -e ".[gui]"
python -m pip install pyinstaller

python tools\build_windows.py --target onedir --clean
python tools\build_windows.py --target onefile
```

---

# 60. Windows artifacts

The packaging workflow creates:

```text
amiga-adf-gui-portable.zip
amiga-adf-gui.exe
```

The onedir build contains:

```text
AmigaADFLibraryBuilder\
    AmigaADFLibraryBuilder.exe
    _internal\
    ...
```

---

# 61. GitHub Actions Windows build

Workflow:

```text
.github/workflows/build-windows.yml
```

It runs on:

```text
windows-latest
```

and uses:

```text
Python 3.12
```

Main steps include:

```text
checkout
install gui dependencies
verify entry point
verify version drift gate
verify Pillow
build onedir
build onefile
offscreen GUI smoke test
package ZIP
upload artifacts
```

---

# 62. Release creation

Tag pushes matching:

```text
v*
```

can trigger release asset publication.

The release job verifies that:

```text
tag version == pyproject.toml version
```

before publishing.

This prevents a v0.2.27 tag from shipping a v0.2.26 binary.

---

# 63. Version drift gate

The Windows workflow checks for stale hard-coded version patterns.

When bumping a version:

1. change canonical project version;
2. regenerate packaging version data/spec as required;
3. run version-identity tests;
4. confirm build artifacts report the new version;
5. tag only after the code version is correct.

---

# 64. PyInstaller spec

`AmigaADFGui.spec` declares hidden imports for core and GUI modules.

If a new dynamically imported module works in development but is missing from the frozen binary, update the packaging generator/spec hidden-import set.

Do not fix frozen-import bugs by importing arbitrary modules at global runtime just to make PyInstaller discover them.

---

# 65. No secrets in the bundle

The PyInstaller spec deliberately ships no runtime vault or credential files.

Runtime files are created after installation.

Never add:

```text
.env
secrets.vault
password files
private config
API tokens
```

to PyInstaller `datas`.

---

# 66. Headless GUI smoke test

CI constructs the GUI under:

```text
QT_QPA_PLATFORM=offscreen
```

This is a packaging sanity check, not full Windows behavioral qualification.

It proves:

- GUI imports;
- QApplication constructs;
- core GUI dependencies are present.

Real operator/QA testing is still required.

---

# 67. Release QA

A Windows release should be tested as the actual packaged artifact, not only from source.

Important checks include:

- application launches;
- version is correct;
- Library path works;
- path with spaces works;
- Preview populates;
- providers/settings load;
- diagnostics work;
- artwork processing works;
- move/merge works;
- lookup works;
- manual lifecycle works;
- check-only export works;
- final export works;
- originals remain unchanged.

---

# 68. Development style

Expected contribution qualities:

- explicit functions;
- clear contracts;
- deterministic logic;
- type hints on public functions;
- tests for behavior changes;
- no swallowed core exceptions;
- no silent fallback that hides data loss;
- factual changelog entries.

---

# 69. Error handling

Distinguish:

```text
clean miss
needs review
recoverable provider failure
configuration error
programming error
security violation
```

Do not convert all exceptions into "not found."

Doing so destroys debuggability and can cause false negative matches.

---

# 70. Logging

Logs should capture enough context to diagnose:

- title queried;
- provider attempted;
- state transition;
- match reason;
- path role;
- export gate;
- review reason.

Never include secret values.

---

# 71. Adding GUI controls

When adding a user-facing control:

1. define the core behavior first;
2. map the control into shared state;
3. persist only if appropriate;
4. add tooltip/help;
5. add equivalence/regression tests;
6. update documentation.

Avoid controls that merely expose internal implementation switches without a clear user need.

---

# 72. Documentation rule

User-facing behavior changes should update the appropriate guide.

Current recommended docs set:

```text
README
Quick Start
Full User Guide
Installation Guide
Windows GUI Walkthrough
Metadata & Provider Guide
Troubleshooting Guide
Developer Guide
```

Internal architecture/research docs can remain separate.

---

# 73. Changelog

Update:

```text
CHANGELOG.md
```

for user-visible changes.

Entries should say what changed, not merely list issue numbers.

---

# 74. Security issues

Do not file public security vulnerabilities as ordinary issues.

Follow:

```text
SECURITY.md
```

for private reporting.

---

# 75. Contribution workflow

Typical workflow:

```text
branch from main
implement focused change
add tests
run full tests
update docs/changelog
build if relevant
open PR
review
merge
release separately
```

Avoid combining unrelated fixes into one PR when doing so makes review or rollback difficult.

---

# 76. Clean checkout qualification

Before opening a PR:

```bash
python -m pytest
```

For a release-affecting change also test build/package paths.

A change that only works in a developer's existing dirty environment is not ready.

---

# 77. Build package verification

For Python packaging, use an isolated environment where possible.

Confirm editable installs are not masking missing package data/import declarations.

---

# 78. Windows-specific development

Windows GUI bugs should eventually be tested on real Windows.

Linux/offscreen tests are valuable but cannot prove:

- Explorer integration;
- native dialogs;
- filesystem ACL behavior;
- SmartScreen;
- frozen runtime path behavior;
- Windows-only Qt issues.

---

# 79. Cross-platform core

Core code should remain portable even though the frozen GUI release currently targets Windows.

Use `pathlib.Path`.

Avoid hard-coded separators.

Avoid assumptions such as:

```text
C:\
/home/user
```

unless the code is explicitly platform-specific.

---

# 80. Filesystem safety

Before any write:

- resolve the role path;
- ensure it is not `original_dir`;
- ensure it is not inside `original_dir`;
- sanitize externally derived names;
- use managed staging/output roots.

Symlink/containment checks must use resolved paths.

---

# 81. Atomic writes

Persistent configuration/state should use atomic write patterns where practical.

Typical pattern:

```text
write temporary file
fsync/close
replace target
```

This is especially important for curation state and SQLite-backed stores.

---

# 82. Schema changes

When changing persistent schemas:

- version the schema;
- implement migration;
- test migration from prior versions;
- fail safely on unsupported future versions;
- preserve operator data.

Do not silently reinterpret old fields.

---

# 83. Manual approval records

Manual approval records are integrity-sensitive.

Schema version must be validated when loading.

Invalid or unsupported records should fail safely instead of being silently treated as valid.

---

# 84. Canonical normalization

Title normalization should be centralized.

Avoid provider-specific normalization rules unless they address provider transport peculiarities.

Canonical rules need regression tests for:

```text
Roman numerals
Arabic numerals
punctuation
subtitle separators
articles
release suffixes
sequels
```

---

# 85. Sequel safety

Sequel distinction is critical.

A matcher should not collapse:

```text
Game
Game II
Game III
```

merely because normalized strings are similar.

Any fuzzy matcher that changes sequel behavior needs explicit regression tests.

---

# 86. Hash identity limitations

Exact hashes are strong, but not universal.

Different cracks or modified disks may not appear in provider databases.

Do not treat a hash miss as proof that the title identity is wrong.

---

# 87. Provider rate limits

Provider code should honor documented limits.

Use bounded retries.

Do not implement unbounded retry loops.

Cache successful results.

---

# 88. Provider response size

Network parsers should enforce maximum response sizes.

This protects the application from accidental or malicious oversized responses.

---

# 89. Artwork source safety

Artwork URLs should pass source validation before download.

Do not allow arbitrary provider-returned filesystem or URL targets to flow directly into write paths.

---

# 90. Manual/document safety

Downloaded/manual files are data.

Do not execute embedded content.

Extraction should be bounded and fail closed on corrupt input.

---

# 91. Testing local-media read-only behavior

Tests should verify that source media roots are never mutated.

Expected operations against source libraries are read/stat/hash/copy-from.

Writes belong only in application-owned caches/output.

---

# 92. Performance

Large collections can stress:

- hashing;
- SQLite indexes;
- provider calls;
- image processing;
- GUI table population.

Optimize after profiling.

Do not trade identity correctness for speed without explicit design review.

---

# 93. Determinism

Given the same:

```text
source files
configuration
cached provider records
curation state
operator decisions
```

the pipeline should produce reproducible results.

Non-deterministic provider/network results should be captured into cache/provenance.

---

# 94. Reproducible releases

A release should identify:

```text
tag
application version
source commit
artifact
```

The current release workflow is designed to preserve that provenance.

Do not publish rebuilt binaries under an existing tag without clearly understanding artifact provenance implications.

---

# 95. Debugging frozen builds

If source mode works but frozen Windows fails:

Check:

```text
hidden imports
optional dependency bundled
portable path
resource path
Qt plugins
frozen version module
working directory assumptions
```

Reproduce using the packaged artifact, not only `python -m`.

---

# 96. Adding dependencies

The core intentionally has no required runtime dependencies.

Before adding a required dependency, consider whether it can remain an optional extra.

A dependency change affects:

- CLI portability;
- GUI packaging;
- Windows artifact size;
- licensing;
- test matrix.

---

# 97. Licensing

The project is MIT licensed.

PySide6 was chosen partly because its LGPL licensing fits the project architecture better than GPL-bound alternatives.

Review licenses before adding bundled libraries.

---

# 98. Architecture documents

Existing internal references include:

```text
docs/ARCHITECTURE.md
docs/archive/architecture/ARCHITECTURE-windows-gui-Issue15.md
docs/STATE-OWNERSHIP-MAP.md
docs/architecture-review/
```

Some older documents contain historical/ticket-era wording.

Use current code and tests as authority when an old design note conflicts with implemented behavior.

---

# 99. Developer pre-commit checklist

Before committing:

```text
Tests pass
No originals touched
No secrets added
No host-specific paths
No stale debug files
No unrelated changes
Docs updated
Changelog updated when user-visible
Version unchanged unless this is a release/version change
```

---

# 100. PR checklist

Before opening a pull request:

```text
Problem is clearly described
Change is scoped
Tests demonstrate the fix
Failure case is covered
State ownership is respected
GUI/core boundary is respected
Provider behavior is bounded
Docs are current
No credentials are included
```

---

# 101. Release checklist

Before cutting a release:

```text
main is clean
tests pass
Windows build passes
version sources agree
tag matches project version
packaged GUI smoke test passes
real Windows QA passes
release assets built from intended commit
CHANGELOG updated
user-facing docs reflect behavior
```

---

# 102. Core invariants

The project should continue to protect these invariants:

```text
originals are immutable
ambiguous identity is not guessed
GUI and CLI share core behavior
operator curation persists
provenance is retained
network is opt-in
secrets are isolated/redacted
export writes are explicit
state ownership is defined
releases are version-identifiable
```

Any architectural change that weakens one of these should be treated as a design change, not a routine refactor.

---

**End of Developer Guide — v0.2.26**
