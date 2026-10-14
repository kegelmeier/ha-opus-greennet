"""A5-04-03 temperature and humidity sensor support."""

import asyncio

import pytest

from custom_components.opus_greennet.const import EEP_MAPPINGS
from tests.ha_helpers import configure_bridge, wait_for_entity

EEP = "A5-04-03"


def test_profile_is_mapped_as_sensor(make_device):
    device = make_device(EEP)
    assert EEP_MAPPINGS[EEP][0] == "sensor"
    assert device.entity_type == "sensor"
    assert device.is_temperature_humidity_sensor is True
    # It is not a heating profile and must not pick up climate behaviour.
    assert device.is_climate is False


def test_other_profiles_are_not_temperature_humidity_sensors(make_device):
    for eep in ("D1-4B-05", "A5-07-03", "F6-05-01"):
        assert make_device(eep).is_temperature_humidity_sensor is False


def test_gateway_telegram_updates_temperature_and_humidity(make_device):
    """Telegram taken from a real gateway: key order is humidity, temperature."""
    device = make_device(EEP)
    device.update_from_telegram(
        {
            "functions": [
                {"key": "humidity", "value": "49.02", "unit": "%"},
                {"key": "temperature", "value": "23.17", "unit": "°C"},
                {"key": "trigger", "value": "heartbeat"},
            ]
        }
    )
    channel = device.channels[0]
    assert channel.temperature == 23.17
    assert channel.humidity == 49.02


@pytest.mark.parametrize("value", [-20, -5.5, 0, 23.17, 59.9, 60])
def test_temperature_range_covers_the_full_profile_range(make_device, value):
    device = make_device(EEP)
    device.update_from_telegram({"functions": [{"key": "temperature", "value": value}]})
    assert device.channels[0].temperature == value


@pytest.mark.parametrize("value", [-20.1, 60.1, "nan", "inf", "bad", True, []])
def test_temperature_outside_range_is_ignored(make_device, value):
    device = make_device(EEP)
    device.update_from_telegram({"functions": [{"key": "temperature", "value": 21.5}]})
    device.update_from_telegram({"functions": [{"key": "temperature", "value": value}]})
    assert device.channels[0].temperature == 21.5


def test_not_available_clears_temperature(make_device):
    device = make_device(EEP)
    device.update_from_telegram({"functions": [{"key": "temperature", "value": 21.5}]})
    device.update_from_telegram(
        {"functions": [{"key": "temperature", "value": "notAvailable"}]}
    )
    assert device.channels[0].temperature is None


@pytest.mark.parametrize("eep", ["D1-4B-05", "D1-4B-06", "D1-4B-07"])
def test_heating_profiles_keep_their_temperature_range(make_device, eep):
    """The wider A5-04-03 range must not leak into the heating profiles."""
    device = make_device(eep)
    device.update_from_telegram({"functions": [{"key": "temperature", "value": 21.5}]})
    for value in (-5, 45):
        device.update_from_telegram(
            {"functions": [{"key": "temperature", "value": value}]}
        )
        assert device.channels[0].temperature == 21.5


@pytest.mark.parametrize(
    "value,expected", [("90%", 90), (100, 100), ("notAvailable", None)]
)
def test_battery_level(make_device, value, expected):
    device = make_device(EEP)
    device.update_from_telegram({"functions": [{"key": "batteryLevel", "value": 50}]})
    device.update_from_telegram(
        {"functions": [{"key": "batteryLevel", "value": value}]}
    )
    assert device.channels[0].battery_level == expected


async def test_entities_are_created_and_updated(hass, mqtt_transport):
    mqtt_transport.devices = [
        {
            "deviceId": "THS1",
            "friendlyId": "Living room",
            "eeps": [{"eep": EEP}],
            "batteryLevel": "90%",
            "states": [
                {"key": "humidity", "value": 49.02},
                {"key": "temperature", "value": 23.32},
                {"key": "trigger", "value": "heartbeat"},
            ],
        }
    ]
    await configure_bridge(hass)
    temperature = await wait_for_entity(hass, "sensor", "AABB0011_THS1_temperature")
    humidity = await wait_for_entity(hass, "sensor", "AABB0011_THS1_humidity")
    battery = await wait_for_entity(hass, "sensor", "AABB0011_THS1_battery_level")

    assert float(hass.states.get(temperature).state) == 23.32
    assert hass.states.get(temperature).attributes["unit_of_measurement"] == "°C"
    assert hass.states.get(temperature).attributes["device_class"] == "temperature"
    assert float(hass.states.get(humidity).state) == 49.02
    assert hass.states.get(humidity).attributes["unit_of_measurement"] == "%"
    assert float(hass.states.get(battery).state) == 90

    mqtt_transport.receive("EnOcean/AABB0011/stream/device/THS1/batteryLevel", "85")
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert float(hass.states.get(battery).state) == 85
