from __future__ import annotations

from pathlib import Path

from amiga_adf_library_builder.models import ParsedRecord, ReleaseGroup
from amiga_adf_library_builder.pipeline import _rtfm_release_diagnostics
from amiga_adf_library_builder.rtfm import RtfmResult


def _group() -> ReleaseGroup:
    record = ParsedRecord(source_filename="A-10 Tank Killer.adf", ext="adf", title="A 10 Tank Killer")
    return ReleaseGroup(
        release_key="a-10|", title="A 10 Tank Killer", edition=None, group=None,
        chipset=None, language=None, version=None, alt_marker=None, ext="adf",
        records=[record], disks=[record],
    )


def test_provenance_only_is_diagnostic_no_output_not_built(tmp_path: Path):
    group = _group()
    provenance = tmp_path / "A 10 Tank Killer.rtfm.provenance.json"
    provenance.write_text("{}", encoding="utf-8")
    result = RtfmResult(
        release_key=group.release_key,
        basename="A 10 Tank Killer",
        routed_for_review=True,
        review_reason="all matched sources failed to decode; routed for review",
        notes=["source skipped (extraction unavailable): A-10 Tank Killer.pdf"],
        provenance_path=provenance,
    )

    row = _rtfm_release_diagnostics(
        [group], [result], {"enabled_reason": "local manual roots"},
        selected=True, export_requested=False, export_result=None,
        verify_only=False,
    )[0]

    assert row["written"] is False
    assert row["rtfm_path"] is None
    assert row["provenance_persisted"] is True
    assert row["no_rtfm_reason"] == "all matched sources failed to decode; routed for review"


def test_only_existing_rtfm_payload_is_marked_written(tmp_path: Path):
    group = _group()
    payload = tmp_path / "A 10 Tank Killer.rtfm"
    payload.write_text("# A 10 Tank Killer\n\n[CONTROLS]\nFire: Space\n", encoding="utf-8")
    result = RtfmResult(
        release_key=group.release_key,
        basename="A 10 Tank Killer",
        written=True,
        rtfm_path=payload,
    )

    row = _rtfm_release_diagnostics(
        [group], [result], {}, selected=True, export_requested=False,
        export_result=None, verify_only=False,
    )[0]

    assert row["written"] is True
    assert row["output_exists"] is True
    assert row["rtfm_path"] == str(payload)
