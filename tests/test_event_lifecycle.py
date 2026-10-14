"""Phase 3: transient button fields must not be replayed by later dispatches."""

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.opus_greennet.enocean_device import EnOceanDevice

DISPATCH = "custom_components.opus_greennet.coordinator.async_dispatcher_send"


def _rocker(coordinator):
    device = EnOceanDevice(
        device_id="ROCKER1", friendly_id="Rocker", eeps=[{"eep": "F6-02-01"}]
    )
    coordinator.devices[device.device_id] = device
    return device


def _press(coordinator, device, seen):
    coordinator._telegram_data[device.device_id] = {
        "from": {"functions": [{"key": "buttonA0", "value": "pressed", "channel": 0}]}
    }
    with patch(DISPATCH, side_effect=lambda *_a: seen.append(_snapshot(device))):
        coordinator._finalize_telegram(device.device_id)


def _snapshot(device):
    channel = device.channels.get(0)
    if channel is None:
        return None
    return (channel.last_button, channel.last_button_action)


def test_press_is_visible_during_dispatch_and_cleared_afterwards(coordinator):
    device = _rocker(coordinator)
    seen = []
    _press(coordinator, device, seen)
    assert ("buttonA0", "pressed") in seen
    assert _snapshot(device) == (None, None)


def test_rssi_update_after_press_does_not_replay_button(coordinator):
    device = _rocker(coordinator)
    _press(coordinator, device, [])
    seen = []
    with patch(DISPATCH, side_effect=lambda *_a: seen.append(_snapshot(device))):
        coordinator._handle_device_stream_message(
            SimpleNamespace(
                topic=f"EnOcean/AABB0011/stream/device/{device.device_id}/dbm",
                payload="-61",
            )
        )
    assert seen
    assert all(item in (None, (None, None)) for item in seen)


def test_two_identical_presses_are_two_occurrences(coordinator):
    device = _rocker(coordinator)
    seen = []
    _press(coordinator, device, seen)
    _press(coordinator, device, seen)
    assert seen.count(("buttonA0", "pressed")) == 2


def test_clear_button_occurrence_resets_all_channels():
    device = EnOceanDevice(device_id="X", friendly_id="X", eeps=[])
    for channel_id in (0, 1):
        channel = device.get_or_create_channel(channel_id)
        channel.last_button = "buttonB0"
        channel.last_button_action = "released"
    device.clear_button_occurrence()
    assert all(
        c.last_button is None and c.last_button_action is None
        for c in device.channels.values()
    )
