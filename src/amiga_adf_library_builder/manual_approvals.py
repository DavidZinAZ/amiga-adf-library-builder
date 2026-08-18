"""Manual approvals for quarantined special-only release keys (manual-approval feature).

Operator-curated JSON records map one or more release keys (base or full pipe
padded form) to an approved canonical title + Gotek folder, an inventory of
approved source filenames, their expected SHA-256 digests, and authoritative
source URLs (metadata / artwork / reference roles).

When an approval matches a group that is currently quarantined for the
special-only condition, the pipeline clears the quarantine, applies the
approved title/folder, records the approved source URLs for NFO provenance,
and routes the group for enrich + NFO + export like any accepted release.

Storage layout (ratified per docs/issue1-security-ratification.md, section 1):

  * ``config/manual-approvals/<approval_id>.json``
        -- committed, reviewed (git-tracked). One file per approval; supports
           merge (multiple release_keys), revocation history, and rich fields.
  * ``config/local.manual-approvals/<approval_id>.json``
        -- operator overrides (git-ignored via ``config/local.*``); wins on
           per-release-key collision with the committed record.

Only the Python standard library is used (``json``, ``hashlib``,
``secrets``). No third-party dependencies.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --- Ratified constants ------------------------------------------------------

#: Finalized URL host allowlist (ratified, docs/issue1-security-ratification.md
#: section 0b). A host is accepted iff it equals an allowed host or is a
#: subdomain of one (suffix match, never infix).
HOST_ALLOWLIST: frozenset[str] = frozenset(
    {
        "lemonamiga.com",
        "amiga.abime.net",
        "openretro.org",
        "halloflight.amiga32.org",
        "wikipedia.org",
        "rawg.io",
        "mobygames.com",
        "images.mobygames.com",
    }
)

#: Allowed source-URL roles.
SOURCE_ROLES: frozenset[str] = frozenset({"metadata", "artwork", "reference"})

#: Allowed URL schemes.
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

#: Approved record schema version.
SCHEMA_VERSION: int = 1

#: Directory (under ``<data_root>/config``) holding committed, git-tracked
#: approval records.
COMMITTED_DIR = "manual-approvals"
#: Directory (under ``<data_root>/config``) holding git-ignored local overrides.
LOCAL_DIR = "local.manual-approvals"


# --- Legacy Approval (preserved for backward-compatible unit tests) ----------


@dataclass
class Approval:
    """Legacy single-key operator approval (v1-compatible).

    Retained so existing unit tests that construct ``Approval(release_key=...,
    title=..., folder=...)`` keep working. It exposes the same accessor
    surface that :func:`apply_approvals` expects of an approval record
    (``release_keys``, ``canonical_title``, ``approved_folder``,
    ``source_urls``, ``expected_sha256``, ``status``).
    """

    release_key: str
    title: str
    folder: str
    source: str = ""
    note: str = ""
    approved_by: str = ""
    approved_at: str = ""

    # --- unified record accessors used by apply_approvals -------------------
    @property
    def release_keys(self) -> list[str]:
        return [self.release_key.lower()]

    @property
    def canonical_title(self) -> str:
        return self.title

    @property
    def approved_folder(self) -> Optional[str]:
        return self.folder

    @property
    def source_urls(self) -> list[dict]:
        return []

    @property
    def expected_sha256(self) -> dict:
        return {}

    @property
    def approved_source_filenames(self) -> list[str]:
        return []

    @property
    def status(self) -> str:
        return "active"

    @property
    def approval_id(self) -> Optional[str]:
        return None

    @property
    def incomplete_set_override(self) -> bool:
        return False

    def to_dict(self) -> dict:
        return {
            "release_key": self.release_key,
            "title": self.title,
            "folder": self.folder,
            "source": self.source,
            "note": self.note,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
        }


# --- Approved record (JSON-backed) ------------------------------------------


@dataclass
class ApprovalRecord:
    """One ratified approval record (one JSON file)."""

    approval_id: str
    release_keys: list[str]
    canonical_title: str
    approved_folder: str
    approved_source_filenames: list[str] = field(default_factory=list)
    expected_sha256: dict = field(default_factory=dict)
    incomplete_set_override: bool = False
    operator_reason: str = ""
    source_urls: list = field(default_factory=list)
    created_at: str = ""
    created_by: str = ""
    status: str = "active"
    revoked_at: Optional[str] = None
    revocation_reason: Optional[str] = None
    superseded_by: Optional[str] = None
    events: list = field(default_factory=list)
    config_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "approval_id": self.approval_id,
            "release_keys": list(self.release_keys),
            "canonical_title": self.canonical_title,
            "approved_folder": self.approved_folder,
            "approved_source_filenames": list(self.approved_source_filenames),
            "expected_sha256": dict(self.expected_sha256),
            "incomplete_set_override": self.incomplete_set_override,
            "operator_reason": self.operator_reason,
            "source_urls": [dict(s) for s in self.source_urls],
            "created_at": self.created_at,
            "created_by": self.created_by,
            "status": self.status,
            "revoked_at": self.revoked_at,
            "revocation_reason": self.revocation_reason,
            "superseded_by": self.superseded_by,
            "events": [dict(e) for e in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalRecord":
        data = data or {}
        return cls(
            approval_id=str(data.get("approval_id", "")),
            release_keys=[str(k).lower() for k in (data.get("release_keys") or [])],
            canonical_title=str(data.get("canonical_title", "")),
            approved_folder=str(data.get("approved_folder", "")),
            approved_source_filenames=[
                str(f) for f in (data.get("approved_source_filenames") or [])
            ],
            expected_sha256={
                str(k): str(v) for k, v in (data.get("expected_sha256") or {}).items()
            },
            incomplete_set_override=bool(data.get("incomplete_set_override", False)),
            operator_reason=str(data.get("operator_reason", "")),
            source_urls=[dict(s) for s in (data.get("source_urls") or [])],
            created_at=str(data.get("created_at", "")),
            created_by=str(data.get("created_by", "")),
            status=str(data.get("status", "active")),
            revoked_at=data.get("revoked_at"),
            revocation_reason=data.get("revocation_reason"),
            superseded_by=data.get("superseded_by"),
            events=[dict(e) for e in (data.get("events") or [])],
        )

    @property
    def is_active(self) -> bool:
        return self.status == "active" and not self.superseded_by


# --- Apply result ------------------------------------------------------------


class ApplyResult(tuple):
    """Return value of :func:`apply_approvals`.

    A 3-tuple ``(groups, applied_keys, unmatched_keys)`` for backward
    compatibility with callers that unpack it, with an additional
    ``hash_failures`` attribute carrying the safe-fail refusals.
    """

    def __new__(cls, groups, applied, unmatched, hash_failures=None):
        obj = super().__new__(cls, (groups, applied, unmatched))
        obj.hash_failures = list(hash_failures or [])
        return obj


# --- Helpers -----------------------------------------------------------------


def _base_key(release_key: str) -> str:
    """Return the base release key (text before the first ``|``), lowercased.

    A release_key is ``title + '||||||'``; the operator may write either the
    full pipe-padded key or the short base (e.g. ``examplequestiii``).
    """
    return release_key.split("|")[0].lower()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically (temp file + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


# --- URL validation (ratified, section 2.1) ----------------------------------


def validate_source_url(url: str) -> tuple[bool, str]:
    """Validate an operator-supplied source URL.

    Returns ``(ok, reason)``. On success ``reason`` is ``""``. The EXACT
    original ``url`` string is what callers must store (verbatim) -- this
    function never normalizes it.

    Rules (ratified):
      * parseable via ``urllib.parse.urlsplit``
      * scheme ``http``/``https`` only
      * no userinfo (``@`` in netloc)
      * host must not be a bare IP literal
      * host must be in the allowlist or a subdomain of an allowlisted host
    """
    from urllib.parse import urlsplit

    if not url or not isinstance(url, str):
        return False, "malformed"
    try:
        parts = urlsplit(url)
    except ValueError:
        return False, "malformed"

    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False, f"scheme:{scheme or 'none'} not allowed"

    netloc = parts.netloc or ""
    if "@" in netloc:
        return False, "userinfo not allowed"

    # Strip any userinfo already (defensive) and port; lowercase host.
    host = netloc.split("@")[-1].split(":")[0].lower().strip(".")
    if not host:
        return False, "malformed"

    # Reject bare IP literals (v4 dotted / v6 in brackets).
    if host.startswith("[") or all(
        c in "0123456789abcdefABCDEF:." for c in host
    ):
        # Allow only if it is not an IP; a bracketed v6 or all-hex/dot is IP.
        import ipaddress

        try:
            ipaddress.ip_address(host.strip("[]"))
            return False, "ip host not allowed"
        except ValueError:
            pass

    for allowed in HOST_ALLOWLIST:
        if host == allowed or host.endswith("." + allowed):
            return True, ""
    return False, f"host:{host} not allowed"


# --- Loading -----------------------------------------------------------------


def _read_json_records(directory: Path) -> list[tuple[ApprovalRecord, str]]:
    """Read every ``*.json`` approval in ``directory``; skip malformed files.

    Returns a list of ``(record, path)`` pairs. A malformed JSON file is
    skipped (never crashes the pipeline).
    """
    directory = Path(directory)
    out: list[tuple[ApprovalRecord, str]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, ValueError):
            # Malformed JSON: skip, never crash.
            continue
        try:
            rec = ApprovalRecord.from_dict(data)
        except (ValueError, TypeError):
            continue
        if not rec.approval_id or not rec.release_keys or not rec.canonical_title:
            # An approval without an id / keys / title is useless.
            continue
        rec.config_path = str(path)
        out.append((rec, str(path)))
    return out


def _index_record(
    rec: ApprovalRecord,
    by_key: dict,
    invalid_url_records: list,
    *,
    force: bool = False,
) -> None:
    """Index an approval record into ``by_key`` by every release key.

    Gating (ratified, sections 2.2 / 4.2):
      * non-active (revoked / superseded) records are NOT applied;
      * records whose ``source_urls`` contain a now-disallowed URL are FLAGGED
        in ``invalid_url_records`` and NOT applied;
      * among active records covering the same key, the newest ``created_at``
        wins (supersession). ``force`` (local override) always wins.
    """
    if not rec.is_active:
        return
    # Flag-and-skip records with disallowed URLs (defense in depth).
    for su in rec.source_urls:
        url = su.get("url", "")
        ok, reason = validate_source_url(url)
        if not ok:
            invalid_url_records.append((rec, url, reason))
            return
    for key in rec.release_keys:
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = rec
            continue
        if force:
            by_key[key] = rec
            continue
        # Supersession: newest active wins (monotonic, deterministic).
        if rec.created_at >= existing.created_at:
            by_key[key] = rec


@dataclass
class LoadedApprovals:
    """Result of loading approval records from a data root."""

    by_key: dict  # release_key -> ApprovalRecord (the index used by apply)
    invalid_url_records: list  # list of (ApprovalRecord, url, reason)
    records: list  # all loaded ApprovalRecords (committed + local)


def load_approval_records(data_root) -> LoadedApprovals:
    """Load committed + local approval records and index them by release key.

    Returns a :class:`LoadedApprovals` with the active index (``by_key``),
    the flagged invalid-URL records, and the full record list. Never raises on
    malformed or missing files.
    """
    root = Path(data_root)
    config_dir = root / "config"
    committed = _read_json_records(config_dir / COMMITTED_DIR)
    local = _read_json_records(config_dir / LOCAL_DIR)

    by_key: dict = {}
    invalid_url_records: list = []
    # Committed first, then local overrides (which force on collision).
    for rec, _ in committed:
        _index_record(rec, by_key, invalid_url_records, force=False)
    for rec, _ in local:
        _index_record(rec, by_key, invalid_url_records, force=True)

    all_records = [rec for rec, _ in committed] + [rec for rec, _ in local]
    return LoadedApprovals(by_key=by_key, invalid_url_records=invalid_url_records, records=all_records)


def load_approvals(data_root) -> dict:
    """Backward-compatible loader: return the active release-key index.

    Equivalent to ``load_approval_records(data_root).by_key``. Indexes both
    the full and base key forms so short keys (``examplequestiii``) and
    pipe-padded keys both match.
    """
    loaded = load_approval_records(data_root)
    indexed: dict = {}
    for key, rec in loaded.by_key.items():
        indexed[key] = rec
        indexed[_base_key(key)] = rec
    return indexed


# --- Writing / revoking -------------------------------------------------------


def write_approval_record(
    *,
    config_dir: Path,
    release_keys: list[str],
    canonical_title: str,
    approved_folder: str,
    approved_source_filenames: Optional[list[str]] = None,
    original_dir: Optional[Path] = None,
    source_urls: Optional[list[dict]] = None,
    operator_reason: str = "",
    incomplete_set_override: bool = False,
    created_by: str = "operator",
) -> ApprovalRecord:
    """Create and persist a new approval record (atomically).

    Validates every source URL (aborts with ``ValueError`` on any failure),
    computes expected SHA-256 digests from ``original_dir`` read-only when
    source filenames are supplied, and marks any previously active record
    covering the same keys as superseded (audit trail on disk).

    Returns the written :class:`ApprovalRecord`.
    """
    release_keys = [str(k).lower() for k in (release_keys or [])]
    if not release_keys:
        raise ValueError("at least one release_key is required")
    if not canonical_title:
        raise ValueError("canonical_title is required")
    if not approved_folder:
        raise ValueError("approved_folder is required")
    source_urls = [dict(s) for s in (source_urls or [])]
    for su in source_urls:
        ok, reason = validate_source_url(su.get("url", ""))
        if not ok:
            raise ValueError(
                f"invalid source url {su.get('url')!r}: {reason}"
            )

    approved_source_filenames = [str(f) for f in (approved_source_filenames or [])]
    expected_sha256: dict = {}
    if original_dir is not None and approved_source_filenames:
        original_dir = Path(original_dir)
        for fname in approved_source_filenames:
            p = original_dir / fname
            if not p.is_file():
                raise ValueError(
                    f"approved source file missing in original/: {fname}"
                )
            expected_sha256[fname] = _sha256_file(p)

    now = utc_now()
    approval_id = (
        f"apr_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"_{secrets.token_hex(4)}"
    )
    record = ApprovalRecord(
        approval_id=approval_id,
        release_keys=release_keys,
        canonical_title=canonical_title,
        approved_folder=approved_folder,
        approved_source_filenames=approved_source_filenames,
        expected_sha256=expected_sha256,
        incomplete_set_override=incomplete_set_override,
        operator_reason=operator_reason,
        source_urls=source_urls,
        created_at=now,
        created_by=created_by,
        status="active",
        events=[{"at": now, "by": created_by, "action": "created"}],
    )

    out_dir = Path(config_dir) / COMMITTED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(out_dir / f"{approval_id}.json", record.to_dict())

    # Supersede any older active records sharing a key (audit trail on disk).
    _mark_superseded(config_dir, release_keys, approval_id, now, created_by)
    return record


def _mark_superseded(
    config_dir: Path,
    new_keys: list[str],
    new_id: str,
    now: str,
    by: str,
) -> None:
    """Mark older active records sharing ``new_keys`` as superseded on disk."""
    new_keys_set = set(new_keys)
    for sub in (COMMITTED_DIR, LOCAL_DIR):
        for path in sorted((Path(config_dir) / sub).glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("approval_id") == new_id:
                continue
            if data.get("status") != "active":
                continue
            rec_keys = {str(k).lower() for k in (data.get("release_keys") or [])}
            if rec_keys.isdisjoint(new_keys_set):
                continue
            data["superseded_by"] = new_id
            data.setdefault("events", []).append(
                {
                    "at": now,
                    "by": by,
                    "action": "superseded",
                    "by_approval_id": new_id,
                }
            )
            _atomic_write_json(path, data)


def revoke_approval(
    *,
    config_dir: Path,
    approval_id: str,
    reason: str,
    by: str = "operator",
) -> Optional[ApprovalRecord]:
    """Revoke an approval record (atomic write; file is never deleted).

    Sets ``status='revoked'``, ``revoked_at``, ``revocation_reason``, and
    appends an ``events`` entry. Returns the updated record, or ``None`` if the
    approval id was not found in either the committed or local directory.
    """
    now = utc_now()
    for sub in (COMMITTED_DIR, LOCAL_DIR):
        path = Path(config_dir) / sub / f"{approval_id}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        data["status"] = "revoked"
        data["revoked_at"] = now
        data["revocation_reason"] = reason
        data.setdefault("events", []).append(
            {"at": now, "by": by, "action": "revoked", "reason": reason}
        )
        _atomic_write_json(path, data)
        return ApprovalRecord.from_dict(data)
    return None


# --- Apply --------------------------------------------------------------------


def _verify_hashes(
    rec,
    original_dir: Path,
    group_source_files: list[str],
    matched_key: str,
) -> list[dict]:
    """Pre-apply hash verification (ratified, section 3.1).

    Returns a list of failure dicts ``{release_key, file, reason}`` where
    reason is one of ``missing`` / ``extra`` / ``mismatch``. An empty list means
    the record's source inventory matches the corpus.
    """
    original_dir = Path(original_dir)
    approved = rec.approved_source_filenames
    failures: list[dict] = []

    if not approved:
        # Legacy / hash-less record: nothing to verify.
        return failures

    # Presence + mismatch.
    for fname in approved:
        p = original_dir / fname
        if not p.is_file():
            failures.append(
                {"release_key": matched_key, "file": fname, "reason": "missing"}
            )
            continue
        actual = _sha256_file(p)
        expected = rec.expected_sha256.get(fname)
        if expected is None or actual != expected:
            failures.append(
                {"release_key": matched_key, "file": fname, "reason": "mismatch"}
            )

    # Extra-file refusal: any group file not in the approved inventory.
    approved_set = set(approved)
    for fname in group_source_files:
        if fname not in approved_set:
            failures.append(
                {"release_key": matched_key, "file": fname, "reason": "extra"}
            )
    return failures


def apply_approvals(groups, approvals: dict, original_dir: Optional[Path] = None):
    """Un-quarantine and retitle approved special-only groups.

    For each group whose base or full ``release_key`` is in ``approvals`` AND
    whose ``quarantine_reason`` is currently set (special-only condition):

      * if ``original_dir`` is supplied and the matched approval carries
        ``expected_sha256`` / ``approved_source_filenames``, the source
        inventory is verified first; on ANY failure the group is NOT
        de-quarantined (safe-fail) and the failure is recorded in
        ``hash_failures``;
      * otherwise ``group.title`` / ``group.folder`` are set from the approval,
        ``group.quarantine_reason`` is cleared, and ``group.approved_sources``
        carries the approved source URLs for NFO provenance.

    Returns an :class:`ApplyResult` (a 3-tuple ``(groups, applied_keys,
    unmatched_keys)`` with a ``.hash_failures`` attribute). Any approval key
    whose base/full matched NO group is reported in ``unmatched_keys``.
    """
    applied: list[str] = []
    unmatched: list[str] = []
    hash_failures: list[dict] = []

    # Collect the set of group keys (base + full) that exist this run.
    group_keys: set[str] = set()
    for g in groups:
        group_keys.add(g.release_key)
        group_keys.add(_base_key(g.release_key))

    matched_approval_keys: set[str] = {
        k for k in approvals if k in group_keys
    }

    for g in groups:
        full = g.release_key
        base = _base_key(full)
        rec = approvals.get(full) or approvals.get(base)
        if rec is None:
            continue
        # Only clear quarantine for the intended special-only condition.
        if g.quarantine_reason is None:
            continue

        matched_key = full if full in approvals else base

        # Pre-apply hash verification (safe-fail).
        if original_dir is not None:
            group_source_files = [r.source_filename for r in g.records]
            failures = _verify_hashes(rec, original_dir, group_source_files, matched_key)
            if failures:
                hash_failures.extend(failures)
                # Quarantine is NEVER bypassed on failure.
                continue

        g.title = rec.canonical_title
        g.folder = rec.approved_folder
        g.quarantine_reason = None
        # Propagate approved source URLs for NFO provenance (ratified section 5).
        g.approved_sources = list(getattr(rec, "source_urls", []) or [])
        applied.append(full)

    for approval_key in approvals:
        if approval_key not in matched_approval_keys:
            unmatched.append(approval_key)

    return ApplyResult(groups, applied, unmatched, hash_failures)
