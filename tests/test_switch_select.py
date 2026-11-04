"""Operation mode, LED and external input function of D2-01 switches as selects."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.const import DEVICE_OPTIONS
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.select import (
    DEVICE_SELECTS,
    OpusGreenNetDeviceSelect,
    async_setup_entry,
    option_slug,
)
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

EAG_ID = "AABB0011"
CONFIG_PATH = "/devices/DEV1/configuration"
FIXTURES = Path(__file__).parent / "fixtures"

(
    OPERATION_MODE,
    LED_COLOR,
    LED_CHANNEL,
    _DIMMER_MODE,
    EXTERNAL_INPUT,
    ROUTING,
    REPEATER,
) = DEVICE_SELECTS
KEYS = (
    "operationMode",
    "ledColor",
    "ledChannel",
    "dimmerMode",
    "functionExternalInput",
    "routingTime",
    "repeaterState",
)


def _switch(eep: str = "D2-01-01", device_id: str = "DEV1") -> EnOceanDevice:
    return EnOceanDevice(device_id, "Switch", [{"eep": eep}])


def _select(
    select, device: EnOceanDevice | None = None, coordinator=None
) -> OpusGreenNetDeviceSelect:
    device = device or _switch("D2-01-11")
    values = select.values_for(device)
    assert values is not None
    return OpusGreenNetDeviceSelect(
        coordinator or MagicMock(), EAG_ID, "gateway", device, select, values
    )


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


@pytest.mark.parametrize(
    ("eep", "keys"),
    [
        (
            "D2-01-01",
            [
                "operation_mode",
                "led_color",
                "function_external_input",
                "routing",
                "repeater",
            ],
        ),
        (
            "D2-01-11",
            [
                "operation_mode",
                "led_color",
                "led_channel",
                "function_external_input",
                "routing",
                "repeater",
            ],
        ),
    ],
)
async def test_setup_creates_one_set_per_catalog_switch(
    hass: HomeAssistant, eep: str, keys: list[str]
) -> None:
    """The LED channel exists on 2-channel switches only."""
    added = await _setup(hass, {"DEV1": _switch(eep)})
    assert [entity.unique_id for entity in added] == [
        f"{EAG_ID}_DEV1_{key}" for key in keys
    ]


async def test_no_selects_for_other_devices(hass: HomeAssistant) -> None:
    """The parameter catalog was read from a D2-01-01 and a D2-01-11 only."""
    added = await _setup(
        hass,
        {
            "OTHER": _switch("D2-01-06", "OTHER"),
            "DIM": EnOceanDevice("DIM", "Dimmer", [{"eep": "D2-01-03"}]),
            "BLIND": EnOceanDevice("BLIND", "Blind", [{"eep": "D2-05-02"}]),
        },
    )
    # A dimmer reports the repeater mode only; a blind also lists ViNET routing.
    # Neither has the other switch settings.
    assert [entity.unique_id for entity in added] == [
        f"{EAG_ID}_DIM_repeater",
        f"{EAG_ID}_BLIND_routing",
        f"{EAG_ID}_BLIND_repeater",
    ]


def test_select_metadata() -> None:
    entity = _select(EXTERNAL_INPUT)
    assert isinstance(entity, SelectEntity)
    assert entity.unique_id == f"{EAG_ID}_DEV1_function_external_input"
    assert entity.translation_key == "function_external_input"
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.entity_registry_enabled_default is False
    assert entity.options == ["channel1", "channel2", "channels_all", "inactive"]


@pytest.mark.parametrize(
    ("select", "eep", "options"),
    [
        (OPERATION_MODE, "D2-01-01", ["bottom_on", "timer", "toggle", "top_on"]),
        (OPERATION_MODE, "D2-01-11", ["bottom_on", "timer", "toggle", "top_on"]),
        (LED_COLOR, "D2-01-01", ["all", "green", "off", "red"]),
        (LED_CHANNEL, "D2-01-11", ["channel1", "channel2"]),
        (EXTERNAL_INPUT, "D2-01-01", ["channel1", "inactive"]),
        (
            EXTERNAL_INPUT,
            "D2-01-11",
            ["channel1", "channel2", "channels_all", "inactive"],
        ),
    ],
)
def test_options_follow_the_gateway_parameter_catalog(select, eep, options) -> None:
    assert _select(select, _switch(eep)).options == options


@pytest.mark.parametrize(
    ("value", "slug"),
    [
        ("bottomOn", "bottom_on"),
        ("topOn", "top_on"),
        ("channelsAll", "channels_all"),
        ("channel1", "channel1"),
        ("toggle", "toggle"),
    ],
)
def test_option_names_are_slugs(value, slug) -> None:
    assert option_slug(value) == slug


def test_current_option_follows_the_channel_field() -> None:
    device = _switch("D2-01-11")
    mode = _select(OPERATION_MODE, device)
    external = _select(EXTERNAL_INPUT, device)
    assert mode.current_option is None
    channel = device.get_or_create_channel()
    assert mode.current_option is None
    channel.operation_mode = "bottomOn"
    channel.function_external_input = "channelsAll"
    assert mode.current_option == "bottom_on"
    assert external.current_option == "channels_all"


@pytest.mark.parametrize("raw", ["0", "unknownMode", "TOGGLE", ""])
def test_an_unlisted_value_is_unknown(raw) -> None:
    """A value outside the catalog is neither mapped nor guessed."""
    device = _switch("D2-01-01")
    device.get_or_create_channel().operation_mode = raw
    assert _select(OPERATION_MODE, device).current_option is None


def test_a_value_of_another_switch_type_is_unknown() -> None:
    """channel2 is listed for the 2-channel switch only."""
    device = _switch("D2-01-01")
    device.get_or_create_channel().function_external_input = "channel2"
    assert _select(EXTERNAL_INPUT, device).current_option is None


async def test_selecting_writes_the_gateway_value() -> None:
    coordinator = MagicMock()
    coordinator.async_set_device_option = AsyncMock()
    entity = _select(EXTERNAL_INPUT, coordinator=coordinator)
    await entity.async_select_option("channels_all")
    coordinator.async_set_device_option.assert_awaited_once_with(
        "DEV1", "functionExternalInput", "channelsAll"
    )


async def test_selecting_writes_the_camel_case_value() -> None:
    coordinator = MagicMock()
    coordinator.async_set_device_option = AsyncMock()
    await _select(OPERATION_MODE, coordinator=coordinator).async_select_option("top_on")
    coordinator.async_set_device_option.assert_awaited_once_with(
        "DEV1", "operationMode", "topOn"
    )


# ── data model ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "eep", "expected"),
    [
        ("d2_01_01", "D2-01-01", ("toggle", "off", None, "channel1")),
        ("d2_01_11", "D2-01-11", ("toggle", "green", "channel1", "channel2")),
    ],
)
def test_options_are_read_from_a_real_device_configuration(name, eep, expected) -> None:
    data = json.loads((FIXTURES / f"configuration_{name}.json").read_text("utf8"))
    functions = protocol.configuration_state_functions(data)
    reported = {function["key"] for function in functions}
    assert reported >= {"operationMode", "ledColor", "functionExternalInput"}
    # A refresh that is not authoritative carries the options as well.
    refreshed = {function["key"] for function in protocol.configuration_functions(data)}
    assert reported == refreshed
    device = _switch(eep)
    device.update_from_telegram({"functions": functions})
    assert list(device.channels) == [0]
    channel = device.channels[0]
    assert (
        channel.operation_mode,
        channel.led_color,
        channel.led_channel,
        channel.function_external_input,
    ) == expected


def test_a_raw_value_outside_the_catalog_is_kept_but_shown_as_unknown() -> None:
    """functionRockerA of a 1-channel switch reports the number 0 (E0)."""
    device = _switch()
    device.update_from_telegram(
        {"functions": [{"key": "functionExternalInput", "value": 0}]}
    )
    assert device.channels[0].function_external_input == "0"
    assert _select(EXTERNAL_INPUT, device).current_option is None


@pytest.mark.parametrize("invalid", [None, True, "", float("nan"), float("inf"), []])
def test_a_missing_or_malformed_value_keeps_the_known_one(invalid) -> None:
    device = _switch()
    device.get_or_create_channel().operation_mode = "timer"
    device.update_from_telegram(
        {"functions": [{"key": "operationMode", "value": invalid}]}
    )
    assert device.channels[0].operation_mode == "timer"


@pytest.mark.parametrize("key", KEYS)
def test_a_foreign_command_never_sets_an_option(key) -> None:
    """Only reports count; a command of another controller is no device state."""
    command = [{"key": key, "value": "toggle"}]
    assert protocol.outbound_state_functions(command) == []
    assert not protocol.has_target_state(command)


def test_reading_an_option_does_not_cancel_a_command_estimate() -> None:
    device = _switch()
    device.get_or_create_channel()
    snapshot = device.snapshot_channel_state(0)
    device.update_from_telegram(
        {"functions": [{"key": key, "value": "toggle"} for key in KEYS]}
    )
    assert device.channel_state_unchanged(snapshot)
    device.update_from_telegram({"functions": [{"key": "switch", "value": "on"}]})
    assert not device.channel_state_unchanged(snapshot)


def test_every_option_key_has_its_channel_field() -> None:
    assert set(DEVICE_OPTIONS) == set(KEYS)
    assert {select.parameter for select in DEVICE_SELECTS} == set(KEYS)
    for select in DEVICE_SELECTS:
        assert DEVICE_OPTIONS[select.parameter] == select.attribute


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (_document(operationMode="timer"), "timer"),
        (_document(operationMode=""), None),
        (_document(operationMode=3), None),
        (_document(ledColor="red"), None),
        ({"configuration": {"parameters": "x"}}, None),
        ({"configuration": {"parameters": ["x", None]}}, None),
        ({}, None),
    ],
)
def test_configuration_option(document, expected) -> None:
    assert protocol.configuration_option(document, "operationMode") == expected


# ── coordinator ────────────────────────────────────────────────────────────


async def test_option_write_is_adopted_after_the_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    channel = device.get_or_create_channel()
    channel.operation_mode = "toggle"
    radio.response = _document(operationMode="timer")

    await coord.async_set_device_option("DEV1", "operationMode", "timer")

    assert radio.sent == [
        (
            "PUT",
            CONFIG_PATH,
            {
                "configuration": {
                    "parameters": [{"key": "operationMode", "value": "timer"}]
                }
            },
        ),
        ("GET", CONFIG_PATH, None),
    ]
    assert channel.operation_mode == "timer"


async def test_device_keeping_the_old_option_is_reported(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    channel = device.get_or_create_channel()
    channel.operation_mode = "toggle"
    radio.response = _document(operationMode="toggle")

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "operationMode", "timer")

    assert error.value.translation_key == "parameter_differs"
    assert channel.operation_mode == "toggle"
    # Neither the write nor the read-back is repeated.
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


@pytest.mark.parametrize("stored", [{"header": {"httpStatus": 200}}, _document()])
async def test_missing_read_back_keeps_the_previous_option(
    radio, connected_coordinator, stored
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    channel = device.get_or_create_channel()
    channel.led_color = "off"
    radio.response = stored

    with pytest.raises(HomeAssistantError):
        await coord.async_set_device_option("DEV1", "ledColor", "red")

    assert channel.led_color == "off"
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


async def test_rejected_option_write_is_not_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _switch()
    radio.response = {"header": {"httpStatus": 400}}
    with pytest.raises(HomeAssistantError):
        await coord.async_set_device_option("DEV1", "ledColor", "red")
    assert [method for method, _, _ in radio.sent] == ["PUT"]


async def test_unknown_device_sends_nothing(radio, connected_coordinator) -> None:
    with pytest.raises(HomeAssistantError) as error:
        await connected_coordinator.async_set_device_option(
            "GONE", "operationMode", "timer"
        )
    assert error.value.translation_key == "device_not_found"
    assert radio.sent == []
