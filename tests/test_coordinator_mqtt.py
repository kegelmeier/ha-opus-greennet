"""Tests for coordinator MQTT message handling and finalization."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.cover import CoverEntityFeature

from custom_components.opus_greennet.coordinator import (
    OUTBOUND_STATE_RECONCILIATION_DELAYS,
    SIGNAL_DEVICE_STATE_UPDATE,
    TELEGRAM_FINALIZE_DELAY,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.cover import OpusGreenNetCover
from custom_components.opus_greennet.enocean_device import EnOceanDevice


@pytest.fixture
def coord():
    """Create a coordinator with mocked hass for MQTT tests."""
    hass = MagicMock()
    c = OpusGreenNetCoordinator(hass, "AABB0011")
    return c


# ── _finalize_telegram ────────────────────────────────────────────────


class TestFinalizeTelegram:
    """Tests for _finalize_telegram processing."""

    def test_extracts_functions_from_from_subkey(self, coord):
        """Flattened MQTT topics nest data under 'from' — the v0.1.3 fix."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "from": {
                "friendlyId": "My Light",
                "functions": [
                    {"key": "switch", "value": "on"},
                ],
                "timestamp": "2024-06-01T12:00:00",
            },
        }

        # Pre-create the device so finalize can find it
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="My Light", eeps=[{"eep": "D2-01-02"}]
        )

        coord._finalize_telegram("DEV1")

        device = coord.devices["DEV1"]
        assert device.channels[0].is_on is True
        assert device.last_update_source == "stream/telegram/from"
        assert device.last_update_dispatched_monotonic is not None

    def test_applies_liquid_detection_from_from_subkey(self, coord):
        """F6-05-01 telegrams update the moisture state through the live path."""
        coord._telegram_data["LEAK1"] = {
            "deviceId": "LEAK1",
            "from": {
                "friendlyId": "Utility room leak sensor",
                "functions": [{"key": "liquidDetected", "value": True}],
            },
        }
        coord.devices["LEAK1"] = EnOceanDevice(
            device_id="LEAK1",
            friendly_id="Utility room leak sensor",
            eeps=[{"eep": "F6-05-01"}],
        )

        coord._finalize_telegram("LEAK1")

        device = coord.devices["LEAK1"]
        assert device.channels[0].liquid_detected is True
        assert device.last_update_source == "stream/telegram/from"

    def test_applies_to_only_state_command(self, coord):
        """Outbound command telegrams are used as optimistic state updates."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "to": {
                "functions": [{"key": "switch", "value": "on"}],
            },
        }
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        cancel_early = MagicMock()
        cancel_late = MagicMock()
        with patch(
            "custom_components.opus_greennet.coordinator.async_call_later",
            side_effect=[cancel_early, cancel_late],
        ) as mock_call_later:
            coord._finalize_telegram("DEV1")

        assert coord.devices["DEV1"].channels[0].is_on is True
        assert coord.devices["DEV1"].last_update_source == "stream/telegram/to"
        assert [call.args[1] for call in mock_call_later.call_args_list] == list(
            OUTBOUND_STATE_RECONCILIATION_DELAYS
        )
        assert coord._pending_reconciliation_queries[("DEV1", 0)] == [
            cancel_early,
            cancel_late,
        ]

    def test_applies_flat_direction_to_state_command(self, coord):
        """Native bridge HomeKit commands can arrive as flat direction=to data."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "direction": "to",
            "functions": [{"key": "switch", "value": "on"}],
        }
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        with patch(
            "custom_components.opus_greennet.coordinator.async_call_later",
            return_value=MagicMock(),
        ) as mock_call_later:
            coord._finalize_telegram("DEV1")

        assert coord.devices["DEV1"].channels[0].is_on is True
        assert coord.devices["DEV1"].last_update_source == "stream/telegram/to"
        assert mock_call_later.call_count == len(OUTBOUND_STATE_RECONCILIATION_DELAYS)

    def test_outbound_command_updates_selected_channel(self, coord):
        """The selector is preserved so an echo updates only its target channel."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "direction": "to",
            "functions": [
                {"key": "channel", "value": "1"},
                {"key": "switch", "value": "on"},
            ],
        }
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1",
            friendly_id="Two-channel switch",
            eeps=[{"eep": "D2-01-11"}],
        )

        with patch(
            "custom_components.opus_greennet.coordinator.async_call_later",
            return_value=MagicMock(),
        ):
            coord._finalize_telegram("DEV1")

        assert 0 not in coord.devices["DEV1"].channels
        assert coord.devices["DEV1"].channels[1].is_on is True
        assert ("DEV1", 1) in coord._pending_reconciliation_queries

    def test_confirmed_from_cancels_pending_reconciliation(self, coord):
        """Confirmed device reports cancel delayed status checks."""
        cancel_early = MagicMock()
        cancel_late = MagicMock()
        coord._pending_reconciliation_queries[("DEV1", 0)] = [
            cancel_early,
            cancel_late,
        ]
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "from": {
                "friendlyId": "Light",
                "functions": [{"key": "switch", "value": "off"}],
            },
        }
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )
        coord.devices["DEV1"].update_from_telegram(
            {"functions": [{"key": "switch", "value": "on"}]}
        )

        coord._finalize_telegram("DEV1")

        assert coord.devices["DEV1"].channels[0].is_on is False
        cancel_early.assert_called_once()
        cancel_late.assert_called_once()
        assert ("DEV1", 0) not in coord._pending_reconciliation_queries

    def test_skips_to_only_query_command(self, coord):
        """Outbound query telegrams do not change device state."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "direction": "to",
            "functions": [{"key": "query", "value": "status"}],
        }
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        with patch(
            "custom_components.opus_greennet.coordinator.async_dispatcher_send"
        ) as mock_dispatch:
            coord._finalize_telegram("DEV1")

        assert 0 not in coord.devices["DEV1"].channels
        mock_dispatch.assert_not_called()

    def test_skips_direction_to(self, coord):
        """Telegrams with direction='to' in effective data are skipped."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "from": {
                "direction": "to",
                "functions": [{"key": "switch", "value": "on"}],
            },
        }
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        coord._finalize_telegram("DEV1")

        assert (
            0 not in coord.devices["DEV1"].channels
            or coord.devices["DEV1"].channels[0].is_on is False
        )

    def test_auto_discovers_unknown_device(self, coord):
        """If device not yet discovered, finalize auto-creates it."""
        coord._telegram_data["NEW1"] = {
            "deviceId": "NEW1",
            "from": {
                "friendlyId": "New Light",
                "functions": [{"key": "switch", "value": "on"}],
            },
        }

        coord._finalize_telegram("NEW1")

        assert "NEW1" in coord.devices
        device = coord.devices["NEW1"]
        assert device.device_id == "NEW1"
        assert device.channels[0].is_on is True

    def test_unknown_telegram_without_eep_does_not_dispatch_discovery(self, coord):
        """Temporary telegram-only devices wait for metadata before HA discovery."""
        coord._telegram_data["NEW1"] = {
            "deviceId": "NEW1",
            "from": {
                "friendlyId": "New Light",
                "functions": [{"key": "switch", "value": "on"}],
            },
        }

        with patch(
            "custom_components.opus_greennet.coordinator.async_dispatcher_send"
        ) as mock_dispatch:
            coord._finalize_telegram("NEW1")

        assert "NEW1" in coord.devices
        assert coord.devices["NEW1"].channels[0].is_on is True
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args[0][1].startswith(
            "opus_greennet_device_state_update_"
        )

    def test_functions_as_dict_from_flattened_mqtt(self, coord):
        """Functions may arrive as a dict (from _set_nested_property indexing)."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "from": {
                "functions": {
                    "0": {"key": "dimValue", "value": "75"},
                },
            },
        }
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        coord._finalize_telegram("DEV1")

        assert coord.devices["DEV1"].channels[0].brightness == 75

    def test_ignores_incomplete_function_fragments(self, coord):
        """Partial flattened telegram functions do not dispatch stale updates."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "from": {
                "functions": [
                    {"key": "switch"},
                    {"value": "on"},
                ],
            },
        }
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        with patch(
            "custom_components.opus_greennet.coordinator.async_dispatcher_send"
        ) as mock_dispatch:
            coord._finalize_telegram("DEV1")

        assert 0 not in coord.devices["DEV1"].channels
        mock_dispatch.assert_not_called()

    def test_applies_only_complete_function_fragments(self, coord):
        """Mixed complete and partial telegram functions keep the valid update."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "from": {
                "functions": [
                    {"key": "localControl"},
                    {"key": "switch", "value": "on"},
                    {"value": "off"},
                ],
            },
        }
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        with patch(
            "custom_components.opus_greennet.coordinator.async_dispatcher_send"
        ) as mock_dispatch:
            coord._finalize_telegram("DEV1")

        assert coord.devices["DEV1"].channels[0].is_on is True
        mock_dispatch.assert_called_once()

    def test_noop_when_no_data(self, coord):
        """Calling finalize for a device with no pending data does nothing."""
        coord._finalize_telegram("NONEXISTENT")
        # Should not raise


