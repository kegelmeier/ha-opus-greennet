"""Read-only profiles from the gateway catalog (see ``readings.py``).

Profiles: A5-08-01, A5-20-06, A5-30-03, D2-11-01, D2-14-41, D2-14-54, D2-32-00,
D2-32-02, D2-34-10, D2-35-00 and D1-4B-00/-01/-02/-04/-10/-15/-50.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.opus_greennet.binary_sensor_descriptions import (
    BINARY_SENSOR_DESCRIPTIONS,
)
from custom_components.opus_greennet.climate import OpusGreenNetClimate
from custom_components.opus_greennet.const import EEP_MAPPINGS
from custom_components.opus_greennet.readings import READINGS, ReadingSpec
from custom_components.opus_greennet.select import option_slug
from custom_components.opus_greennet.sensor import (
    OpusGreenNetReadingSensor,
)
from custom_components.opus_greennet.sensor import (
    async_setup_entry as async_setup_sensors,
)

COMPONENT = Path("custom_components/opus_greennet")
ALL_SPECS = [(eep, spec) for eep, specs in READINGS.items() for spec in specs]
NUMBERS = [(eep, spec) for eep, spec in ALL_SPECS if spec.kind == "number"]


def _id(item: tuple[str, ReadingSpec]) -> str:
    return f"{item[0]}:{item[1].key}"


def _sample(spec: ReadingSpec) -> object:
    """A valid gateway value for a reading."""
    if spec.kind == "number":
        assert spec.minimum is not None and spec.maximum is not None
        return str(spec.maximum)
    if spec.kind == "enum":
        return spec.options[0]
    if spec.kind == "bool":
        return spec.true_values[0]
    return "value-1"


def test_every_profile_is_mapped() -> None:
    for eep in READINGS:
        assert eep in EEP_MAPPINGS
    for eep in (
        "A5-08-01",
        "A5-20-06",
        "A5-30-03",
        "D2-11-01",
        "D2-14-41",
        "D2-14-54",
        "D2-32-00",
        "D2-32-02",
        "D2-34-10",
        "D2-35-00",
        "D1-4B-00",
        "D1-4B-01",
        "D1-4B-02",
        "D1-4B-04",
        "D1-4B-10",
        "D1-4B-15",
        "D1-4B-50",
    ):
        assert eep in EEP_MAPPINGS


def test_keys_and_suffixes_are_unique_per_profile() -> None:
    for eep, specs in READINGS.items():
        assert len({spec.key for spec in specs}) == len(specs), eep
        assert len({spec.suffix for spec in specs}) == len(specs), eep


def test_translations_cover_every_reading() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    german = json.loads(
        (COMPONENT / "translations/de.json").read_text(encoding="utf-8")
    )
    assert strings == json.loads(
        (COMPONENT / "translations/en.json").read_text(encoding="utf-8")
    )
    for catalog in (strings, german):
        for _eep, spec in ALL_SPECS:
            domain = "binary_sensor" if spec.kind == "bool" else "sensor"
            entry = catalog["entity"][domain][spec.suffix]
            assert entry["name"]
            if spec.kind == "enum":
                assert {option_slug(o) for o in spec.options} <= set(entry["state"])


@pytest.mark.parametrize("item", ALL_SPECS, ids=_id)
def test_valid_value_is_stored(make_device, make_telegram, item) -> None:
    eep, spec = item
    device = make_device(eep)

    device.update_from_telegram(
        make_telegram([{"key": spec.key, "value": _sample(spec)}])
    )

    stored = device.channels[0].readings[spec.key]
    if spec.kind == "number":
        assert stored == spec.maximum
    elif spec.kind == "bool":
        assert stored is True
    elif spec.kind == "enum":
        assert stored == spec.options[0]
    else:
        assert stored == "value-1"


@pytest.mark.parametrize("item", NUMBERS, ids=_id)
def test_number_out_of_range_or_malformed_keeps_value(
    make_device, make_telegram, item
) -> None:
    eep, spec = item
    assert spec.minimum is not None and spec.maximum is not None
    device = make_device(eep)
    device.update_from_telegram(
        make_telegram([{"key": spec.key, "value": str(spec.minimum)}])
    )

    for bad in (str(spec.maximum + 1), str(spec.minimum - 1), "abc", None, True, {}):
        device.update_from_telegram(make_telegram([{"key": spec.key, "value": bad}]))
        assert device.channels[0].readings[spec.key] == spec.minimum


@pytest.mark.parametrize(
    "item",
    [pair for pair in ALL_SPECS if pair[1].unavailable],
    ids=_id,
)
def test_listed_unavailable_value_clears_the_reading(
    make_device, make_telegram, item
) -> None:
    eep, spec = item
    device = make_device(eep)
    device.update_from_telegram(
        make_telegram([{"key": spec.key, "value": _sample(spec)}])
    )

    for marker in spec.unavailable:
        device.update_from_telegram(
            make_telegram([{"key": spec.key, "value": _sample(spec)}])
        )
        device.update_from_telegram(make_telegram([{"key": spec.key, "value": marker}]))
        assert device.channels[0].readings[spec.key] is None


@pytest.mark.parametrize(
    "item",
    [pair for pair in ALL_SPECS if pair[1].kind in ("enum", "bool", "text")],
    ids=_id,
)
def test_unlisted_value_keeps_the_known_one(make_device, make_telegram, item) -> None:
    eep, spec = item
    device = make_device(eep)
    device.update_from_telegram(
        make_telegram([{"key": spec.key, "value": _sample(spec)}])
    )
    known = device.channels[0].readings[spec.key]

    # Free text takes any non-empty string; the other kinds only listed values.
    bad_values = ["", None, 1, {}, [], True]
    if spec.kind != "text":
        bad_values.append("noChange")
    for bad in bad_values:
        device.update_from_telegram(make_telegram([{"key": spec.key, "value": bad}]))
        assert device.channels[0].readings[spec.key] == known


def test_values_of_other_profiles_are_not_taken_over(
    make_device, make_telegram
) -> None:
    device = make_device("D2-35-00")
    device.update_from_telegram(
        make_telegram(
            [
                # Documented with two meanings or contradicting text: not mapped.
                {"key": "presence", "value": "occupied"},
                {"key": "power", "value": "850"},
            ]
        )
    )

    assert "presence" not in device.channels[0].readings
    assert device.channels[0].readings["power"] == 850


def test_wide_temperature_range_of_the_multisensor(make_device, make_telegram) -> None:
    """The generic temperature parser would reject values below 0 °C."""
    device = make_device("D2-14-41")
    device.update_from_telegram(
        make_telegram([{"key": "temperature", "value": "-12.5"}])
    )

    assert device.channels[0].readings["temperature"] == -12.5
    assert device.channels[0].temperature is None


def test_leak_sensor_values(make_device, make_telegram) -> None:
    device = make_device("D2-14-54")
    device.update_from_telegram(
        make_telegram(
            [
                {"key": "liquidDetected", "value": "true"},
                {"key": "pastLeakDetection", "value": "false"},
                {"key": "humidity", "value": "notSupported"},
                {"key": "temperature", "value": "21.3"},
            ]
        )
    )
    readings = device.channels[0].readings

    assert readings["liquidDetected"] is True
    assert readings["pastLeakDetection"] is False
    assert readings["humidity"] is None
    assert readings["temperature"] == 21.3


def _entities(hass, device):
    coordinator = MagicMock(
        eag_id="AABB0011", devices={device.device_id: device}, available=True
    )
    entry = SimpleNamespace(
        data={"eag_id": "AABB0011"},
        runtime_data=SimpleNamespace(coordinator=coordinator, gateway_device_id="gw"),
        entry_id="entry",
        async_on_unload=MagicMock(),
    )
    add = MagicMock()
    return entry, add


@pytest.mark.parametrize("eep", sorted(READINGS))
async def test_sensor_platform_creates_the_readings(hass, make_device, eep) -> None:
    device = make_device(eep)
    entry, add = _entities(hass, device)

    await async_setup_sensors(hass, entry, add)

    suffixes = {
        entity.unique_id.removeprefix("AABB0011_AABB1122_")
        for call in add.call_args_list
        for entity in call.args[0]
    }
    expected = {spec.suffix for spec in READINGS[eep] if spec.kind != "bool"}
    assert expected <= suffixes


@pytest.mark.parametrize("eep", sorted(READINGS))
def test_binary_descriptions_apply_to_the_profile_only(make_device, eep) -> None:
    device = make_device(eep)
    other = make_device("F6-05-02")
    keys = {d.key for d in BINARY_SENSOR_DESCRIPTIONS if d.applies_to(device)}
    expected = {spec.suffix for spec in READINGS[eep] if spec.kind == "bool"}

    assert expected <= keys
    assert not any(
        d.key in expected for d in BINARY_SENSOR_DESCRIPTIONS if d.applies_to(other)
    )


def test_binary_reading_states(make_device, make_telegram) -> None:
    device = make_device("A5-30-03")
    description = next(
        d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == "digital_input_2"
    )

    assert description.value_fn(device) is None
    device.update_from_telegram(
        make_telegram([{"key": "digitalInput2", "value": "high"}])
    )
    assert description.value_fn(device) is True
    device.update_from_telegram(
        make_telegram([{"key": "digitalInput2", "value": "low"}])
    )
    assert description.value_fn(device) is False


def test_reading_sensor_metadata(make_device, make_telegram) -> None:
    device = make_device("D2-14-54")
    spec = next(s for s in READINGS["D2-14-54"] if s.key == "temperature")
    sensor = OpusGreenNetReadingSensor(MagicMock(), "AABB0011", "gw", device, spec)

    assert sensor.native_value is None
    device.update_from_telegram(
        make_telegram([{"key": "temperature", "value": "19.5"}])
    )
    assert sensor.native_value == 19.5
    assert sensor.device_class is SensorDeviceClass.TEMPERATURE
    assert sensor.unique_id == "AABB0011_AABB1122_temperature"


def test_enum_sensor_uses_option_slugs(make_device, make_telegram) -> None:
    device = make_device("D2-34-10")
    spec = next(s for s in READINGS["D2-34-10"] if s.key == "error")
    sensor = OpusGreenNetReadingSensor(MagicMock(), "AABB0011", "gw", device, spec)

    assert sensor.device_class is SensorDeviceClass.ENUM
    assert sensor.options == ["general_error", "heat_circuit_error", "no_error"]
    device.update_from_telegram(
        make_telegram([{"key": "error", "value": "heatCircuitError"}])
    )
    assert sensor.native_value == "heat_circuit_error"


def test_heat_area_3_virtual_is_a_read_only_climate(make_device, make_telegram) -> None:
    device = make_device("D1-4B-04")
    device.update_from_telegram(
        make_telegram(
            [
                {"key": "heaterMode", "value": "cooling"},
                {"key": "temperature", "value": "24.1"},
                {"key": "temperatureSetpoint", "value": "23.0"},
                {"key": "valvePosition", "value": "20"},
                {"key": "missingData", "value": "info"},
            ]
        )
    )
    entity = OpusGreenNetClimate(MagicMock(), "AABB0011", "gw", device)
    descriptions = {d.key for d in BINARY_SENSOR_DESCRIPTIONS if d.applies_to(device)}

    assert device.entity_type == "climate"
    assert (entity.min_temp, entity.max_temp) == (5, 30)
    assert entity.target_temperature_step == 0.2
    assert entity.hvac_mode is HVACMode.COOL
    assert entity.current_temperature == 24.1
    assert descriptions == {"heat_circuits_error", "data_mismatch", "missing_data"}
    assert device.channels[0].readings["missingData"] is True


def test_icons_belong_to_translated_entities_and_real_states() -> None:
    icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))["entity"]
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))[
        "entity"
    ]
    for domain in ("sensor", "binary_sensor"):
        for suffix in icons.get(domain, {}):
            assert suffix in strings[domain], (domain, suffix)
    options = {
        spec.suffix: {option_slug(o) for o in spec.options}
        for _eep, spec in ALL_SPECS
        if spec.kind == "enum"
    }
    for suffix, entry in icons["sensor"].items():
        if suffix in options and "state" in entry:
            assert set(entry["state"]) <= options[suffix], suffix
    for suffix, entry in icons["binary_sensor"].items():
        if "state" in entry and any(
            spec.suffix == suffix and spec.kind == "bool" for _e, spec in ALL_SPECS
        ):
            assert set(entry["state"]) <= {"on", "off"}, suffix
