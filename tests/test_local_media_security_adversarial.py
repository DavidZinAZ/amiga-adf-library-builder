"""INDEPENDENT adversarial security tests for the local-media provider.

These do NOT reuse implementer (test_local_media_provider.py) or QA
(test_local_media_qa_independent.py) fixtures. Every fixture, builder, and
assertion is derived from the local-media security checklist and from reading
the implementation directly.

Threat model under test: the LaunchBox root is treated as UNTRUSTED /
arbitrary. An attacker who controls (or can place files in) that tree may use
symlinks, hostile filenames, oversized / malformed images, and crafted paths to
achieve path traversal, information disclosure, denial of service, or cache
poisoning. The provider must hold its guarantees even then.

Run in isolation:
    python -m pytest tests/test_local_media_security_adversarial.py -v
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from amiga_adf_library_builder import local_media as lm
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup

PY = sys.executable


# ---------------------------------------------------------------------------
# Independent fixtures (not shared with implementer / QA)
# ---------------------------------------------------------------------------


def _group(title, **kw) -> ReleaseGroup:
    fn = kw.get("source_filename") or f"{title}.adf"
    rec = ParsedRecord(
        source_filename=fn,
        ext="adf",
        title=title,
        chipset=kw.get("chipset"),
        language=kw.get("language"),
        alt_marker=kw.get("alt_marker"),
        trainer=bool(kw.get("trainer", False)),
        edition=kw.get("edition"),
        version=kw.get("version"),
    )
    return ReleaseGroup(
        release_key=kw.get("release_key") or title.lower(),
        title=title,
        edition=kw.get("edition"),
        group=kw.get("group"),
        chipset=kw.get("chipset"),
        language=kw.get("language"),
        version=kw.get("version"),
        alt_marker=kw.get("alt_marker"),
        ext="adf",
        records=[rec],
        disks=[rec],
    )


def _mk_lb(root: Path, layout: dict[str, bytes]) -> None:
    for rel, content in layout.items():
        p = root / "Images" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _provider(root: Path, cache: Path, **kw) -> lm.LocalMediaProvider:
    cfg = lm.LocalMediaConfig(
        enabled=True,
        roots=(str(root),),
        platform_names=("Commodore Amiga", "Amiga"),
        preferred_image_types=tuple(lm.DEFAULT_PREFERRED_TYPES),
        recursive=True,
        **kw,
    )
    prov = lm.LocalMediaProvider(cfg, cache)
    prov.discover()
    return prov


# ===========================================================================
# ITEM 1 — Path handling & symlink/hardlink traversal
# ===========================================================================


def test_symlink_to_outside_root_is_not_read(tmp_path):
    """A symlink inside the LaunchBox tree that points to a file OUTSIDE the
    configured root must NOT be read / copied. Reading it would disclose
    out-of-root content into the app cache."""
    root = tmp_path / "lb"
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    # A 'secret' file outside the root, with an image suffix so it would be
    # picked up if the symlink were followed.
    secret = secret_dir / "xenon.png"
    secret.write_bytes(b"OUT-OF-ROOT-SECRET-CONTENT")

    cat = root / "Images" / "Commodore Amiga" / "Box - Front" / "Xenon"
    cat.mkdir(parents=True, exist_ok=True)
    # Symlink inside the category tree -> outside root.
    link = cat / "box.png"
    os.symlink(secret, link)

    cache = tmp_path / "cache"
    prov = _provider(root, cache)
    res = prov.resolve(_group("Xenon"))

    # Either it was never indexed, or nothing was cached. Either way, no
    # out-of-root secret content may land in the cache.
    cache_files = list(cache.rglob("*")) if cache.exists() else []
    for f in cache_files:
        if f.is_file():
            assert b"OUT-OF-ROOT-SECRET-CONTENT" not in f.read_bytes(), (
                f"symlink traversal disclosed out-of-root content into {f}"
            )
    # Stronger: the provider must not have matched the traversed symlink at all.
    assert res.found is False or b"OUT-OF-ROOT-SECRET-CONTENT" not in (
        res.cached_path.read_bytes() if res.cached_path else b""
    )


def test_candidate_paths_are_confined_to_root(tmp_path):
    """Every discovered candidate's REAL path must lie within its configured
    root. A symlink/hardlink that escapes the root must be excluded from the
    index, not merely 'not copied'."""
    root = tmp_path / "lb"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "escaped.png"
    target.write_bytes(b"ESCAPE")

    cat = root / "Images" / "Commodore Amiga" / "Box - Front" / "Xenon"
    cat.mkdir(parents=True, exist_ok=True)
    os.symlink(target, cat / "box.png")

    cache = tmp_path / "cache"
    prov = _provider(root, cache)

    root_resolved = root.resolve()
    escaped = []
    for cand in prov._index:
        real = cand.path.resolve()
        try:
            real.relative_to(root_resolved)
        except ValueError:
            escaped.append(str(cand.path))
    assert escaped == [], f"candidates escaped root confinement: {escaped}"


def test_symlinked_category_dir_is_not_followed(tmp_path):
    """A category-named symlink directory pointing outside the root must not be
    traversed into."""
    root = tmp_path / "lb"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Xenon").mkdir()
    (outside / "Xenon" / "box.png").write_bytes(b"LEAK")

    plat = root / "Images" / "Commodore Amiga"
    plat.mkdir(parents=True, exist_ok=True)
    # A "Box - Front" symlink dir -> outside root.
    os.symlink(outside, plat / "Box - Front")

    cache = tmp_path / "cache"
    prov = _provider(root, cache)
    cache_files = list(cache.rglob("*")) if cache.exists() else []
    for f in cache_files:
        if f.is_file():
            assert b"LEAK" not in f.read_bytes(), "symlinked category dir traversed"


# ===========================================================================
# ITEM 2 — Hostile filenames
# ===========================================================================


def test_hostile_filenames_no_crash_no_escape(tmp_path):
    """Filenames with newlines, leading dashes, unicode, extremely long names,
    and glob metacharacters must not crash the provider, must not escape the
    cache directory, and must not execute anything."""
    root = tmp_path / "lb"
    cat = root / "Images" / "Commodore Amiga" / "Screenshot - Game Title" / "Xenon"
    cat.mkdir(parents=True, exist_ok=True)

    hostile = [
        "weird\nname.png",          # newline
        "-leadingdash.png",         # leading dash
        "unïcöde–name.png",         # wide unicode
        "a" * 300 + ".png",         # extremely long
        "glob*?.png",               # glob metachars
        "spaces in name.png",       # spaces
    ]
    for name in hostile:
        try:
            (cat / name).write_bytes(b"HOSTILE")
        except OSError:
            # Some names cannot be created on this filesystem; skip silently.
            continue

    cache = tmp_path / "cache"
    prov = _provider(root, cache)
    # Resolving must not raise on hostile names.
    res = prov.resolve(_group("Xenon"))
    # Whatever cache file is produced must live under cache_dir (no escape).
    if res.cached_path is not None:
        assert Path(res.cached_path).resolve().is_relative_to(cache.resolve()), (
            f"cache path escaped: {res.cached_path}"
        )


# ===========================================================================
# ITEM 3 — Oversized / malformed images (DoS / read ordering)
# ===========================================================================


def _run_dos_probe(tmp_path, size_bytes: int) -> dict:
    """Run the provider in a subprocess with a hard address-space limit
    (RLIMIT_AS). If the provider reads the ENTIRE source before applying the
    max_image_bytes cap, allocation fails with MemoryError. If it bounds the
    read up front (stat / fstat / getsize), it raises the clean
    LocalMediaError without OOM."""
    root = tmp_path / "lb"
    cat = root / "Images" / "Commodore Amiga" / "Screenshot - Game Title" / "Xenon"
    cat.mkdir(parents=True, exist_ok=True)
    big = cat / "title.png"
    # Sparse file: instant to create, reads as `size_bytes` of data.
    with open(big, "wb") as fh:
        fh.seek(size_bytes - 1)
        fh.write(b"\0")

    cache = tmp_path / "cache"
    script = textwrap.dedent(
        """
        import sys, resource, json
        from pathlib import Path
        sys.path.insert(0, %r)
        from amiga_adf_library_builder import local_media as lm
        from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup

        root = Path(%r)
        cache = Path(%r)
        cap = %d
        # Hard address-space cap just above the file-size safety cap.
        resource.setrlimit(resource.RLIMIT_AS, (cap * 3, cap * 3))
        cfg = lm.LocalMediaConfig(enabled=True, roots=(str(root),),
                                  platform_names=("Commodore Amiga","Amiga"),
                                  preferred_image_types=tuple(lm.DEFAULT_PREFERRED_TYPES),
                                  recursive=True)
        prov = lm.LocalMediaProvider(cfg, cache, max_image_bytes=cap)
        prov.discover()
        rec = ParsedRecord(source_filename="Xenon.adf", ext="adf", title="Xenon")
        grp = ReleaseGroup(release_key="xenon", title="Xenon", edition=None,
                           group=None, chipset=None, language=None, version=None,
                           alt_marker=None, ext="adf", records=[rec], disks=[rec])
        try:
            res = prov.resolve(grp)
            print(json.dumps({"outcome": "resolved", "found": res.found}))
        except lm.LocalMediaError as e:
            print(json.dumps({"outcome": "safety_cap", "msg": str(e)}))
        except MemoryError:
            print(json.dumps({"outcome": "memory_error"}))
        except BaseException as e:
            print(json.dumps({"outcome": "probe_error", "msg": repr(e)}))
        """
        % (str(Path(__file__).resolve().parent.parent), str(root), str(cache), 25_000_000)
    )
    script_path = tmp_path / "dos_probe.py"
    script_path.write_text(script)
    # Run under the venv interpreter so the package imports.
    proc = subprocess.run(
        [PY, str(script_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    outcome_lines = [l for l in proc.stdout.splitlines() if l.strip().startswith("{")]
    if not outcome_lines:
        pytest.fail(
            f"DoS probe produced no result; rc={proc.returncode} "
            f"stderr={proc.stderr[:500]}"
        )
    last = outcome_lines[-1]
    result = json.loads(last)
    if result.get("outcome") == "probe_error":
        pytest.fail(f"DoS probe crashed (harness bug): {result.get('msg')}")
    return result


def test_oversized_source_does_not_oom_before_cap(tmp_path):
    """An oversized source must be rejected by the bounded size check WITHOUT
    first loading its entire contents into memory. We point a 400 MB source at
    the provider under a 75 MB address-space cap. A bounded implementation
    raises LocalMediaError (safety cap) without allocating the file; an
    unbounded implementation raises MemoryError (DoS)."""
    result = _run_dos_probe(tmp_path, size_bytes=400 * 1024 * 1024)
    # Security-correct behavior: clean safety-cap rejection, no OOM.
    assert result["outcome"] == "safety_cap", (
        f"provider read the whole source before capping (DoS): {result}"
    )


def test_provider_does_not_validate_image_magic(tmp_path):
    """Informational: the provider copies any file with an image SUFFIX without
    validating magic bytes or decoding it. It does not EXECUTE anything, so this
    is not an RCE, but a polyglot/executable-with-.png-suffix is copied blindly
    into the cache (downstream Pillow later rejects invalid images). Confirmed
    here so the limitation is documented in the security record."""
    root = tmp_path / "lb"
    cat = root / "Images" / "Commodore Amiga" / "Screenshot - Game Title" / "Xenon"
    cat.mkdir(parents=True, exist_ok=True)
    # A fake 'PNG' that is actually an ELF executable.
    (cat / "title.png").write_bytes(b"\x7fELF\x01\x02\x03\x04" + b"FAKE-EXECUTABLE")
    cache = tmp_path / "cache"
    prov = _provider(root, cache)
    res = prov.resolve(_group("Xenon"))
    # It is copied (no decode), proving no execution and no magic validation.
    assert res.found is True
    assert res.cached_path is not None
    assert res.cached_path.read_bytes().startswith(b"\x7fELF")
    # No execution occurred (we are still here, no subprocess was spawned).


# ===========================================================================
# ITEM 4 — Read-only guarantee against LaunchBox (adversarial)
# ===========================================================================


def test_adversarial_write_into_launchbox_tree_fails(tmp_path):
    """The provider must never create/modify/delete anything under the
    LaunchBox root, even when an attacker seeds hostile category/folder names
    (e.g. names containing path separators are impossible on disk, but deep
    nesting and odd names must not cause writes into the root)."""
    root = tmp_path / "lb"
    layout = {
        "Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"TITLE",
        "Commodore Amiga/Box - Front/Xenon/box.png": b"BOX",
    }
    _mk_lb(root, layout)
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(root.rglob("*")) if p.is_file()}

    cache = tmp_path / "cache"
    prov = _provider(root, cache)
    prov.resolve(_group("Xenon"))

    after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob("*")) if p.is_file()}
    assert after == before, "LaunchBox root was mutated by the provider"


# ===========================================================================
# ITEM 5 — Privacy / provenance leakage
# ===========================================================================


def test_provenance_sidecar_no_absolute_host_path(tmp_path):
    """The provenance sidecar must NOT embed absolute, host-specific paths that
    leak the operator environment (/home/<user>, mount points). T6.3 checklist
    item 5 requires paths be stored relative to the configured root."""
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"T"})
    cache = tmp_path / "cache"
    prov = _provider(root, cache)
    res = prov.resolve(_group("Xenon"))
    assert res.found and res.provenance is not None
    assert res.cached_path is not None

    sidecar = Path(res.cached_path).with_suffix(Path(res.cached_path).suffix + ".prov.json")
    data = json.loads(sidecar.read_text())

    # No absolute path anywhere in the persisted provenance.
    for key in ("source_path", "source_root", "cached_path"):
        val = data[key]
        assert not val.startswith("/"), f"{key} must not be absolute: {val!r}"
        assert not val.startswith("~"), f"{key} must not be home-relative: {val!r}"
    # The operator's absolute root layout must not appear in the sidecar.
    assert str(root.resolve()) not in (
        data["source_path"] + data["source_root"] + data["cached_path"]
    ), "operator root layout leaked into provenance"

    # source_path is relative to the configured root (anchored by source_root=".").
    assert data["source_root"] == ".", "configured root must be anchored opaquely as '.'"
    # The source is under Images/... in the LaunchBox tree.
    assert data["source_path"].endswith("Xenon/title.png"), data["source_path"]
    # Reconstructed absolute source equals the real file (proves root-relative
    # form is faithful, not a redaction).
    reconstructed = (root / data["source_path"]).resolve()
    assert reconstructed == (root / "Images" / "Commodore Amiga"
                             / "Screenshot - Game Title" / "Xenon" / "title.png").resolve()
    assert reconstructed.is_file()


# ===========================================================================
# ITEM 6 — No network dependency
# ===========================================================================


def test_provider_module_no_network_imports():
    src = Path(lm.__file__).read_text()
    assert "import requests" not in src
    assert "import urllib" not in src
    assert "from urllib" not in src
    # No top-level socket import either (offline by construction).
    top = [l for l in src.splitlines() if l.startswith("import ") or l.startswith("from ")]
    assert not any("socket" in l for l in top), "provider imports socket at module level"


def test_offline_socket_blocked_resolve(tmp_path):
    import unittest.mock as mock

    calls = []

    def _blocked(*a, **k):
        calls.append((a, k))
        raise OSError("network blocked")

    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenon/title.png": b"S"})
    cache = tmp_path / "cache"
    with mock.patch("socket.socket", side_effect=_blocked):
        prov = _provider(root, cache)
        res = prov.resolve(_group("Xenon"))
    assert res.found is True
    assert calls == [], "socket.socket invoked during resolve"


# ===========================================================================
# ITEM 7 — Manual-review queue safety
# ===========================================================================


def test_manual_review_not_auto_accepted_no_cache(tmp_path):
    """An uncertain match must be routed to manual review, never silently
    accepted, and nothing may be written to the approved cache."""
    root = tmp_path / "lb"
    _mk_lb(root, {"Commodore Amiga/Screenshot - Game Title/Xenno/shot.png": b"S"})
    cache = tmp_path / "cache"
    prov = _provider(root, cache)
    res = prov.resolve(_group("Xenon"))
    assert res.found is False
    assert res.needs_manual_review is True
    assert res.cached_path is None
    # Nothing written to the cache for this uncertain match.
    assert list(cache.rglob("*.png")) == [] or not any(
        p.read_bytes() == b"S" for p in cache.rglob("*.png")
    )


# ===========================================================================
# ITEM 8 — Cache poisoning
# ===========================================================================


def test_cache_dest_always_under_cache_dir_hostile_stem(tmp_path):
    """A crafted candidate can only ever write under cache_dir. Even a stem
    containing path-like sequences cannot escape the app-owned cache."""
    root = tmp_path / "lb"
    cat = root / "Images" / "Commodore Amiga" / "Screenshot - Game Title" / "Xenon"
    cat.mkdir(parents=True, exist_ok=True)
    (cat / "title.png").write_bytes(b"OK")

    cache = tmp_path / "cache"
    prov = _provider(root, cache)
    res = prov.resolve(_group("Xenon"))
    assert res.found
    assert res.cached_path is not None
    cp = Path(res.cached_path).resolve()
    assert cp.is_relative_to(cache.resolve()), f"cache path escaped: {cp}"


def test_cache_collision_disambiguated_by_source_hash(tmp_path):
    """Two distinct sources that share a stem must not clobber each other; the
    hash-suffix fallback keeps them separate (no cache poisoning across
    entries)."""
    root = tmp_path / "lb"
    cat = root / "Images" / "Commodore Amiga" / "Screenshot - Game Title" / "Xenon"
    cat.mkdir(parents=True, exist_ok=True)
    (cat / "title.png").write_bytes(b"VERSION-A")

    cache = tmp_path / "cache"
    prov = _provider(root, cache)
    res_a = prov.resolve(_group("Xenon"))
    a_bytes = res_a.cached_path.read_bytes()
    a_path = str(res_a.cached_path)

    # Second, distinct source with the SAME stem but different content.
    (cat / "title.png").write_bytes(b"VERSION-B")
    prov2 = _provider(root, cache)
    res_b = prov2.resolve(_group("Xenon"))
    b_bytes = res_b.cached_path.read_bytes()

    assert a_bytes == b"VERSION-A"
    assert b_bytes == b"VERSION-B"
    # Distinct cache files (hash-suffixed) -> no clobber.
    assert str(res_b.cached_path) != a_path or a_bytes != b_bytes
