"""Ramping, short-press and long-press time of D2-01-03 dimmers as numbers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import (
    DEVICE_TIMES,
    DIMMER_TIMES,
    OpusGreenNetTimeNumber,
    async_setup_entry,
)
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

EAG_ID = "AABB0011"
# Step and minimum the number entities pass with every write.
STEPS = {
    time.parameter: {"step": time.step, "minimum": time.minimum}
    for time in DEVICE_TIMES
}
CONFIG_PATH = "/devices/DEV1/configuration"
FIXTURE = Path(__file__).parent / "fixtures" / "configuration_d2_01_03.json"

RAMPING, SHORT_PRESS, LONG_PRESS = DIMMER_TIMES[:3]


def _dimmer(eep: str = "D2-01-03") -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Dimmer", [{"eep": eep}])


def _document(**values: object) -> dict:
    return {
        "header": {"httpStatus": 200},
        "configuration": {
            "parameters": [
                {"key": key, "value": value} for key, value in values.items()
            ]
        },
    }


def _entry(coordinator) -> SimpleNamespace:
    return SimpleNamespace(
        data={"eag_id": EAG_ID},
        runtime_data=SimpleNamespace(
            coordinator=coordinator, gateway_device_id="gateway"
        ),
        async_on_unload=MagicMock(),
    )


async def _setup(hass: HomeAssistant, devices: dict) -> list:
    coordinator = MagicMock()
    coordinator.devices = devices
    added: list = []
    await async_setup_entry(
        hass, _entry(coordinator), lambda entities, *_a, **_k: added.extend(entities)
    )
    return added


# ── entities ───────────────────────────────────────────────────────────────


async def test_setup_creates_the_times_for_the_catalog_dimmer(
    hass: HomeAssistant,
) -> None:
    added = await _setup(hass, {"DEV1": _dimmer()})
    assert [entity.unique_id for entity in added] == [
        f"{EAG_ID}_DEV1_ramping_time",
        f"{EAG_ID}_DEV1_short_press_time",
        f"{EAG_ID}_DEV1_long_press_time",
    ]


async def test_no_dimmer_times_for_other_devices(hass: HomeAssistant) -> None:
    """The parameter catalog was read from a D2-01-03 only."""
    added = await _setup(
        hass,
        {
            "DIM": _dimmer("D2-01-02"),
            "SW": EnOceanDevice("SW", "Switch", [{"eep": "D2-01-01"}]),
            "SW2": EnOceanDevice("SW2", "Switch", [{"eep": "D2-01-11"}]),
            "SENSOR": EnOceanDevice("SENSOR", "Sensor", [{"eep": "A5-02-05"}]),
        },
    )
    # The switches have number entities of their own, but no dimmer times.
    switch_prefixes = (f"{EAG_ID}_SW_", f"{EAG_ID}_SW2_")
    unique_ids = [entity.unique_id for entity in added]
    assert [uid for uid in unique_ids if not uid.startswith(switch_prefixes)] == []
    assert not [
        uid
        for uid in unique_ids
        if uid.endswith(("_ramping_time", "_short_press_time", "_long_press_time"))
    ]


async def test_blinds_do_not_get_dimmer_times_and_dimmers_no_blind_times(
    hass: HomeAssistant,
) -> None:
    added = await _setup(
        hass,
        {
            "DEV1": _dimmer(),
            "BLIND": EnOceanDevice("BLIND", "Blind", [{"eep": "D2-05-02"}]),
        },
    )
    assert [entity.unique_id for entity in added] == [
        f"{EAG_ID}_DEV1_ramping_time",
        f"{EAG_ID}_DEV1_short_press_time",
        f"{EAG_ID}_DEV1_long_press_time",
        f"{EAG_ID}_BLIND_vertical_movement_time",
        f"{EAG_ID}_BLIND_rotation_time",
        f"{EAG_ID}_BLIND_max_holding_time",
    ]


@pytest.mark.parametrize(
    ("time", "key", "maximum", "step"),
    [
        (RAMPING, "ramping_time", 25.5, 0.1),
        (SHORT_PRESS, "short_press_time", 2.55, 0.01),
        (LONG_PRESS, "long_press_time", 2.55, 0.01),
    ],
)
def test_number_metadata_follows_the_gateway_parameter_catalog(
    time, key, maximum, step
) -> None:
    entity = OpusGreenNetTimeNumber(MagicMock(), EAG_ID, "gateway", _dimmer(), time)
    assert entity.unique_id == f"{EAG_ID}_DEV1_{key}"
    assert entity.translation_key == key
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.entity_registry_enabled_default is False
    assert entity.device_class is NumberDeviceClass.DURATION
    assert entity.mode is NumberMode.BOX
    assert (entity.native_min_value, entity.native_max_value) == (0, maximum)
    assert entity.native_step == step
    assert entity.native_unit_of_measurement == "s"


def test_value_follows_the_channel_fields() -> None:
    device = _dimmer()
    ramping, short, long = (
        OpusGreenNetTimeNumber(MagicMock(), EAG_ID, "gateway", device, time)
        for time in DIMMER_TIMES[:3]
    )
    assert ramping.native_value is None
    channel = device.get_or_create_channel()
    assert (ramping.native_value, short.native_value, long.native_value) == (
        None,
        None,
        None,
    )
    channel.ramping_time = 5.0
    channel.short_press_time = 0.03
    channel.long_press_time = 0
    assert ramping.native_value == 5.0
    assert short.native_value == 0.03
    assert long.native_value == 0


@pytest.mark.parametrize(
    ("time", "key"),
    [(RAMPING, "rampingTime"), (SHORT_PRESS, "shortPress"), (LONG_PRESS, "longPress")],
)
async def test_setting_a_value_writes_the_gateway_parameter(time, key) -> None:
    coordinator = MagicMock()
    coordinator.async_set_device_parameter = AsyncMock()
    entity = OpusGreenNetTimeNumber(coordinator, EAG_ID, "gateway", _dimmer(), time)
    await entity.async_set_native_value(0.5)
    coordinator.async_set_device_parameter.assert_awaited_once_with(
        "DEV1", key, 0.5, **STEPS[key]
    )


# ── data model ─────────────────────────────────────────────────────────────


def test_times_are_read_from_a_real_device_configuration() -> None:
    """The configuration of a D2-01-03 as the gateway delivers it."""
    data = json.loads(FIXTURE.read_text(encoding="utf8"))
    functions = protocol.configuration_state_functions(data)
    assert [(f["key"], f["value"]) for f in functions] == [
        ("rampingTime", 0.0),
        ("shortPress", 0.03),
        ("longPress", 0.0),
    ]
    # A refresh that is not authoritative still carries the settings.
    keys = {function["key"] for function in protocol.configuration_functions(data)}
    assert keys == {"rampingTime", "shortPress", "longPress"}
    device = _dimmer()
    device.update_from_telegram({"functions": functions})
    channel = device.channels[0]
    assert (
        channel.ramping_time,
        channel.short_press_time,
        channel.long_press_time,
    ) == (
        0.0,
        0.03,
        0.0,
    )


@pytest.mark.parametrize(
    ("key", "attribute", "value", "expected"),
    [
        ("rampingTime", "ramping_time", 0, 0.0),
        ("rampingTime", "ramping_time", "5.00", 5.0),
        ("rampingTime", "ramping_time", 25.5, 25.5),
        ("shortPress", "short_press_time", 0.03, 0.03),
        ("shortPress", "short_press_time", "2.55", 2.55),
        ("longPress", "long_press_time", 0, 0.0),
        ("longPress", "long_press_time", 2.55, 2.55),
    ],
)
def test_values_in_the_catalog_range_are_accepted(
    key, attribute, value, expected
) -> None:
    device = _dimmer()
    device.update_from_telegram({"functions": [{"key": key, "value": value}]})
    assert getattr(device.channels[0], attribute) == expected


@pytest.mark.parametrize(
    ("key", "attribute", "invalid"),
    [
        ("rampingTime", "ramping_time", 25.51),
        ("shortPress", "short_press_time", 2.56),
        ("longPress", "long_press_time", 2.56),
        ("rampingTime", "ramping_time", -0.1),
        ("shortPress", "short_press_time", None),
        ("longPress", "long_press_time", True),
        ("rampingTime", "ramping_time", ""),
        ("shortPress", "short_press_time", "off"),
        ("longPress", "long_press_time", "noChange"),
        ("rampingTime", "ramping_time", float("nan")),
        ("shortPress", "short_press_time", float("inf")),
    ],
)
def test_invalid_values_keep_the_known_one(key, attribute, invalid) -> None:
    device = _dimmer()
    setattr(device.get_or_create_channel(), attribute, 0.5)
    device.update_from_telegram({"functions": [{"key": key, "value": invalid}]})
    assert getattr(device.channels[0], attribute) == 0.5


@pytest.mark.parametrize("key", ["rampingTime", "shortPress", "longPress"])
def test_a_foreign_command_never_sets_a_dimmer_time(key) -> None:
    """Units of a command of another controller are not known; only reports count."""
    command = [{"key": key, "value": 1}]
    assert protocol.outbound_state_functions(command) == []
    assert not protocol.has_target_state(command)


def test_reading_a_setting_does_not_cancel_a_command_estimate() -> None:
    """A stored setting is no feedback; a brightness report is."""
    device = _dimmer()
    device.get_or_create_channel()
    snapshot = device.snapshot_channel_state(0)

    device.update_from_telegram(
        {
            "functions": [
                {"key": "rampingTime", "value": 5.0},
                {"key": "shortPress", "value": 0.1},
                {"key": "longPress", "value": 0.1},
            ]
        }
    )
    assert device.channel_state_unchanged(snapshot)

    device.update_from_telegram({"functions": [{"key": "dimValue", "value": 40}]})
    assert not device.channel_state_unchanged(snapshot)


# ── coordinator ────────────────────────────────────────────────────────────


async def test_write_is_adopted_after_the_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _dimmer()
    channel = device.get_or_create_channel()
    channel.ramping_time = 5.0
    radio.response = _document(rampingTime=2.5)

    await coord.async_set_device_parameter(
        "DEV1", "rampingTime", 2.5, **STEPS["rampingTime"]
    )

    assert radio.sent == [
        (
            "PUT",
            CONFIG_PATH,
            {"configuration": {"parameters": [{"key": "rampingTime", "value": 2.5}]}},
        ),
        ("GET", CONFIG_PATH, None),
    ]
    assert channel.ramping_time == 2.5


async def test_short_press_keeps_its_two_decimals(radio, connected_coordinator) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _dimmer()
    radio.response = _document(shortPress=0.05)

    await coord.async_set_device_parameter(
        "DEV1", "shortPress", 0.0500001, **STEPS["shortPress"]
    )

    assert radio.sent[0][2]["configuration"]["parameters"][0]["value"] == 0.05
    assert device.channels[0].short_press_time == 0.05


async def test_device_keeping_the_old_value_is_reported(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _dimmer()
    channel = device.get_or_create_channel()
    channel.long_press_time = 0.1
    radio.response = _document(longPress=0.1)

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_parameter(
            "DEV1", "longPress", 0.5, **STEPS["longPress"]
        )

    assert error.value.translation_key == "parameter_differs"
    assert channel.long_press_time == 0.1
    # Neither the write nor the read-back is repeated.
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]
