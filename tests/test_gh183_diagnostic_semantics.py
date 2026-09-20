"""Canonical persistence, provenance and export are separate evidence claims."""
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from amiga_adf_library_builder import enrich, metadata, pipeline
from test_gh183_production_corrections import library, trace


@pytest.fixture
def lemon_run(tmp_path, monkeypatch):
    root, manuals, cfg, run = library(tmp_path, ("Hacker",))
    pages = {
        "https://www.lemonamiga.com/game/hacker":
            '<h1>Hacker</h1><div class="docs"><a href="/doc/hacker/763">Hints</a></div>',
        "https://www.lemonamiga.com/doc/hacker/763":
            '<code>Enter the secret terminal code BLUEBIRD to open the door.</code>',
    }
    requests = []

    def response(url, **kwargs):
        requests.append(url)
        return pages[url], url

    monkeypatch.setattr(metadata, "_text_get", response)
    monkeypatch.setattr(enrich, "lookup_metadata", lambda *a, **k: (None, "fixture", []))
    return root, cfg, replace(run, online=True), requests


@pytest.mark.parametrize("mutation,expected", [
    ("deleted-claim", "not-persisted"),
    ("different-content", "not-persisted"),
    ("different-url", "not-persisted"),
    ("different-provider", "not-persisted"),
    ("different-type", "not-persisted"),
    ("different-game", "not-persisted"),
    ("missing-database", "not-persisted"),
    ("unreadable-database", "unverified"),
])
def test_written_lemon_output_does_not_prove_canonical_persistence(lemon_run, monkeypatch, mutation, expected):
    root, cfg, run, requests = lemon_run
    real_diagnostics = pipeline._rtfm_release_diagnostics

    def remove_persistence_evidence(groups, results, trace, **kwargs):
        db = root / "curation" / "canonical.db"
        if mutation == "missing-database":
            db.unlink()
        elif mutation == "unreadable-database":
            db.write_bytes(b"not a SQLite database")
        else:
            with sqlite3.connect(db) as conn:
                if mutation == "deleted-claim":
                    conn.execute("DELETE FROM field_claim WHERE field_name = 'rtfm_document'")
                else:
                    column = {
                        "different-content": "value", "different-url": "url",
                        "different-provider": "source", "different-type": "record_key",
                        "different-game": "entity_id",
                    }[mutation]
                    conn.execute(f"UPDATE field_claim SET {column} = ? WHERE field_name = 'rtfm_document'",
                                 ("different from the acquired document",))
        return real_diagnostics(groups, results, trace, **kwargs)

    monkeypatch.setattr(pipeline, "_rtfm_release_diagnostics", remove_persistence_evidence)
    row = trace(pipeline.run_pipeline(cfg, run))["Hacker"]
    assert len(requests) == 2
    assert row["written"] is True
    assert Path(row["rtfm_path"]).is_file()
    assert row["provenance_persisted"] is True
    assert row["export_status"] == "exported"
    assert Path(row["export_path"]).is_file()
    assert row["persistence_status"] == expected
    assert row["canonical_associations_expected"] == 1
    assert row["canonical_associations_reloaded"] == 0
    # Name+URL is source provenance, never evidence of an explicit provider ID.
    assert row["provider_source_preserved"] is True
    assert "provider_id_preserved" not in row


@pytest.mark.parametrize("removed", ["generated", "provenance", "exported"])
def test_physical_statuses_are_independent_of_reloadable_association(lemon_run, monkeypatch, removed):
    root, cfg, run, _ = lemon_run
    real_diagnostics = pipeline._rtfm_release_diagnostics

    def remove_physical_evidence(groups, results, trace, **kwargs):
        generated = results[0]
        target = {
            "generated": generated.rtfm_path,
            "provenance": generated.provenance_path,
            "exported": kwargs["export_result"].rtfm_exports[generated.release_key],
        }[removed]
        Path(target).unlink()
        return real_diagnostics(groups, results, trace, **kwargs)

    monkeypatch.setattr(pipeline, "_rtfm_release_diagnostics", remove_physical_evidence)
    result = pipeline.run_pipeline(cfg, run)
    row = trace(result)["Hacker"]
    assert row["persistence_status"] == "persisted"
    assert row["canonical_associations_reloaded"] == 1
    assert row["written"] is (removed != "generated")
    assert (row["output_path"] is not None) is (removed != "generated")
    assert row["provenance_persisted"] is (removed != "provenance")
    assert row["export_status"] == ("not-exported" if removed == "exported" else "exported")
    assert (row["export_path"] is not None) is (removed != "exported")
    assert result["rtfm"]["manual_trace"]["export_status"] == ("skipped" if removed == "exported" else "completed")


def test_persisted_document_can_be_rejected_without_physical_generation(lemon_run):
    root, cfg, run, _ = lemon_run
    # Force review routing after acquisition by setting a valid but tiny budget.
    Path(run.rtfm_config_path).write_text('[rtfm]\nenabled = true\nmax_bytes = 1\n')
    row = trace(pipeline.run_pipeline(cfg, run))["Hacker"]
    assert row["written"] is False
    assert row["routed_for_review"] is True
    assert row["persistence_status"] == "persisted"
    assert row["canonical_associations_reloaded"] == 1
    assert row["provenance_persisted"] is True
    assert row["export_path"] is None
