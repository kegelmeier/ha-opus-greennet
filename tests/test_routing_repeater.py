"""Routing (off/on), repeater mode of D2-01 switches and whole-number display."""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfTime
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.const import ROUTING_ON_SECONDS
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import DEVICE_TIMES
from custom_components.opus_greennet.sensor import OpusGreenNetSettingSensor
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

CONFIG_PATH = "/devices/DEV1/configuration"
REPEATER_PATH = "/devices/DEV1/repeaterState"


def _switch() -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])


def _document(**values: object) -> dict:
    return {
        "header": {"httpStatus": 200},
        "configuration": {
            "parameters": [{"key": k, "value": v} for k, v in values.items()]
        },
    }


# ── protocol and model ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("off", "off"),
        (0.3, "on"),
        (2.55, "on"),
        ("1.5", "on"),
        (0, None),
        (-1, None),
        ("on", "on"),
        (None, None),
        (True, None),
    ],
)
def test_routing_is_reported_as_off_or_on(value, expected) -> None:
    assert EnOceanDevice.routing_reading(value) == expected
    assert protocol.configuration_routing(_document(routingTime=value)) == expected


def test_a_reported_routing_time_is_stored_as_on() -> None:
    device = _switch()
    device.update_from_telegram(
        {"functions": [{"channel": 0, "key": "routingTime", "value": 0.5}]}
    )
    assert device.channels[0].routing == "on"
    # An unusable value keeps the known one.
    device.update_from_telegram(
        {"functions": [{"channel": 0, "key": "routingTime", "value": "x"}]}
    )
    assert device.channels[0].routing == "on"


def test_the_configuration_read_normalises_routing() -> None:
    functions = protocol.configuration_state_functions(
        _document(routingTime=0.3, timer=300)
    )
    assert {"channel": 0, "key": "routingTime", "value": "on"} in functions


def test_repeater_state_function() -> None:
    assert protocol.repeater_state_function({"repeaterState": "level_2"}) == {
        "channel": 0,
        "key": "repeaterState",
        "value": "level_2",
    }
    assert protocol.repeater_state_function({"repeaterState": ""}) is None
    assert protocol.repeater_state_function({}) is None


# ── writing routing ────────────────────────────────────────────────────────


async def test_routing_on_is_written_as_the_smallest_time_and_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().routing = "off"
    radio.response = _document(routingTime=ROUTING_ON_SECONDS)

    await coord.async_set_device_option("DEV1", "routingTime", "on")

    assert radio.sent == [
        (
            "PUT",
            CONFIG_PATH,
            {
                "configuration": {
                    "parameters": [{"key": "routingTime", "value": ROUTING_ON_SECONDS}]
                }
            },
        ),
        ("GET", CONFIG_PATH, None),
    ]
    assert device.channels[0].routing == "on"


async def test_routing_off_is_written_as_text(radio, connected_coordinator) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().routing = "on"
    radio.response = _document(routingTime="off")

    await coord.async_set_device_option("DEV1", "routingTime", "off")

    assert radio.sent[0][2]["configuration"]["parameters"] == [
        {"key": "routingTime", "value": "off"}
    ]
    assert device.channels[0].routing == "off"


