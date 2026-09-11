"""Data coordinator for Opus GreenNet Bridge integration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from time import monotonic
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt import ReceiveMessage
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import (
    DOMAIN,
    KEY_CHANNEL,
    KEY_ROTATION_TIME,
    KNOWN_STATE_KEYS,
    TOPIC_BASE,
    TOPIC_GET_ANSWER_DEVICE_CONFIGURATION,
    TOPIC_GET_ANSWER_DEVICE_PARAMETERS,
    TOPIC_GET_ANSWER_DEVICE_PROFILE,
    TOPIC_GET_ANSWER_DEVICES,
    TOPIC_GET_ANSWER_SYSTEM_INFO,
    TOPIC_GET_ANSWER_SYSTEM_UPTIME,
    TOPIC_GET_DEVICE_CONFIGURATION,
    TOPIC_GET_DEVICE_PARAMETERS,
    TOPIC_GET_DEVICE_PROFILE,
    TOPIC_GET_DEVICES,
    TOPIC_GET_SYSTEM_INFO,
    TOPIC_GET_SYSTEM_UPTIME,
    TOPIC_PUT_DEVICE_CONFIGURATION,
    TOPIC_PUT_STATE,
    TOPIC_SUB_DEVICE_STREAM_ALL,
    TOPIC_SUB_DEVICES_ALL,
    TOPIC_SUB_PUT_ANSWER_STATE,
    TOPIC_SUB_TELEGRAM_FROM_ALL,
)
from .enocean_device import EnOceanDevice

_LOGGER = logging.getLogger(__name__)

# Dispatcher signals
SIGNAL_DEVICE_DISCOVERED = f"{DOMAIN}_device_discovered"
SIGNAL_DEVICE_STATE_UPDATE = f"{DOMAIN}_device_state_update"

DEVICE_STREAM_FINALIZE_DELAY = 0.02
TELEGRAM_FINALIZE_DELAY = 0.15
RAW_MQTT_DEBUG_PAYLOAD_LIMIT = 500
OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)

# Regex to parse device topics (plural - initial full state at boot)
# EnOcean/{EAG}/stream/devices/{DeviceID}/{property}
DEVICE_TOPIC_PATTERN = re.compile(r"EnOcean/([^/]+)/stream/devices/([^/]+)/(.+)")

# Regex to parse telegram topics
# EnOcean/{EAG}/stream/telegram/{DeviceID}/{property}
TELEGRAM_TOPIC_PATTERN = re.compile(r"EnOcean/([^/]+)/stream/telegram/([^/]+)/(.+)")

# Regex to parse device stream topics (singular - live deltas)
# EnOcean/{EAG}/stream/device/{DeviceID}/{property}
DEVICE_STREAM_TOPIC_PATTERN = re.compile(r"EnOcean/([^/]+)/stream/device/([^/]+)/(.+)")
PUT_ANSWER_STATE_TOPIC_PATTERN = re.compile(
    r"EnOcean/([^/]+)/putAnswer/devices/([^/]+)/state"
)


class OpusGreenNetCoordinator:
    """Coordinator for managing MQTT communication with Opus GreenNet Bridge."""

    def __init__(self, hass: HomeAssistant, eag_id: str) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.eag_id = eag_id
        self.devices: dict[str, EnOceanDevice] = {}
        self._device_data: dict[str, dict[str, Any]] = {}  # Raw device properties
        self._telegram_data: dict[str, dict[str, Any]] = {}  # Raw telegram properties
        self._device_stream_data: dict[str, dict[str, Any]] = {}  # Device stream deltas
        self._subscriptions: list[Callable[[], None]] = []
        self._discovery_complete = False
        self._pending_devices: set[str] = set()
        self._pending_telegrams: dict[str, Callable | None] = {}  # Timers per device
        self._pending_device_streams: dict[
            str, Callable | None
        ] = {}  # Timers per device
        self._telegram_received_at: dict[str, float] = {}
        self._device_stream_received_at: dict[str, float] = {}
        self._pending_reconciliation_queries: dict[
            tuple[str, int], list[Callable[[], None]]
        ] = {}
        self._temporary_cancellations: list[Callable[[], None]] = []
        self._discovery_timer: Callable | None = None
        # Gateway info
        self.gateway_info: dict[str, Any] = {}
        self.gateway_uptime: str | None = None

    @property
    def available(self) -> bool:
        """Return whether Home Assistant's MQTT client is connected."""
        return mqtt.is_connected(self.hass)

    async def async_setup(self) -> bool:
        """Set up the coordinator and start MQTT subscriptions."""
        _LOGGER.debug("Setting up Opus GreenNet coordinator for EAG %s", self.eag_id)

        # Subscribe to telegram stream with # wildcard (flattened structure)
        topic_telegram = TOPIC_SUB_TELEGRAM_FROM_ALL.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
        self._subscriptions.append(
            await mqtt.async_subscribe(
                self.hass, topic_telegram, self._handle_telegram_property_message, qos=1
            )
        )
        _LOGGER.info("Subscribed to telegram topic: %s", topic_telegram)

        # Subscribe to ALL device properties with # wildcard (initial full state)
        topic_devices_all = TOPIC_SUB_DEVICES_ALL.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
        self._subscriptions.append(
            await mqtt.async_subscribe(
                self.hass,
                topic_devices_all,
                self._handle_device_property_message,
                qos=1,
            )
        )
        _LOGGER.info("Subscribed to devices topic: %s", topic_devices_all)

        # Subscribe to device stream (singular) for live delta updates
        topic_device_stream = TOPIC_SUB_DEVICE_STREAM_ALL.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
        self._subscriptions.append(
            await mqtt.async_subscribe(
                self.hass,
                topic_device_stream,
                self._handle_device_stream_message,
                qos=1,
            )
        )
        _LOGGER.info("Subscribed to device stream topic: %s", topic_device_stream)

        # Subscribe to getAnswer/devices for active discovery
        topic_get_answer = TOPIC_GET_ANSWER_DEVICES.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
        self._subscriptions.append(
            await mqtt.async_subscribe(
                self.hass,
                topic_get_answer,
                self._handle_get_answer_devices,
                qos=1,
            )
        )
        _LOGGER.info("Subscribed to getAnswer topic: %s", topic_get_answer)

        topic_put_answer = TOPIC_SUB_PUT_ANSWER_STATE.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
        self._subscriptions.append(
            await mqtt.async_subscribe(
                self.hass,
                topic_put_answer,
                self._handle_put_answer_state,
                qos=1,
            )
        )

        # Subscribe to gateway system info answers
        topic_system_info = TOPIC_GET_ANSWER_SYSTEM_INFO.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
        self._subscriptions.append(
            await mqtt.async_subscribe(
                self.hass,
                topic_system_info,
                self._handle_system_info,
                qos=1,
            )
        )

        topic_system_uptime = TOPIC_GET_ANSWER_SYSTEM_UPTIME.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
        self._subscriptions.append(
            await mqtt.async_subscribe(
                self.hass,
                topic_system_uptime,
                self._handle_system_uptime,
                qos=1,
            )
        )

        # Request device list via GET (active discovery)
        topic_get = TOPIC_GET_DEVICES.format(base=TOPIC_BASE, eag_id=self.eag_id)
        await mqtt.async_publish(self.hass, topic_get, "", qos=1)
        _LOGGER.info("Requested device list via GET: %s", topic_get)

        # Request gateway system info
        await self._request_gateway_info()

        # Schedule device discovery finalization after 5 seconds
        self._discovery_timer = async_call_later(self.hass, 5, self._finalize_discovery)

        return True

    async def async_unload(self) -> None:
        """Unload the coordinator and unsubscribe from MQTT."""
        _LOGGER.debug("Unloading Opus GreenNet coordinator for EAG %s", self.eag_id)
        if self._discovery_timer:
            self._discovery_timer()
            self._discovery_timer = None
        for cancel in self._pending_telegrams.values():
            if cancel:
                cancel()
        for cancel in self._pending_device_streams.values():
            if cancel:
                cancel()
        for device_id, channel_id in list(self._pending_reconciliation_queries):
            self._cancel_reconciliation_queries(device_id, channel_id)
        for cancel in self._temporary_cancellations:
            cancel()
        self._temporary_cancellations.clear()
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()
        self._pending_telegrams.clear()
        self._pending_device_streams.clear()
        self._telegram_data.clear()
        self._device_stream_data.clear()
        self._telegram_received_at.clear()
        self._device_stream_received_at.clear()

    # ──────────────────────────────────────────────────────────────────────
    # Device property messages (stream/devices - initial full state at boot)
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _handle_device_property_message(self, msg: ReceiveMessage) -> None:
        """Handle incoming device property messages from flattened MQTT structure."""
        try:
            self._log_raw_mqtt_message("stream/devices", msg)

            match = DEVICE_TOPIC_PATTERN.match(msg.topic)
            if not match:
                return

            eag_id, device_id, property_path = match.groups()

            if eag_id != self.eag_id:
                return

            received_at = monotonic()
            self._log_latency_received(
                "stream/devices", device_id, property_path, received_at
            )

            if device_id not in self._device_data:
                self._device_data[device_id] = {"deviceId": device_id}
                if self._find_device_by_id(device_id) is None:
                    self._pending_devices.add(device_id)

            payload = (
                msg.payload.decode()
                if isinstance(msg.payload, bytes)
                else str(msg.payload)
            )
            self._set_nested_property(
                self._device_data[device_id], property_path, payload
            )

            if self._apply_known_device_state_property(
                device_id, property_path, payload, received_at
            ):
                return

            # Reset discovery timer on each message
            if self._discovery_timer:
                self._discovery_timer()
            self._discovery_timer = async_call_later(
                self.hass, 2, self._finalize_discovery
            )

        except Exception as err:
            _LOGGER.exception("Error handling device property message: %s", err)

    # ──────────────────────────────────────────────────────────────────────
    # Device stream messages (stream/device - live deltas)
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _handle_device_stream_message(self, msg: ReceiveMessage) -> None:
        """Handle live device model delta messages from stream/device/{EURID}."""
        try:
            self._log_raw_mqtt_message("stream/device", msg)

            match = DEVICE_STREAM_TOPIC_PATTERN.match(msg.topic)
            if not match:
                return

            eag_id, device_id, property_path = match.groups()

            if eag_id != self.eag_id:
                return

            received_at = monotonic()
            self._device_stream_received_at.setdefault(device_id, received_at)
            self._log_latency_received(
                "stream/device", device_id, property_path, received_at
            )

            if device_id not in self._device_stream_data:
                self._device_stream_data[device_id] = {"deviceId": device_id}

            payload = (
                msg.payload.decode()
                if isinstance(msg.payload, bytes)
                else str(msg.payload)
            )
            self._set_nested_property(
                self._device_stream_data[device_id], property_path, payload
            )

            # Reset stream timer for this device - finalize after short delay
            if (
                device_id in self._pending_device_streams
                and self._pending_device_streams[device_id]
            ):
                self._pending_device_streams[device_id]()

            @callback
            def finalize_callback(_now, did=device_id):
                self._finalize_device_stream(did)

            # Short debounce: gateway publishes all properties within ms,
            # so this is ample to collect a full delta while keeping UI snappy.
            self._pending_device_streams[device_id] = async_call_later(
                self.hass, DEVICE_STREAM_FINALIZE_DELAY, finalize_callback
            )

        except Exception as err:
            _LOGGER.exception("Error handling device stream message: %s", err)

    @callback
    def _finalize_device_stream(self, device_id: str) -> None:
        """Finalize device stream delta processing."""
        if device_id not in self._device_stream_data:
            return

        stream_data = self._device_stream_data.pop(device_id)
        self._pending_device_streams.pop(device_id, None)
        received_at = self._device_stream_received_at.pop(device_id, None)
        finalized_at = monotonic()

        device = self.devices.get(device_id)

        if device is None:
            # Device not yet discovered - store for later discovery
            self._device_data[device_id] = stream_data
            self._pending_devices.add(device_id)
            if not self._discovery_timer:
                self._discovery_timer = async_call_later(
                    self.hass, 2, self._finalize_discovery
                )
            return

        functions = self._device_state_functions(stream_data)

        if functions:
            self._log_latency_finalized(
                "stream/device", device_id, received_at, finalized_at, len(functions)
            )
            channel_id = self._channel_from_functions(functions)
            if self._has_operational_state(functions):
                self._cancel_reconciliation_queries(device_id, channel_id)
                device.last_command_error = None
            telegram = {"functions": functions}
            device.update_from_telegram(telegram)

            signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
            self._mark_and_log_dispatch(
                device, "stream/device", signal, received_at, finalized_at
            )
            async_dispatcher_send(self.hass, signal, device)

    # ──────────────────────────────────────────────────────────────────────
    # GET answer handler (active discovery)
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _handle_get_answer_devices(self, msg: ReceiveMessage) -> None:
        """Handle getAnswer/devices response with device data."""
        try:
            self._log_raw_mqtt_message("getAnswer/devices", msg)

            payload = msg.payload
            if isinstance(payload, bytes):
                payload = payload.decode()

            data = json.loads(payload)

            # Response may be a list of devices or a single device object
            if isinstance(data, list):
                devices_list = data
            elif isinstance(data, dict):
                # Could be a single device or a wrapper with device list
                if "devices" in data:
                    devices_list = data["devices"]
                else:
                    devices_list = [data]
            else:
                return

            if not isinstance(devices_list, list):
                return

            for raw_device_data in devices_list:
                if not isinstance(raw_device_data, dict):
                    continue
                device_data = raw_device_data.get("device", raw_device_data)
                if not isinstance(device_data, dict):
                    continue
                device_id = str(device_data.get("deviceId", ""))
                if device_id:
                    self._device_data[device_id] = device_data
                    self._pending_devices.add(device_id)

            # Reset discovery timer
            if self._discovery_timer:
                self._discovery_timer()
            self._discovery_timer = async_call_later(
                self.hass, 2, self._finalize_discovery
            )

        except json.JSONDecodeError:
            # Not JSON - might be a flattened property, ignore
            pass
        except Exception as err:
            _LOGGER.exception("Error handling getAnswer/devices: %s", err)

    @callback
    def _handle_put_answer_state(self, msg: ReceiveMessage) -> None:
        """Handle the protocol's asynchronous command acknowledgement."""
        match = PUT_ANSWER_STATE_TOPIC_PATTERN.fullmatch(msg.topic)
        if not match or match.group(1) != self.eag_id:
            return

        device_id = match.group(2)
        payload = (
            msg.payload.decode(errors="replace")
            if isinstance(msg.payload, bytes)
            else str(msg.payload)
        )

        status: int | None = None
        try:
            response = json.loads(payload)
            if isinstance(response, dict):
                header = response.get("header")
                if isinstance(header, dict):
                    status = int(header["httpStatus"])
        except json.JSONDecodeError, KeyError, TypeError, ValueError:
            pass

        device = self.get_device(device_id)
        if status is not None and 200 <= status < 300:
            _LOGGER.debug(
                "OPUS command accepted for %s with HTTP status %d",
                device_id,
                status,
            )
            if device is not None and device.last_command_error is not None:
                device.last_command_error = None
                signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
                async_dispatcher_send(self.hass, signal, device)
            return

        error = payload or "Gateway rejected the state command"
        _LOGGER.warning("OPUS command failed for %s: %s", device_id, error)

        if device is None:
            return

        device.last_command_error = error
        self._cancel_reconciliation_queries(device_id)
        signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
        async_dispatcher_send(self.hass, signal, device)

    # ──────────────────────────────────────────────────────────────────────
    # Gateway system info
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _handle_system_info(self, msg: ReceiveMessage) -> None:
        """Handle gateway system info response."""
        try:
            payload = msg.payload
            if isinstance(payload, bytes):
                payload = payload.decode()
            data = json.loads(payload)
            if isinstance(data, dict):
                self.gateway_info = data
                _LOGGER.info("Gateway info: %s", data)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as err:
            _LOGGER.debug("Could not parse system info: %s", err)

    @callback
    def _handle_system_uptime(self, msg: ReceiveMessage) -> None:
        """Handle gateway uptime response."""
        try:
            payload = msg.payload
            if isinstance(payload, bytes):
                payload = payload.decode()
            self.gateway_uptime = payload
            _LOGGER.debug("Gateway uptime: %s", payload)
        except UnicodeDecodeError as err:
            _LOGGER.debug("Could not parse system uptime: %s", err)

    async def _request_gateway_info(self) -> None:
        """Request gateway system info and uptime."""
        topic_info = TOPIC_GET_SYSTEM_INFO.format(base=TOPIC_BASE, eag_id=self.eag_id)
        topic_uptime = TOPIC_GET_SYSTEM_UPTIME.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
        await mqtt.async_publish(self.hass, topic_info, "", qos=1)
        await mqtt.async_publish(self.hass, topic_uptime, "", qos=1)

    # ──────────────────────────────────────────────────────────────────────
    # Shared helpers
    # ──────────────────────────────────────────────────────────────────────

    def _log_raw_mqtt_message(self, source: str, msg: ReceiveMessage) -> None:
        """Log raw OPUS MQTT message details when debug logging is enabled."""
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return

        payload = msg.payload
        if isinstance(payload, bytes):
            payload_text = payload.decode("utf-8", errors="replace")
        else:
            payload_text = str(payload)

        if len(payload_text) > RAW_MQTT_DEBUG_PAYLOAD_LIMIT:
            payload_text = (
                f"{payload_text[:RAW_MQTT_DEBUG_PAYLOAD_LIMIT]}..."
                f" [truncated {len(payload_text)} chars]"
            )

        _LOGGER.debug(
            "Raw OPUS MQTT %s: topic=%s payload=%r",
            source,
            msg.topic,
            payload_text,
        )

    def _duration_ms(self, start: float | None, end: float) -> str:
        """Return a displayable millisecond duration."""
        if start is None:
            return "unknown"
        return f"{(end - start) * 1000:.1f}"

    def _log_latency_received(
        self, source: str, device_id: str, property_path: str, received_at: float
    ) -> None:
        """Log the point where a valid MQTT state message enters the integration."""
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return

        _LOGGER.debug(
            "OPUS update latency received: source=%s device_id=%s path=%s "
            "received_at=%.6f",
            source,
            device_id,
            property_path,
            received_at,
        )

    def _log_latency_finalized(
        self,
        source: str,
        device_id: str,
        received_at: float | None,
        finalized_at: float,
        function_count: int,
    ) -> None:
        """Log when a debounced MQTT update has been converted to functions."""
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return

        _LOGGER.debug(
            "OPUS update latency finalized: source=%s device_id=%s "
            "functions=%d receive_to_finalize_ms=%s",
            source,
            device_id,
            function_count,
            self._duration_ms(received_at, finalized_at),
        )

    def _mark_and_log_dispatch(
        self,
        device: EnOceanDevice,
        source: str,
        signal: str,
        received_at: float | None,
        finalized_at: float | None,
    ) -> None:
        """Stamp a device update and log just before dispatcher notification."""
        dispatched_at = monotonic()
        device.last_update_source = source
        device.last_update_received_monotonic = received_at
        device.last_update_finalized_monotonic = finalized_at
        device.last_update_dispatched_monotonic = dispatched_at

        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return

        _LOGGER.debug(
            "OPUS update latency dispatch: source=%s device_id=%s "
            "friendly_id=%s signal=%s receive_to_dispatch_ms=%s "
            "finalize_to_dispatch_ms=%s",
            source,
            device.device_id,
            device.friendly_id,
            signal,
            self._duration_ms(received_at, dispatched_at),
            self._duration_ms(finalized_at, dispatched_at),
        )

    def _find_device_by_id(self, device_id: str) -> tuple[str, EnOceanDevice] | None:
        """Find an existing device by its stable EURID."""
        if (device := self.devices.get(device_id)) is None:
            return None
        return device_id, device

    def _apply_known_device_state_property(
        self,
        device_id: str,
        property_path: str,
        payload: str,
        received_at: float | None = None,
    ) -> bool:
        """Apply stream/devices state updates immediately for known devices.

        stream/devices is primarily a startup snapshot, but some bridges may also
        publish local-control state there. Once a device is known, state updates
        should not wait on the discovery debounce.
        """
        if not property_path.startswith("states/"):
            return False

        device_entry = self._find_device_by_id(device_id)
        if device_entry is None:
            return False

        state_key = property_path.removeprefix("states/").split("/", 1)[0]
        if state_key not in KNOWN_STATE_KEYS:
            return False

        _, device = device_entry
        value = self._parse_value(payload)
        if state_key != KEY_ROTATION_TIME:
            self._cancel_reconciliation_queries(device_id)
            device.last_command_error = None
        device.update_from_telegram(
            {
                "functions": [
                    {
                        "key": state_key,
                        "value": value,
                    }
                ]
            }
        )

        signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
        self._log_latency_finalized(
            "stream/devices", device_id, received_at, received_at or monotonic(), 1
        )
        self._mark_and_log_dispatch(
            device, "stream/devices", signal, received_at, received_at
        )
        _LOGGER.debug(
            "Fast stream/devices state update for %s: %s=%r",
            device_id,
            state_key,
            value,
        )
        async_dispatcher_send(self.hass, signal, device)
        return True

    def _cancel_reconciliation_queries(
        self, device_id: str, channel_id: int | None = None
    ) -> None:
        """Cancel delayed status queries for one device or channel."""
        keys = [
            key
            for key in self._pending_reconciliation_queries
            if key[0] == device_id and (channel_id is None or key[1] == channel_id)
        ]
        for key in keys:
            for cancel in self._pending_reconciliation_queries.pop(key, []):
                cancel()

    def _schedule_reconciliation_queries(self, device_id: str, channel_id: int) -> None:
        """Schedule delayed status checks after an optimistic state update."""
        key = (device_id, channel_id)
        self._cancel_reconciliation_queries(device_id, channel_id)
        self._pending_reconciliation_queries[key] = []

        for delay in OUTBOUND_STATE_RECONCILIATION_DELAYS:

            @callback
            def query_callback(_now, did=device_id, channel=channel_id, seconds=delay):
                _LOGGER.debug(
                    "Querying OPUS status for %s channel %s %.0fs after command",
                    did,
                    channel,
                    seconds,
                )
                self.hass.async_create_task(
                    self.async_query_device_status(did, channel)
                )

            self._pending_reconciliation_queries[key].append(
                async_call_later(self.hass, delay, query_callback)
            )

    @staticmethod
    def _channel_from_functions(functions: list[dict[str, Any]]) -> int:
        """Return the telegram channel selector, defaulting to channel zero."""
        for function in functions:
            if function.get("key") != KEY_CHANNEL:
                continue
            try:
                return int(function.get("value", 0))
            except TypeError, ValueError:
                return 0
        return 0

    def _set_nested_property(self, data: dict, path: str, value: str) -> None:
        """Set a nested property in a dict using a path like 'eeps/0/eep'."""
        parts = path.split("/")
        current = data

        for i, part in enumerate(parts[:-1]):
            # Check if next part is a number (array index)
            if parts[i + 1].isdigit():
                if part not in current:
                    current[part] = []
                current = current[part]
            elif part.isdigit():
                # This is an array index
                idx = int(part)
                while len(current) <= idx:
                    current.append({})
                current = current[idx]
            else:
                if part not in current:
                    current[part] = {}
                current = current[part]

        # Set the final value
        final_key = parts[-1]
        if final_key.isdigit():
            idx = int(final_key)
            while len(current) <= idx:
                current.append(None)
            current[idx] = self._parse_value(value)
        else:
            current[final_key] = self._parse_value(value)

    def _parse_value(self, value: str) -> Any:
        """Parse a string value to appropriate type."""
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value

    # ──────────────────────────────────────────────────────────────────────
    # Device discovery finalization
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _finalize_discovery(self, *args) -> None:
        """Finalize device discovery after receiving all properties."""
        _LOGGER.info(
            "Finalizing device discovery, found %d devices", len(self._device_data)
        )

        for device_id, data in self._device_data.items():
            if device_id in self._pending_devices:
                self._pending_devices.discard(device_id)
                self._create_device_from_data(device_id, data)

        self._discovery_complete = True

    def _create_device_from_data(self, device_id: str, data: dict) -> None:
        """Create an EnOceanDevice from collected property data."""
        try:
            friendly_id = data.get("friendlyId", device_id)
            existing_entry = self._find_device_by_id(device_id)
            existing_device = existing_entry[1] if existing_entry else None

            # Build EEPs list
            eeps = []
            eeps_data = data.get("eeps", {})
            if isinstance(eeps_data, list):
                eeps = eeps_data
            elif isinstance(eeps_data, dict):
                for idx in sorted(
                    eeps_data.keys(), key=lambda x: int(x) if x.isdigit() else x
                ):
                    eep_entry = eeps_data[idx]
                    if isinstance(eep_entry, dict):
                        eeps.append(eep_entry)
                    elif isinstance(eep_entry, str):
                        eeps.append({"eep": eep_entry})

            is_new = existing_device is None
            was_incomplete = existing_device is not None and not existing_device.eeps

            device = EnOceanDevice(
                device_id=device_id,
                friendly_id=friendly_id,
                eeps=eeps,
                manufacturer=data.get("manufacturer", ""),
                physical_device=data.get("physicalDevice", ""),
                first_seen=str(data.get("firstSeen", "")),
                last_seen=str(data.get("lastSeen", "")),
                dbm=data.get("dbm"),
            )

            # Preserve existing channel state or apply initial state from discovery
            rotation_functions = []
            if existing_device is not None:
                device.channels = existing_device.channels
                device.profile = existing_device.profile
                device.last_update_source = existing_device.last_update_source
                device.last_update_received_monotonic = (
                    existing_device.last_update_received_monotonic
                )
                device.last_update_finalized_monotonic = (
                    existing_device.last_update_finalized_monotonic
                )
                device.last_update_dispatched_monotonic = (
                    existing_device.last_update_dispatched_monotonic
                )
                device.last_command_error = existing_device.last_command_error
                # Refresh configuration without replacing newer position/state
                # values with a potentially stale discovery snapshot.
                rotation_functions = [
                    function
                    for function in self._device_state_functions(data)
                    if function.get("key") in (KEY_CHANNEL, KEY_ROTATION_TIME)
                ]
                if any(f.get("key") == KEY_ROTATION_TIME for f in rotation_functions):
                    device.update_from_telegram({"functions": rotation_functions})
            else:
                self._apply_initial_state(device, data)

            self.devices[device_id] = device

            _LOGGER.info(
                "Device %s: %s (%s) - EEPs: %s - Type: %s",
                "discovered" if is_new else "updated",
                friendly_id,
                device_id,
                [eep.get("eep") if isinstance(eep, dict) else eep for eep in eeps],
                device.entity_type,
            )

            # Send discovery signal for new devices or devices that were incomplete
            if is_new or was_incomplete:
                async_dispatcher_send(
                    self.hass,
                    f"{SIGNAL_DEVICE_DISCOVERED}_{self.eag_id}",
                    device,
                )
            elif rotation_functions:
                async_dispatcher_send(
                    self.hass,
                    f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}",
                    device,
                )

        except Exception as err:
            _LOGGER.exception("Error creating device from data: %s", err)

    @staticmethod
    def _device_state_functions(data: dict) -> list[dict[str, Any]]:
        """Read cached state from function arrays or OPUS's flat states map."""
        state = data.get("state", {})
        if isinstance(state, dict):
            functions = state.get("functions", [])
            if isinstance(functions, dict):
                functions = [
                    functions[index]
                    for index in sorted(
                        functions, key=lambda x: int(x) if str(x).isdigit() else x
                    )
                ]
            if isinstance(functions, list):
                complete = [
                    function
                    for function in functions
                    if isinstance(function, dict)
                    and "key" in function
                    and "value" in function
                ]
                if complete:
                    return complete
        states = data.get("states", {})
        if isinstance(states, dict):
            return [
                {"key": key, "value": value}
                for key, value in states.items()
                if key in KNOWN_STATE_KEYS
            ]
        return []

    @staticmethod
    def _has_operational_state(functions: list[dict[str, Any]]) -> bool:
        """Exclude rotation metadata from movement/status reconciliation."""
        return any(
            function.get("key") in KNOWN_STATE_KEYS
            and function.get("key") != KEY_ROTATION_TIME
            for function in functions
        )

    def _apply_initial_state(self, device: EnOceanDevice, data: dict) -> None:
        """Apply initial state from device discovery data."""
        _LOGGER.debug(
            "INITIAL STATE: device=%s data_keys=%s",
            device.friendly_id,
            list(data.keys()),
        )
        functions = self._device_state_functions(data)

        if functions:
            telegram = {"functions": functions}
            device.update_from_telegram(telegram)
            device.last_update_source = "discovery"
            _LOGGER.debug(
                "Applied initial state to device %s: %s",
                device.friendly_id,
                functions,
            )

    # ──────────────────────────────────────────────────────────────────────
    # Telegram messages (stream/telegram - raw radio traffic)
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _handle_telegram_property_message(self, msg: ReceiveMessage) -> None:
        """Handle incoming telegram property messages from flattened MQTT structure."""
        try:
            self._log_raw_mqtt_message("stream/telegram", msg)

            match = TELEGRAM_TOPIC_PATTERN.match(msg.topic)
            if not match:
                return

            eag_id, device_id, property_path = match.groups()

            if eag_id != self.eag_id:
                return

            received_at = monotonic()
            self._telegram_received_at.setdefault(device_id, received_at)
            self._log_latency_received(
                "stream/telegram", device_id, property_path, received_at
            )

            if device_id not in self._telegram_data:
                self._telegram_data[device_id] = {"deviceId": device_id}

            payload = (
                msg.payload.decode()
                if isinstance(msg.payload, bytes)
                else str(msg.payload)
            )
            self._set_nested_property(
                self._telegram_data[device_id], property_path, payload
            )

            # Reset telegram timer for this device - finalize after short delay
            if (
                device_id in self._pending_telegrams
                and self._pending_telegrams[device_id]
            ):
                self._pending_telegrams[device_id]()

            @callback
            def finalize_callback(_now, did=device_id):
                self._finalize_telegram(did)

            # Telegram function key/value pairs can arrive as separate flattened
            # MQTT messages. Wait long enough to avoid dispatching partial pairs.
            self._pending_telegrams[device_id] = async_call_later(
                self.hass, TELEGRAM_FINALIZE_DELAY, finalize_callback
            )

        except Exception as err:
            _LOGGER.exception("Error handling telegram property message: %s", err)

    @callback
    def _finalize_telegram(self, device_id: str) -> None:
        """Finalize telegram processing after receiving all properties."""
        if device_id not in self._telegram_data:
            return

        telegram_data = self._telegram_data.pop(device_id)
        self._pending_telegrams.pop(device_id, None)
        received_at = self._telegram_received_at.pop(device_id, None)
        finalized_at = monotonic()

        # Flattened MQTT topics can nest data under "from" or "to" sub-keys:
        #   stream/telegram/{DEVICE}/from/functions/0/key → {"from": {"functions": ...}}
        #   stream/telegram/{DEVICE}/to/... → {"to": {...}}
        # OPUS also publishes flat messages with a top-level "direction" field.
        # Prefer confirmed "from" device reports when they are present. If only a
        # "to" command telegram is available, use state functions optimistically;
        # this captures native bridge HomeKit commands that otherwise have no
        # immediate confirmed status telegram.
        from_data = telegram_data.get("from", {})
        to_data = telegram_data.get("to", {})

        if from_data:
            effective_data = from_data
        elif to_data:
            effective_data = to_data
        else:
            effective_data = telegram_data

        direction = effective_data.get("direction") or telegram_data.get("direction")
        is_outbound_command = direction == "to" or (to_data and not from_data)

        # Inconsistent data under a "from" topic with direction=to is not a device
        # report and should not be treated as confirmed state.
        if from_data and is_outbound_command:
            return

        friendly_id = (
            effective_data.get("friendlyId")
            or telegram_data.get("friendlyId")
            or device_id
        )

        # Build functions list from the telegram data
        functions = []
        functions_data = effective_data.get("functions", [])

        if isinstance(functions_data, list):
            functions = [f for f in functions_data if isinstance(f, dict)]
        elif isinstance(functions_data, dict):
            for idx in sorted(
                functions_data.keys(), key=lambda x: int(x) if str(x).isdigit() else x
            ):
                func_entry = functions_data[idx]
                if isinstance(func_entry, dict):
                    functions.append(func_entry)

        complete_functions = [
            func
            for func in functions
            if func.get("key") is not None and func.get("value") is not None
        ]
        dropped_count = len(functions) - len(complete_functions)
        if dropped_count:
            _LOGGER.debug(
                "Dropping %d incomplete telegram function(s) for %s: %s",
                dropped_count,
                device_id,
                [func for func in functions if func not in complete_functions],
            )
        functions = complete_functions
        if not functions:
            _LOGGER.debug(
                "Ignoring telegram for %s because it contains no complete functions",
                device_id,
            )
            return

        if is_outbound_command:
            state_functions = [
                func
                for func in functions
                if func.get("key") in KNOWN_STATE_KEYS or func.get("key") == KEY_CHANNEL
            ]
            ignored_count = len(functions) - len(state_functions)
            if ignored_count:
                _LOGGER.debug(
                    "Ignoring %d non-state outbound telegram function(s) for %s: %s",
                    ignored_count,
                    device_id,
                    [func for func in functions if func not in state_functions],
                )
            functions = state_functions
            if not any(
                function.get("key") in KNOWN_STATE_KEYS for function in functions
            ):
                _LOGGER.debug(
                    "Ignoring outbound telegram for %s because it has no "
                    "state functions",
                    device_id,
                )
                return
            _LOGGER.debug(
                "Using outbound telegram for optimistic state update of %s: %s",
                device_id,
                functions,
            )
        channel_id = self._channel_from_functions(functions)
        if not is_outbound_command and self._has_operational_state(functions):
            self._cancel_reconciliation_queries(device_id, channel_id)

        update_source = (
            "stream/telegram/to" if is_outbound_command else "stream/telegram/from"
        )
        self._log_latency_finalized(
            update_source, device_id, received_at, finalized_at, len(functions)
        )

        # Create telegram dict in the format expected by update_from_telegram
        telegram = {
            "deviceId": device_id,
            "friendlyId": friendly_id,
            "functions": functions,
            "timestamp": effective_data.get("timestamp")
            or telegram_data.get("timestamp"),
            "telegramInfo": effective_data.get("telegramInfo")
            or telegram_data.get("telegramInfo", {}),
        }

        device = self.devices.get(device_id)

        if device is None:
            # Create a basic device entry if we haven't discovered it yet
            device = EnOceanDevice(
                device_id=device_id,
                friendly_id=friendly_id,
                eeps=effective_data.get("eeps", []),
            )
            self.devices[device_id] = device
            _LOGGER.info(
                "Cached device from telegram before discovery: %s",
                device_id,
            )
            if device.entity_type is not None:
                async_dispatcher_send(
                    self.hass,
                    f"{SIGNAL_DEVICE_DISCOVERED}_{self.eag_id}",
                    device,
                )

        # Update device state
        if functions:
            _LOGGER.debug(
                "Telegram update for %s (%s): %d functions: %s",
                friendly_id,
                device_id,
                len(functions),
                functions,
            )
        device.update_from_telegram(telegram)
        if not is_outbound_command and self._has_operational_state(functions):
            device.last_command_error = None

        # Notify listeners of state update
        signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
        self._mark_and_log_dispatch(
            device, update_source, signal, received_at, finalized_at
        )
        async_dispatcher_send(self.hass, signal, device)

        if is_outbound_command and self._has_operational_state(functions):
            self._schedule_reconciliation_queries(device_id, channel_id)

    # ──────────────────────────────────────────────────────────────────────
    # Command sending
    # ──────────────────────────────────────────────────────────────────────

    async def async_send_command(
        self,
        device_id: str,
        functions: list[dict[str, Any]],
    ) -> None:
        """Send a command to a device using JSON state message."""
        topic = TOPIC_PUT_STATE.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )

        state_message = {
            "state": {
                "functions": functions,
            }
        }

        payload = json.dumps(state_message)
        _LOGGER.debug("Sending command to %s: %s", topic, payload)

        await mqtt.async_publish(self.hass, topic, payload, qos=1, retain=False)

    def _with_channel_if_needed(
        self,
        device_id: str,
        functions: list[dict[str, Any]],
        channel: int,
    ) -> list[dict[str, Any]]:
        """Prepend the mandatory selector for multi-channel actuator commands."""
        device = self.get_device(device_id)
        if channel > 0 or (device is not None and device.channel_count > 1):
            return [{"key": KEY_CHANNEL, "value": str(channel)}, *functions]
        return functions

    async def async_turn_on(
        self,
        device_id: str,
        channel: int = 0,
        brightness: int | None = None,
        is_dimmable: bool = False,
    ) -> None:
        """Turn on a switch or light."""
        if brightness is not None:
            functions = [{"key": "dimValue", "value": str(brightness)}]
        elif is_dimmable:
            # Dimmers use dimValue, not switch — per OPUS MQTT spec section 5.3
            functions = [{"key": "dimValue", "value": "100"}]
        else:
            functions = [{"key": "switch", "value": "on"}]

        functions = self._with_channel_if_needed(device_id, functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_turn_off(
        self,
        device_id: str,
        channel: int = 0,
        is_dimmable: bool = False,
    ) -> None:
        """Turn off a switch or light."""
        if is_dimmable:
            # Dimmers use dimValue 0, not switch off — per OPUS MQTT spec section 5.3
            functions = [{"key": "dimValue", "value": "0"}]
        else:
            functions = [{"key": "switch", "value": "off"}]
        functions = self._with_channel_if_needed(device_id, functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_set_cover_position(
        self,
        device_id: str,
        position: int,
        channel: int = 0,
    ) -> None:
        """Set cover position (0 = closed, 100 = open)."""
        functions = [{"key": "position", "value": str(position)}]
        functions = self._with_channel_if_needed(device_id, functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_set_cover_tilt(
        self,
        device_id: str,
        tilt: int,
        channel: int = 0,
    ) -> None:
        """Set cover tilt angle."""
        functions = [{"key": "angle", "value": str(tilt)}]
        functions = self._with_channel_if_needed(device_id, functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_stop_cover(self, device_id: str, channel: int = 0) -> None:
        """Stop cover movement."""
        functions = [{"key": "position", "value": "stop"}]
        functions = self._with_channel_if_needed(device_id, functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_query_device_status(
        self,
        device_id: str,
        channel: int = 0,
    ) -> None:
        """Query the current device status."""
        functions = [{"key": "query", "value": "status"}]
        functions = self._with_channel_if_needed(device_id, functions, channel)
        await self.async_send_command(device_id, functions)

    # ──────────────────────────────────────────────────────────────────────
    # Climate commands
    # ──────────────────────────────────────────────────────────────────────

    async def async_set_climate_setpoint(
        self,
        device_id: str,
        temperature: float,
    ) -> None:
        """Set the temperature setpoint for a climate device."""
        functions = [{"key": "temperatureSetpoint", "value": str(temperature)}]
        await self.async_send_command(device_id, functions)

    async def async_set_climate_mode(
        self,
        device_id: str,
        mode: str,
    ) -> None:
        """Set the heater mode for a climate device."""
        functions = [{"key": "heaterMode", "value": mode}]
        await self.async_send_command(device_id, functions)

    async def async_query_climate_status(
        self,
        device_id: str,
    ) -> None:
        """Query the current status of a climate device."""
        await self.async_query_device_status(device_id)

    # ──────────────────────────────────────────────────────────────────────
    # Device profile queries
    # ──────────────────────────────────────────────────────────────────────

    async def async_get_device_profile(self, device_id: str) -> None:
        """Request device profile from gateway."""
        topic = TOPIC_GET_DEVICE_PROFILE.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )
        answer_topic = TOPIC_GET_ANSWER_DEVICE_PROFILE.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )

        @callback
        def handle_profile(msg: ReceiveMessage) -> None:
            try:
                payload = msg.payload
                if isinstance(payload, bytes):
                    payload = payload.decode()
                data = json.loads(payload)

                if (device := self.get_device(device_id)) is not None:
                    device.profile = data
                    _LOGGER.info("Received profile for %s", device_id)
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as err:
                _LOGGER.debug("Could not parse device profile: %s", err)

        unsub = await mqtt.async_subscribe(
            self.hass, answer_topic, handle_profile, qos=1
        )
        cancel_timer = async_call_later(self.hass, 10, lambda _: unsub())
        self._temporary_cancellations.extend((cancel_timer, unsub))

        await mqtt.async_publish(self.hass, topic, "", qos=1)

    # ──────────────────────────────────────────────────────────────────────
    # Device configuration (ReCom API)
    # ──────────────────────────────────────────────────────────────────────

    async def async_get_device_configuration(
        self, device_id: str
    ) -> dict[str, Any] | None:
        """Get device configuration via ReCom API."""
        topic = TOPIC_GET_DEVICE_CONFIGURATION.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )
        answer_topic = TOPIC_GET_ANSWER_DEVICE_CONFIGURATION.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )

        result: dict[str, Any] | None = None
        event = asyncio.Event()

        @callback
        def handle_response(msg: ReceiveMessage) -> None:
            nonlocal result
            try:
                payload = msg.payload
                if isinstance(payload, bytes):
                    payload = payload.decode()
                result = json.loads(payload)
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as err:
                _LOGGER.debug("Could not parse device configuration: %s", err)
            event.set()

        unsub = await mqtt.async_subscribe(
            self.hass, answer_topic, handle_response, qos=1
        )

        try:
            await mqtt.async_publish(self.hass, topic, "", qos=1)
            try:
                await asyncio.wait_for(event.wait(), timeout=10.0)
            except TimeoutError:
                _LOGGER.warning("Timeout getting configuration for %s", device_id)
        finally:
            unsub()

        return result

    async def async_set_device_configuration(
        self, device_id: str, config: dict[str, Any]
    ) -> None:
        """Set device configuration via ReCom API."""
        topic = TOPIC_PUT_DEVICE_CONFIGURATION.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )
        payload = json.dumps(config)
        await mqtt.async_publish(self.hass, topic, payload, qos=1, retain=False)

    async def async_get_device_parameters(
        self, device_id: str
    ) -> dict[str, Any] | None:
        """Get device DDF parameters via ReCom API."""
        topic = TOPIC_GET_DEVICE_PARAMETERS.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )
        answer_topic = TOPIC_GET_ANSWER_DEVICE_PARAMETERS.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )

        result: dict[str, Any] | None = None
        event = asyncio.Event()

        @callback
        def handle_response(msg: ReceiveMessage) -> None:
            nonlocal result
            try:
                payload = msg.payload
                if isinstance(payload, bytes):
                    payload = payload.decode()
                result = json.loads(payload)
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as err:
                _LOGGER.debug("Could not parse device parameters: %s", err)
            event.set()

        unsub = await mqtt.async_subscribe(
            self.hass, answer_topic, handle_response, qos=1
        )

        try:
            await mqtt.async_publish(self.hass, topic, "", qos=1)
            try:
                await asyncio.wait_for(event.wait(), timeout=10.0)
            except TimeoutError:
                _LOGGER.warning("Timeout getting parameters for %s", device_id)
        finally:
            unsub()

        return result

    # ──────────────────────────────────────────────────────────────────────
    # Device lookup helpers
    # ──────────────────────────────────────────────────────────────────────

    def get_device(self, device_id: str) -> EnOceanDevice | None:
        """Get a device by ID."""
        return self.devices.get(device_id)

    def get_devices_by_type(self, entity_type: str) -> list[EnOceanDevice]:
        """Get all devices of a specific entity type."""
        return [
            device
            for device in self.devices.values()
            if device.entity_type == entity_type
        ]
