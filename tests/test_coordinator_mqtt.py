"""Tests for coordinator MQTT message handling and finalization."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
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
        coord.devices["My Light"] = EnOceanDevice(
            device_id="DEV1", friendly_id="My Light", eeps=[{"eep": "D2-01-02"}]
        )

        coord._finalize_telegram("DEV1")

        device = coord.devices["My Light"]
        assert device.channels[0].is_on is True

    def test_skips_to_only_telegram(self, coord):
        """Telegrams with only 'to' data (outbound commands) are skipped."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "to": {
                "functions": [{"key": "switch", "value": "on"}],
            },
        }
        coord.devices["Light"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        coord._finalize_telegram("DEV1")

        # Device state should NOT have changed
        assert 0 not in coord.devices["Light"].channels or coord.devices["Light"].channels[0].is_on is False

    def test_skips_direction_to(self, coord):
        """Telegrams with direction='to' in effective data are skipped."""
        coord._telegram_data["DEV1"] = {
            "deviceId": "DEV1",
            "from": {
                "direction": "to",
                "functions": [{"key": "switch", "value": "on"}],
            },
        }
        coord.devices["Light"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        coord._finalize_telegram("DEV1")

        assert 0 not in coord.devices["Light"].channels or coord.devices["Light"].channels[0].is_on is False

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

        assert "New Light" in coord.devices
        device = coord.devices["New Light"]
        assert device.device_id == "NEW1"
        assert device.channels[0].is_on is True

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
        coord.devices["Light"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )

        coord._finalize_telegram("DEV1")

        assert coord.devices["Light"].channels[0].brightness == 75

    def test_noop_when_no_data(self, coord):
        """Calling finalize for a device with no pending data does nothing."""
        coord._finalize_telegram("NONEXISTENT")
        # Should not raise


# ── _finalize_device_stream ───────────────────────────────────────────


class TestFinalizeDeviceStream:
    """Tests for _finalize_device_stream processing."""

    def test_state_functions_array_format(self, coord):
        """stream/device deltas use state.functions array format."""
        coord.devices["Light"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Light", eeps=[{"eep": "D2-01-02"}]
        )
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

        ch = coord.devices["Light"].channels[0]
        assert ch.is_on is True
        assert ch.brightness == 50

    def test_state_functions_dict_format(self, coord):
        """state.functions may arrive as a dict from _set_nested_property."""
        coord.devices["Light"] = EnOceanDevice(
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

        assert coord.devices["Light"].channels[0].is_on is True

    def test_states_flat_dict_format(self, coord):
        """Boot data uses states flat dict (key: value pairs)."""
        coord.devices["Light"] = EnOceanDevice(
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

        ch = coord.devices["Light"].channels[0]
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
        coord.devices["Switch"] = EnOceanDevice(
            device_id="DEV1", friendly_id="Switch", eeps=[{"eep": "D2-01-00"}]
        )
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

        assert coord.devices["Switch"].channels[0].is_on is True
        assert "DEV1" not in coord._pending_devices
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
            await coord.async_send_command(
                "DEV1", [{"key": "switch", "value": "on"}]
            )

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

    @pytest.mark.asyncio
    async def test_publishes_with_qos_1(self):
        """Commands are published with QoS 1."""
        hass = MagicMock()
        coord = OpusGreenNetCoordinator(hass, "AABB0011")

        with patch(
            "custom_components.opus_greennet.coordinator.mqtt.async_publish",
            new_callable=AsyncMock,
        ) as mock_publish:
            await coord.async_send_command(
                "DEV1", [{"key": "dimValue", "value": "50"}]
            )

            call_kwargs = mock_publish.call_args
            # qos is passed as keyword or positional
            assert call_kwargs[1].get("qos", call_kwargs[0][3] if len(call_kwargs[0]) > 3 else None) == 1


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

        assert "Living Room" in coord.devices
        dev = coord.devices["Living Room"]
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

        dev = coord.devices["Dimmer"]
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

        dev = coord.devices["Switch"]
        assert dev.primary_eep == "D2-01-00"
