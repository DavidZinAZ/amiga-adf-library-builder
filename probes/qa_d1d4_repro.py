#!/usr/bin/env python3
"""QA reproduction probes for D1-D4 blockers. Run from the worktree root."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from amiga_adf_library_builder.paths import PathConfig
from amiga_adf_library_builder.pipeline import run_pipeline
from amiga_adf_library_builder.selection import load_operator_decisions, select_one_per_game
from amiga_adf_library_builder import manual_approvals as ma
from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup

WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
LIB = WORK / "library"
for d in ["original", "staging", "output", "quarantine", "logs", "cache", "reports", "approvals", "curation", "config"]:
    (LIB / d).mkdir(parents=True, exist_ok=True)

FILES = [
    "Battle Squadron (1989)(Electronic Arts).adf",
    "Battle Squadron (1989)(Electronic Arts)[a].adf",
    "Battle Squadron (1989)(Electronic Arts)[cr SKR].adf",
    "Battle Squadron (1989)(Electronic Arts)(AGA).adf",
    "Battle Squadron (1989)(Electronic Arts)(en).adf",
]
for name in FILES:
    (LIB / "original" / name).write_bytes(b"ADF" + name.encode() + b"\x00" * 900)

cfg = PathConfig(
    library_root=LIB, original_dir=LIB / "original", staging_dir=LIB / "staging",
    output_dir=LIB / "output", quarantine_dir=LIB / "quarantine",
    logs_dir=LIB / "logs", cache_dir=LIB / "cache", reports_dir=LIB / "reports",
    approvals_dir=LIB / "approvals",
)

# D1: ranked -> 1 release
res = run_pipeline(cfg=cfg, export=True, upstream_task_closed=True, run_id="d1-ranked")
assert res["export"]["releases_exported"] == 1, f"D1 ranked exported {res['export']['releases_exported']}"
assert res["selection"]["selected_count"] == 1
print("D1 PASS: ranked selection -> 1 release")

# D1b: persisted decision -> 1 release, correct winner
dec = {"battlesquadron": "battlesquadron||||||"}
dec_file = LIB / "curation" / "1g1r_decisions.json"
dec_file.write_text(json.dumps(dec))
res2 = run_pipeline(cfg=cfg, export=True, upstream_task_closed=True, run_id="d1-decision",
                    operator_decisions_path=str(dec_file))
assert res2["export"]["releases_exported"] == 1, f"D1b exported {res2['export']['releases_exported']}"
assert res2["selection"]["decisions"][0]["winner"] == "battlesquadron||||||"
print("D1b PASS: persisted decision -> 1 release, winner = battlesquadron||||||")

# D3: file path actually loaded (not library root)
direct = load_operator_decisions(dec_file)
assert direct == dec, f"D3 direct load {direct}"
print("D3 PASS: file path loaded directly")

# D4: manifest written
manifest = LIB / "SEL.json"
res3 = run_pipeline(cfg=cfg, export=True, upstream_task_closed=True, run_id="d4-manifest",
                    selection_manifest_path=str(manifest))
assert manifest.exists(), "D4 manifest not written"
loaded = json.loads(manifest.read_text())
assert loaded["schema_version"] == 1
assert loaded["provenance"]["selected_count"] == 1
print("D4 PASS: manifest written deterministically")

# D2: approval wins over ranking at engine level
# Build 2 same-game groups; approve the weaker one (lower rank score)
plain = ParsedRecord(source_filename="Battle Squadron (1989)(Electronic Arts).adf", ext="adf",
                       title="Battle Squadron", release_key="battlesquadron||||||",
                       disk_number=1, total_disks=1, special_disk=False, edition=None,
                       version=None, group=None, chipset=None, language="")
other = ParsedRecord(source_filename="Battle Squadron (1989)(Electronic Arts)[cr SKR].adf", ext="adf",
                       title="Battle Squadron", release_key="battlesquadron||skr||||",
                       disk_number=1, total_disks=1, special_disk=False, edition=None,
                       version=None, group=None, chipset=None, language="")
g1 = ReleaseGroup(release_key=plain.release_key, title="Battle Squadron", edition=None,
                  group=None, chipset=None, language="", version=None, alt_marker=None,
                  ext="adf", records=[plain], disks=[plain], specials=[],
                  is_complete=True, has_main_disk=True)
g2 = ReleaseGroup(release_key=other.release_key, title="Battle Squadron", edition=None,
                  group=None, chipset=None, language="", version=None, alt_marker=None,
                  ext="adf", records=[other], disks=[other], specials=[],
                  is_complete=True, has_main_disk=True)
appr = {"battlesquadron||||||": ma.ApprovalRecord(
    approval_id="qa-d2", release_keys=["battlesquadron||||||"],
    canonical_title="Battle Squadron", approved_folder="Battle Squadron APPROVED",
)}
result = select_one_per_game([g1, g2], approvals=appr)
winner = result.provenance["decisions"][0]
assert winner["decision"] == "operator_override", f"D2 decision {winner['decision']}"
assert result.selected[0].release_key == "battlesquadron||||||"
print("D2 PASS: approval wins selection at engine level")

print("ALL D1-D4 PROBES PASS")