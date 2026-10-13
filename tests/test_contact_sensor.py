"""D5-00-01 contact readings from gateway snapshots and live MQTT traffic."""

import asyncio
import json

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN, EEP_MAPPINGS, KNOWN_STATE_KEYS
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from tests.ha_helpers import configure_bridge, wait_for_entity

EEP = "D5-00-01"


async def wait_for_state(hass, entity_id, expected):
    """Wait for fragment buffering or background discovery to reach HA state."""
    async with asyncio.timeout(6):
        while (state := hass.states.get(entity_id)) is None or state.state != expected:
            await asyncio.sleep(0.01)
            await hass.async_block_till_done()


def test_contact_profile_is_discovered(make_device):
    device = make_device(EEP)
    assert EEP_MAPPINGS[EEP][0] == "binary_sensor"
    assert "contact" in KNOWN_STATE_KEYS
    assert device.is_contact_sensor
    assert not device.is_climate


@pytest.mark.parametrize("value,expected", [("open", True), ("closed", False)])
def test_contact_state_matches_the_gateway(make_device, value, expected):
    device = make_device(EEP)
    device.update_from_telegram({"functions": [{"key": "contact", "value": value}]})
    assert device.channels[0].contact is expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "unknown",
        "notAvailable",
        "on",
        "off",
        "true",
        "false",
        True,
        False,
        0,
        1,
        [],
        {},
    ],
)
def test_unrecognized_contact_values_become_unknown(make_device, value):
    device = make_device(EEP)
    device.update_from_telegram({"functions": [{"key": "contact", "value": "open"}]})
    device.update_from_telegram({"functions": [{"key": "contact", "value": value}]})
    assert device.channels[0].contact is None


@pytest.mark.parametrize("eep", ["F6-05-01", "F6-10-00", "D1-4B-05", "D2-01-01"])
def test_contact_state_is_scoped_to_its_profile(make_device, eep):
    device = make_device(eep)
    device.update_from_telegram({"functions": [{"key": "contact", "value": "open"}]})
    assert device.channels[0].contact is None


@pytest.mark.parametrize(
    "states,expected",
    [
        ({"contact": "closed"}, False),
        ([{"key": "contact", "value": "open"}], True),
        ([{"key": "contact", "value": "notAvailable"}], None),
    ],
)
def test_gateway_snapshot_applies_contact_state(make_device, states, expected):
    device = make_device(EEP)
    functions = OpusGreenNetCoordinator._device_state_functions(
        {"eeps": [{"eep": EEP}], "states": states}
    )
    device.update_from_telegram({"functions": functions})
    assert device.channels[0].contact is expected


@pytest.mark.parametrize("initial", [None, "closed", "open", "unknown"])
@pytest.mark.parametrize("message_format", ["json", "flattened"])
async def test_contact_entity_tracks_live_telegrams(
    hass, mqtt_transport, initial, message_format
):
    definition = {
        "deviceId": "CONTACT1",
        "friendlyId": "Window",
        "eeps": [{"eep": EEP}],
        "batteryLevel": None,
        "transmitModes": [{"key": "contact", "transmitOnEvent": True}],
    }
    if initial is not None:
        definition["states"] = [{"key": "contact", "value": initial}]
    mqtt_transport.devices = [definition]
    await configure_bridge(hass)
    contact = await wait_for_entity(hass, "binary_sensor", "AABB0011_CONTACT1_contact")
    expected = {"open": "on", "closed": "off"}.get(initial, "unknown")
    state = hass.states.get(contact)
    assert state.state == expected
    assert state.attributes["device_class"] == "opening"
    registry = er.async_get(hass)
    entries = [
        entry
        for entry in registry.entities.values()
        if entry.platform == DOMAIN and entry.unique_id.startswith("AABB0011_CONTACT1_")
    ]
    assert [
        entry.entity_id for entry in entries if entry.domain == "binary_sensor"
    ] == [contact]
    assert not any("battery" in entry.unique_id for entry in entries)

    for value, expected in [
        ("open", "on"),
        ("closed", "off"),
        ("notAvailable", "unknown"),
    ]:
        topic = "EnOcean/AABB0011/stream/telegram/CONTACT1"
        if message_format == "json":
            mqtt_transport.receive(
                topic,
                {
                    "telegramInfo": {"rorg": "D5"},
                    "functions": [{"key": "contact", "value": value}],
                },
            )
        else:
            mqtt_transport.receive(f"{topic}/functions/0/key", "contact")
            mqtt_transport.receive(f"{topic}/functions/0/value", value)
        await wait_for_state(hass, contact, expected)
        assert hass.states.get(contact).state == expected
    await mqtt_transport.set_connected(False)
    assert hass.states.get(contact).state == "unavailable"


async def test_contact_id_survives_reload_and_device_stream_updates(
    hass, mqtt_transport
):
    mqtt_transport.devices = [
        {
            "deviceId": "CONTACT1",
            "eeps": [{"eep": EEP}],
            "states": {"contact": "closed"},
        }
    ]
    entry = (await configure_bridge(hass))["result"]
    contact = await wait_for_entity(hass, "binary_sensor", "AABB0011_CONTACT1_contact")
    assert hass.states.get(contact).state == "off"
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert (
        await wait_for_entity(hass, "binary_sensor", "AABB0011_CONTACT1_contact")
        == contact
    )
    await wait_for_state(hass, contact, "off")
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/device/CONTACT1/states/contact",
        json.dumps("open"),
    )
    await wait_for_state(hass, contact, "on")
    assert hass.states.get(contact).state == "on"
