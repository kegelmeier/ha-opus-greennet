"""Regression traffic for JSON packets, flattened frames, and rediscovery."""

from __future__ import annotations

import json
import logging
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
        coord._command_fields_by_channel(
            [
                {"key": "channel", "value": channel},
                {"key": "switch", "value": "on"},
            ]
        )
        == {}
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


def test_observed_device_states_list_initializes_dimmer(coord):
    """The H11 MQTT snapshot uses descriptors, rather than a flat states map."""
    coord._create_device_from_data(
        "DEV1",
        {
            "deviceId": "DEV1",
            "friendlyId": "Test dimmer",
            "eeps": [{"eep": "D2-01-02"}],
            "states": [
                {
                    "key": "dimValue",
                    "value": 0.0,
                    "timestamp": "2026-09-30T12:00:00+0200",
                },
                {"key": "localControl", "value": "on"},
            ],
        },
    )
    channel = coord.devices["DEV1"].channels[0]
    assert channel.brightness == 0
    assert channel.is_on is False
    assert channel.local_control is True


@pytest.mark.parametrize("embedded", [True, False])
def test_states_list_preserves_channel_selectors(coord, embedded):
    functions = [
        {"key": "dimValue", "value": 25.0},
        {"key": "localControl", "value": "on"},
    ]
    if embedded:
        for function in functions:
            function["channel"] = 1
    else:
        functions.insert(0, {"key": "channel", "value": 1})
    coord._create_device_from_data(
        "DEV1",
        {
            "eeps": [{"eep": "D2-01-12"}],
            "states": functions,
        },
    )
    assert 0 not in coord.devices["DEV1"].channels
    channel = coord.devices["DEV1"].channels[1]
    assert channel.brightness == 25
    assert channel.local_control is True


def test_reconnect_states_list_refreshes_existing_device(coord):
    coord._create_device_from_data(
        "DEV1",
        {
            "eeps": [{"eep": "D2-01-02"}],
            "states": [{"key": "dimValue", "value": 0.0}],
        },
    )
    coord._resync_requested_at = 2.0
    coord.devices["DEV1"].last_update_received_monotonic = 1.0
    coord._create_device_from_data(
        "DEV1",
        {
            "eeps": [{"eep": "D2-01-02"}],
            "states": [{"key": "dimValue", "value": 70.0}],
        },
    )
    assert coord.devices["DEV1"].channels[0].brightness == 70


def test_flattened_states_list_discovers_initial_device(coord):
    for path, payload in (
        ("eeps/0/eep", "D2-01-12"),
        ("states/0/key", "dimValue"),
        ("states/0/value", "25.0"),
        ("states/0/channel", "1"),
    ):
        coord._handle_device_property_message(
            SimpleNamespace(
                topic=f"EnOcean/AABB0011/stream/devices/DEV1/{path}",
                payload=payload,
            )
        )
    coord._finalize_discovery()
    assert coord.devices["DEV1"].channels[1].brightness == 25
    assert 0 not in coord.devices["DEV1"].channels


@pytest.mark.parametrize("stream", ["device", "devices"])
def test_flattened_states_list_updates_known_device(coord, stream):
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Dimmer", [{"eep": "D2-01-12"}])
    handler = (
        coord._handle_device_property_message
        if stream == "devices"
        else coord._handle_device_stream_message
    )
    for path, payload in (
        ("states/0/key", "dimValue"),
        ("states/0/value", "25.0"),
        ("states/0/channel", "1"),
    ):
        handler(
            SimpleNamespace(
                topic=f"EnOcean/AABB0011/stream/{stream}/DEV1/{path}",
                payload=payload,
            )
        )
    coord._finalize_device_stream("DEV1")
    assert coord.devices["DEV1"].channels[1].brightness == 25
    assert 0 not in coord.devices["DEV1"].channels

    # Subsequent model deltas may change just a value, retaining the index's
    # original key and channel from the full device snapshot.
    handler(
        SimpleNamespace(
            topic=f"EnOcean/AABB0011/stream/{stream}/DEV1/states/0/value",
            payload="60.0",
        )
    )
    coord._finalize_device_stream("DEV1")
    assert coord.devices["DEV1"].channels[1].brightness == 60


def test_states_list_ignores_incomplete_or_malformed_descriptors(coord):
    functions = coord._device_state_functions(
        {
            "states": [
                None,
                "not a descriptor",
                {"key": "dimValue"},
                {"value": 10},
                {"key": ["switch"], "value": "on"},
                {"key": "dimValue", "value": 0.0, "channel": 1},
            ]
        }
    )
    assert functions == [{"key": "dimValue", "value": 0.0, "channel": 1}]


