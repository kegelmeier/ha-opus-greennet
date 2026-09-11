"""Regression traffic for JSON packets, flattened frames, and rediscovery."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice


@pytest.fixture
def coord(monkeypatch):
    coordinator = OpusGreenNetCoordinator(MagicMock(), "AABB0011")
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_call_later", MagicMock()
    )
    return coordinator


def message(path, payload, *, retain=False):
    return SimpleNamespace(
        topic=f"EnOcean/AABB0011/stream/telegram/DEV1/{path}",
        payload=payload,
        retain=retain,
    )


@pytest.mark.parametrize("direction", ["from", "to"])
@pytest.mark.parametrize("wrapped", [False, True])
async def test_complete_json_telegram_updates_state(coord, direction, wrapped):
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Light", [{"eep": "D2-01-02"}])
    document = {"functions": [{"key": "dimValue", "value": "50"}]}
    if wrapped:
        document = {"state": document}
    coord._handle_telegram_property_message(message(direction, json.dumps(document)))
    assert coord.devices["DEV1"].channels[0].brightness == 50
    assert coord.devices["DEV1"].last_update_source == f"stream/telegram/{direction}"
    assert not coord._telegram_data


@pytest.mark.parametrize("payload", ["not json", "null", "[]", '"string"'])
def test_invalid_complete_packet_does_not_create_device(coord, payload):
    coord._handle_telegram_property_message(message("to", payload))
    assert not coord.devices


def test_fast_press_release_keeps_both_events(coord, monkeypatch):
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Rocker", [{"eep": "F6-02-01"}])
    events = []

    def dispatch(hass, signal, device):
        events.append(device.channels[0].last_button_action)

    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send", dispatch
    )
    for action in ("pressed", "released"):
        coord._handle_telegram_property_message(
            message("from/functions/0/key", "buttonA0")
        )
        coord._handle_telegram_property_message(
            message("from/functions/0/value", action)
        )
    coord._finalize_telegram("DEV1")
    assert events == ["pressed", "released"]


def test_consecutive_channel_frames_do_not_overwrite_each_other(coord):
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-11"}])
    for channel in (0, 1):
        for path, payload in (
            ("from/functions/0/key", "channel"),
            ("from/functions/0/value", str(channel)),
            ("from/functions/1/key", "switch"),
            ("from/functions/1/value", "on"),
        ):
            coord._handle_telegram_property_message(message(path, payload))
    coord._finalize_telegram("DEV1")
    assert coord.devices["DEV1"].channels[0].is_on is True
    assert coord.devices["DEV1"].channels[1].is_on is True


def test_retained_radio_telegram_does_not_fire_an_event(coord):
    coord._handle_telegram_property_message(
        message(
            "from",
            json.dumps({"functions": [{"key": "buttonA0", "value": "pressed"}]}),
            retain=True,
        )
    )
    assert not coord._telegram_data
    assert not coord.devices


def test_disconnect_discards_partial_frames(coord):
    coord._handle_telegram_property_message(message("from/functions/0/key", "switch"))
    cancel = coord._pending_telegrams["DEV1"]
    coord._handle_connection_status(False)
    cancel.assert_called_once()
    assert not coord._telegram_data
    assert not coord._pending_telegrams
    assert not coord._telegram_paths


def test_own_unacknowledged_echo_does_not_change_state(coord):
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-11"}])
    device.get_or_create_channel().is_on = False
    coord.devices["DEV1"] = device
    coord._command_waiters["DEV1"] = 1
    coord._handle_telegram_property_message(
        message(
            "to",
            json.dumps({"functions": [{"key": "switch", "value": "on"}]}),
        )
    )
    assert device.channels[0].is_on is False


def test_live_device_discovery_runs_after_initial_timer_completed(coord, monkeypatch):
    timer = MagicMock()
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_call_later", timer
    )
    coord._discovery_timer = MagicMock()
    coord._finalize_discovery()
    assert coord._discovery_timer is None
    coord._device_stream_data["NEW1"] = {
        "deviceId": "NEW1",
        "friendlyId": "New switch",
        "eeps": [{"eep": "D2-01-01"}],
    }
    coord._finalize_device_stream("NEW1")
    timer.assert_called_once()
    coord._finalize_discovery()
    assert coord.devices["NEW1"].primary_eep == "D2-01-01"


def test_separate_discovery_deltas_preserve_metadata(coord):
    coord._device_stream_data["NEW1"] = {
        "deviceId": "NEW1",
        "eeps": [{"eep": "D2-01-01"}],
    }
    coord._finalize_device_stream("NEW1")
    coord._device_stream_data["NEW1"] = {"friendlyId": "New switch"}
    coord._finalize_device_stream("NEW1")
    coord._finalize_discovery()
    assert coord.devices["NEW1"].primary_eep == "D2-01-01"
    assert coord.devices["NEW1"].friendly_id == "New switch"


def test_embedded_channel_report_cancels_only_matching_reconciliation(coord):
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-11"}])
    zero, one = MagicMock(), MagicMock()
    coord._pending_reconciliation_queries = {("DEV1", 0): [zero], ("DEV1", 1): [one]}
    coord._handle_telegram_property_message(
        message(
            "from",
            json.dumps({"functions": [{"key": "switch", "value": "on", "channel": 1}]}),
        )
    )
    assert coord.devices["DEV1"].channels[1].is_on is True
    one.assert_called_once()
    zero.assert_not_called()


@pytest.mark.parametrize("channel", [-1, True, "NaN", "inf", "1.5", None])
def test_malformed_channels_do_not_reconcile_channel_zero(coord, channel):
    assert (
        coord._channels_from_functions(
            [
                {"key": "channel", "value": channel},
                {"key": "switch", "value": "on"},
            ]
        )
        == set()
    )


def test_request_responses_do_not_enter_discovery(coord):
    coord._handle_get_answer_devices(
        SimpleNamespace(
            topic="EnOcean/AABB0011/getAnswer/devices/DEV1/configuration",
            payload=json.dumps({"deviceId": "unrelated", "configuration": {}}),
        )
    )
    assert not coord._device_data


def test_extreme_array_index_is_rejected_before_allocation(coord):
    with pytest.raises(ValueError):
        coord._set_nested_property({}, "from/functions/999999999/value", "1")


def test_reconnect_snapshot_dispatches_changed_existing_switch(coord, monkeypatch):
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])
    device.get_or_create_channel().is_on = False
    device.last_update_received_monotonic = 1.0
    coord.devices["DEV1"] = device
    coord._resync_requested_at = 2.0
    dispatch = MagicMock()
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send", dispatch
    )
    coord._create_device_from_data(
        "DEV1",
        {
            "friendlyId": "Updated switch",
            "eeps": [{"eep": "D2-01-01"}],
            "states": {"switch": "on"},
        },
    )
    updated = coord.devices["DEV1"]
    assert updated.channels[0].is_on is True
    assert updated.friendly_id == "Updated switch"
    dispatch.assert_called_once_with(
        coord.hass, "opus_greennet_device_state_update_AABB0011_DEV1", updated
    )


def test_discovery_metadata_does_not_replay_cached_rocker_event(coord, monkeypatch):
    device = EnOceanDevice("DEV1", "Rocker", [{"eep": "F6-02-01"}])
    device.update_from_telegram(
        {"functions": [{"key": "buttonA0", "value": "pressed"}]}
    )
    coord.devices["DEV1"] = device
    coord._resync_requested_at = 2.0
    dispatch = MagicMock()
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send", dispatch
    )
    coord._create_device_from_data(
        "DEV1",
        {
            "friendlyId": "Renamed rocker",
            "eeps": [{"eep": "F6-02-01"}],
            "states": {"buttonA0": "pressed"},
        },
    )
    updated = coord.devices["DEV1"]
    assert updated.channels[0].last_button_action is None
    assert updated.channels[0].last_button is None
    dispatch.assert_called_once()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (-65, -65),
        ("-72", -72),
        (None, None),
        (True, None),
        ("NaN", None),
        ("inf", None),
        ("invalid", None),
        (-65.5, None),
    ],
)
def test_discovery_rssi_accepts_only_finite_integers(coord, raw, expected):
    coord._create_device_from_data(
        "DEV1",
        {
            "eeps": [{"eep": "D2-01-01"}],
            "dbm": raw,
        },
    )
    assert coord.devices["DEV1"].dbm == expected
