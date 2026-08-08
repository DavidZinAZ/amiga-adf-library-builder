"""Public qualification tests for the manual-approval mechanism.

All fixtures are synthetic and created in temporary directories. No maintainer
corpus, local approval record, private path, or machine-specific data is used.
"""
import hashlib
import json
import tempfile
from pathlib import Path


from amiga_adf_library_builder import artwork as artwork_mod
from amiga_adf_library_builder.grouper import group_records
from amiga_adf_library_builder.manual_approvals import (
    Approval,
    apply_approvals,
    ApprovalRecord,
    write_approval_record,
    revoke_approval,
    load_approval_records,
    validate_source_url,
    HOST_ALLOWLIST,
    COMMITTED_DIR,
    LOCAL_DIR,
)
from amiga_adf_library_builder.naming import _sanitize, release_basename
from amiga_adf_library_builder.parser import parse_filename
from amiga_adf_library_builder.paths import PathConfig, resolve_config
from amiga_adf_library_builder.pipeline import run_pipeline

# Two EXAMPLE base keys committed in config/manual-approvals.toml (manual-approval feature).
EXAMPLE_KEYS = ("examplequestiii", "exampleqest3")
EXAMPLE_FOLDER_A = "Example Quest III"
EXAMPLE_FOLDER_B = "Example Quest III (Variant)"
EXAMPLE_CANON_TITLE = "Example Quest III"
EXAMPLE_NFO_TITLE_LINE = f"Title: {EXAMPLE_CANON_TITLE}"
# Operator-provided authoritative source for Example Quest III (Lemon Amiga).
EXAMPLE_SOURCE_URL = "https://www.lemonamiga.com/games/details.php?id=example"


def _cfg(root: Path) -> PathConfig:
    return resolve_config(library_root=str(root))[0]


# --- U1: apply_approvals (synthetic, via group_records) -----------------------


def test_u1_apply_approvals_dequarantines_retitles_and_reports_unmatched():
    # Build the real special-only groups exactly the way the pipeline does.
    groups = group_records([
        parse_filename("Example_Quest_III_Character.adf"),
        parse_filename("Example_Qest3_Char.adf"),
    ])
    # Two separate release keys, both quarantined for the special-only condition.
    assert len(groups) == 2
    g = next(grp for grp in groups if grp.release_key.startswith("examplequestiii"))
    assert g.quarantine_reason is not None, "precondition: group starts quarantined"

    # Only the examplequestiii group is approved; 'zzz' is a bogus key.
    approvals = {
        "examplequestiii": Approval(
            release_key="examplequestiii",
            title=EXAMPLE_CANON_TITLE,
            folder=EXAMPLE_FOLDER_A,
        ),
        "zzz": Approval(release_key="zzz", title="Bogus", folder="Bogus"),
    }
    out_groups, applied, unmatched = apply_approvals([g], approvals)

    # Quarantine cleared and retitled for the matched group.
    assert g.quarantine_reason is None
    assert g.title == EXAMPLE_CANON_TITLE
    assert g.folder == EXAMPLE_FOLDER_A
    assert applied == [g.release_key]

    # The bogus key matched no group -> reported in unmatched_approval_keys.
    assert "zzz" in unmatched
    # The real (matched) key must NOT appear as unmatched.
    assert "examplequestiii" not in unmatched


# --- U2: release_basename honors / ignores folder -----------------------------


def test_u2_release_basename_uses_folder_when_set():
    groups = group_records([parse_filename("Example_Quest_III_Character.adf")])
    g = next(grp for grp in groups if grp.release_key.startswith("examplequestiii"))
    g.folder = EXAMPLE_FOLDER_A
    # Operator-approved folder override is used verbatim (FAT32-sanitized).
    assert release_basename(g) == _sanitize(g.folder)
    assert release_basename(g) == EXAMPLE_FOLDER_A


def test_u2_release_basename_falls_back_when_folder_none():
    groups = group_records([parse_filename("Example_Quest_III_Character.adf")])
    g = next(grp for grp in groups if grp.release_key.startswith("examplequestiii"))
    g.folder = None
    # Regression vs existing naming behavior: derived from identity when no folder.
    derived = release_basename(g)
    assert derived  # never empty
    assert g.folder is None
    assert derived == _sanitize(g.title)  # title-based default