@pytest.mark.parametrize("stream", ["device", "devices"])
def test_indexed_value_delta_keeps_preceding_channel_without_replaying_state(
    coord, stream
):
    device = EnOceanDevice("DEV1", "Dimmer", [{"eep": "D2-01-12"}])
    device.get_or_create_channel(0).brightness = 10
    device.get_or_create_channel(1).brightness = 25
    device.channels[1].local_control = False
    coord.devices["DEV1"] = device
    coord._device_data["DEV1"] = {
        "states": [
            {"key": "channel", "value": 1},
            {"key": "dimValue", "value": 25},
            # An older cached value must not overwrite a newer live reading.
            {"key": "localControl", "value": "on"},
        ],
    }
    handler = (
        coord._handle_device_property_message
        if stream == "devices"
        else coord._handle_device_stream_message
    )
    handler(
        SimpleNamespace(
            topic=f"EnOcean/AABB0011/stream/{stream}/DEV1/states/1/value",
            payload="60.0",
        )
    )
    coord._finalize_device_stream("DEV1")
    assert device.channels[1].brightness == 60
    assert device.channels[0].brightness == 10
    assert device.channels[1].local_control is False


@pytest.mark.parametrize("stream", ["device", "devices"])
def test_indexed_delta_explicit_channel_overrides_cached_selector(coord, stream):
    device = EnOceanDevice("DEV1", "Dimmer", [{"eep": "D2-01-12"}])
    device.get_or_create_channel(0).brightness = 10
    device.get_or_create_channel(1).brightness = 25
    coord.devices["DEV1"] = device
    coord._device_data["DEV1"] = {
        "states": [
            {"key": "channel", "value": 1},
            {"key": "dimValue", "value": 10, "channel": 0},
        ]
    }
    handler = (
        coord._handle_device_property_message
        if stream == "devices"
        else coord._handle_device_stream_message
    )
    handler(
        SimpleNamespace(
            topic=f"EnOcean/AABB0011/stream/{stream}/DEV1/states/1/value",
            payload="60.0",
        )
    )
    coord._finalize_device_stream("DEV1")
    assert device.channels[0].brightness == 60
    assert device.channels[1].brightness == 25


@pytest.mark.parametrize("stream", ["device", "devices"])
@pytest.mark.parametrize("retained", [False, True])
def test_indexed_rocker_device_model_state_does_not_fire_event(
    coord, monkeypatch, stream, retained
):
    device = EnOceanDevice("DEV1", "Rocker", [{"eep": "F6-02-01"}])
    channel = device.get_or_create_channel()
    coord.devices["DEV1"] = device
    dispatch = MagicMock()
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send", dispatch
    )
    handler = (
        coord._handle_device_property_message
        if stream == "devices"
        else coord._handle_device_stream_message
    )
    for path, payload in (
        ("states/0/key", "buttonA0"),
        ("states/0/value", "pressed"),
    ):
        handler(
            SimpleNamespace(
                topic=f"EnOcean/AABB0011/stream/{stream}/DEV1/{path}",
                payload=payload,
                retain=retained,
            )
        )
    coord._finalize_device_stream("DEV1")
    assert channel.last_button is None
    assert channel.last_button_action is None
    dispatch.assert_not_called()


def test_flat_rocker_device_model_state_does_not_fire_event(coord, monkeypatch):
    device = EnOceanDevice("DEV1", "Rocker", [{"eep": "F6-02-01"}])
    channel = device.get_or_create_channel()
    coord.devices["DEV1"] = device
    dispatch = MagicMock()
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send", dispatch
    )
    coord._handle_device_property_message(
        SimpleNamespace(
            topic="EnOcean/AABB0011/stream/devices/DEV1/states/buttonA0",
            payload="pressed",
            retain=True,
        )
    )
    assert channel.last_button is None
    dispatch.assert_not_called()


@pytest.mark.parametrize("stream", ["device", "devices"])
def test_retained_model_replay_omits_per_property_debug_logs(coord, caplog, stream):
    logger = "custom_components.opus_greennet.coordinator"
    caplog.set_level(logging.DEBUG, logger=logger)
    handler = (
        coord._handle_device_property_message
        if stream == "devices"
        else coord._handle_device_stream_message
    )
    for index in range(1000):
        handler(
            SimpleNamespace(
                topic=f"EnOcean/AABB0011/stream/{stream}/DEV1/properties/p{index}",
                payload="retained value",
                retain=True,
            )
        )
    assert not [record for record in caplog.records if record.name == logger]

    handler(
        SimpleNamespace(
            topic=f"EnOcean/AABB0011/stream/{stream}/DEV1/properties/live",
            payload="live value",
            retain=False,
        )
    )
    messages = [
        record.getMessage() for record in caplog.records if record.name == logger
    ]
    assert any("Raw OPUS MQTT" in message for message in messages)
    assert any("latency received" in message for message in messages)