# ── _handle_telegram_property_message ────────────────────────────────


class TestTelegramPropertyMessage:
    """Tests for stream/telegram property message handling."""

    def test_telegram_debounce_allows_fragmented_key_value_pairs(self, coord):
        """Telegram finalization waits long enough for split key/value messages."""
        msg = SimpleNamespace(
            topic="EnOcean/AABB0011/stream/telegram/DEV1/from/functions/0/key",
            payload=b"switch",
        )

        with patch(
            "custom_components.opus_greennet.coordinator.async_call_later"
        ) as mock_call_later:
            coord._handle_telegram_property_message(msg)

        assert coord._telegram_data["DEV1"]["from"]["functions"][0]["key"] == "switch"
        mock_call_later.assert_called_once()
        assert mock_call_later.call_args[0][1] == TELEGRAM_FINALIZE_DELAY


class TestAnswerMessages:
    """Tests for structured discovery and command error responses."""

    def test_discovery_unwraps_device_objects(self, coord):
        """getAnswer may wrap each device in a device property."""
        msg = SimpleNamespace(
            topic="EnOcean/AABB0011/getAnswer/devices",
            payload=json.dumps(
                {
                    "devices": [
                        {
                            "device": {
                                "deviceId": "DEV1",
                                "friendlyId": "Wrapped switch",
                                "eeps": [{"eep": "D2-01-11"}],
                            }
                        }
                    ]
                }
            ).encode(),
        )

        with patch(
            "custom_components.opus_greennet.coordinator.async_call_later",
            return_value=MagicMock(),
        ):
            coord._handle_get_answer_devices(msg)

        assert coord._device_data["DEV1"]["friendlyId"] == "Wrapped switch"
        assert "DEV1" in coord._pending_devices

    @pytest.mark.parametrize("status", [200, 201])
    def test_put_answer_accepts_success_status(self, coord, status):
        """Successful acknowledgements clear errors without stopping retries."""
        cancel = MagicMock()
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1",
            friendly_id="Switch",
            eeps=[{"eep": "D2-01-00"}],
        )
        coord.devices["DEV1"].last_command_error = "previous failure"
        coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
        msg = SimpleNamespace(
            topic="EnOcean/AABB0011/putAnswer/devices/DEV1/state",
            payload=json.dumps({"header": {"httpStatus": status}}).encode(),
        )

        with (
            patch(
                "custom_components.opus_greennet.coordinator.async_dispatcher_send"
            ) as mock_dispatch,
            patch(
                "custom_components.opus_greennet.coordinator._LOGGER.warning"
            ) as mock_warning,
        ):
            coord._handle_put_answer_state(msg)

        assert coord.devices["DEV1"].last_command_error is None
        cancel.assert_not_called()
        assert ("DEV1", 0) in coord._pending_reconciliation_queries
        mock_dispatch.assert_called_once()
        mock_warning.assert_not_called()

    def test_put_answer_records_structured_error_status(self, coord):
        """A non-success status remains visible as a command failure."""
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1",
            friendly_id="Switch",
            eeps=[{"eep": "D2-01-00"}],
        )
        payload = json.dumps(
            {"header": {"httpStatus": 400}, "error": "invalid command"}
        )
        msg = SimpleNamespace(
            topic="EnOcean/AABB0011/putAnswer/devices/DEV1/state",
            payload=payload.encode(),
        )

        coord._handle_put_answer_state(msg)

        assert (
            coord.devices["DEV1"].last_command_error
            == "The gateway returned status 400"
        )

    def test_put_answer_records_error_and_cancels_reconciliation(self, coord):
        """A gateway command error is exposed in diagnostics and stops retries."""
        cancel = MagicMock()
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1",
            friendly_id="Switch",
            eeps=[{"eep": "D2-01-00"}],
        )
        coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
        msg = SimpleNamespace(
            topic="EnOcean/AABB0011/putAnswer/devices/DEV1/state",
            payload=b"channel selector missing",
        )

        with patch(
            "custom_components.opus_greennet.coordinator.async_dispatcher_send"
        ) as mock_dispatch:
            coord._handle_put_answer_state(msg)

        assert (
            coord.devices["DEV1"].last_command_error
            == "The gateway returned invalid JSON"
        )
        cancel.assert_called_once()
        assert not coord._pending_reconciliation_queries
        mock_dispatch.assert_called_once()


