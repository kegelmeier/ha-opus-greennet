"""Tests for the OpusGreenNetEvent entity (rocker switch events)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.opus_greennet.const import BUTTON_KEYS
from custom_components.opus_greennet.enocean_device import EnOceanChannel, EnOceanDevice
from custom_components.opus_greennet.event import EVENT_TYPES, OpusGreenNetEvent


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
def test_fires_event_for_each_button_action(event_entity, rocker_device, button, action):
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