def test_snapshot_configuration_rotation_coexists_with_cover_states(coord):
    coord._create_device_from_data(
        "DEV1",
        {
            "eeps": [{"eep": "D2-05-00"}],
            "states": [
                {"key": "position", "value": 78.0},
                {"key": "angle", "value": "unknown"},
            ],
            "configuration": {
                "parameters": [
                    {"key": "rotationTime", "value": 0.0, "unit": "s"},
                ]
            },
        },
    )
    device = coord.devices["DEV1"]
    assert device.channels[0].position == 78
    assert device.channels[0].rotation_time == 0
    assert device.supports_tilt is False


@pytest.mark.parametrize(
    "parameters",
    [
        [{"key": "rotationTime", "defaultValue": 0.0}],
        [{"key": "rotationTime", "value": "noChange", "defaultValue": 0.0}],
        [{"key": "rotationTime", "value": None, "defaultValue": 0.0}],
        [{"key": "unrelated", "value": 0.0}],
    ],
)
def test_configuration_parameter_defaults_do_not_change_tilt(coord, parameters):
    device = EnOceanDevice("DEV1", "Cover", [{"eep": "D2-05-00"}])
    device.get_or_create_channel().rotation_time = 1.5
    coord.devices["DEV1"] = device
    coord._create_device_from_data(
        "DEV1",
        {
            "eeps": [{"eep": "D2-05-00"}],
            "configuration": {"parameters": parameters},
        },
    )
    assert coord.devices["DEV1"].channels[0].rotation_time == 1.5
    assert coord.devices["DEV1"].supports_tilt is True


@pytest.mark.parametrize("embedded", [True, False])
def test_configuration_rotation_preserves_its_reported_channel(coord, embedded):
    parameters = [{"key": "rotationTime", "value": 0.0}]
    if embedded:
        parameters[0]["channel"] = 1
    else:
        parameters.insert(0, {"key": "channel", "value": 1})
    coord._create_device_from_data(
        "DEV1",
        {
            "eeps": [{"eep": "D2-05-00"}],
            "states": [{"key": "position", "value": 78, "channel": 0}],
            "configuration": {"parameters": parameters},
        },
    )
    device = coord.devices["DEV1"]
    assert device.channels[0].position == 78
    assert device.channels[0].rotation_time is None
    assert device.channels[1].rotation_time == 0


def test_rediscovery_configuration_preserves_newer_live_position(coord):
    device = EnOceanDevice("DEV1", "Cover", [{"eep": "D2-05-00"}])
    device.get_or_create_channel().position = 40
    device.last_update_received_monotonic = 3.0
    coord.devices["DEV1"] = device
    coord._resync_requested_at = 2.0
    coord._create_device_from_data(
        "DEV1",
        {
            "eeps": [{"eep": "D2-05-00"}],
            "states": [{"key": "position", "value": 78}],
            "configuration": {"parameters": [{"key": "rotationTime", "value": 0.0}]},
        },
    )
    device = coord.devices["DEV1"]
    assert device.channels[0].position == 40
    assert device.channels[0].rotation_time == 0


@pytest.mark.parametrize("stream", ["device", "devices"])
@pytest.mark.parametrize("embedded", [True, False])
def test_indexed_configuration_value_changes_tilt_without_replaying_position(
    coord, monkeypatch, stream, embedded
):
    device = EnOceanDevice("DEV1", "Cover", [{"eep": "D2-05-00"}])
    device.get_or_create_channel(0).position = 40
    device.get_or_create_channel(1).rotation_time = 1.5
    coord.devices["DEV1"] = device
    parameters = [{"key": "rotationTime", "value": 1.5, "unit": "s"}]
    index = 0
    if embedded:
        parameters[0]["channel"] = 1
    else:
        parameters.insert(0, {"key": "channel", "value": 1})
        index = 1
    coord._device_data["DEV1"] = {
        "states": [{"key": "position", "value": 78}],
        "configuration": {"parameters": parameters},
    }
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    dispatch = MagicMock()
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send", dispatch
    )
    handler = (
        coord._handle_device_property_message
        if stream == "devices"
        else coord._handle_device_stream_message
    )
    handler(
        SimpleNamespace(
            topic=f"EnOcean/AABB0011/stream/{stream}/DEV1/configuration/parameters/{index}/value",
            payload="0.0",
        )
    )
    coord._finalize_device_stream("DEV1")
    assert device.channels[1].rotation_time == 0
    assert device.channels[0].rotation_time is None
    assert device.channels[0].position == 40
    cancel.assert_not_called()
    dispatch.assert_called_once()