# ── _finalize_device_stream ───────────────────────────────────────────


class TestFinalizeDeviceStream:
    """Tests for _finalize_device_stream processing."""

    def test_state_functions_array_format(self, coord):
        """stream/device deltas use state.functions array format."""
        cancel_query = MagicMock()
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )
        coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel_query]
        coord._device_stream_data["DEV1"] = {
            "deviceId": "DEV1",
            "state": {
                "functions": [
                    {"key": "switch", "value": "on"},
                    {"key": "dimValue", "value": "50"},
                ],
            },
        }

        coord._finalize_device_stream("DEV1")

        ch = coord.devices["DEV1"].channels[0]
        assert ch.is_on is True
        assert ch.brightness == 50
        assert coord.devices["DEV1"].last_update_source == "stream/device"
        cancel_query.assert_called_once()
        assert ("DEV1", 0) not in coord._pending_reconciliation_queries

    def test_state_functions_dict_format(self, coord):
        """state.functions may arrive as a dict from _set_nested_property."""
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )
        coord._device_stream_data["DEV1"] = {
            "deviceId": "DEV1",
            "state": {
                "functions": {
                    "0": {"key": "switch", "value": "on"},
                },
            },
        }

        coord._finalize_device_stream("DEV1")

        assert coord.devices["DEV1"].channels[0].is_on is True

    def test_states_flat_dict_format(self, coord):
        """Boot data uses states flat dict (key: value pairs)."""
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )
        coord._device_stream_data["DEV1"] = {
            "deviceId": "DEV1",
            "states": {
                "switch": "on",
                "dimValue": "80",
            },
        }

        coord._finalize_device_stream("DEV1")

        ch = coord.devices["DEV1"].channels[0]
        assert ch.is_on is True
        assert ch.brightness == 80

    def test_unknown_device_queued_for_discovery(self, coord):
        """Unknown device in stream data is queued for later discovery."""
        coord._device_stream_data["NEW1"] = {
            "deviceId": "NEW1",
            "friendlyId": "New Device",
            "state": {
                "functions": [{"key": "switch", "value": "on"}],
            },
        }

        coord._finalize_device_stream("NEW1")

        # Device should be stored in _device_data for later discovery
        assert "NEW1" in coord._device_data
        assert "NEW1" in coord._pending_devices


