"""Data coordinator for Opus GreenNet Bridge integration."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt import ReceiveMessage
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import (
    DOMAIN,
    KNOWN_STATE_KEYS,
    TOPIC_BASE,
    TOPIC_GET_ANSWER_DEVICES,
    TOPIC_GET_ANSWER_DEVICE_CONFIGURATION,
    TOPIC_GET_ANSWER_DEVICE_PROFILE,
    TOPIC_GET_ANSWER_SYSTEM_INFO,
    TOPIC_GET_ANSWER_SYSTEM_UPTIME,
    TOPIC_GET_DEVICES,
    TOPIC_GET_DEVICE_CONFIGURATION,
    TOPIC_GET_DEVICE_PARAMETERS,
    TOPIC_GET_DEVICE_PROFILE,
    TOPIC_GET_SYSTEM_INFO,
    TOPIC_GET_SYSTEM_UPTIME,
    TOPIC_PUT_DEVICE_CONFIGURATION,
    TOPIC_PUT_STATE,
    TOPIC_SUB_DEVICE_STREAM_ALL,
    TOPIC_SUB_DEVICES_ALL,
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

# Regex to parse device topics (plural - initial full state at boot)
# EnOcean/{EAG}/stream/devices/{DeviceID}/{property}
DEVICE_TOPIC_PATTERN = re.compile(
    r"EnOcean/([^/]+)/stream/devices/([^/]+)/(.+)"
)

# Regex to parse telegram topics
# EnOcean/{EAG}/stream/telegram/{DeviceID}/{property}
TELEGRAM_TOPIC_PATTERN = re.compile(
    r"EnOcean/([^/]+)/stream/telegram/([^/]+)/(.+)"
)

# Regex to parse device stream topics (singular - live deltas)
# EnOcean/{EAG}/stream/device/{DeviceID}/{property}
DEVICE_STREAM_TOPIC_PATTERN = re.compile(
    r"EnOcean/([^/]+)/stream/device/([^/]+)/(.+)"
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
        self._pending_device_streams: dict[str, Callable | None] = {}  # Timers per device
        self._discovery_timer: Callable | None = None
        # Gateway info
        self.gateway_info: dict[str, Any] = {}
        self.gateway_uptime: str | None = None

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
                self.hass, topic_devices_all, self._handle_device_property_message, qos=1
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
        topic_get = TOPIC_GET_DEVICES.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
        await mqtt.async_publish(self.hass, topic_get, "", qos=1)
        _LOGGER.info("Requested device list via GET: %s", topic_get)

        # Request gateway system info
        await self._request_gateway_info()

        # Schedule device discovery finalization after 5 seconds
        self._discovery_timer = async_call_later(
            self.hass, 5, self._finalize_discovery
        )

        return True

    async def async_unload(self) -> None:
        """Unload the coordinator and unsubscribe from MQTT."""
        _LOGGER.debug("Unloading Opus GreenNet coordinator for EAG %s", self.eag_id)
        if self._discovery_timer:
            self._discovery_timer()
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()

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

            if device_id not in self._device_data:
                self._device_data[device_id] = {"deviceId": device_id}
                if self._find_device_by_id(device_id) is None:
                    self._pending_devices.add(device_id)

            payload = msg.payload.decode() if isinstance(msg.payload, bytes) else str(msg.payload)
            self._set_nested_property(self._device_data[device_id], property_path, payload)

            if self._apply_known_device_state_property(
                device_id, property_path, payload
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

            if device_id not in self._device_stream_data:
                self._device_stream_data[device_id] = {"deviceId": device_id}

            payload = msg.payload.decode() if isinstance(msg.payload, bytes) else str(msg.payload)
            self._set_nested_property(
                self._device_stream_data[device_id], property_path, payload
            )

            # Reset stream timer for this device - finalize after short delay
            if device_id in self._pending_device_streams and self._pending_device_streams[device_id]:
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

        # Find the device
        device = None
        device_key = None
        for key, dev in self.devices.items():
            if dev.device_id == device_id:
                device = dev
                device_key = key
                break

        if device is None:
            # Device not yet discovered - store for later discovery
            friendly_id = stream_data.get("friendlyId", device_id)
            self._device_data[device_id] = stream_data
            self._pending_devices.add(device_id)
            if not self._discovery_timer:
                self._discovery_timer = async_call_later(
                    self.hass, 2, self._finalize_discovery
                )
            return

        # Build functions from the delta data.
        # stream/device topics use "state/functions/N/key|value" (singular "state",
        # functions array) while stream/devices boot data uses "states/<key>" (plural
        # "states", flat dict). We must handle both formats.
        functions = []

        # Format 1: state.functions array (from stream/device deltas)
        state_obj = stream_data.get("state", {})
        if isinstance(state_obj, dict):
            functions_data = state_obj.get("functions", [])
            if isinstance(functions_data, list):
                functions = [f for f in functions_data if isinstance(f, dict)]
            elif isinstance(functions_data, dict):
                for idx in sorted(functions_data.keys(), key=lambda x: int(x) if str(x).isdigit() else x):
                    func_entry = functions_data[idx]
                    if isinstance(func_entry, dict):
                        functions.append(func_entry)

        # Format 2: states flat dict (from stream/devices boot data, if routed here)
        if not functions:
            states = stream_data.get("states", {})
            if states and isinstance(states, dict):
                for key, value in states.items():
                    if key in KNOWN_STATE_KEYS:
                        functions.append({"key": key, "value": value})

        if functions:
            telegram = {"functions": functions}
            device.update_from_telegram(telegram)

            signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_key}"
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

            for device_data in devices_list:
                device_id = device_data.get("deviceId", "")
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
            self.gateway_info = data
            _LOGGER.info("Gateway info: %s", data)
        except (json.JSONDecodeError, Exception) as err:
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
        except Exception as err:
            _LOGGER.debug("Could not parse system uptime: %s", err)

    async def _request_gateway_info(self) -> None:
        """Request gateway system info and uptime."""
        topic_info = TOPIC_GET_SYSTEM_INFO.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )
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

    def _find_device_by_id(self, device_id: str) -> tuple[str, EnOceanDevice] | None:
        """Find an existing device by EURID and return its coordinator key."""
        for device_key, device in self.devices.items():
            if device.device_id == device_id:
                return device_key, device
        return None

    def _apply_known_device_state_property(
        self, device_id: str, property_path: str, payload: str
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

        device_key, device = device_entry
        value = self._parse_value(payload)
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

        signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_key}"
        _LOGGER.debug(
            "Fast stream/devices state update for %s: %s=%r",
            device_id,
            state_key,
            value,
        )
        async_dispatcher_send(self.hass, signal, device)
        return True

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
        _LOGGER.info("Finalizing device discovery, found %d devices", len(self._device_data))

        for device_id, data in self._device_data.items():
            if device_id in self._pending_devices:
                self._pending_devices.discard(device_id)
                self._create_device_from_data(device_id, data)

        self._discovery_complete = True

    def _create_device_from_data(self, device_id: str, data: dict) -> None:
        """Create an EnOceanDevice from collected property data."""
        try:
            friendly_id = data.get("friendlyId", device_id)
            device_key = friendly_id
            existing_entry = self._find_device_by_id(device_id)
            existing_key = existing_entry[0] if existing_entry else None
            existing_device = existing_entry[1] if existing_entry else None

            # Build EEPs list
            eeps = []
            eeps_data = data.get("eeps", {})
            if isinstance(eeps_data, list):
                eeps = eeps_data
            elif isinstance(eeps_data, dict):
                for idx in sorted(eeps_data.keys(), key=lambda x: int(x) if x.isdigit() else x):
                    eep_entry = eeps_data[idx]
                    if isinstance(eep_entry, dict):
                        eeps.append(eep_entry)
                    elif isinstance(eep_entry, str):
                        eeps.append({"eep": eep_entry})

            is_new = existing_entry is None and device_key not in self.devices
            was_incomplete = (
                existing_device is not None and not existing_device.eeps
            ) or (
                device_key in self.devices and not self.devices[device_key].eeps
            )

            device = EnOceanDevice(
                device_id=device_id,
                friendly_id=friendly_id,
                eeps=eeps,
                manufacturer=data.get("manufacturer", ""),
                physical_device=data.get("physicalDevice", ""),
                first_seen=str(data.get("firstSeen", "")),
                last_seen=str(data.get("lastSeen", "")),
                dbm=data.get("dbm", 0),
            )

            # Preserve existing channel state or apply initial state from discovery
            if existing_device is not None:
                device.channels = existing_device.channels
                device.profile = existing_device.profile
                if existing_key and existing_key != device_key:
                    _LOGGER.debug(
                        "Re-keying device %s from %s to %s after discovery",
                        device_id,
                        existing_key,
                        device_key,
                    )
                    self.devices.pop(existing_key, None)
            elif device_key in self.devices:
                device.channels = self.devices[device_key].channels
                device.profile = self.devices[device_key].profile
            else:
                self._apply_initial_state(device, data)

            self.devices[device_key] = device

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

        except Exception as err:
            _LOGGER.exception("Error creating device from data: %s", err)

    def _apply_initial_state(self, device: EnOceanDevice, data: dict) -> None:
        """Apply initial state from device discovery data."""
        _LOGGER.debug("INITIAL STATE: device=%s data_keys=%s", device.friendly_id, list(data.keys()))
        states = data.get("states", {})
        if not states or not isinstance(states, dict):
            _LOGGER.debug("INITIAL STATE: No 'states' in data for %s", device.friendly_id)
            return

        # Build a telegram-like structure from states data using all known keys
        functions = []
        for key, value in states.items():
            if key in KNOWN_STATE_KEYS:
                functions.append({"key": key, "value": value})

        if functions:
            telegram = {"functions": functions}
            device.update_from_telegram(telegram)
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

            if device_id not in self._telegram_data:
                self._telegram_data[device_id] = {"deviceId": device_id}

            payload = msg.payload.decode() if isinstance(msg.payload, bytes) else str(msg.payload)
            self._set_nested_property(self._telegram_data[device_id], property_path, payload)

            # Reset telegram timer for this device - finalize after short delay
            if device_id in self._pending_telegrams and self._pending_telegrams[device_id]:
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

        # Flattened MQTT topics nest data under "from" or "to" sub-keys:
        #   stream/telegram/{DEVICE}/from/functions/0/key → {"from": {"functions": ...}}
        #   stream/telegram/{DEVICE}/to/... → {"to": {...}}
        # We only want "from" telegrams (device reports), not "to" (outbound commands).
        from_data = telegram_data.get("from", {})
        to_data = telegram_data.get("to", {})

        if to_data and not from_data:
            # Only "to" (command) data — skip
            return

        # Use the "from" sub-object as primary data source; fall back to top-level
        # for backwards compatibility (e.g. if topic structure differs).
        effective_data = from_data if from_data else telegram_data

        # Also check top-level direction field (legacy fallback)
        direction = effective_data.get("direction") or telegram_data.get("direction")
        if direction == "to":
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
            for idx in sorted(functions_data.keys(), key=lambda x: int(x) if str(x).isdigit() else x):
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

        # Create telegram dict in the format expected by update_from_telegram
        telegram = {
            "deviceId": device_id,
            "friendlyId": friendly_id,
            "functions": functions,
            "timestamp": effective_data.get("timestamp") or telegram_data.get("timestamp"),
            "telegramInfo": effective_data.get("telegramInfo") or telegram_data.get("telegramInfo", {}),
        }

        # Find device by device_id (devices are keyed by friendly_id)
        device = None
        device_key = None
        for key, dev in self.devices.items():
            if dev.device_id == device_id:
                device = dev
                device_key = key
                break

        if device is None:
            # Create a basic device entry if we haven't discovered it yet
            device_key = friendly_id
            device = EnOceanDevice(
                device_id=device_id,
                friendly_id=friendly_id,
                eeps=effective_data.get("eeps", []),
            )
            self.devices[device_key] = device
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
                friendly_id, device_id, len(functions), functions,
            )
        device.update_from_telegram(telegram)

        # Notify listeners of state update
        signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_key}"
        async_dispatcher_send(self.hass, signal, device)

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

        try:
            await mqtt.async_publish(self.hass, topic, payload, qos=1)
        except Exception as err:
            _LOGGER.error("Failed to send command to %s: %s", device_id, err)

    def _add_channel_if_needed(
        self, functions: list[dict[str, Any]], channel: int
    ) -> None:
        """Add channel to functions list for multi-channel devices."""
        if channel > 0:
            functions.append({"key": "channel", "value": str(channel)})

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

        self._add_channel_if_needed(functions, channel)
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
        self._add_channel_if_needed(functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_set_cover_position(
        self,
        device_id: str,
        position: int,
        channel: int = 0,
    ) -> None:
        """Set cover position (0 = closed, 100 = open)."""
        functions = [{"key": "position", "value": str(position)}]
        self._add_channel_if_needed(functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_set_cover_tilt(
        self,
        device_id: str,
        tilt: int,
        channel: int = 0,
    ) -> None:
        """Set cover tilt angle."""
        functions = [{"key": "angle", "value": str(tilt)}]
        self._add_channel_if_needed(functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_stop_cover(self, device_id: str, channel: int = 0) -> None:
        """Stop cover movement."""
        functions = [{"key": "position", "value": "stop"}]
        self._add_channel_if_needed(functions, channel)
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
        functions = [{"key": "query", "value": "status"}]
        await self.async_send_command(device_id, functions)

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

                # Store profile on device
                for dev in self.devices.values():
                    if dev.device_id == device_id:
                        dev.profile = data
                        _LOGGER.info("Received profile for %s", device_id)
                        break
            except (json.JSONDecodeError, Exception) as err:
                _LOGGER.debug("Could not parse device profile: %s", err)

        unsub = await mqtt.async_subscribe(self.hass, answer_topic, handle_profile, qos=1)
        # Auto-unsubscribe after 10 seconds
        async_call_later(self.hass, 10, lambda _: unsub())

        await mqtt.async_publish(self.hass, topic, "", qos=1)

    # ──────────────────────────────────────────────────────────────────────
    # Device configuration (ReCom API)
    # ──────────────────────────────────────────────────────────────────────

    async def async_get_device_configuration(self, device_id: str) -> dict[str, Any] | None:
        """Get device configuration via ReCom API."""
        import asyncio

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
            except (json.JSONDecodeError, Exception) as err:
                _LOGGER.debug("Could not parse device configuration: %s", err)
            event.set()

        unsub = await mqtt.async_subscribe(self.hass, answer_topic, handle_response, qos=1)
        await mqtt.async_publish(self.hass, topic, "", qos=1)

        try:
            await asyncio.wait_for(event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout getting configuration for %s", device_id)
        finally:
            unsub()

        return result

    async def async_set_device_configuration(
        self, device_id: str, config: dict[str, Any]
    ) -> bool:
        """Set device configuration via ReCom API."""
        topic = TOPIC_PUT_DEVICE_CONFIGURATION.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )
        payload = json.dumps(config)
        try:
            await mqtt.async_publish(self.hass, topic, payload, qos=1)
            return True
        except Exception as err:
            _LOGGER.error("Failed to set configuration for %s: %s", device_id, err)
            return False

    async def async_get_device_parameters(self, device_id: str) -> dict[str, Any] | None:
        """Get device DDF parameters via ReCom API."""
        import asyncio

        topic = TOPIC_GET_DEVICE_PARAMETERS.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )
        answer_topic = f"EnOcean/{self.eag_id}/getAnswer/devices/{device_id}/parameters"

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
            except (json.JSONDecodeError, Exception) as err:
                _LOGGER.debug("Could not parse device parameters: %s", err)
            event.set()

        unsub = await mqtt.async_subscribe(self.hass, answer_topic, handle_response, qos=1)
        await mqtt.async_publish(self.hass, topic, "", qos=1)

        try:
            await asyncio.wait_for(event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
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
