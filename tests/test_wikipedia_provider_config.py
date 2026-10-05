"""Focused tests for the authoritative Wikipedia provider configuration.

Covers the ticket's Wikipedia requirements: documented defaults, range
enforcement, safe fallback on corrupt values, and — critically — that the
config actually reaches the shared request gate rather than sitting inert in
the config file.
"""
import pytest

from amiga_adf_library_builder.wikipedia_client import (
    WikipediaGate,
    WikipediaPolicy,
    get_global_gate,
    reset_global_gate,
)
from amiga_adf_library_builder import wikipedia_config as wc


@pytest.fixture(autouse=True)
def _reset_gate():
    reset_global_gate()
    yield
    reset_global_gate()


# --- defaults ----------------------------------------------------------------

def test_defaults_are_the_known_good_values():
    cfg = wc.WikipediaConfig.from_dict({})
    assert cfg.enabled is True
    assert cfg.min_interval_seconds == 1.0
    assert cfg.retries_enabled is True
    assert cfg.max_retries == 2
    assert cfg.respect_retry_after is True
    assert cfg.retry_after_cap_seconds == 60.0


def test_defaults_survive_absent_table():
    assert wc.WikipediaConfig.from_dict(None).max_retries == 2
    assert wc.WikipediaConfig.from_dict(None).enabled is True


# --- corrupt / invalid values fall back safely -------------------------------

@pytest.mark.parametrize("bad", ["not-a-number", "", None, [], {}, "1e999", float("nan")])
def test_corrupt_request_delay_falls_back_to_default(bad):
    cfg = wc.WikipediaConfig.from_dict({"min_interval_seconds": bad})
    assert cfg.min_interval_seconds == 1.0


def test_corrupt_max_retries_falls_back_to_default():
    assert wc.WikipediaConfig.from_dict({"max_retries": "abc"}).max_retries == 2


def test_corrupt_retry_after_cap_falls_back_to_default():
    cfg = wc.WikipediaConfig.from_dict({"retry_after_cap_seconds": "soon"})
    assert cfg.retry_after_cap_seconds == 60.0


def test_negative_retry_count_clamps_to_zero_not_negative():
    assert wc.WikipediaConfig.from_dict({"max_retries": -5}).max_retries == 0


def test_one_bad_key_does_not_discard_the_rest_of_the_file():
    cfg = wc.WikipediaConfig.from_dict({
        "min_interval_seconds": "garbage",
        "max_retries": 4,
        "enabled": "false",
    })
    assert cfg.min_interval_seconds == 1.0   # bad key defaulted
    assert cfg.max_retries == 4              # good key preserved
    assert cfg.enabled is False              # good key preserved


def test_request_delay_out_of_range_is_clamped_to_documented_bounds():
    assert wc.WikipediaConfig.from_dict({"min_interval_seconds": 0.01}).min_interval_seconds == 0.5
    assert wc.WikipediaConfig.from_dict({"min_interval_seconds": 999}).min_interval_seconds == 10.0


def test_allowed_range_boundaries_are_accepted_verbatim():
    assert wc.WikipediaConfig.from_dict({"min_interval_seconds": 0.5}).min_interval_seconds == 0.5
    assert wc.WikipediaConfig.from_dict({"min_interval_seconds": 10.0}).min_interval_seconds == 10.0


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("false", False), ("yes", True), ("no", False),
    ("on", True), ("off", False), ("1", True), ("0", False),
    (True, True), (False, False),
])
def test_bool_strings_from_gui_or_hand_edited_toml(raw, expected):
    assert wc.coerce_bool(raw, not expected) is expected


def test_unrecognised_bool_falls_back_to_default():
    assert wc.coerce_bool("maybe", True) is True
    assert wc.coerce_bool("maybe", False) is False


# --- round trip --------------------------------------------------------------

def test_config_round_trips_through_dict():
    original = wc.WikipediaConfig.from_dict({
        "enabled": False,
        "min_interval_seconds": 2.5,
        "max_retries": 5,
        "retry_after_cap_seconds": 30.0,
        "use_cache": False,
    })
    again = wc.WikipediaConfig.from_dict(original.to_dict())
    assert again == original


# --- SCOPE 4: settings must reach the real request gate ----------------------

def test_effective_policy_carries_operator_values_into_the_policy():
    policy = wc.effective_policy({
        "min_interval_seconds": 3.5,
        "max_retries": 7,
        "respect_retry_after": False,
        "retry_after_cap_seconds": 12.0,
        "retries_enabled": False,
    })
    assert policy.min_interval_seconds == 3.5
    assert policy.max_retries == 7
    assert policy.respect_retry_after is False
    assert policy.retry_after_cap_seconds == 12.0
    assert policy.retries_enabled is False