# ── _handle_device_property_message ──────────────────────────────────


class TestDevicePropertyMessage:
    """Tests for stream/devices property message handling."""

    def test_known_device_state_updates_immediately(self, coord):
        """Known device state from stream/devices skips discovery debounce."""
        cancel_query = MagicMock()
        coord.devices["DEV1"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Switch", eeps=[{"eep": "D2-01-00"}]
        )
        coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel_query]
        msg = SimpleNamespace(
            topic="EnOcean/AABB0011/stream/devices/DEV1/states/switch",
            payload=b"on",
        )

        with (
            patch(
                "custom_components.opus_greennet.coordinator.async_call_later"
            ) as mock_call_later,
            patch(
                "custom_components.opus_greennet.coordinator.async_dispatcher_send"
            ) as mock_dispatch,
        ):
            coord._handle_device_property_message(msg)

        assert coord.devices["DEV1"].channels[0].is_on is True
        assert coord.devices["DEV1"].last_update_source == "stream/devices"
        assert coord.devices["DEV1"].last_update_received_monotonic is not None
        assert "DEV1" not in coord._pending_devices
        cancel_query.assert_called_once()
        assert ("DEV1", 0) not in coord._pending_reconciliation_queries
        mock_call_later.assert_not_called()
        mock_dispatch.assert_called_once()

    def test_unknown_device_state_still_uses_discovery(self, coord):
        """Unknown stream/devices state remains part of delayed discovery."""
        msg = SimpleNamespace(
            topic="EnOcean/AABB0011/stream/devices/DEV1/states/switch",
            payload=b"on",
        )

        with patch(
            "custom_components.opus_greennet.coordinator.async_call_later"
        ) as mock_call_later:
            coord._handle_device_property_message(msg)

        assert coord._device_data["DEV1"]["states"]["switch"] == "on"
        assert "DEV1" in coord._pending_devices
        mock_call_later.assert_called_once()


