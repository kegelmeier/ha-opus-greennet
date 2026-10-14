"""OPUS motion detector (Jaeger Direkt 004000000009): F6-02-01 + A5-04-01.

The gateway announces the detector with two profiles. Motion arrives as rocker
telegrams: ``buttonAI`` pressed while motion is detected, ``buttonA0`` pressed
once the follow-up time has expired. Temperature and humidity arrive as A5-04-01.
The payloads below were captured from a real OPUS-GW v1.21.34.
"""

import asyncio
import json

import pytest

from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.ha_helpers import configure_bridge, wait_for_entity

MOTION_EEPS = [
    {"eep": "F6-02-01", "version": 0.9, "direction": "from"},
    {"eep": "A5-04-01", "version": 0.9, "direction": "from"},
]


def motion_device() -> EnOceanDevice:
    return EnOceanDevice("050E7680", "Motion01", [dict(e) for e in MOTION_EEPS])


def press(device: EnOceanDevice, button: str, action: str = "pressed") -> None:
    device.update_from_telegram({"functions": [{"key": button, "value": action}]})


def test_profile_combination_is_a_motion_detector():
    assert motion_device().is_opus_motion_detector is True


@pytest.mark.parametrize(
    "eeps",
    [
        [{"eep": "F6-02-01"}],
        [{"eep": "F6-03-01"}, {"eep": "A5-04-01"}],
        [{"eep": "A5-04-01"}],
        [{"eep": "D2-01-03"}, {"eep": "F6-02-01"}],
    ],
)
def test_other_profiles_are_not_motion_detectors(eeps):
    assert EnOceanDevice("DEV1", "Other", eeps).is_opus_motion_detector is False


def test_ai_press_reports_motion():
    device = motion_device()
    press(device, "buttonAI")
    assert device.channels[0].motion is True


def test_a0_press_reports_no_motion():
    device = motion_device()
    press(device, "buttonAI")
    press(device, "buttonA0")
    assert device.channels[0].motion is False


@pytest.mark.parametrize("button", ["buttonAI", "buttonA0"])
def test_release_telegrams_keep_the_motion_state(button):
    """F6 release telegrams carry no button information."""
    device = motion_device()
    press(device, "buttonAI")
    press(device, button, "released")
    assert device.channels[0].motion is True


def test_rocker_events_are_still_reported():
    device = motion_device()
    press(device, "buttonAI")
    assert device.channels[0].last_button == "buttonAI"
    assert device.channels[0].last_button_action == "pressed"


def test_plain_rocker_presses_do_not_report_motion():
    device = EnOceanDevice("DEV1", "Rocker", [{"eep": "F6-02-01"}])
    press(device, "buttonAI")
    assert device.channels[0].motion is None


def test_motion_detector_reports_temperature_and_humidity():
    device = motion_device()
    assert device.is_temperature_humidity_sensor is True
    device.update_from_telegram(
        {
            "functions": [
                {"key": "humidity", "value": 59.6, "unit": "%"},
                {"key": "temperature", "value": 16.96, "unit": "°C"},
            ]
        }
    )
    assert device.channels[0].temperature == 16.96
    assert device.channels[0].humidity == 59.6


@pytest.mark.parametrize(
    "eep,inside,outside",
    [
        ("A5-04-01", (0, 40), (-0.1, 40.1)),
        ("A5-04-02", (-20, 60), (-20.1, 60.1)),
        ("A5-04-03", (-20, 60), (-20.1, 60.1)),
    ],
)
def test_temperature_range_follows_the_a5_04_profile(eep, inside, outside):
    device = EnOceanDevice("DEV1", "Sensor", [{"eep": eep}])
    assert device.is_temperature_humidity_sensor is True
    for value in inside:
        device.update_from_telegram(
            {"functions": [{"key": "temperature", "value": value}]}
        )
        assert device.channels[0].temperature == value
    for value in outside:
        device.update_from_telegram(
            {"functions": [{"key": "temperature", "value": value}]}
        )
        assert device.channels[0].temperature == inside[-1]


async def test_entities_are_created_and_follow_motion(hass, mqtt_transport):
    mqtt_transport.devices = [
        {
            "deviceId": "MOT1",
            "friendlyId": "Motion01",
            "eeps": [dict(e) for e in MOTION_EEPS],
            "productId": "004000000009",
            "manufacturer": "Jaeger Direkt",
            "batteryLevel": "58%",
            "states": [
                {"key": "humidity", "value": 59.6, "unit": "%"},
                {"key": "temperature", "value": 16.96, "unit": "°C"},
            ],
        }
    ]
    await configure_bridge(hass)
    motion = await wait_for_entity(hass, "binary_sensor", "AABB0011_MOT1_motion")
    temperature = await wait_for_entity(hass, "sensor", "AABB0011_MOT1_temperature")
    humidity = await wait_for_entity(hass, "sensor", "AABB0011_MOT1_humidity")
    battery = await wait_for_entity(hass, "sensor", "AABB0011_MOT1_battery_level")
    # Existing rocker automations keep working.
    await wait_for_entity(hass, "event", "AABB0011_MOT1")

    assert hass.states.get(motion).attributes["device_class"] == "motion"
    assert float(hass.states.get(temperature).state) == 16.96
    assert float(hass.states.get(humidity).state) == 59.6
    assert float(hass.states.get(battery).state) == 58

    for button, expected in (("buttonAI", "on"), ("buttonA0", "off")):
        mqtt_transport.receive(
            "EnOcean/AABB0011/stream/telegram/MOT1/from",
            json.dumps({"functions": [{"key": button, "value": "pressed"}]}),
        )
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
        assert hass.states.get(motion).state == expected