def test_applying_config_changes_the_shared_global_gate():
    """The gate every call site uses must reflect GUI settings."""
    apply = wc.apply_effective_policy({"min_interval_seconds": 4.0, "max_retries": 9})
    gate = get_global_gate()
    assert gate.policy.min_interval_seconds == 4.0
    assert gate.policy.max_retries == 9
    assert apply.min_interval_seconds == 4.0


def test_runtime_actually_throttles_to_the_configured_delay():
    """Proof of wiring, not of settings storage: the gate sleeps the delay."""
    slept = []
    clock = {"t": 0.0}

    def fake_clock():
        return clock["t"]

    def fake_sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    gate = WikipediaGate(clock=fake_clock, sleep_fn=fake_sleep)
    wc.apply_policy_to_gate(gate, {"min_interval_seconds": 5.0})
    gate.begin_request()
    gate.note_request()
    gate.begin_request()   # second request must be paced by 5.0s
    assert slept == [5.0], f"expected one 5.0s throttle, got {slept}"


def test_max_retries_setting_controls_the_retry_ceiling():
    policy = wc.effective_policy({"max_retries": 4})
    assert policy.backoff_for(1) > 0
    # retries_enabled=False means the caller makes exactly one attempt; the
    # policy carries the flag the runtime reads.
    assert wc.effective_policy({"retries_enabled": False}).retries_enabled is False


def test_honour_retry_after_toggle_controls_cap_application():
    honoured = wc.effective_policy({"respect_retry_after": True,
                                    "retry_after_cap_seconds": 60.0})
    ignored = wc.effective_policy({"respect_retry_after": False,
                                   "retry_after_cap_seconds": 60.0})
    # Server asks for 48s: honoured path waits 48s, ignored path falls back to
    # exponential backoff (2s for the first retry) instead.
    assert honoured.backoff_for(1, retry_after=48.0) == 48.0
    assert ignored.backoff_for(1, retry_after=48.0) == 2.0


def test_max_retry_after_setting_caps_the_server_demand():
    policy = wc.effective_policy({"retry_after_cap_seconds": 5.0})
    assert policy.backoff_for(1, retry_after=600.0) == 5.0


def test_apply_policy_to_gate_preserves_gate_identity_and_counters():
    gate = WikipediaGate()
    gate.note_request()
    wc.apply_policy_to_gate(gate, {"max_retries": 3})
    assert gate.requests_made == 1, "counters must survive policy application"
    assert gate.policy.max_retries == 3


def test_no_hard_coded_runtime_override_beats_the_config():
    """A second application must not be silently overridden by a default."""
    wc.apply_effective_policy({"min_interval_seconds": 7.5})
    first = get_global_gate()
    wc.apply_effective_policy({"min_interval_seconds": 1.0})
    second = get_global_gate()
    assert first.policy.min_interval_seconds == 7.5
    assert second.policy.min_interval_seconds == 1.0


# --- SCOPE 5: diagnostics must show EFFECTIVE values -------------------------

def test_effective_settings_report_shows_runtime_gate_values():
    wc.apply_effective_policy({"min_interval_seconds": 2.5, "max_retries": 5})
    gate = get_global_gate()
    report = wc.effective_settings_report(use_gate_policy=True, gate=gate)
    assert report["request delay"] == "2.5 seconds"
    assert report["maximum retries"] == 5
    assert report["source"] == "gate"


def test_effective_settings_report_contains_every_required_line():
    report = wc.effective_settings_report({"min_interval_seconds": 1.0})
    for key in ("wikipedia enabled", "request delay", "retry on 429",
                "maximum retries", "honor Retry-After",
                "maximum Retry-After wait", "cache policy"):
        assert key in report


def test_effective_settings_report_reflects_disabled_state():
    report = wc.effective_settings_report({"enabled": False})
    assert report["wikipedia enabled"] is False


def test_cache_policy_is_reported_in_plain_language():
    assert "cached" in wc.effective_settings_report({"use_cache": True})["cache policy"]
    assert "fresh" in wc.effective_settings_report({"use_cache": False})["cache policy"]


# --- safety ceilings must stay library-owned --------------------------------

def test_operator_config_cannot_loosen_library_safety_ceilings():
    """Budget/backoff base are not operator knobs and keep their defaults."""
    policy = wc.effective_policy({"max_requests": 10 ** 9, "backoff_base_seconds": 99.0})
    base = WikipediaPolicy()
    assert policy.max_requests == base.max_requests
    assert policy.backoff_base_seconds == base.backoff_base_seconds


# --- stale/obsolete config keys must not break startup ----------------------

def test_unknown_and_removed_provider_keys_are_ignored():
    cfg = wc.WikipediaConfig.from_dict({
        "min_interval_seconds": 2.0,
        "hall_of_light_token": "legacy",
        "lemonamiga": {"enabled": True},
        "igdb_client_id": "legacy",
    })
    assert cfg.min_interval_seconds == 2.0
    assert "igdb_client_id" not in cfg.to_dict()