# ── _finalize_discovery ──────────────────────────────────────────────


class TestFinalizeDiscovery:
    """Tests for _finalize_discovery creating devices."""

    def test_creates_device_from_accumulated_data(self, coord):
        """Discovery builds EnOceanDevice from accumulated _device_data."""
        coord._device_data["DEV1"] = {
            "deviceId": "DEV1",
            "friendlyId": "Living Room",
            "eeps": [{"eep": "D2-01-02"}],
            "manufacturer": "OPUS",
        }
        coord._pending_devices.add("DEV1")

        coord._finalize_discovery()

        assert "DEV1" in coord.devices
        dev = coord.devices["DEV1"]
        assert dev.device_id == "DEV1"
        assert dev.primary_eep == "D2-01-02"
        assert dev.manufacturer == "OPUS"

    def test_applies_initial_state(self, coord):
        """Discovery applies states dict to newly created devices."""
        coord._device_data["DEV1"] = {
            "deviceId": "DEV1",
            "friendlyId": "Dimmer",
            "eeps": [{"eep": "D2-01-02"}],
            "states": {
                "switch": "on",
                "dimValue": "60",
            },
        }
        coord._pending_devices.add("DEV1")

        coord._finalize_discovery()

        dev = coord.devices["DEV1"]
        ch = dev.channels[0]
        assert ch.is_on is True
        assert ch.brightness == 60

    def test_applies_initial_liquid_state(self, coord):
        """Discovery restores a valid retained F6-05-01 state."""
        coord._device_data["LEAK1"] = {
            "deviceId": "LEAK1",
            "friendlyId": "Utility room leak sensor",
            "eeps": [{"eep": "F6-05-01"}],
            "states": {"liquidDetected": False},
        }
        coord._pending_devices.add("LEAK1")

        coord._finalize_discovery()

        device = coord.devices["LEAK1"]
        assert device.channels[0].liquid_detected is False
        assert device.last_update_source == "discovery"

    def test_eeps_as_dict_from_flattened_mqtt(self, coord):
        """EEPs may arrive as a dict (from _set_nested_property indexing)."""
        coord._device_data["DEV1"] = {
            "deviceId": "DEV1",
            "friendlyId": "Switch",
            "eeps": {
                "0": {"eep": "D2-01-00"},
            },
        }
        coord._pending_devices.add("DEV1")

        coord._finalize_discovery()

        dev = coord.devices["DEV1"]
        assert dev.primary_eep == "D2-01-00"

    def test_discovery_rekeys_cached_telegram_device_by_device_id(self, coord):
        """Discovery metadata merges with an earlier telegram-only device."""
        cached = EnOceanDevice(device_id="DEV1", friendly_id="Temporary Name")
        cached.update_from_telegram({"functions": [{"key": "switch", "value": "on"}]})
        coord.devices["DEV1"] = cached
        coord._device_data["DEV1"] = {
            "deviceId": "DEV1",
            "friendlyId": "Final Name",
            "eeps": [{"eep": "D2-01-01"}],
        }
        coord._pending_devices.add("DEV1")

        with patch(
            "custom_components.opus_greennet.coordinator.async_dispatcher_send"
        ) as mock_dispatch:
            coord._finalize_discovery()

        assert list(coord.devices) == ["DEV1"]
        assert coord.devices["DEV1"].friendly_id == "Final Name"
        assert coord.devices["DEV1"].channels[0].is_on is True
        mock_dispatch.assert_called_once()


