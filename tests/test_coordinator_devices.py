"""Device list application: discovery, resynchronisation and metadata refresh.

Authoritative lists (setup and the start of every stream) carry the current
state; the periodic refresh only updates configuration, battery and metadata.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.core import HomeAssistant

from custom_components.opus_greennet.coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_STATE_UPDATE,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.cover import OpusGreenNetCover
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

MODULE = "custom_components.opus_greennet.coordinator"
DISCOVERED = f"{SIGNAL_DEVICE_DISCOVERED}_AABB0011"
STATE_SIGNAL = f"{SIGNAL_DEVICE_STATE_UPDATE}_AABB0011_DEV1"


@pytest.fixture
def coord() -> OpusGreenNetCoordinator:
    return OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")


@pytest.fixture
def dispatch() -> Iterator[MagicMock]:
    with patch(f"{MODULE}.async_dispatcher_send") as mock:
        yield mock


@pytest.fixture
def call_later() -> Iterator[MagicMock]:
    with patch(f"{MODULE}.async_call_later") as mock:
        yield mock


def _apply(coord, data: dict, *, authoritative: bool = True) -> EnOceanDevice:
    coord._apply_device_object(
        {"deviceId": "DEV1", **data}, authoritative=authoritative
    )
    return coord.devices["DEV1"]


def _signals(dispatch: MagicMock) -> list[str]:
    return [call.args[1] for call in dispatch.call_args_list]


# ── Discovery ────────────────────────────────────────────────────────────────


def test_device_list_creates_devices_and_dispatches_discovery(coord, dispatch) -> None:
    coord._apply_device_list(
        {
            "devices": [
                device_object(
                    "DEV1",
                    "D2-01-02",
                    friendly_id="Living Room",
                    physicalDevice="OPUS dimmer",
                    firstSeen="2026-01-01T00:00:00.000+0100",
                    lastSeen="2026-10-19T18:17:17.571+0200",
                    dbm=-79,
                ),
                # getAnswer-style wrapper around each device object.
                {"device": {"deviceId": "DEV2", "friendlyId": 123, "eeps": []}},
                {"device": "junk"},
                {"deviceId": ""},
                {"friendlyId": "No id"},
                "junk",
            ]
        },
        authoritative=True,
    )
    assert list(coord.devices) == ["DEV1", "DEV2"]
    device = coord.devices["DEV1"]
    assert device.friendly_id == "Living Room"
    assert device.primary_eep == "D2-01-02"
    assert device.manufacturer == "Jaeger Direkt"
    assert device.physical_device == "OPUS dimmer"
    assert device.first_seen == "2026-01-01T00:00:00.000+0100"
    assert device.last_seen == "2026-10-19T18:17:17.571+0200"
    assert device.dbm == -79
    # A numeric name survives; a missing name falls back to the device id.
    assert coord.devices["DEV2"].friendly_id == "123"
    assert _signals(dispatch) == [DISCOVERED, DISCOVERED]


@pytest.mark.parametrize("devices", [None, "junk", {"deviceId": "DEV1"}])
def test_malformed_device_list_is_ignored(coord, dispatch, devices) -> None:
    coord._apply_device_list({"devices": devices}, authoritative=True)
    assert coord.devices == {}
    dispatch.assert_not_called()


def test_missing_name_falls_back_to_device_id(coord, dispatch) -> None:
    device = _apply(coord, {"friendlyId": None, "eeps": [{"eep": "D2-01-01"}]})
    assert device.friendly_id == "DEV1"


def test_device_document_creates_unknown_device(coord, dispatch) -> None:
    coord._handle_document(
        {
            "header": {"content": "device"},
            "device": device_object(
                "NEW1", "D2-01-01", states=[{"key": "switch", "value": "on"}]
            ),
        }
    )
    assert coord.devices["NEW1"].channels[0].is_on is True
    assert _signals(dispatch) == [DISCOVERED]


def test_known_device_dispatches_state_update(coord, dispatch) -> None:
    _apply(coord, {"eeps": [{"eep": "D2-01-01"}]})
    dispatch.reset_mock()
    device = _apply(coord, {"eeps": [{"eep": "D2-01-01"}]})
    dispatch.assert_called_once_with(coord.hass, STATE_SIGNAL, device)


def test_device_gains_eeps_and_is_discovered_then(coord, dispatch) -> None:
    """A device listed without EEPs gets its entities once they appear."""
    first = _apply(coord, {"eeps": [], "states": [{"key": "switch", "value": "on"}]})
    assert first.entity_type is None
    second = _apply(coord, {"eeps": [{"eep": "D2-01-01"}]}, authoritative=False)
    assert second.entity_type == "switch"
    assert second.channels[0].is_on is True
    assert _signals(dispatch) == [DISCOVERED, DISCOVERED]


@pytest.mark.parametrize(
    ("eeps", "expected"),
    [
        ([{"eep": "D2-01-00"}, "F6-03-01", None], [{"eep": "D2-01-00"}]),
        (["D2-01-00"], []),
        ({"0": {"eep": "D2-01-00"}}, []),
        (None, []),
    ],
)
def test_only_eep_objects_are_kept(coord, dispatch, eeps, expected) -> None:
    device = _apply(coord, {"eeps": eeps})
    assert device.eeps == expected


def test_real_eep_objects_keep_their_fields(coord, dispatch) -> None:
    eeps = [
        {"eep": "D2-01-11", "version": 0.9, "direction": "both"},
        {"eep": "F6-03-01", "version": 0.9, "direction": "from"},
    ]
    device = _apply(coord, {"eeps": eeps})
    assert device.eeps == eeps
    assert device.entity_type == "switch"
    assert device.channel_count == 2


# ── Initial state ────────────────────────────────────────────────────────────


def test_device_states_list_initializes_dimmer(coord, dispatch) -> None:
    device = _apply(
        coord,
        {
            "friendlyId": "Test dimmer",
            "eeps": [{"eep": "D2-01-03"}],
            "states": [
                {
                    "key": "dimValue",
                    "value": 0.0,
                    "unit": "%",
                    "timestamp": "2026-10-19T18:17:17.571+0200",
                    "age": 26885,
                },
                {"key": "localControl", "value": "on", "meaning": "enabled"},
            ],
        },
    )
    channel = device.channels[0]
    assert channel.brightness == 0
    assert channel.is_on is False
    assert channel.local_control is True
    assert device.last_update_source == "device list"


def test_applies_initial_liquid_state(coord, dispatch) -> None:
    device = _apply(
        coord,
        {
            "eeps": [{"eep": "F6-05-01"}],
            "states": [{"key": "liquidDetected", "value": False}],
        },
    )
    assert device.channels[0].liquid_detected is False
    assert device.last_update_source == "device list"


def test_state_functions_form_initializes_device(coord, dispatch) -> None:
    device = _apply(
        coord,
        {
            "eeps": [{"eep": "D2-01-01"}],
            "state": {"functions": [{"key": "switch", "value": "on"}]},
        },
    )
    assert device.channels[0].is_on is True


@pytest.mark.parametrize("embedded", [True, False])
def test_states_list_preserves_channel_selectors(coord, dispatch, embedded) -> None:
    functions = [
        {"key": "dimValue", "value": 25.0},
        {"key": "localControl", "value": "on"},
    ]
    if embedded:
        for function in functions:
            function["channel"] = 1
    else:
        functions.insert(0, {"key": "channel", "value": 1})
    device = _apply(coord, {"eeps": [{"eep": "D2-01-12"}], "states": functions})
    assert 0 not in device.channels
    assert device.channels[1].brightness == 25
    assert device.channels[1].local_control is True


def test_device_without_state_has_no_source(coord, dispatch) -> None:
    device = _apply(coord, {"eeps": [{"eep": "D2-01-01"}], "states": []})
    assert device.channels == {}
    assert device.last_update_source == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
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
def test_discovery_rssi_accepts_only_finite_integers(
    coord, dispatch, raw, expected
) -> None:
    device = _apply(coord, {"eeps": [{"eep": "D2-01-01"}], "dbm": raw})
    assert device.dbm == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("96%", 96.0), ("127%", 75.0), (None, 75.0), ("notAvailable", None)],
)
def test_battery_level_from_device_list(coord, dispatch, value, expected) -> None:
    _apply(coord, {"eeps": [{"eep": "D2-01-11"}]}).get_or_create_channel()
    coord.devices["DEV1"].channels[0].battery_level = 75.0
    device = _apply(
        coord,
        {"eeps": [{"eep": "D2-01-11"}], "batteryLevel": value},
        authoritative=False,
    )
    assert device.channels[0].battery_level == expected


# ── Resynchronisation of known devices ──────────────────────────────────────


def test_reconnect_snapshot_dispatches_changed_existing_switch(coord, dispatch) -> None:
    original = _apply(
        coord,
        {"friendlyId": "Switch", "eeps": [{"eep": "D2-01-01"}], "states": []},
    )
    original.get_or_create_channel().is_on = False
    dispatch.reset_mock()
    updated = _apply(
        coord,
        {
            "friendlyId": "Updated switch",
            "eeps": [{"eep": "D2-01-01"}],
            "states": [{"key": "switch", "value": "on"}],
        },
    )
    # REST phase 02 (E6, B-4): the known device is updated in place.
    assert updated is original
    assert updated.channels is original.channels
    assert updated.channels[0].is_on is True
    assert updated.friendly_id == "Updated switch"
    dispatch.assert_called_once_with(coord.hass, STATE_SIGNAL, updated)


def test_authoritative_list_replaces_state_refresh_keeps_it(coord, dispatch) -> None:
    eeps = [{"eep": "D2-01-02"}]
    _apply(coord, {"eeps": eeps, "states": [{"key": "dimValue", "value": 0.0}]})
    device = _apply(
        coord,
        {"eeps": eeps, "states": [{"key": "dimValue", "value": 70.0}]},
        authoritative=False,
    )
    assert device.channels[0].brightness == 0
    device = _apply(
        coord, {"eeps": eeps, "states": [{"key": "dimValue", "value": 70.0}]}
    )
    assert device.channels[0].brightness == 70


def test_known_device_keeps_runtime_metadata(coord, dispatch) -> None:
    original = _apply(
        coord,
        {"eeps": [{"eep": "D2-01-01"}], "dbm": -70, "lastSeen": "earlier"},
    )
    original.last_command_error = "status 400: rejected"
    original.last_update_source = "telegram/from"
    original.last_update_received_monotonic = 1.0
    original.last_update_dispatched_monotonic = 2.0

    refreshed = _apply(coord, {"eeps": [{"eep": "D2-01-01"}]}, authoritative=False)
    assert refreshed.dbm == -70
    assert refreshed.last_seen == "earlier"
    assert refreshed.last_command_error == "status 400: rejected"
    assert refreshed.last_update_source == "telegram/from"
    assert refreshed.last_update_received_monotonic == 1.0
    assert refreshed.last_update_dispatched_monotonic == 2.0

    newer = _apply(
        coord,
        {"eeps": [{"eep": "D2-01-01"}], "dbm": -50, "lastSeen": "now"},
        authoritative=False,
    )
    assert (newer.dbm, newer.last_seen) == (-50, "now")


def test_device_list_is_not_command_feedback(coord, dispatch, call_later) -> None:
    """A device list is a snapshot, not feedback: it never cancels checks."""
    device = _apply(coord, {"eeps": [{"eep": "D2-01-01"}]})
    device.last_command_error = "status 400: rejected"
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    device = _apply(
        coord,
        {"eeps": [{"eep": "D2-01-01"}], "states": [{"key": "switch", "value": "on"}]},
    )
    assert device.channels[0].is_on is True
    assert device.last_command_error == "status 400: rejected"
    cancel.assert_not_called()
    call_later.assert_not_called()


def test_single_device_document_is_live_feedback(coord, dispatch, call_later) -> None:
    """A streamed ``device`` document confirms the fields it reports."""
    _apply(coord, {"eeps": [{"eep": "D2-01-11"}]})
    zero, one = MagicMock(), MagicMock()
    coord._pending_reconciliation_queries = {("DEV1", 0): [zero], ("DEV1", 1): [one]}
    coord._pending_reconciliation_fields = {("DEV1", 0): {"switch"}}
    coord._handle_document(
        {
            "header": {"content": "device"},
            "device": device_object(
                "DEV1",
                "D2-01-11",
                states=[{"key": "switch", "channel": 0, "value": "on"}],
            ),
        }
    )
    assert coord.devices["DEV1"].channels[0].is_on is True
    zero.assert_called_once()
    one.assert_not_called()


@pytest.mark.parametrize("device_id", [None, 7, ["DEV1"]])
def test_device_document_without_id_is_ignored(coord, dispatch, device_id) -> None:
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel := MagicMock()]
    coord._handle_document(
        {
            "header": {"content": "device"},
            "device": {"deviceId": device_id, "eeps": [{"eep": "D2-01-01"}]},
        }
    )
    assert coord.devices == {}
    cancel.assert_not_called()
    dispatch.assert_not_called()


def test_discovery_metadata_does_not_replay_cached_rocker_event(
    coord, dispatch
) -> None:
    device = _apply(coord, {"eeps": [{"eep": "F6-02-01"}]})
    device.update_from_telegram(
        {"functions": [{"key": "buttonA0", "value": "pressed"}]}
    )
    seen = []
    dispatch.side_effect = lambda hass, signal, dev: seen.append(
        dev.channels[0].last_button
    )
    updated = _apply(
        coord,
        {
            "friendlyId": "Renamed rocker",
            "eeps": [{"eep": "F6-02-01"}],
            "states": [{"key": "buttonA0", "value": "pressed"}],
        },
    )
    assert seen == [None]
    assert updated.channels[0].last_button is None
    assert updated.channels[0].last_button_action is None


@pytest.mark.parametrize("authoritative", [True, False])
def test_new_rocker_from_list_has_no_button_occurrence(
    coord, dispatch, authoritative
) -> None:
    device = _apply(
        coord,
        {
            "eeps": [{"eep": "F6-02-01"}],
            "states": [{"key": "buttonA0", "value": "pressed"}],
        },
        authoritative=authoritative,
    )
    assert all(channel.last_button is None for channel in device.channels.values())


# ── RWM transmit modes ──────────────────────────────────────────────────────


def test_rwm_transmit_mode_slots_are_state_of_authoritative_lists(
    coord, dispatch
) -> None:
    eeps = [{"eep": "F6-05-02"}]
    device = _apply(
        coord, {"eeps": eeps, "transmitModes": [{"value": "on"}, {"value": "low"}]}
    )
    assert device.channels[0].smoke_alarm is True
    assert device.channels[0].battery_low is True

    device = _apply(
        coord,
        {"eeps": eeps, "transmitModes": [{"value": "off"}, {"value": "ok"}]},
        authoritative=False,
    )
    assert device.channels[0].smoke_alarm is True
    assert device.channels[0].battery_low is True

    device = _apply(
        coord, {"eeps": eeps, "transmitModes": [{"value": "off"}, {"value": "ok"}]}
    )
    assert device.channels[0].smoke_alarm is False
    assert device.channels[0].battery_low is False


# ── Cover rotation and movement time metadata ───────────────────────────────


@pytest.fixture
def cover(coord) -> tuple[EnOceanDevice, MagicMock]:
    """A cover awaiting movement confirmation with useful live state."""
    device = EnOceanDevice("DEV1", "Shutter", [{"eep": "D2-05-00"}])
    device.update_from_telegram(
        {"functions": [{"key": "position", "value": 20}, {"key": "angle", "value": 45}]}
    )
    device.last_command_error = "previous movement failed"
    coord.devices["DEV1"] = device
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    return device, cancel


def _cover_entity(coord, device) -> OpusGreenNetCover:
    entity = OpusGreenNetCover(coord, "AABB0011", "gateway", device)
    entity.async_write_ha_state = MagicMock()
    return entity


@pytest.mark.parametrize(
    "state_data",
    [
        {
            "states": [
                {"key": "rotationTime", "value": "0"},
                {"key": "position", "value": "20"},
            ]
        },
        {
            "state": {
                "functions": [
                    {"key": "rotationTime", "value": "noRotation"},
                    {"key": "position", "value": 20},
                ]
            }
        },
        {
            "states": [{"key": "position", "value": 20.0}],
            "configuration": {"parameters": [{"key": "rotationTime", "value": 0.0}]},
        },
    ],
    ids=["states-list", "state-functions", "configuration"],
)
def test_initial_discovery_applies_rotation_setting(
    coord, dispatch, state_data
) -> None:
    device = _apply(
        coord, {"friendlyId": "Shutter", "eeps": [{"eep": "D2-05-00"}], **state_data}
    )
    assert device.channels[0].rotation_time == 0
    assert device.channels[0].position == 20
    assert device.supports_tilt is False


def test_late_configuration_toggles_tilt_without_movement_effects(
    coord, cover, call_later
) -> None:
    device, cancel = cover
    entity = _cover_entity(coord, device)
    tilt = CoverEntityFeature.SET_TILT_POSITION
    assert entity.supported_features & tilt
    assert entity.current_cover_tilt_position == 45

    def deliver(_hass, signal, updated):
        assert signal == STATE_SIGNAL
        entity._handle_state_update(updated)

    with patch(f"{MODULE}.async_dispatcher_send", side_effect=deliver) as dispatch:
        for rotation in (0.0, 1.5):
            coord._apply_device_list(
                {
                    "devices": [
                        {
                            "deviceId": "DEV1",
                            "eeps": [{"eep": "D2-05-00"}],
                            "states": [{"key": "position", "value": 100}],
                            "configuration": {
                                "parameters": [
                                    {"key": "rotationTime", "value": rotation}
                                ]
                            },
                        }
                    ]
                },
                authoritative=False,
            )
            if rotation == 0:
                assert not entity.supported_features & tilt
                assert entity.current_cover_tilt_position is None
                assert entity.current_cover_position == 80

    assert entity.supported_features & tilt
    assert entity.current_cover_tilt_position == 45
    assert entity.current_cover_position == 80
    assert coord.devices["DEV1"].last_command_error == "previous movement failed"
    assert coord._pending_reconciliation_queries[("DEV1", 0)] == [cancel]
    assert dispatch.call_count == 2
    assert entity.async_write_ha_state.call_count == 2
    cancel.assert_not_called()
    call_later.assert_not_called()


@pytest.mark.parametrize(
    "state_data",
    [
        {
            "states": [
                {"key": "position", "value": 100},
                {"key": "angle", "value": 0},
                {"key": "rotationTime", "value": 0},
            ]
        },
        {
            "state": {
                "functions": [
                    {"key": "position", "value": 100},
                    {"key": "angle", "value": 0},
                    {"key": "rotationTime", "value": "noRotation"},
                ]
            }
        },
    ],
    ids=["states-list", "state-functions"],
)
def test_refresh_updates_existing_cover_preserving_live_position(
    coord, cover, call_later, state_data
) -> None:
    original, cancel = cover
    entity = _cover_entity(coord, original)
    with patch(
        f"{MODULE}.async_dispatcher_send",
        side_effect=lambda _hass, _signal, updated: entity._handle_state_update(
            updated
        ),
    ) as dispatch:
        coord._apply_device_object(
            {"deviceId": "DEV1", "eeps": [{"eep": "D2-05-00"}], **state_data},
            authoritative=False,
        )
    updated = coord.devices["DEV1"]
    # REST phase 02 (E6, B-4): the known device is updated in place.
    assert updated is original
    assert entity._device is updated
    assert updated.channels[0].position == 20
    assert updated.channels[0].angle == 45
    assert updated.channels[0].rotation_time == 0
    assert not entity.supported_features & CoverEntityFeature.SET_TILT_POSITION
    assert entity.current_cover_tilt_position is None
    assert updated.last_command_error == "previous movement failed"
    assert coord._pending_reconciliation_queries[("DEV1", 0)] == [cancel]
    cancel.assert_not_called()
    dispatch.assert_called_once_with(coord.hass, STATE_SIGNAL, updated)


def test_stream_resync_replaces_cover_state(coord, cover, dispatch) -> None:
    _device, cancel = cover
    updated = _apply(
        coord,
        {
            "eeps": [{"eep": "D2-05-00"}],
            "states": [
                {"key": "position", "value": 100.0},
                {"key": "angle", "value": 0.0},
            ],
        },
    )
    assert updated.channels[0].position == 100
    assert updated.channels[0].angle == 0
    assert updated.last_update_source == "device list"
    cancel.assert_not_called()


@pytest.mark.parametrize("rotation_value", [None, "noChange", "invalid", -1, True])
def test_rediscovery_keeps_known_rotation_when_setting_is_unknown(
    coord, cover, dispatch, rotation_value
) -> None:
    original, _cancel = cover
    original.channels[0].rotation_time = 0
    states = [{"key": "position", "value": 100}]
    if rotation_value is not None:
        states.append({"key": "rotationTime", "value": rotation_value})
    device = _apply(
        coord, {"eeps": [{"eep": "D2-05-00"}], "states": states}, authoritative=False
    )
    assert device.channels[0].rotation_time == 0
    assert device.channels[0].position == 20


@pytest.mark.parametrize(
    "functions",
    [
        [{"key": "channel", "value": "1"}, {"key": "rotationTime", "value": 0}],
        [{"key": "rotationTime", "value": 0, "channel": 1}],
    ],
    ids=["channel-selector", "function-channel"],
)
@pytest.mark.parametrize("authoritative", [True, False])
def test_rediscovery_refreshes_only_the_reported_channel(
    coord, cover, dispatch, functions, authoritative
) -> None:
    original, _cancel = cover
    original.get_or_create_channel(1).rotation_time = 200
    updated = _apply(
        coord,
        {"eeps": [{"eep": "D2-05-00"}], "state": {"functions": functions}},
        authoritative=authoritative,
    )
    assert updated.supports_tilt_for_channel(0) is True
    assert updated.supports_tilt_for_channel(1) is False
    assert updated.channels[0].rotation_time is None
    assert updated.channels[0].position == 20


def test_snapshot_configuration_rotation_coexists_with_cover_states(
    coord, dispatch
) -> None:
    """Real D2-05-02 entries report ``angle: "unknown"`` and float positions."""
    device = _apply(
        coord,
        {
            "eeps": [
                {"eep": "D2-05-02", "version": 0.9, "direction": "both"},
                {"eep": "F6-03-01", "version": 0.9, "direction": "from"},
            ],
            "states": [
                {"key": "angle", "value": "unknown", "meaning": "Angle unknown"},
                {"key": "lockingMode", "value": "unblock"},
                {"key": "position", "value": 78.0, "unit": "%"},
            ],
            "configuration": {
                "parameters": [
                    {"key": "verticalMovementTime", "value": 56.26, "unit": "s"},
                    {"key": "rotationTime", "value": 0.0, "unit": "s"},
                ]
            },
        },
    )
    channel = device.channels[0]
    assert channel.position == 78
    assert channel.angle is None
    assert channel.rotation_time == 0
    assert channel.vertical_movement_time == 56.26
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
@pytest.mark.parametrize("authoritative", [True, False])
def test_configuration_parameter_defaults_do_not_change_tilt(
    coord, dispatch, parameters, authoritative
) -> None:
    device = EnOceanDevice("DEV1", "Cover", [{"eep": "D2-05-00"}])
    device.get_or_create_channel().rotation_time = 1.5
    coord.devices["DEV1"] = device
    updated = _apply(
        coord,
        {"eeps": [{"eep": "D2-05-00"}], "configuration": {"parameters": parameters}},
        authoritative=authoritative,
    )
    assert updated.channels[0].rotation_time == 1.5
    assert updated.supports_tilt is True


@pytest.mark.parametrize("embedded", [True, False])
def test_configuration_rotation_preserves_its_reported_channel(
    coord, dispatch, embedded
) -> None:
    parameters = [{"key": "rotationTime", "value": 0.0}]
    if embedded:
        parameters[0]["channel"] = 1
    else:
        parameters.insert(0, {"key": "channel", "value": 1})
    device = _apply(
        coord,
        {
            "eeps": [{"eep": "D2-05-00"}],
            "states": [{"key": "position", "value": 78, "channel": 0}],
            "configuration": {"parameters": parameters},
        },
    )
    assert device.channels[0].position == 78
    assert device.channels[0].rotation_time is None
    assert device.channels[1].rotation_time == 0


def test_refresh_configuration_preserves_newer_live_position(coord, dispatch) -> None:
    device = EnOceanDevice("DEV1", "Cover", [{"eep": "D2-05-00"}])
    device.get_or_create_channel().position = 40
    coord.devices["DEV1"] = device
    updated = _apply(
        coord,
        {
            "eeps": [{"eep": "D2-05-00"}],
            "states": [{"key": "position", "value": 78}],
            "configuration": {
                "parameters": [
                    {"key": "rotationTime", "value": 0.0},
                    {"key": "verticalMovementTime", "value": 30.0},
                ]
            },
        },
        authoritative=False,
    )
    assert updated.channels[0].position == 40
    assert updated.channels[0].rotation_time == 0
    assert updated.channels[0].vertical_movement_time == 30.0


@pytest.mark.parametrize("embedded", [True, False])
def test_refresh_configuration_changes_tilt_of_one_channel(
    coord, call_later, embedded
) -> None:
    device = EnOceanDevice("DEV1", "Cover", [{"eep": "D2-05-00"}])
    device.get_or_create_channel(0).position = 40
    device.get_or_create_channel(1).rotation_time = 1.5
    coord.devices["DEV1"] = device
    parameters = [{"key": "rotationTime", "value": 0.0, "unit": "s"}]
    if embedded:
        parameters[0]["channel"] = 1
    else:
        parameters.insert(0, {"key": "channel", "value": 1})
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    with patch(f"{MODULE}.async_dispatcher_send") as dispatch:
        updated = _apply(
            coord,
            {
                "eeps": [{"eep": "D2-05-00"}],
                "states": [{"key": "position", "value": 78}],
                "configuration": {"parameters": parameters},
            },
            authoritative=False,
        )
    assert updated.channels[1].rotation_time == 0
    assert updated.channels[0].rotation_time is None
    assert updated.channels[0].position == 40
    cancel.assert_not_called()
    dispatch.assert_called_once()


# ── End to end ──────────────────────────────────────────────────────────────


async def test_rediscovery_keeps_entities_and_live_state(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """A new stream's device list updates names and state of known devices."""
    gateway.devices = [
        device_object(
            "0507C89C",
            ["D2-05-02", "F6-03-01"],
            friendly_id="EG_Kueche",
            states=[
                {"key": "angle", "value": 0.0},
                {"key": "position", "value": 0.0},
            ],
            configuration={
                "parameters": [{"key": "rotationTime", "value": 1.73, "unit": "s"}]
            },
        )
    ]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "cover", "AABB0011_0507C89C")
    state = hass.states.get(entity_id)
    assert state.attributes["current_position"] == 100
    assert state.attributes["current_tilt_position"] == 0

    gateway.devices = [
        device_object(
            "0507C89C",
            ["D2-05-02", "F6-03-01"],
            friendly_id="EG_Kueche",
            states=[
                {"key": "angle", "value": "unknown"},
                {"key": "position", "value": 98.0},
            ],
            configuration={"parameters": [{"key": "rotationTime", "value": 0.0}]},
        )
    ]
    await gateway.close_streams()
    await wait_until(
        lambda: hass.states.get(entity_id).attributes.get("current_position") == 2
    )
    state = hass.states.get(entity_id)
    assert "current_tilt_position" not in state.attributes
    assert hass.states.async_entity_ids("cover") == [entity_id]
    coordinator = entry.runtime_data.coordinator
    assert coordinator.devices["0507C89C"].channels[0].rotation_time == 0