# --- Synthetic feature and negative tests -------------------------------------

# --- manual-approval feature negative + feature tests (ratified design) ---------------------
# These are qualification tests for the CLI / hash-binding /
# revocation / merge / URL-validation behavior. They use tempfile + a copied
# "original" so the synthetic fixture set is never touched. Each test is independent.


def _make_pseudo_original(td: Path) -> Path:
    """Create a tiny pseudo original/ with two special-only group files."""
    orig = td / "original"
    orig.mkdir(parents=True, exist_ok=True)
    (orig / "Foo_Boot.adf").write_bytes(b"BOOTDISK-CONTENTS-1234")
    (orig / "Foo_Char.adf").write_bytes(b"CHARDISK-CONTENTS-5678")
    (orig / "Bar_Char.adf").write_bytes(b"BAR-CHAR-9999")
    return orig


def _sha_str(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json_approval(data_root: Path, record: dict) -> Path:
    d = data_root / "config" / COMMITTED_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{record['approval_id']}.json"
    p.write_text(json.dumps(record, indent=2) + "\n")
    return p


def _groups_for(data_root: Path):
    from amiga_adf_library_builder import scanner as _scanner
    from amiga_adf_library_builder.parser import parse_filename

    scans = _scanner.scan_intake(data_root / "original")
    return group_records([parse_filename(s.filename) for s in scans])


# T2: URL validation allow/deny (ratified section 2.1).
def test_validate_source_url_allows_approved_hosts():
    for host in HOST_ALLOWLIST:
        ok, reason = validate_source_url(f"https://{host}/x")
        assert ok, f"{host} should be allowed: {reason}"
        ok2, _ = validate_source_url(f"https://www.{host}/x")
        assert ok2, f"www.{host} should be allowed"


def test_validate_source_url_rejects_scheme_host_userinfo_ip():
    ok, reason = validate_source_url("ftp://example.com/x")
    assert not ok and "scheme" in reason.lower()
    ok, reason = validate_source_url("javascript:alert(1)")
    assert not ok and "scheme" in reason.lower()
    ok, reason = validate_source_url("https://user@example.com/x")
    assert not ok and "userinfo" in reason.lower()
    # 203.0.113.5 is RFC 5737 TEST-NET-1: a real IP literal that is reserved
    # for documentation (not a LAN/private range, not a real host). It must
    # be rejected as a bare IP host, not fall through to the generic host-deny.
    ok, reason = validate_source_url("https://203.0.113.5/x")
    assert not ok and "ip host" in reason.lower()
    ok, reason = validate_source_url("https://evil-lemonamiga.com/x")
    assert not ok and "host" in reason.lower()
    ok, reason = validate_source_url("https://lemonamiga.com.evil.example/x")
    assert not ok and "host" in reason.lower()
    ok, reason = validate_source_url("not a url")
    assert not ok and reason


def test_apply_approvals_refuses_hash_mismatch():
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        orig = _make_pseudo_original(data_root)
        rec = {
            "schema_version": 1, "approval_id": "apr_foo", "release_keys": ["foo"],
            "canonical_title": "Foo Quest", "approved_folder": "Foo Quest",
            "approved_source_filenames": ["Foo_Boot.adf", "Foo_Char.adf"],
            "expected_sha256": {
                "Foo_Boot.adf": _sha_str(b"BOOTDISK-CONTENTS-1234"),
                "Foo_Char.adf": "0" * 64,
            },
            "status": "active", "source_urls": [], "events": [],
        }
        _write_json_approval(data_root, rec)
        groups = _groups_for(data_root)
        result = apply_approvals(groups, load_approval_records(data_root).by_key, original_dir=orig)
        assert result[1] == []
        assert any(f["reason"] == "mismatch" for f in result.hash_failures)
        g = next(grp for grp in groups if grp.release_key.startswith("foo"))
        assert g.quarantine_reason is not None


def test_apply_approvals_refuses_missing_file():
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        orig = _make_pseudo_original(data_root)
        rec = {
            "schema_version": 1, "approval_id": "apr_foo", "release_keys": ["foo"],
            "canonical_title": "Foo Quest", "approved_folder": "Foo Quest",
            "approved_source_filenames": ["Foo_Boot.adf", "Foo_Missing.adf"],
            "expected_sha256": {
                "Foo_Boot.adf": _sha_str(b"BOOTDISK-CONTENTS-1234"),
                "Foo_Missing.adf": "0" * 64,
            },
            "status": "active", "source_urls": [], "events": [],
        }
        _write_json_approval(data_root, rec)
        groups = _groups_for(data_root)
        result = apply_approvals(groups, load_approval_records(data_root).by_key, original_dir=orig)
        assert result[1] == []
        assert any(f["reason"] == "missing" for f in result.hash_failures)


def test_apply_approvals_refuses_extra_file():
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        orig = _make_pseudo_original(data_root)
        rec = {
            "schema_version": 1, "approval_id": "apr_foo", "release_keys": ["foo"],
            "canonical_title": "Foo Quest", "approved_folder": "Foo Quest",
            "approved_source_filenames": ["Foo_Boot.adf"],
            "expected_sha256": {"Foo_Boot.adf": _sha_str(b"BOOTDISK-CONTENTS-1234")},
            "status": "active", "source_urls": [], "events": [],
        }
        _write_json_approval(data_root, rec)
        groups = _groups_for(data_root)
        result = apply_approvals(groups, load_approval_records(data_root).by_key, original_dir=orig)
        assert result[1] == []
        assert any(f["reason"] == "extra" for f in result.hash_failures)


def test_apply_approvals_hashless_record_applies_without_original():
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        _make_pseudo_original(data_root)
        groups = _groups_for(data_root)
        approvals = {"foo": ApprovalRecord(
            approval_id="legacy", release_keys=["foo"],
            canonical_title="Foo Quest", approved_folder="Foo Quest")}
        result = apply_approvals(groups, approvals)
        assert result[1]
        g = next(grp for grp in groups if grp.release_key.startswith("foo"))
        assert g.quarantine_reason is None and g.title == "Foo Quest"


# T1: malformed JSON record skipped (never crashes).
def test_load_approvals_skips_malformed_json(tmp_path):
    cfg = tmp_path / "config" / COMMITTED_DIR
    cfg.mkdir(parents=True)
    (cfg / "broken.json").write_text("{ not valid json,,,")
    (cfg / "good.json").write_text(json.dumps({
        "schema_version": 1, "approval_id": "apr_ok", "release_keys": ["ok"],
        "canonical_title": "OK Game", "approved_folder": "OK Game",
        "status": "active", "source_urls": [], "events": [],
    }, indent=2))
    loaded = load_approval_records(tmp_path)
    assert "ok" in loaded.by_key
    assert "apr_ok" in [r.approval_id for r in loaded.records]


# T2/T9: invalid URL in a committed record is flagged and NOT applied.
def test_load_approvals_flags_invalid_url_and_skips(tmp_path):
    cfg = tmp_path / "config" / COMMITTED_DIR
    cfg.mkdir(parents=True)
    rec = {
        "schema_version": 1, "approval_id": "apr_bad", "release_keys": ["bad"],
        "canonical_title": "Bad Host Game", "approved_folder": "Bad Host Game",
        "status": "active",
        "source_urls": [{"url": "https://evil.example.com/x", "role": "metadata"}],
        "events": [],
    }
    (cfg / "apr_bad.json").write_text(json.dumps(rec))
    loaded = load_approval_records(tmp_path)
    assert "bad" not in loaded.by_key
    assert loaded.invalid_url_records


# T4: approve rejects invalid URL at creation (aborts, no file written).
def test_approve_rejects_invalid_url_cli(tmp_path):
    from amiga_adf_library_builder import cli as cli_mod

    _make_pseudo_original(tmp_path)
    rc = cli_mod.main([
        "approve", "--library-root", str(tmp_path),
        "--release-key", "foo", "--title", "Foo Quest", "--folder", "Foo Quest",
        "--source-url", "https://evil.example.com/x", "--role", "metadata",
        "--allow-incomplete",
    ])
    assert rc == 2
    assert not list((tmp_path / "config" / COMMITTED_DIR).glob("*.json"))


# T4: approve writes a record, computes hashes read-only from original/.
def test_approve_writes_record_with_hashes(tmp_path):
    from amiga_adf_library_builder import cli as cli_mod

    orig = _make_pseudo_original(tmp_path)
    rc = cli_mod.main([
        "approve", "--library-root", str(tmp_path),
        "--release-key", "foo", "--title", "Foo Quest", "--folder", "Foo Quest",
        "--source-url", "https://www.lemonamiga.com/games/details.php?id=1",
        "--role", "metadata", "--allow-incomplete", "--reason", "unit test approval",
    ])
    assert rc == 0
    files = list((tmp_path / "config" / COMMITTED_DIR).glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["release_keys"] == ["foo"]
    assert rec["canonical_title"] == "Foo Quest"
    assert rec["expected_sha256"]["Foo_Boot.adf"] == _sha_str(b"BOOTDISK-CONTENTS-1234")
    assert rec["source_urls"] == [
        {"url": "https://www.lemonamiga.com/games/details.php?id=1", "role": "metadata"}
    ]
    assert orig.joinpath("Foo_Boot.adf").read_bytes() == b"BOOTDISK-CONTENTS-1234"


# T5: merge - one record with multiple release_keys.
def test_merge_one_record_multiple_release_keys(tmp_path):
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        orig = _make_pseudo_original(data_root)
        rec = {
            "schema_version": 1, "approval_id": "apr_merge",
            "release_keys": ["foo", "bar"], "canonical_title": "Merged Title",
            "approved_folder": "Merged Folder",
            "approved_source_filenames": ["Foo_Boot.adf", "Foo_Char.adf", "Bar_Char.adf"],
            "expected_sha256": {
                "Foo_Boot.adf": _sha_str(b"BOOTDISK-CONTENTS-1234"),
                "Foo_Char.adf": _sha_str(b"CHARDISK-CONTENTS-5678"),
                "Bar_Char.adf": _sha_str(b"BAR-CHAR-9999"),
            },
            "status": "active", "source_urls": [], "events": [],
        }
        _write_json_approval(data_root, rec)
        groups = _groups_for(data_root)
        result = apply_approvals(groups, load_approval_records(data_root).by_key, original_dir=orig)
        assert set(result[1])
        gfoo = next(g for g in groups if g.release_key.startswith("foo"))
        gbar = next(g for g in groups if g.release_key.startswith("bar"))
        assert gfoo.title == gbar.title == "Merged Title"
        assert gfoo.folder == gbar.folder == "Merged Folder"
        assert gfoo.quarantine_reason is None and gbar.quarantine_reason is None


# T4: revoke flips status, keeps history, file never deleted.
def test_revoke_marks_revoked_keeps_history(tmp_path):
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        _make_pseudo_original(data_root)
        rec = {
            "schema_version": 1, "approval_id": "apr_foo", "release_keys": ["foo"],
            "canonical_title": "Foo Quest", "approved_folder": "Foo Quest",
            "status": "active", "source_urls": [],
            "created_at": "2026-08-04T00:00:00Z",
            "events": [{"at": "2026-08-04T00:00:00Z", "by": "operator", "action": "created"}],
        }
        p = _write_json_approval(data_root, rec)
        updated = revoke_approval(
            config_dir=data_root / "config", approval_id="apr_foo", reason="no longer approved")
        assert updated is not None and updated.status == "revoked"
        assert updated.revoked_at is not None and updated.revocation_reason == "no longer approved"
        assert p.is_file()
        data = json.loads(p.read_text())
        assert any(e["action"] == "revoked" for e in data["events"])
        groups = _groups_for(data_root)
        assert "foo" not in load_approval_records(data_root).by_key


# Revoked record returns a previously-approved group to quarantine on rerun.
def test_revoked_returns_group_to_quarantine(tmp_path):
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        orig = _make_pseudo_original(data_root)
        rec = {
            "schema_version": 1, "approval_id": "apr_foo", "release_keys": ["foo"],
            "canonical_title": "Foo Quest", "approved_folder": "Foo Quest",
            "approved_source_filenames": ["Foo_Boot.adf", "Foo_Char.adf"],
            "expected_sha256": {
                "Foo_Boot.adf": _sha_str(b"BOOTDISK-CONTENTS-1234"),
                "Foo_Char.adf": _sha_str(b"CHARDISK-CONTENTS-5678"),
            },
            "status": "active", "source_urls": [], "events": [],
        }
        _write_json_approval(data_root, rec)
        groups = _groups_for(data_root)
        r1 = apply_approvals(groups, load_approval_records(data_root).by_key, original_dir=orig)
        assert r1[1]
        revoke_approval(config_dir=data_root / "config", approval_id="apr_foo", reason="x")
        groups2 = _groups_for(data_root)
        r2 = apply_approvals(groups2, load_approval_records(data_root).by_key, original_dir=orig)
        assert r2[1] == []
        g = next(grp for grp in groups2 if grp.release_key.startswith("foo"))
        assert g.quarantine_reason is not None


# T4: repeat run is deterministic (idempotent apply).
def test_apply_approvals_deterministic_repeat(tmp_path):
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        orig = _make_pseudo_original(data_root)
        rec = {
            "schema_version": 1, "approval_id": "apr_foo", "release_keys": ["foo"],
            "canonical_title": "Foo Quest", "approved_folder": "Foo Quest",
            "approved_source_filenames": ["Foo_Boot.adf", "Foo_Char.adf"],
            "expected_sha256": {
                "Foo_Boot.adf": _sha_str(b"BOOTDISK-CONTENTS-1234"),
                "Foo_Char.adf": _sha_str(b"CHARDISK-CONTENTS-5678"),
            },
            "status": "active", "source_urls": [], "events": [],
        }
        _write_json_approval(data_root, rec)
        groups = _groups_for(data_root)
        approvals = load_approval_records(data_root).by_key
        r1 = apply_approvals(list(groups), approvals, original_dir=orig)
        r2 = apply_approvals(list(groups), approvals, original_dir=orig)
        # Deterministic: hash verdict stable; an already-applied group stays
        # de-quarantined (no-op) rather than re-listed in applied_keys.
        assert r1.hash_failures == r2.hash_failures
        g = next(grp for grp in groups if grp.release_key.startswith("foo"))
        assert g.quarantine_reason is None


# Non-approved releases are unchanged.
def test_non_approved_releases_unchanged(tmp_path):
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        _make_pseudo_original(data_root)
        groups = _groups_for(data_root)
        result = apply_approvals(groups, load_approval_records(data_root).by_key)
        assert result[1] == []
        assert all(g.quarantine_reason is not None for g in groups)


# Supersession: newest-active wins for overlapping release key.
def test_supersession_newest_active_wins(tmp_path):
    cfg = tmp_path / "config" / COMMITTED_DIR
    cfg.mkdir(parents=True)
    (cfg / "apr_old.json").write_text(json.dumps({
        "schema_version": 1, "approval_id": "apr_old", "release_keys": ["foo"],
        "canonical_title": "Foo Old", "approved_folder": "Foo Old",
        "status": "active", "created_at": "2026-08-01T00:00:00Z",
        "source_urls": [], "events": []}))
    (cfg / "apr_new.json").write_text(json.dumps({
        "schema_version": 1, "approval_id": "apr_new", "release_keys": ["foo"],
        "canonical_title": "Foo New", "approved_folder": "Foo New",
        "status": "active", "created_at": "2026-08-04T00:00:00Z",
        "source_urls": [], "events": []}))
    loaded = load_approval_records(tmp_path)
    assert loaded.by_key["foo"].approval_id == "apr_new"


# T6: Gotek-facing NFO omits Approved source line when no source URLs supplied
# (no guessing); provenance lives only in the durable sidecar (Gotek NFO contract).
def test_nfo_omits_approved_source_when_absent(tmp_path):
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        orig = _make_pseudo_original(data_root)
        rec = {
            "schema_version": 1, "approval_id": "apr_foo", "release_keys": ["foo"],
            "canonical_title": "Foo Quest", "approved_folder": "Foo Quest",
            "approved_source_filenames": ["Foo_Boot.adf", "Foo_Char.adf"],
            "expected_sha256": {
                "Foo_Boot.adf": _sha_str(b"BOOTDISK-CONTENTS-1234"),
                "Foo_Char.adf": _sha_str(b"CHARDISK-CONTENTS-5678"),
            },
            "status": "active", "source_urls": [], "events": [],
        }
        _write_json_approval(data_root, rec)
        res = run_pipeline(
            cfg=_cfg(data_root), upstream_task_closed=True, run_id="nfo-no-url",
            verified_artwork_width=artwork_mod.ARTWORK_MAX_W,
            verified_artwork_height=artwork_mod.ARTWORK_MAX_H)
        nfo = (data_root / "assets" / "nfo" / "Foo Quest.nfo").read_text()
        # Gotek NFO never embeds provenance (Gotek NFO contract).
        assert "Approved source:" not in nfo
        # Durable sidecar still exists and is empty of approved sources.
        prov = json.loads((data_root / "assets" / "nfo" / "Foo Quest.provenance.json").read_text())
        assert prov["approved_sources"] == []
        assert res["original_preserved"] is True


# T6: durable provenance sidecar cites exact per-role URLs when supplied.
def test_nfo_cites_approved_source_per_role(tmp_path):
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        orig = _make_pseudo_original(data_root)
        meta_url = "https://www.lemonamiga.com/games/details.php?id=example"
        art_url = "https://amiga.abime.net/screenshots/foo.png"
        rec = {
            "schema_version": 1, "approval_id": "apr_foo", "release_keys": ["foo"],
            "canonical_title": "Foo Quest", "approved_folder": "Foo Quest",
            "approved_source_filenames": ["Foo_Boot.adf", "Foo_Char.adf"],
            "expected_sha256": {
                "Foo_Boot.adf": _sha_str(b"BOOTDISK-CONTENTS-1234"),
                "Foo_Char.adf": _sha_str(b"CHARDISK-CONTENTS-5678"),
            },
            "status": "active",
            "source_urls": [
                {"url": meta_url, "role": "metadata"},
                {"url": art_url, "role": "artwork"}],
            "events": [],
        }
        _write_json_approval(data_root, rec)
        run_pipeline(
            cfg=_cfg(data_root), upstream_task_closed=True, run_id="nfo-url",
            verified_artwork_width=artwork_mod.ARTWORK_MAX_W,
            verified_artwork_height=artwork_mod.ARTWORK_MAX_H)
        nfo = (data_root / "assets" / "nfo" / "Foo Quest.nfo").read_text()
        # Gotek NFO must NOT embed the approved-source URLs.
        assert "Approved source:" not in nfo
        assert meta_url not in nfo
        assert art_url not in nfo
        # The URLs are durable in the per-release provenance sidecar.
        prov = json.loads((data_root / "assets" / "nfo" / "Foo Quest.provenance.json").read_text())
        by_role = {a["role"]: a["url"] for a in prov["approved_sources"]}
        assert by_role.get("metadata") == meta_url
        assert by_role.get("artwork") == art_url
        # And durably in the human-readable text sidecar.
        prov_txt = (data_root / "assets" / "nfo" / "Foo Quest.provenance.txt").read_text()
        assert f"- (metadata) {meta_url}" in prov_txt
        assert f"- (artwork) {art_url}" in prov_txt


# T3: apply_approvals performs zero network I/O (offline-safe, ratified #23).
def test_apply_approvals_offline_safe_monkeypatched_socket(tmp_path, monkeypatch):
    import socket
    import urllib.request

    def _boom(*args, **kwargs):
        raise OSError("network blocked in test")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td) / "data"
        (data_root / "config").mkdir(parents=True, exist_ok=True)
        orig = _make_pseudo_original(data_root)
        rec = {
            "schema_version": 1, "approval_id": "apr_foo", "release_keys": ["foo"],
            "canonical_title": "Foo Quest", "approved_folder": "Foo Quest",
            "approved_source_filenames": ["Foo_Boot.adf", "Foo_Char.adf"],
            "expected_sha256": {
                "Foo_Boot.adf": _sha_str(b"BOOTDISK-CONTENTS-1234"),
                "Foo_Char.adf": _sha_str(b"CHARDISK-CONTENTS-5678"),
            },
            "status": "active", "source_urls": [], "events": [],
        }
        _write_json_approval(data_root, rec)
        groups = _groups_for(data_root)
        result = apply_approvals(groups, load_approval_records(data_root).by_key, original_dir=orig)
        assert result[1]
