"""Tests for the OpusGreenNetEvent entity (rocker switch events)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.event import EventDeviceClass
from homeassistant.core import HomeAssistant

from custom_components.opus_greennet.const import BUTTON_KEYS
from custom_components.opus_greennet.enocean_device import EnOceanChannel, EnOceanDevice
from custom_components.opus_greennet.event import EVENT_TYPES, OpusGreenNetEvent
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until


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


@pytest.mark.parametrize("eep", ["F6-02-01", "F6-03-01"])
def test_rocker_event_is_a_button_with_unchanged_metadata(eep: str) -> None:
    entity = OpusGreenNetEvent(
        coordinator=MagicMock(),
        eag_id="AABB0011",
        gateway_device_id="gateway-device-id",
        device=EnOceanDevice(
            device_id="ROCKER1", friendly_id="Rocker", eeps=[{"eep": eep}]
        ),
    )

    assert entity.device_class is EventDeviceClass.BUTTON
    assert entity.unique_id == "AABB0011_ROCKER1"
    assert entity.event_types == EVENT_TYPES
    assert entity.entity_category is None
    assert entity.entity_registry_enabled_default is True


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
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("ROCKER1", "F6-02-01", friendly_id="Test")]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "event", "AABB0011_ROCKER1")
    coordinator = entry.runtime_data.coordinator
    await gateway.push(
        telegram("ROCKER1", [{"key": "buttonA0", "value": "pressed", "channel": 0}])
    )
    await wait_until(lambda: hass.states.get(entity_id).state != "unknown")
    original = hass.states.get(entity_id)
    assert original.attributes["event_type"] == "buttonA0_pressed"

    with patch(
        "custom_components.opus_greennet.coordinator.STREAM_BACKOFF_MAX",
        0.02,
    ):
        await gateway.stop()
        await wait_until(lambda: hass.states.get(entity_id).state == "unavailable")
        # The reconnected stream starts with a device list (full resync).
        await gateway.start()
        await gateway.wait_for_stream()
        await wait_until(lambda: coordinator.available)
    await hass.async_block_till_done()
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
