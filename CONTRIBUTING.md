# Contributing to Amiga ADF Library Builder

Thanks for your interest in improving Amiga ADF Library Builder. This document
explains how to file issues, open pull requests, and set up a development
environment. It is written for a preservation tool, so the bar for safety and
reproducibility is deliberately high.

## Code of conduct

All participation is governed by our
[Code of Conduct](./CODE_OF_CONDUCT.md). By contributing you agree to uphold it.

## Reporting security issues

**Do not open a public issue for security vulnerabilities.** See
[SECURITY.md](./SECURITY.md) for the private reporting channel and disclosure
policy.

## Development setup

The project targets **Python 3.11+** and has **zero required runtime
dependencies** — the core library uses only the standard library. Artwork
enrichment (Pillow) and the test suite are optional extras.

From a clone of this repository:

```bash
# 1. Create and activate an isolated virtual environment.
python3 -m venv .venv
. .venv/bin/activate

# 2. Install the project with dev + artwork extras (editable).
python -m pip install -U pip
python -m pip install -e '.[artwork,dev]'
```

`artwork` pulls in Pillow (used only for artwork resize/processing). `dev`
pulls in pytest. The build backend is setuptools (declared in `pyproject.toml`).

## Running the tests

```bash
# From the repository root, with the venv active:
python -m pytest
```

The suite is fully offline: no test makes a real network call. Network-dependent
code paths are exercised with injected fake openers, and the SSRF guard is tested
with IP literals and stubbed DNS. The fixture in `tests/conftest.py` isolates all
path configuration so tests never read your real `~/.config` or `~/.cache`.

## Style and quality expectations

- Small, explicit functions with clear contracts.
- Deterministic behavior; no reliance on wall-clock time or external state in
  testable code.
- Type hints on public functions.
- Every behavior change needs a test (normal path, boundary, and failure path).
- No silent fallbacks or swallowed exceptions in the core library.
- Run `python -m pytest` green from a clean checkout before opening a PR.

## Submitting changes

1. Fork the repository and create a topic branch from `main` with a descriptive
   name (e.g. `fix/issue-12-metadata-timeout`, `feature/issue-13-csv-export`).
2. Keep changes scoped. Separate unrelated concerns into separate PRs.
3. Add or update tests for your change. Update `CHANGELOG.md` if the change is
   user-visible.
4. Ensure `python -m pytest` passes and the build succeeds
   (`python -m build` from a clean checkout).
5. Open a pull request describing the problem, the approach, and how it was
   verified.

## Release notes and changelog

User-visible changes should be summarized under the appropriate heading in
`CHANGELOG.md`, following the existing format. Keep entries factual: what
changed and why.

## License

By contributing, you agree that your contributions are licensed under the
project's MIT license (see [LICENSE](./LICENSE)).
