"""GH-192/#189 RC4: Windows-safe review filenames.

Regression tests for the v0.2.40 packaged Windows crash:

    [Errno 22] Invalid argument:
    D:\\AmigaADFLibraryBuilder\\Library-Root\\review\\review_defender||||_ambiguous_midband.json

The internal release key legitimately contains '|' (keys are shaped
``title||||edition|group|...``). It was interpolated verbatim into the
review/ filename, which Windows rejects.

Invariants pinned here:
  * the FILENAME is Windows-safe for every forbidden character;
  * the INTERNAL KEY is unchanged and round-trips through the JSON payload;
  * encoding is injective -- distinct keys never collide on one filename;
  * reserved device names and trailing dots/spaces are neutralized.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amiga_adf_library_builder.enrich import ReviewItem  # noqa: E402
from amiga_adf_library_builder.quarantine import (  # noqa: E402
    encode_filename_component,
    route_quarantine,
)

WIN_FORBIDDEN = set('<>:"/\\|?*')
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1", "CONIN$"}

# The exact key from the production crash report.
CRASH_KEY = "defender||||"


def _review_item(key: str = CRASH_KEY, reason: str = "ambiguous_midband"):
    return ReviewItem(
        source="metadata-online",
        provider="wikipedia",
        candidate_title="Defender",
        score=0.5,
        reason=reason,
        evidence=["regression"],
        release_key=key,
        routed_at="2026-10-04T00:00:00Z",
    )


def assert_windows_safe(name: str) -> None:
    """A name must be creatable on Windows: no forbidden chars, no
    trailing dot/space, not a reserved device name."""
    offenders = sorted(WIN_FORBIDDEN & set(name))
    assert not offenders, f"{name!r} contains Windows-forbidden {offenders}"
    assert name == name.rstrip(" ."), f"{name!r} has a trailing dot/space"
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    assert stem not in WIN_RESERVED, f"{name!r} is a reserved device name"


# --- the production crash ---------------------------------------------------

def test_crash_key_produces_windows_safe_review_filename(tmp_path):
    """THE regression: 'defender||||' must not reach the filesystem raw."""
    out = route_quarantine(
        [], review_dir=tmp_path / "review", unknown_dir=tmp_path / "unknown",
        review_items=[_review_item()],
    )
    assert len(out["review"]) == 1
    name = Path(out["review"][0]).name
    assert_windows_safe(name)
    assert "%7c" in name, f"'|' must be encoded, got {name!r}"
    # The file actually exists where we said it does.
    assert Path(out["review"][0]).is_file()


def test_internal_release_key_semantics_unchanged_and_persisted(tmp_path):
    """Sanitizing is a FILENAME concern only; the JSON keeps the verbatim key."""
    out = route_quarantine(
        [], review_dir=tmp_path / "review", unknown_dir=tmp_path / "unknown",
        review_items=[_review_item()],
    )
    payload = json.loads(Path(out["review"][0]).read_text(encoding="utf-8"))
    assert payload["release_key"] == CRASH_KEY
    # and the same key is what downstream matching logic reads back
    assert payload["release_key"].split("|")[0].lower() == "defender"


# --- every Windows-forbidden character --------------------------------------

@pytest.mark.parametrize("bad", sorted(WIN_FORBIDDEN))
def test_every_windows_forbidden_character_is_encoded(tmp_path, bad):
    key = f"title{bad}edition"
    out = route_quarantine(
        [], review_dir=tmp_path / "review", unknown_dir=tmp_path / "unknown",
        review_items=[_review_item(key=key)],
    )
    name = Path(out["review"][0]).name
    assert_windows_safe(name)
    assert json.loads(
        Path(out["review"][0]).read_text(encoding="utf-8"))["release_key"] == key


def test_control_characters_are_encoded():
    assert_windows_safe("a" + encode_filename_component("a\x00\x1f\x07b") + ".json")


def test_literal_percent_is_encoded_to_keep_mapping_unambiguous():
    assert encode_filename_component("a%b") == "a%25b"
    # '%' and the escaped form must not collide
    assert encode_filename_component("a%b") != encode_filename_component("a%25b")


# --- reserved names and trailing junk ---------------------------------------

@pytest.mark.parametrize("name", sorted(WIN_RESERVED))
def test_reserved_device_names_neutralized(name):
    assert_windows_safe(encode_filename_component(name) + ".json")


def test_trailing_dot_and_space_stripped():
    for raw in ("title.", "title ", "title.  ", "title . "):
        assert_windows_safe(encode_filename_component(raw) + ".json")


def test_empty_key_yields_nonempty_component():
    assert encode_filename_component("") == "unknown"
    assert encode_filename_component("", max_length=0)


# --- injectivity: the real reason to encode instead of drop ------------------

def test_distinct_keys_never_collide_on_one_filename():
    """Encoding (not dropping) is what guarantees this."""
    keys = [
        "defender||||",
        "defender_a|b",
        "defender|a|b",
        "defender%7c%7c%7c%7c",
        "defender||",
    ]
    encoded = [encode_filename_component(k) for k in keys]
    assert len(set(encoded)) == len(encoded), (
        f"collision: {dict(zip(keys, encoded))}")


def test_long_keys_sharing_a_prefix_stay_distinct():
    prefix = "x" * 200
    a = encode_filename_component(prefix + "a")
    b = encode_filename_component(prefix + "b")
    assert a != b
    assert len(a) <= 120 + 1 + 12
    assert_windows_safe(a + ".json")


def test_readable_keys_are_not_mangled():
    """Regression guard: pre-existing safe names must stay readable."""
    assert encode_filename_component("Bard's Tale III") == "Bard's Tale III"
    assert encode_filename_component("Defender (1991)") == "Defender (1991)"