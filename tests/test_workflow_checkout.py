"""Workflow checkout regression test (t_00696c14).

Verifies that the release-windows job has an actions/checkout step
as its first step, so that pyproject.toml is available when the
version-equality gate runs.

Regression test for: release-windows job reads pyproject.toml but
was missing actions/checkout, causing FileNotFoundError on runner.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "build-windows.yml"


def _load_workflow() -> dict:
    """Parse the workflow YAML."""
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def test_release_windows_has_checkout_as_first_step():
    """release-windows job must start with actions/checkout@v4."""
    wf = _load_workflow()
    steps = wf["jobs"]["release-windows"]["steps"]
    first = steps[0]
    assert first.get("uses", "").startswith("actions/checkout"), (
        f"First step is {first.get('name')}, not checkout"
    )
    assert first["uses"] == "actions/checkout@v4", (
        f"Expected actions/checkout@v4, got {first['uses']}"
    )


def test_release_windows_checkout_before_version_gate():
    """Checkout must precede the GH-119 version-equality step."""
    wf = _load_workflow()
    steps = wf["jobs"]["release-windows"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    checkout_idx = step_names.index("Checkout")
    gate_idx = step_names.index("Verify tag matches canonical application version")
    assert checkout_idx < gate_idx, "Checkout must come before version gate"


def test_release_windows_version_gate_preserved():
    """The GH-119 tag/version equality gate must still exist."""
    wf = _load_workflow()
    steps = wf["jobs"]["release-windows"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    assert "Verify tag matches canonical application version" in step_names, (
        "GH-119 version gate missing"
    )


def test_build_windows_checkout_preserved():
    """build-windows job must still have its checkout step (unchanged)."""
    wf = _load_workflow()
    steps = wf["jobs"]["build-windows"]["steps"]
    uses = [s.get("uses", "") for s in steps]
    assert any(u.startswith("actions/checkout") for u in uses), (
        "build-windows lost its checkout step"
    )


def test_workflow_yaml_parses():
    """Workflow must be valid YAML."""
    _load_workflow()  # raises on invalid YAML
