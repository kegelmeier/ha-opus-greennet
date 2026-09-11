"""Tests for the OpusGreenNetEvent entity (rocker switch events)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.opus_greennet.const import BUTTON_KEYS
from custom_components.opus_greennet.coordinator import (
    SIGNAL_DEVICE_STATE_UPDATE,
)
from custom_components.opus_greennet.enocean_device import EnOceanChannel, EnOceanDevice
from custom_components.opus_greennet.event import EVENT_TYPES, OpusGreenNetEvent
from tests.ha_helpers import configure_bridge, wait_for_entity


@pytest.fixture
def rocker_device():
    return EnOceanDevice(
        device_id="ROCKER1",
        friendly_id="Living Room Rocker",
        eeps=[{"eep": "F6-02-01"}],
    )


@pytest.fixture
def event_entity(rocker_device):
    entity = OpusGreenNetEvent(
        coordinator=MagicMock(),
        eag_id="AABB0011",
        gateway_device_id="gateway-device-id",
        device=rocker_device,
    )
    entity._trigger_event = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


def test_event_types_cover_all_button_action_combinations():
    expected = {f"{b}_{a}" for b in BUTTON_KEYS for a in ("pressed", "released")}
    assert set(EVENT_TYPES) == expected
    assert len(EVENT_TYPES) == len(expected)


@pytest.mark.parametrize(
    "button,action",
    [
        ("buttonA0", "pressed"),
        ("buttonA0", "released"),
        ("buttonAI", "pressed"),
        ("buttonB0", "released"),
        ("buttonBI", "pressed"),
        ("multipleButtons", "pressed"),
    ],
)
def test_fires_event_for_each_button_action(
    event_entity, rocker_device, button, action
):
    rocker_device.channels[0] = EnOceanChannel(
        channel_id=0,
        last_button=button,
        last_button_action=action,
    )

    event_entity._handle_state_update(rocker_device)

    event_entity._trigger_event.assert_called_once_with(
        f"{button}_{action}", {"button": button, "action": action}
    )
    event_entity.async_write_ha_state.assert_called_once()


def test_no_event_when_button_fields_unset(event_entity, rocker_device):
    rocker_device.channels[0] = EnOceanChannel(channel_id=0)

    event_entity._handle_state_update(rocker_device)

    event_entity._trigger_event.assert_not_called()
    event_entity.async_write_ha_state.assert_not_called()


async def test_availability_changes_do_not_replay_last_rocker_event(
    hass, mqtt_transport
):
    mqtt_transport.devices = [
        {"deviceId": "ROCKER1", "friendlyId": "Test", "eeps": [{"eep": "F6-02-01"}]}
    ]
    result = await configure_bridge(hass)
    entity_id = await wait_for_entity(hass, "event", "AABB0011_ROCKER1")
    coordinator = result["result"].runtime_data.coordinator
    rocker_device = coordinator.get_device("ROCKER1")
    rocker_device.channels[0] = EnOceanChannel(
        channel_id=0, last_button="buttonA0", last_button_action="pressed"
    )
    async_dispatcher_send(
        hass, f"{SIGNAL_DEVICE_STATE_UPDATE}_AABB0011_ROCKER1", rocker_device
    )
    await hass.async_block_till_done()
    original = hass.states.get(entity_id)
    assert original.attributes["event_type"] == "buttonA0_pressed"

    await mqtt_transport.set_connected(False)
    assert hass.states.get(entity_id).state == "unavailable"

    await mqtt_transport.set_connected(True)
    restored = hass.states.get(entity_id)
    assert restored.state == original.state
    assert restored.attributes["event_type"] == "buttonA0_pressed"


def test_no_event_when_no_channel(event_entity, rocker_device):
    rocker_device.channels.clear()

    event_entity._handle_state_update(rocker_device)

    event_entity._trigger_event.assert_not_called()


def test_no_event_for_unknown_button_action(event_entity, rocker_device):
    rocker_device.channels[0] = EnOceanChannel(
        channel_id=0,
        last_button="buttonA0",
        last_button_action="held",  # not in pressed/released
    )

    event_entity._handle_state_update(rocker_device)

    event_entity._trigger_event.assert_not_called()