@pytest.fixture
def cover_with_pending_movement(coord):
    """A cover awaiting movement confirmation already has useful live state."""
    device = EnOceanDevice(
        device_id="DEV1", friendly_id="Shutter", eeps=[{"eep": "D2-05-00"}]
    )
    device.update_from_telegram(
        {
            "functions": [
                {"key": "position", "value": 20},
                {"key": "angle", "value": 45},
            ]
        }
    )
    device.last_command_error = "previous movement failed"
    coord.devices["DEV1"] = device
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    return device, cancel


class TestCoverRotationMetadata:
    """Reported rotation settings update covers without confirming movement."""

    @pytest.mark.parametrize(
        "state_data",
        [
            {"states": {"rotationTime": "0", "position": "20"}},
            {
                "state": {
                    "functions": [
                        {"key": "rotationTime", "value": "noRotation"},
                        {"key": "position", "value": 20},
                    ]
                }
            },
            {
                "state": {
                    "functions": {
                        "0": {"key": "rotationTime", "value": 0},
                        "1": {"key": "position", "value": 20},
                    }
                }
            },
        ],
        ids=["flat-states", "function-list", "indexed-functions"],
    )
    def test_initial_discovery_applies_rotation_setting(self, coord, state_data):
        coord._device_data["DEV1"] = {
            "deviceId": "DEV1",
            "friendlyId": "Shutter",
            "eeps": [{"eep": "D2-05-00"}],
            **state_data,
        }
        coord._pending_devices.add("DEV1")

        coord._finalize_discovery()

        device = coord.devices["DEV1"]
        assert device.channels[0].rotation_time == 0
        assert device.channels[0].position == 20
        assert device.supports_tilt is False

    def test_early_live_metadata_survives_discovery(self, coord):
        coord._device_stream_data["DEV1"] = {
            "deviceId": "DEV1",
            "friendlyId": "Shutter",
            "eeps": [{"eep": "D2-05-00"}],
            "state": {"functions": [{"key": "rotationTime", "value": "noRotation"}]},
        }

        with patch("custom_components.opus_greennet.coordinator.async_call_later"):
            coord._finalize_device_stream("DEV1")
        coord._finalize_discovery()

        assert coord.devices["DEV1"].supports_tilt is False

    def test_late_cached_setting_updates_existing_cover_without_movement_effects(
        self, coord, cover_with_pending_movement
    ):
        device, cancel = cover_with_pending_movement
        entity = OpusGreenNetCover(coord, "AABB0011", "gateway", device)
        entity.async_write_ha_state = MagicMock()
        tilt_feature = CoverEntityFeature.SET_TILT_POSITION
        assert entity.supported_features & tilt_feature
        assert entity.current_cover_tilt_position == 45

        def deliver_state_update(_hass, signal, updated_device):
            assert signal == f"{SIGNAL_DEVICE_STATE_UPDATE}_AABB0011_DEV1"
            entity._handle_state_update(updated_device)

        with (
            patch(
                "custom_components.opus_greennet.coordinator.async_dispatcher_send",
                side_effect=deliver_state_update,
            ) as dispatch,
            patch(
                "custom_components.opus_greennet.coordinator.async_call_later"
            ) as later,
        ):
            coord._handle_device_property_message(
                SimpleNamespace(
                    topic="EnOcean/AABB0011/stream/devices/DEV1/states/rotationTime",
                    payload=b"0",
                )
            )

            assert not entity.supported_features & tilt_feature
            assert entity.current_cover_tilt_position is None
            assert entity.current_cover_position == 80
            assert coord._device_data["DEV1"]["states"]["rotationTime"] == 0

            coord._handle_device_property_message(
                SimpleNamespace(
                    topic="EnOcean/AABB0011/stream/devices/DEV1/states/rotationTime",
                    payload=b"200",
                )
            )

        assert entity.supported_features & tilt_feature
        assert entity.current_cover_tilt_position == 45
        assert device.last_command_error == "previous movement failed"
        assert coord._pending_reconciliation_queries[("DEV1", 0)] == [cancel]
        assert dispatch.call_count == 2
        assert entity.async_write_ha_state.call_count == 2
        cancel.assert_not_called()
        later.assert_not_called()

    @pytest.mark.parametrize(
        "state_data",
        [
            {"states": {"position": 100, "angle": 0, "rotationTime": 0}},
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
        ids=["flat-states", "function-list"],
    )
    def test_get_refresh_updates_existing_cover_preserving_live_position(
        self, coord, cover_with_pending_movement, state_data
    ):
        original, cancel = cover_with_pending_movement
        entity = OpusGreenNetCover(coord, "AABB0011", "gateway", original)
        entity.async_write_ha_state = MagicMock()
        payload = {
            "device": {
                "deviceId": "DEV1",
                "friendlyId": "Shutter",
                "eeps": [{"eep": "D2-05-00"}],
                **state_data,
            }
        }

        with (
            patch("custom_components.opus_greennet.coordinator.async_call_later"),
            patch(
                "custom_components.opus_greennet.coordinator.async_dispatcher_send",
                side_effect=lambda _hass, _signal, updated: entity._handle_state_update(
                    updated
                ),
            ) as dispatch,
        ):
            coord._handle_get_answer_devices(
                SimpleNamespace(
                    topic="EnOcean/AABB0011/getAnswer/devices/DEV1",
                    payload=json.dumps(payload).encode(),
                )
            )
            coord._finalize_discovery()

        updated = coord.devices["DEV1"]
        assert updated is not original
        assert entity._device is updated
        assert updated.channels[0].position == 20
        assert updated.channels[0].angle == 45
        assert updated.channels[0].rotation_time == 0
        assert not entity.supported_features & CoverEntityFeature.SET_TILT_POSITION
        assert entity.current_cover_tilt_position is None
        assert coord._device_data["DEV1"] == payload["device"]
        assert updated.last_command_error == "previous movement failed"
        assert coord._pending_reconciliation_queries[("DEV1", 0)] == [cancel]
        cancel.assert_not_called()
        dispatch.assert_called_once_with(
            coord.hass, f"{SIGNAL_DEVICE_STATE_UPDATE}_AABB0011_DEV1", updated
        )

    @pytest.mark.parametrize("rotation_value", [None, "noChange", "invalid"])
    def test_rediscovery_keeps_known_rotation_when_setting_is_unknown(
        self, coord, cover_with_pending_movement, rotation_value
    ):
        original, _cancel = cover_with_pending_movement
        original.channels[0].rotation_time = 0
        data = {
            "deviceId": "DEV1",
            "eeps": [{"eep": "D2-05-00"}],
            "states": {"position": 100},
        }
        if rotation_value is not None:
            data["states"]["rotationTime"] = rotation_value

        coord._create_device_from_data("DEV1", data)

        assert coord.devices["DEV1"].channels[0].rotation_time == 0
        assert coord.devices["DEV1"].channels[0].position == 20

    @pytest.mark.parametrize(
        "functions",
        [
            [
                {"key": "channel", "value": "1"},
                {"key": "rotationTime", "value": 0},
            ],
            [{"key": "rotationTime", "value": 0, "channel": 1}],
        ],
        ids=["channel-selector", "function-channel"],
    )
    def test_rediscovery_refreshes_only_the_reported_channel(
        self, coord, cover_with_pending_movement, functions
    ):
        device, _cancel = cover_with_pending_movement
        device.get_or_create_channel(1).rotation_time = 200
        coord._create_device_from_data(
            "DEV1",
            {
                "deviceId": "DEV1",
                "eeps": [{"eep": "D2-05-00"}],
                "state": {"functions": functions},
            },
        )

        updated = coord.devices["DEV1"]
        assert updated.supports_tilt_for_channel(0) is True
        assert updated.supports_tilt_for_channel(1) is False
        assert updated.channels[0].rotation_time is None

    @pytest.mark.parametrize(
        "state_data",
        [
            {"states": {"rotationTime": 0}},
            {"state": {"functions": [{"key": "rotationTime", "value": 0}]}},
        ],
        ids=["flat-states", "function-list"],
    )
    def test_live_rotation_metadata_preserves_pending_movement(
        self, coord, cover_with_pending_movement, state_data
    ):
        device, cancel = cover_with_pending_movement
        coord._device_stream_data["DEV1"] = {"deviceId": "DEV1", **state_data}

        with (
            patch(
                "custom_components.opus_greennet.coordinator.async_call_later"
            ) as later,
            patch(
                "custom_components.opus_greennet.coordinator.async_dispatcher_send"
            ) as dispatch,
        ):
            coord._finalize_device_stream("DEV1")

        assert device.supports_tilt is False
        assert device.channels[0].position == 20
        assert device.last_command_error == "previous movement failed"
        assert coord._pending_reconciliation_queries[("DEV1", 0)] == [cancel]
        cancel.assert_not_called()
        later.assert_not_called()
        dispatch.assert_called_once()

    @pytest.mark.parametrize("direction", ["from", "to"])
    @pytest.mark.parametrize("rotation_value", ["noRotation", "noChange"])
    def test_rotation_telegram_does_not_confirm_or_retry_movement(
        self, coord, cover_with_pending_movement, direction, rotation_value
    ):
        device, cancel = cover_with_pending_movement
        device.channels[0].rotation_time = 0
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            direction: {
                "functions": [{"key": "rotationTime", "value": rotation_value}]
            },
        }

        with patch(
            "custom_components.opus_greennet.coordinator.async_call_later"
        ) as later:
            coord._finalize_telegram("DEV1")

        assert device.channels[0].rotation_time == 0
        assert device.last_command_error == "previous movement failed"
        assert coord._pending_reconciliation_queries[("DEV1", 0)] == [cancel]
        cancel.assert_not_called()
        later.assert_not_called()

    @pytest.mark.parametrize("direction", ["from", "to"])
    def test_mixed_rotation_and_position_telegram_still_reconciles_movement(
        self, coord, cover_with_pending_movement, direction
    ):
        device, cancel = cover_with_pending_movement
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            direction: {
                "functions": [
                    {"key": "rotationTime", "value": 0},
                    {"key": "position", "value": 70},
                ]
            },
        }

        with patch(
            "custom_components.opus_greennet.coordinator.async_call_later",
            return_value=MagicMock(),
        ) as later:
            coord._finalize_telegram("DEV1")

        assert device.channels[0].rotation_time == 0
        assert device.channels[0].position == 70
        cancel.assert_called_once()
        if direction == "to":
            assert [call.args[1] for call in later.call_args_list] == list(
                OUTBOUND_STATE_RECONCILIATION_DELAYS
            )
            assert device.last_command_error == "previous movement failed"
        else:
            later.assert_not_called()
            assert ("DEV1", 0) not in coord._pending_reconciliation_queries
            assert device.last_command_error is None
