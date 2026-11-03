"""``readings.py`` against the profiles of the gateway catalog.

``tests/fixtures/profiles_read_only.json`` holds the reported functions of the
profiles as the gateway described them (``GET /profiles/<EEP>``, catalog, no
radio request). The tests keep the table honest: every key, range and value in
``readings.py`` must exist in the profile, and every reported key the table does
not map must be named here with a reason. A firmware that changes a profile
then fails a test instead of showing wrong values.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.opus_greennet.const import EEP_MAPPINGS, KNOWN_STATE_KEYS
from custom_components.opus_greennet.readings import CHANNEL_READINGS, READINGS

FIXTURE = Path(__file__).parent / "fixtures/profiles_read_only.json"
PROFILES = json.loads(FIXTURE.read_text(encoding="utf-8"))["profiles"]

# Reported keys that readings.py leaves out on purpose.
NOT_MAPPED: dict[str, set[str]] = {
    # Two meanings of "temperature" (room and panel surface), contradicting
    # "presence" text.
    "D2-35-00": {"temperature", "presence"},
    # Display values of the panel or values the panel echoes.
    "D2-11-01": {"setpointOffset", "telegramType", "validTempCorrection"},
    # The raw telegram is not shown.
    "D1-4B-10": {"payload"},
    # The channel selector itself; the per-channel values are in CHANNEL_READINGS.
    "D2-34-10": {"channel"},
    # Climate values of the heat area, held in typed channel fields.
    "D1-4B-04": {
        "heaterMode",
        "temperature",
        "temperatureSetpoint",
        "valvePosition",
        "heatCircuitsError",
    },
}
# Profiles with a dedicated model (climate and its typed channel fields): every
# reported key must be a known state key.
TYPED_PROFILES = ("D1-4B-03", "D1-4B-04")


def _specs(eep: str) -> tuple:
    """Every reading of a profile: of the device and per channel."""
    return (*READINGS.get(eep, ()), *CHANNEL_READINGS.get(eep, ()))


def _reported(eep: str) -> dict:
    return PROFILES[eep]["from"]


def _profile_values(entry: dict) -> set[str]:
    return set(entry.get("values", []))


@pytest.mark.parametrize("eep", sorted(READINGS))
def test_every_reading_matches_the_profile(eep: str) -> None:
    reported = _reported(eep)
    for spec in _specs(eep):
        assert spec.key in reported, (eep, spec.key)
        entry = reported[spec.key]
        values = _profile_values(entry)
        where = (eep, spec.key)
        if spec.kind == "number":
            assert (spec.minimum, spec.maximum) == (entry["min"], entry["max"]), where
            if spec.unit is not None and "unit" in entry:
                assert spec.unit == entry["unit"], where
            # Every "no reading" marker of the profile is handled.
            assert values <= set(spec.unavailable), where
            assert set(spec.unavailable) - {"notAvailable"} <= values, where
        elif spec.kind == "enum":
            assert values <= set(spec.options) | set(spec.unavailable), where
            assert set(spec.options) <= values, where
        elif spec.kind == "bool":
            handled = set(spec.true_values) | set(spec.false_values)
            assert values <= handled | set(spec.unavailable), where
            assert values & set(spec.true_values), where
            assert values & set(spec.false_values), where


@pytest.mark.parametrize("eep", sorted(READINGS))
def test_every_reported_key_is_mapped_or_named(eep: str) -> None:
    mapped = {spec.key for spec in _specs(eep)}
    assert set(_reported(eep)) - mapped == NOT_MAPPED.get(eep, set()), eep


@pytest.mark.parametrize("eep", TYPED_PROFILES)
def test_typed_profiles_know_every_reported_key(eep: str) -> None:
    mapped = {spec.key for spec in READINGS.get(eep, ())}
    for key in _reported(eep):
        assert key in KNOWN_STATE_KEYS | mapped, (eep, key)


def test_channel_readings_belong_to_profiles_of_the_table() -> None:
    assert set(CHANNEL_READINGS) <= set(READINGS)


def test_every_fixture_profile_is_a_supported_eep() -> None:
    for eep in PROFILES:
        assert eep in EEP_MAPPINGS, eep
    assert set(READINGS) <= set(PROFILES)