async def test_routing_already_in_the_wanted_state_sends_nothing(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().routing = "off"

    await coord.async_set_device_option("DEV1", "routingTime", "off")

    assert radio.sent == []


async def test_unconfirmed_routing_keeps_the_previous_state(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().routing = "off"
    radio.response = _document(routingTime="off")

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "routingTime", "on")

    assert error.value.translation_key == "parameter_not_confirmed"
    assert device.channels[0].routing == "off"
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


# ── repeater mode ──────────────────────────────────────────────────────────


async def test_repeater_mode_is_written_and_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().repeater = "off"
    radio.response = {"header": {"httpStatus": 200}, "repeaterState": "level_2"}

    await coord.async_set_device_option("DEV1", "repeaterState", "level_2")

    assert radio.sent == [
        ("PUT", REPEATER_PATH, {"repeaterState": "level_2"}),
        ("GET", REPEATER_PATH, None),
    ]
    assert device.channels[0].repeater == "level_2"


async def test_unconfirmed_repeater_mode_is_not_adopted_or_repeated(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().repeater = "off"
    radio.response = {"header": {"httpStatus": 200}, "repeaterState": "off"}

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "repeaterState", "level_1")

    assert error.value.translation_key == "parameter_not_confirmed"
    assert device.channels[0].repeater == "off"
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


async def test_rejected_repeater_write_is_not_read_back(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _switch()
    radio.response = {"header": {"httpStatus": 400}}

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "repeaterState", "level_1")

    assert error.value.translation_key == "parameter_write_failed"
    assert [method for method, _, _ in radio.sent] == ["PUT"]


async def test_read_settings_also_reads_routing_and_repeater_mode(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel()
    responses = {
        CONFIG_PATH: _document(routingTime="off", timer=300),
        REPEATER_PATH: {"header": {"httpStatus": 200}, "repeaterState": "level_2"},
    }
    original = radio._request

    async def routed(method, path, body, timeout):
        radio.response = responses[path]
        return await original(method, path, body, timeout)

    radio._request = routed

    await coord.async_reread_device_settings("DEV1")

    assert [(m, p) for m, p, _ in radio.sent] == [
        ("GET", CONFIG_PATH),
        ("GET", REPEATER_PATH),
    ]
    channel = device.channels[0]
    assert (channel.routing, channel.repeater, channel.timer) == (
        "off",
        "level_2",
        300,
    )


async def test_a_failing_repeater_read_keeps_the_other_settings(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().repeater = "level_1"
    original = radio._request

    async def routed(method, path, body, timeout):
        radio.response = (
            {"header": {"httpStatus": 500}}
            if path == REPEATER_PATH
            else _document(timer=300)
        )
        return await original(method, path, body, timeout)

    radio._request = routed

    await coord.async_reread_device_settings("DEV1")

    assert device.channels[0].timer == 300
    assert device.channels[0].repeater == "level_1"


# ── display ────────────────────────────────────────────────────────────────


def _sensor(translation_key: str) -> OpusGreenNetSettingSensor:
    from unittest.mock import MagicMock

    time = next(t for t in DEVICE_TIMES if t.translation_key == translation_key)
    return OpusGreenNetSettingSensor(
        MagicMock(), "AABB0011", "gateway", _switch(), time
    )


@pytest.mark.parametrize("key", ["switch_off_warning", "illumination_threshold"])
def test_switch_setting_sensors_show_whole_numbers(key: str) -> None:
    assert _sensor(key).suggested_display_precision == 0


@pytest.mark.parametrize("key", ["timer", "timer_long"])
def test_the_timer_durations_show_whole_seconds(key: str) -> None:
    sensor = _sensor(key)
    assert sensor.suggested_display_precision == 0
    assert sensor.suggested_unit_of_measurement is None


def test_the_warning_signal_shows_one_decimal_in_seconds() -> None:
    sensor = _sensor("switch_off_warning_signal")
    assert sensor.device_class is SensorDeviceClass.DURATION
    assert sensor.native_unit_of_measurement == UnitOfTime.SECONDS
    # No suggested unit: it would scale the precision of existing entities.
    assert sensor.suggested_unit_of_measurement is None
    assert sensor.suggested_display_precision == 1


def test_whole_values_are_not_floats() -> None:
    sensor = _sensor("timer")
    sensor._device.get_or_create_channel().timer = 300.0
    assert sensor.native_value == 300
    assert isinstance(sensor.native_value, int)
    sensor._device.channels[0].timer = 90.5
    assert sensor.native_value == 90.5


def test_blind_setting_sensors_keep_their_decimals() -> None:
    time = next(
        t for t in DEVICE_TIMES if t.translation_key == "vertical_movement_time"
    )
    from unittest.mock import MagicMock

    blind = EnOceanDevice("DEV1", "Blind", [{"eep": "D2-05-02"}])
    sensor = OpusGreenNetSettingSensor(MagicMock(), "AABB0011", "gateway", blind, time)
    assert sensor.suggested_display_precision is None


# ── repeater mode in the device list ───────────────────────────────────────


def test_the_device_list_carries_the_repeater_mode() -> None:
    """Shown after a restart without pressing Read settings (no radio request)."""
    device = EnOceanDevice("DEV1", "Dimmer", [{"eep": "D2-01-03"}])
    data = {"deviceId": "DEV1", "repeaterState": "level_1", "states": []}
    device.update_from_telegram({"functions": protocol.device_state_functions(data)})
    assert device.channels[0].repeater == "level_1"


def test_a_device_list_without_the_repeater_mode_keeps_the_known_one() -> None:
    device = _switch()
    device.get_or_create_channel().repeater = "level_2"
    data = {"deviceId": "DEV1", "states": [{"key": "switch", "value": "on"}]}
    device.update_from_telegram({"functions": protocol.device_state_functions(data)})
    assert device.channels[0].repeater == "level_2"


def test_the_repeater_mode_is_offered_for_dimmers_too() -> None:
    from custom_components.opus_greennet.select import DEVICE_SELECTS

    (REPEATER,) = (s for s in DEVICE_SELECTS if s.parameter == "repeaterState")
    dimmer = EnOceanDevice("DIM", "Dimmer", [{"eep": "D2-01-03"}])
    assert REPEATER.values_for(dimmer) == ("off", "level_1", "level_2")
    assert protocol.supports_repeater_state(dimmer)


def test_no_routing_select_is_offered_for_dimmers() -> None:
    # The gateway lists no routing time for a D2-01-03 (read on 2026-10-31,
    # firmware 1.21.30), although the OPUS config tool can set ViNET there.
    from custom_components.opus_greennet.select import DEVICE_SELECTS

    (ROUTING,) = (s for s in DEVICE_SELECTS if s.parameter == "routingTime")
    dimmer = EnOceanDevice("DIM", "Dimmer", [{"eep": "D2-01-03"}])
    assert ROUTING.values_for(dimmer) is None


# ── device information ─────────────────────────────────────────────────────


def _info(device: EnOceanDevice):
    from unittest.mock import MagicMock

    from custom_components.opus_greennet.entity import OpusGreenNetEntity

    return OpusGreenNetEntity(MagicMock(), "AABB0011", "gateway", device).device_info


def test_device_information_has_product_id_and_software_version_no_serial() -> None:
    device = EnOceanDevice.from_device_object(
        {
            "deviceId": "019D5EC7",
            "friendlyId": "Bad",
            "productId": "004000000005",
            "softwareRevision": "0.1.0.0",
            "hardwareRevision": "0.24.17.20",
        }
    )
    info = _info(device)
    assert info["hw_version"] == "0.24.17.20"
    assert info["model_id"] == "004000000005"
    assert info["sw_version"] == "0.1.0.0"
    # The device ID is also shown as the serial number.
    assert info["serial_number"] == "019D5EC7"


def test_device_information_without_the_new_fields() -> None:
    info = _info(_switch())
    assert info["model_id"] is None
    assert info["sw_version"] is None
    assert info["hw_version"] is None


def test_a_list_without_product_data_keeps_the_known_values() -> None:
    device = EnOceanDevice.from_device_object(
        {
            "deviceId": "D",
            "productId": "1",
            "softwareRevision": "2",
            "hardwareRevision": "5",
        }
    )
    device.apply_listed_metadata(EnOceanDevice.from_device_object({"deviceId": "D"}))
    assert (device.product_id, device.software_revision) == ("1", "2")
    assert device.hardware_revision == "5"
    device.apply_listed_metadata(
        EnOceanDevice.from_device_object(
            {"deviceId": "D", "productId": "3", "softwareRevision": "4"}
        )
    )
    assert (device.product_id, device.software_revision) == ("3", "4")


def test_the_device_id_is_a_diagnostic_sensor_disabled_by_default() -> None:
    from unittest.mock import MagicMock

    from homeassistant.const import EntityCategory

    from custom_components.opus_greennet.sensor import OpusGreenNetDeviceIdSensor

    device = EnOceanDevice("019D6415", "Switch", [{"eep": "D2-01-01"}])
    sensor = OpusGreenNetDeviceIdSensor(MagicMock(), "AABB0011", "gw", device)
    assert sensor.native_value == "019D6415"
    assert sensor.unique_id == "AABB0011_019D6415_device_id"
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is False
