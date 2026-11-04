"""Settings entities and writes follow the keys a device reports.

A device lists exactly the settings it has in its gateway configuration. The
numbers, selects and sensors of those settings are only created for such keys,
and a write of any other key is refused before it is sent. Until a
configuration was seen, the EEP decides as before.
"""

from __future__ import annotations

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import (
    DEVICE_TIMES,
    PRESENCE_SETTINGS,
)
from custom_components.opus_greennet.select import DEVICE_SELECTS
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

SWITCH_KEYS = frozenset({"operationMode", "timer", "ledColor", "routingTime"})


def _switch(reported: frozenset[str] | None = None) -> EnOceanDevice:
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])
    device.reported_settings = reported
    return device


def _number_keys(device: EnOceanDevice) -> set[str]:
    return {
        t.parameter for t in (*DEVICE_TIMES, *PRESENCE_SETTINGS) if t.applies_to(device)
    }


def _select_keys(device: EnOceanDevice) -> set[str]:
    return {s.parameter for s in DEVICE_SELECTS if s.values_for(device) is not None}


# ── reading the keys ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, None),
        ({"configuration": None}, None),
        ({"configuration": {"lastUpdateTime": "x"}}, None),  # no parameter list
        ({"configuration": {"parameters": []}}, frozenset()),
        (
            {
                "configuration": {
                    "parameters": [
                        {"key": "timer", "value": 5},
                        {"key": "functionRockerA", "value": 0},  # any value counts
                        {"value": 1},  # no key
                        "junk",
                        {"key": 3},  # key not text
                    ]
                }
            },
            frozenset({"timer", "functionRockerA"}),
        ),
    ],
)
def test_the_reported_keys_are_read(data, expected) -> None:
    assert protocol.configuration_keys(data) == expected


# ── entities ───────────────────────────────────────────────────────────────


def test_without_a_configuration_the_eep_decides() -> None:
    device = _switch(None)
    assert device.reports_setting("anything")
    assert {"timer", "timerLong"} <= _number_keys(device)
    assert {"operationMode", "ledColor"} <= _select_keys(device)


def test_only_reported_settings_get_entities() -> None:
    device = _switch(SWITCH_KEYS)
    assert _number_keys(device) == {"timer"}
    assert _select_keys(device) == {
        "operationMode",
        "ledColor",
        "routingTime",
        "repeaterState",  # a device resource, not a configuration key
    }


def test_a_device_that_reports_nothing_gets_no_setting_entities() -> None:
    device = _switch(frozenset())
    assert _number_keys(device) == set()
    assert _select_keys(device) == {"repeaterState"}


def test_a_presence_sensor_gets_only_what_it_reports() -> None:
    device = EnOceanDevice("DEV1", "SMS", [{"eep": "A5-07-03"}])
    device.reported_settings = frozenset({"aliveCycle", "pirOffTime"})
    assert _number_keys(device) == {"aliveCycle", "pirOffTime"}
    device.reported_settings = frozenset()  # e.g. a device with no settings
    assert _number_keys(device) == set()


def test_a_dimmer_gets_the_settings_of_its_model_and_its_keys() -> None:
    old = EnOceanDevice("DEV1", "D", [{"eep": "D2-01-03"}], product_id="004000000003")
    old.reported_settings = frozenset({"rampingTime", "shortPress", "longPress"})
    assert _number_keys(old) == {"rampingTime", "shortPress", "longPress"}
    universal = EnOceanDevice(
        "DEV2", "U", [{"eep": "D2-01-03"}], product_id="00400000001E"
    )
    universal.reported_settings = frozenset({"lowerLimit", "upperLimit", "dimmerMode"})
    # The model decides which set, the keys narrow it to what is reported.
    assert _number_keys(universal) == {"lowerLimit", "upperLimit"}
    assert "dimmerMode" in _select_keys(universal)


# ── lists and reads ────────────────────────────────────────────────────────


def test_a_list_with_a_configuration_replaces_the_keys_and_one_without_keeps_them():
    device = _switch(frozenset({"timer"}))
    device.apply_listed_metadata(_switch(None))
    assert device.reported_settings == frozenset({"timer"})
    device.apply_listed_metadata(_switch(frozenset({"timer", "ledColor"})))
    assert device.reported_settings == frozenset({"timer", "ledColor"})
    device.apply_listed_metadata(_switch(frozenset()))
    assert device.reported_settings == frozenset()


def test_a_device_list_object_carries_its_keys(connected_coordinator) -> None:
    coordinator = connected_coordinator
    coordinator._apply_device_object(  # noqa: SLF001
        {
            "deviceId": "DEV9",
            "eeps": [{"eep": "D2-01-01"}],
            "configuration": {"parameters": [{"key": "ledColor", "value": "off"}]},
        },
        authoritative=True,
    )
    assert coordinator.devices["DEV9"].reported_settings == frozenset({"ledColor"})


# ── writes ─────────────────────────────────────────────────────────────────


async def test_a_number_the_device_lacks_is_not_written(
    radio, connected_coordinator
) -> None:
    connected_coordinator.devices["DEV1"] = _switch(frozenset({"ledColor"}))

    with pytest.raises(ServiceValidationError) as raised:
        await connected_coordinator.async_set_device_parameter(
            "DEV1", "timer", 600, step=1, minimum=0
        )

    assert raised.value.translation_key == "setting_not_reported"
    assert raised.value.translation_placeholders == {
        "device_id": "DEV1",
        "key": "timer",
    }
    assert radio.sent == []


@pytest.mark.parametrize("key", ["ledColor", "routingTime"])
async def test_a_text_setting_the_device_lacks_is_not_written(
    radio, connected_coordinator, key
) -> None:
    connected_coordinator.devices["DEV1"] = _switch(frozenset({"timer"}))

    with pytest.raises(ServiceValidationError) as raised:
        await connected_coordinator.async_set_device_option("DEV1", key, "off")

    assert raised.value.translation_key == "setting_not_reported"
    assert radio.sent == []


async def test_a_reported_setting_is_still_written(
    radio, connected_coordinator
) -> None:
    connected_coordinator.devices["DEV1"] = _switch(frozenset({"timer"}))
    radio.response = {
        "header": {"httpStatus": 200},
        "configuration": {"parameters": [{"key": "timer", "value": 600.0}]},
    }

    await connected_coordinator.async_set_device_parameter(
        "DEV1", "timer", 600, step=1, minimum=0
    )

    assert radio.sent
