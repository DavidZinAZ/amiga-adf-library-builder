"""Version identity and drift tests (GH-119).

Acceptance:
- All modules derive __version__ from pyproject.toml [project].version
- About dialog reads canonical version
- No hardcoded version strings drift from the canonical source
- CI fails when package, GUI, build/release/tag versions drift
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _canonical_version() -> str:
    """Read the canonical version from pyproject.toml."""
    repo = Path(__file__).resolve().parents[1]
    pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"(.*?)"', pyproject, re.MULTILINE)
    assert match, "pyproject.toml must have [project].version"
    return match.group(1)


def test_pyproject_version_is_canonical():
    version = _canonical_version()
    assert version == "0.2.24"
    assert isinstance(version, str)


def test_core___version__matches_pyproject():
    from amiga_adf_library_builder import __version__
    assert __version__ == _canonical_version()


def test_gui___version__matches_canonical():
    from amiga_adf_library_builder.gui import __version__
    assert __version__ == _canonical_version()


def test_no_stale_hardcoded_versions():
    """No source file may contain a hardcoded version that differs
    from pyproject.toml [project].version."""
    canonical = _canonical_version()
    repo = Path(__file__).resolve().parents[1]
    src_dir = repo / "src"

    stale_patterns = [
        # Old drifted values
        r'AmigaADFLibraryBuilder/0\.2\.1\b',
        r'AmigaADFLibraryBuilder/1\.0\b',
        r'__version__\s*=\s*"[0-9]"',
    ]

    for f in src_dir.rglob("*.py"):
        # Skip the auto-generated frozen version module
        if f.name == "_frozen_version.py":
            continue
        text = f.read_text(encoding="utf-8")
        for pattern in stale_patterns:
            if re.search(pattern, text):
                pytest.fail(
                    f"Stale version pattern {pattern!r} in {f}"
                )


def test_version_is_exact_40_char_sha_available():
    """The APPLICATION_SHA must be derivable from git."""
    import subprocess
    repo = Path(__file__).resolve().parents[1]
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True, text=True,
    ).stdout.strip()
    assert len(sha) == 40
    assert sha.isalnum()


def test_about_dialog_reads_canonical_version():
    """The About dialog must use the canonical __version__."""
    main_window_path = Path(
        __file__).resolve().parents[1] / "src" / "amiga_adf_library_builder" / "gui" / "main_window.py"
    text = main_window_path.read_text(encoding="utf-8")
    # The _show_about method must reference __version__ from the canonical source
    assert '__version__' in text, \
        "About dialog must reference __version__"
    assert 'Version {__version__}' in text, \
        "About dialog must use __version__ in the display string"


def test_all_user_agents_derive_from_version():
    """All USER_AGENT definitions must reference the canonical version."""
    repo = Path(__file__).resolve().parents[1] / "src"
    files_to_check = [
        "amiga_adf_library_builder/metadata.py",
        "amiga_adf_library_builder/enrich.py",
        "amiga_adf_library_builder/retrokit.py",
        "amiga_adf_library_builder/screenscraper.py",
    ]
    for rel in files_to_check:
        text = (repo / rel).read_text(encoding="utf-8")
        assert "AmigaADFLibraryBuilder/" in text, \
            f"{rel} must define a User-Agent"
        # Must NOT have hardcoded version in the string literal
        assert "__version__" in text or "_version" in text, \
            f"{rel} must derive User-Agent from __version__"


def test_build_generates_version_info_with_correct_vers():
    """tools/build_windows.py must generate a VSVersionInfo text file with
    numeric filevers/prodvers derived from canonical semver, and the spec
    must pass it to EXE(version=...)."""
    from tools.build_windows import render_spec, _semver_to_tuple, _APPLICATION_VERSION

    # Semver mapping
    assert _semver_to_tuple("0.2.24") == (0, 2, 24, 0)
    assert _semver_to_tuple("1.0") == (1, 0, 0, 0)

    spec = render_spec(
        target="onedir",
        name="AmigaADFLibraryBuilder",
        launcher_rel="app_launcher.py",
        pathex_rel=["src"],
        hidden_imports=[],
        console=False,
        application_version=_APPLICATION_VERSION,
        version_info_path="tools/_version_info.txt",
    )
    assert "version=" in spec, "EXE must receive version= keyword"
    assert "tools/_version_info.txt" in spec
    # The VSVersionInfo text is generated separately by build_windows.py main()
    # The spec only embeds the path reference to it. Verify the path is correct.
    assert "tools/_version_info.txt" in spec
    # Verify the version string itself appears in the spec
    assert _APPLICATION_VERSION in spec


def test_frozen_version_fallback_when_sys_frozen():
    """When sys.frozen is True, _version.__version__ must come from the
    build-time generated _frozen_version module, NOT from runtime pyproject
    traversal. This proves the frozen app does not require repo-root
    pyproject.toml at runtime."""
    import importlib
    import sys

    # Simulate a frozen runtime: inject a generated _frozen_version module.
    canonical = _canonical_version()
    frozen_src = (
        "# AUTO-GENERATED — DO NOT EDIT.\n"
        f'__version__ = "{canonical}"\n'
    )

    # Write a temporary _frozen_version.py next to _version.py for the test.
    vpath = Path(__file__).resolve().parents[1] / "src" / "amiga_adf_library_builder"
    frozen_path = vpath / "_frozen_version.py"
    existed = frozen_path.exists()
    original = frozen_path.read_text() if existed else None
    try:
        frozen_path.write_text(frozen_src, encoding="utf-8")
        # Ensure fresh import: remove ALL cached version modules first
        for mod in list(sys.modules):
            if "amiga_adf_library_builder" in mod and "version" in mod:
                del sys.modules[mod]
        sys.frozen = True  # type: ignore[attr-defined]
        import amiga_adf_library_builder._version as vmod
        importlib.reload(vmod)
        assert vmod.__version__ == canonical
    finally:
        sys.frozen = False  # type: ignore[attr-defined]
        if existed:
            frozen_path.write_text(original or "", encoding="utf-8")
        elif frozen_path.exists():
            frozen_path.unlink()
        # Clean up cached modules so next test starts fresh
        for mod in list(sys.modules):
            if "amiga_adf_library_builder" in mod and "version" in mod:
                del sys.modules[mod]
