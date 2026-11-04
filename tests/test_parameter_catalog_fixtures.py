"""Parameter catalogs and configurations read from a real gateway.

The files in ``tests/fixtures`` are the ``GET /devices/{id}/parameters`` and
``GET /devices/{id}/configuration`` documents of one device per actuator type
(gateway firmware 1.21.30), anonymized: device ID ``DEV1``, name ``Test``, no
timestamps. They keep the gateway's order, spelling and oddities, which the
settings entities have to cope with.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import DEVICE_TIMES
from custom_components.opus_greennet.select import CATALOG_SELECTS, EXTRA_SELECTS

FIXTURES = Path(__file__).parent / "fixtures"
TYPES = ("d2_05_02", "d2_01_01", "d2_01_11", "d2_01_03", "d2_01_03_fw1_21_34")

_SWITCH_KEYS = {
    "functionExternalInput",
    "functionRockerA",
    "functionRockerB",
    "illuminationThreshold",
    "ledColor",
    "operationMode",
    "routingTime",
    "switchOffWarning",
    "switchOffWarningSignal",
    "timer",
    "timerLong",
}
CATALOG_KEYS = {
    "d2_05_02": {
        "functionRockerA",
        "functionRockerB",
        "lastVerticalMovementTime",
        "maxHoldingTime",
        "rotationTime",
        "routingTime",
        "verticalMovementTime",
    },
    "d2_01_01": _SWITCH_KEYS,
    "d2_01_11": _SWITCH_KEYS | {"ledChannel"},
    "d2_01_03": {"longPress", "rampingTime", "shortPress"},
    # The same type with another parameter set (two dimmers, gateway 1.21.34).
    "d2_01_03_fw1_21_34": {
        "dimmerMode",
        "functionRockerA",
        "functionRockerB",
        "ledColor",
        "lowerLimit",
        "rampingTimeRocker1",
        "rampingTimeRocker2",
        "rampingTimeRocker3",
        "rampingTimeSwitch",
        "routingTime",
        "upperLimit",
    },
}
# Parameters without a unit in their catalog range (percent, per the manual).
UNITLESS = {"lowerLimit", "upperLimit"}
# Configured values the catalog does not list (the device reports a raw value).
UNLISTED_VALUES = {("d2_01_01", "functionRockerA"): 0}


def _load(kind: str, name: str) -> dict:
    return json.loads((FIXTURES / f"{kind}_{name}.json").read_text(encoding="utf8"))


def _catalog(name: str) -> dict[str, dict]:
    return {entry["key"]: entry for entry in _load("parameters", name)["parameters"]}


def _configuration(name: str) -> list[dict]:
    return _load("configuration", name)["configuration"]["parameters"]


@pytest.mark.parametrize("name", TYPES)
def test_catalog_lists_the_parameters_of_the_type(name: str) -> None:
    catalog = _catalog(name)
    assert set(catalog) == CATALOG_KEYS[name]
    for entry in catalog.values():
        assert entry["description"]
        assert entry["values"], entry["key"]


@pytest.mark.parametrize("name", TYPES)
def test_every_configured_key_is_in_the_catalog(name: str) -> None:
    catalog = _catalog(name)
    keys = [parameter["key"] for parameter in _configuration(name)]
    assert len(keys) == len(set(keys))
    assert set(keys) <= set(catalog)


@pytest.mark.parametrize("name", TYPES)
def test_numeric_ranges_and_defaults_are_consistent(name: str) -> None:
    for key, entry in _catalog(name).items():
        ranges = [item["range"] for item in entry["values"] if "range" in item]
        for item in ranges:
            assert 0 <= item["min"] <= item["max"], key
            assert item["step"] > 0, key
            assert item["unit"] or key in UNITLESS, key
        default = entry["defaultValue"]
        listed = [item["value"] for item in entry["values"] if "value" in item]
        if (name, key) in UNLISTED_VALUES:
            assert default not in listed
        elif ranges and isinstance(default, int | float):
            assert any(item["min"] <= default <= item["max"] for item in ranges), key
        else:
            assert default in listed, key


def _on_step(value: float, minimum: float, step: float) -> bool:
    steps = (value - minimum) / step
    return abs(steps - round(steps)) < 1e-6


@pytest.mark.parametrize("name", TYPES)
def test_configured_values_follow_the_catalog(name: str) -> None:
    catalog = _catalog(name)
    for parameter in _configuration(name):
        key, value = parameter["key"], parameter["value"]
        values = catalog[key]["values"]
        listed = [item["value"] for item in values if "value" in item]
        ranges = [item["range"] for item in values if "range" in item]
        if (name, key) in UNLISTED_VALUES:
            assert value == UNLISTED_VALUES[name, key]
            assert value not in listed
        elif value in listed:
            continue
        else:
            assert isinstance(value, int | float), (key, value)
            assert any(
                item["min"] <= value <= item["max"]
                and _on_step(value, item["min"], item["step"])
                for item in ranges
            ), (key, value)


def test_catalog_oddities_are_kept() -> None:
    blind = _catalog("d2_05_02")
    values = [item["value"] for item in blind["functionRockerA"]["values"]]
    assert values.count("inactive") == 3
    switch = _catalog("d2_01_01")
    # A numeric default and a numeric configured value next to a text list.
    assert switch["functionRockerA"]["defaultValue"] == 3
    assert "meaning" not in next(
        item for item in _configuration("d2_01_01") if item["key"] == "functionRockerA"
    )
    # routingTime is a number or the word off.
    routing = switch["routingTime"]["values"]
    assert {"range", "value"} <= {key for item in routing for key in item}
    assert [item["value"] for item in routing if "value" in item] == ["off"]
    # A step finer than the two decimals the write path rounds to.
    assert switch["switchOffWarningSignal"]["values"][0]["range"]["step"] == 0.001
    assert _catalog("d2_01_11")["ledChannel"]["defaultValue"] == "channel1"


@pytest.mark.parametrize("kind", ("parameters", "configuration"))
@pytest.mark.parametrize("name", TYPES)
def test_fixtures_are_anonymized(kind: str, name: str) -> None:
    text = (FIXTURES / f"{kind}_{name}.json").read_text(encoding="utf8")
    document = json.loads(text)
    assert set(document["header"]) == {"httpStatus", "content"}
    assert document["header"]["content"] == kind
    # No 8-digit hexadecimal device IDs, addresses or time stamps.
    assert not re.search(r"\b[0-9A-Fa-f]{8}\b", text)
    assert not re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|\d{4}-\d{2}-\d{2}", text)
    if kind == "configuration":
        assert document["configuration"]["deviceId"] == "DEV1"
        assert document["configuration"]["friendlyId"] == "Test"


def test_blind_configuration_feeds_the_cover_model() -> None:
    document = _load("configuration", "d2_05_02")
    assert protocol.configuration_parameter(document, "verticalMovementTime") == 25.51
    assert protocol.configuration_parameter(document, "rotationTime") == 0.0
    functions = protocol.configuration_state_functions(document)
    assert {"verticalMovementTime", "rotationTime"} <= {f["key"] for f in functions}
    device = EnOceanDevice("DEV1", "Test", [{"eep": "D2-05-02"}])
    device.update_from_telegram({"functions": functions})
    channel = device.channels[0]
    assert (channel.vertical_movement_time, channel.rotation_time) == (25.51, 0.0)
    # A rotation time of zero means a roller shutter without tilt.
    assert device.supports_tilt is False


# ── Entity coverage of the catalogs ──────────────────────────────────────────

EEPS = {
    "d2_05_02": "D2-05-02",
    "d2_01_01": "D2-01-01",
    "d2_01_11": "D2-01-11",
    "d2_01_03": "D2-01-03",
    "d2_01_03_fw1_21_34": "D2-01-03",
}
# Both D2-01-03 catalogs belong to one EEP; the product ID of the device decides
# which parameter set a dimmer gets (the universal dimmer 563.076 reports the
# second one).
PRODUCT_IDS = {"d2_01_03_fw1_21_34": "00400000001E"}
# Catalog parameters without an entity, each with the reason. A new parameter
# in a catalog fails the coverage test until it gets an entity or is listed here.
_SWITCH_DEFERRED = {
    "functionRockerA": "raw value 0 is outside the options; needs a text write test",
    "functionRockerB": "raw value 0 is outside the options; needs a text write test",
}
DEFERRED = {
    "d2_05_02": {
        "functionRockerA": "same options as the switches; needs a text write test",
        "functionRockerB": "same options as the switches; needs a text write test",
        "lastVerticalMovementTime": "read only; shown in the diagnostics",
    },
    "d2_01_01": _SWITCH_DEFERRED,
    "d2_01_11": _SWITCH_DEFERRED,
    "d2_01_03": {},
    "d2_01_03_fw1_21_34": {
        "functionRockerA": "same options as the switches; needs a text write test",
        "functionRockerB": "same options as the switches; needs a text write test",
        "ledColor": "the dimmer lists no 'all' option; no select for dimmers yet",
        "routingTime": "unit (10 ms) and the value for 'on' are not verified",
    },
}


def _device(name: str) -> EnOceanDevice:
    return EnOceanDevice(
        "DEV1", "Test", [{"eep": EEPS[name]}], product_id=PRODUCT_IDS.get(name, "")
    )


def _entity_parameters(name: str) -> set[str]:
    device = _device(name)
    numbers = {time.parameter for time in DEVICE_TIMES if time.applies_to(device)}
    selects = {
        select.parameter
        for select in (*CATALOG_SELECTS, *EXTRA_SELECTS)
        if select.values_for(device) is not None
        and select.parameter != "repeaterState"  # a device resource, no parameter
    }
    return numbers | selects


@pytest.mark.parametrize("name", TYPES)
def test_every_catalog_parameter_has_an_entity_or_a_reason(name: str) -> None:
    catalog = set(_catalog(name))
    covered = _entity_parameters(name)
    deferred = set(DEFERRED[name])
    assert not covered - catalog, "an entity for a parameter the catalog lacks"
    assert not covered & deferred, "listed as deferred, but it has an entity"
    assert not deferred - catalog, "deferred entry for a parameter the catalog lacks"
    assert catalog == covered | deferred
    assert all(DEFERRED[name].values())


@pytest.mark.parametrize("name", TYPES)
def test_number_entities_follow_the_catalog_ranges(name: str) -> None:
    device = _device(name)
    catalog = _catalog(name)
    for time in DEVICE_TIMES:
        if not time.applies_to(device):
            continue
        (rng,) = (
            item["range"]
            for item in catalog[time.parameter]["values"]
            if "range" in item
        )
        assert (time.minimum, time.maximum, time.step) == (
            rng["min"],
            rng["max"],
            rng["step"],
        ), time.parameter


@pytest.mark.parametrize("name", TYPES)
def test_select_entities_offer_the_catalog_values(name: str) -> None:
    device = _device(name)
    catalog = _catalog(name)
    for select in CATALOG_SELECTS:
        values = select.values_for(device)
        if values is None:
            continue
        listed = {
            item["value"]
            for item in catalog[select.parameter]["values"]
            if "value" in item
        }
        assert set(values) == listed, select.parameter
