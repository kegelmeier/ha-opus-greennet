"""Tests for coordinator MQTT message handling and finalization."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.opus_greennet.coordinator import (
    OUTBOUND_STATE_RECONCILIATION_DELAYS,
    TELEGRAM_FINALIZE_DELAY,
    OpusGreenNetCoordinator,
)
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

        assert coord.devices["DEV1"].last_command_error == payload

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

        assert coord.devices["DEV1"].last_command_error == "channel selector missing"
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


# ── async_send_command ────────────────────────────────────────────────


class TestAsyncSendCommand:
    """Tests for async_send_command MQTT publishing."""

    @pytest.mark.asyncio
    async def test_publishes_correct_json(self):
        """async_send_command publishes correct JSON to put/devices/{id}/state."""
        hass = MagicMock()
        coord = OpusGreenNetCoordinator(hass, "AABB0011")

        with patch(
            "custom_components.opus_greennet.coordinator.mqtt.async_publish",
            new_callable=AsyncMock,
        ) as mock_publish:
            await coord.async_send_command("DEV1", [{"key": "switch", "value": "on"}])

            mock_publish.assert_called_once()
            call_args = mock_publish.call_args
            topic = call_args[0][1]  # positional: hass, topic, payload, ...
            payload_str = call_args[0][2]
            payload = json.loads(payload_str)

            assert topic == "EnOcean/AABB0011/put/devices/DEV1/state"
            assert payload == {
                "state": {
                    "functions": [{"key": "switch", "value": "on"}],
                }
            }
            assert call_args.kwargs == {"qos": 1, "retain": False}

    @pytest.mark.asyncio
    async def test_publish_error_is_propagated(self):
        """Home Assistant sees MQTT publish failures instead of a false success."""
        coord = OpusGreenNetCoordinator(MagicMock(), "AABB0011")

        with (
            patch(
                "custom_components.opus_greennet.coordinator.mqtt.async_publish",
                new_callable=AsyncMock,
                side_effect=RuntimeError("MQTT disconnected"),
            ),
            pytest.raises(RuntimeError, match="MQTT disconnected"),
        ):
            await coord.async_send_command("DEV1", [{"key": "switch", "value": "on"}])

    @pytest.mark.asyncio
    async def test_publishes_with_qos_1(self):
        """Commands are published with QoS 1."""
        hass = MagicMock()
        coord = OpusGreenNetCoordinator(hass, "AABB0011")

        with patch(
            "custom_components.opus_greennet.coordinator.mqtt.async_publish",
            new_callable=AsyncMock,
        ) as mock_publish:
            await coord.async_send_command("DEV1", [{"key": "dimValue", "value": "50"}])

            call_kwargs = mock_publish.call_args
            # qos is passed as keyword or positional
            assert (
                call_kwargs[1].get(
                    "qos", call_kwargs[0][3] if len(call_kwargs[0]) > 3 else None
                )
                == 1
            )

    @pytest.mark.asyncio
    async def test_query_device_status(self):
        """Device status queries use the OPUS query=status function."""
        hass = MagicMock()
        coord = OpusGreenNetCoordinator(hass, "AABB0011")

        with patch(
            "custom_components.opus_greennet.coordinator.mqtt.async_publish",
            new_callable=AsyncMock,
        ) as mock_publish:
            await coord.async_query_device_status("DEV1")

        payload = json.loads(mock_publish.call_args[0][2])
        assert payload == {
            "state": {
                "functions": [{"key": "query", "value": "status"}],
            }
        }


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
