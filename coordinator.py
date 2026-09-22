"""Data coordinator for Opus GreenNet Bridge integration.

Base: kegelmeier v0.3.3b0 (PR #30 "harden MQTT recovery and Home Assistant
state handling").

FIX HISTORY (this file)
------------------------
1. Removed a permanent subscription to TOPIC_GET_ANSWER_SYSTEM_INFO /
   TOPIC_GET_ANSWER_SYSTEM_UPTIME that raced against the request-scoped
   subscription MQTTRequestManager opens for the same topic.
2. Made the system-info probe (get/config/system/info) best-effort instead
   of mandatory for gateway availability.
3. Stopped retrying a known-unsupported system-info probe on every 60s
   health tick via the tri-state `_system_info_supported` flag.
4. UPSTREAM-REPORTED BUG: _has_operational_state() only checked the function
   `key`, never the `value`, mistaking the "unknown" interim sentinel for
   command confirmation. Fixed via UNKNOWN_VALUE_SENTINEL.
5. UPSTREAM BUG: async_stop_cover() sent an invalid {"key": "position",
   "value": "stop"} payload. Per EEP D2-05-00/D2-05-06, stop is its own
   function (key "stop", value "true"). Fixed via KEY_STOP.
6. QUALITY-SCALE (Gold, "repair-issues"): the system-info-unsupported case
   from fix #3 now also raises a dismissable Home Assistant repair issue in
   addition to the log warning, instead of being log-only.

NOTE ON A REVERTED EXPERIMENT: a prior revision of mqtt_transport.py briefly
changed mqtt.async_on_subscribe_done() to be awaited with keyword arguments,
based on a developer-blog example rather than verified behavior. This broke
setup with `TypeError: 'functools.partial' object can't be awaited`,
proving the function is in fact synchronous and returns a plain callable.
That change was reverted. Do not "modernize" this call again without first
confirming the exact signature against a real, running Home Assistant
instance - see mqtt_transport.py module docstring.

Ported extensions (fubu2k fork, reconciled against the hardened base):
  * Generalized indexed key/value fragment parsing via one of several
    top-level containers (const.INDEXED_STATE_CONTAINERS): "states/{n}" for
    the OPUS SMS presence sensor (A5-07-03), "transmitModes/{n}" for the
    Jaeger Direkt RWM (F6-05-02).
  * A batteryLevel/dbm fast-path for stream/device/{id}/... live deltas.
  * async_set_window_handle_lock() for the HOPPE eLock command topic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Coroutine
from datetime import timedelta
from time import monotonic
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt import ReceiveMessage
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    BUTTON_KEYS,
    DOMAIN,
    INDEXED_STATE_CONTAINERS,
    KEY_CHANNEL,
    KEY_ROTATION_TIME,
    KEY_STOP,
    KNOWN_STATE_KEYS,
    LOCK_COMMAND_ALLOWED,
    LOCK_COMMAND_NOT_ALLOWED,
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
    TOPIC_PUT_ANSWER_DEVICE_CONFIGURATION,
    TOPIC_PUT_DEVICE_CONFIGURATION,
    TOPIC_PUT_STATE,
    TOPIC_SUB_DEVICE_STREAM_ALL,
    TOPIC_SUB_DEVICES_ALL,
    TOPIC_SUB_PUT_ANSWER_STATE,
    TOPIC_SUB_TELEGRAM_FROM_ALL,
    TOPIC_WINDOW_HANDLE_ACCESS,
)
from .enocean_device import EnOceanDevice
from .mqtt_transport import (
    MQTTRequestManager,
    async_wait_for_subscriptions,
    decode_response,
    request_error,
)

_LOGGER = logging.getLogger(__name__)

SIGNAL_DEVICE_DISCOVERED = f"{DOMAIN}_device_discovered"
SIGNAL_DEVICE_STATE_UPDATE = f"{DOMAIN}_device_state_update"
SIGNAL_AVAILABILITY_UPDATE = f"{DOMAIN}_availability_update"

DEVICE_STREAM_FINALIZE_DELAY = 0.02
TELEGRAM_FINALIZE_DELAY = 0.15
RAW_MQTT_DEBUG_PAYLOAD_LIMIT = 500
OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)
GATEWAY_HEALTH_INTERVAL = timedelta(seconds=60)
UPTIME_SUBSCRIPTION_TTL = 10
UNKNOWN_VALUE_SENTINEL = "unknown"
ISSUE_SYSTEM_INFO_UNSUPPORTED = "system_info_unsupported"

DEVICE_TOPIC_PATTERN = re.compile(r"EnOcean/([^/]+)/stream/devices/([^/]+)/(.+)")
TELEGRAM_TOPIC_PATTERN = re.compile(
    r"EnOcean/([^/]+)/stream/telegram/([^/]+)(?:/(.+))?"
)
DEVICE_STREAM_TOPIC_PATTERN = re.compile(r"EnOcean/([^/]+)/stream/device/([^/]+)/(.+)")
PUT_ANSWER_STATE_TOPIC_PATTERN = re.compile(
    r"EnOcean/([^/]+)/putAnswer/devices/([^/]+)/state"
)

_FAST_PATH_PROPERTIES = frozenset({"batteryLevel", "dbm"})


class OpusGreenNetCoordinator:
    """Coordinator for managing MQTT communication with Opus GreenNet Bridge."""

    def __init__(self, hass: HomeAssistant, eag_id: str) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.eag_id = eag_id
        self.devices: dict[str, EnOceanDevice] = {}
        self._device_data: dict[str, dict[str, Any]] = {}
        self._telegram_data: dict[str, dict[str, Any]] = {}
        self._device_stream_data: dict[str, dict[str, Any]] = {}
        self._subscriptions: list[Callable[[], None]] = []
        self._discovery_complete = False
        self._pending_devices: set[str] = set()
        self._pending_telegrams: dict[str, Callable | None] = {}
        self._pending_device_streams: dict[str, Callable | None] = {}
        self._telegram_received_at: dict[str, float] = {}
        self._device_stream_received_at: dict[str, float] = {}
        self._pending_reconciliation_queries: dict[
            tuple[str, int], list[Callable[[], None]]
        ] = {}
        self._discovery_timer: Callable | None = None
        self._requests = MQTTRequestManager(hass)
        self._tasks: set[asyncio.Task] = set()
        self._gateway_available = False
        self._bridge_connected: bool | None = None
        self._unloaded = False
        self._started = False
        self._refresh_task: asyncio.Task | None = None
        self._subscription_topics: list[str] = []
        self._resync_requested_at: float | None = None
        self._telegram_paths: dict[str, set[str]] = {}
        self._command_waiters: dict[str, int] = {}
        self.gateway_info: dict[str, Any] = {}
        self.gateway_uptime: str | None = None
        self._system_info_supported: bool | None = None

    @property
    def available(self) -> bool:
        """Require both the broker connection and a responsive OPUS gateway."""
        return (
            not self._unloaded
            and self._gateway_available
            and self._bridge_connected is not False
            and mqtt.is_connected(self.hass)
        )

    async def async_setup(self) -> bool:
        """Subscribe before probing the gateway and requesting its snapshot."""
        subscriptions = (
            (TOPIC_SUB_TELEGRAM_FROM_ALL, self._handle_telegram_property_message),
            (TOPIC_SUB_DEVICES_ALL, self._handle_device_property_message),
            (TOPIC_SUB_DEVICE_STREAM_ALL, self._handle_device_stream_message),
            (TOPIC_GET_ANSWER_DEVICES, self._handle_get_answer_devices),
            (TOPIC_SUB_PUT_ANSWER_STATE, self._handle_put_answer_state),
            ("opus_greennet/{eag_id}/bridge/status", self._handle_bridge_status),
        )
        self._subscriptions.append(
            mqtt.async_subscribe_connection_status(
                self.hass, self._handle_connection_status
            )
        )
        try:
            for pattern, handler in subscriptions:
                topic = pattern.format(base=TOPIC_BASE, eag_id=self.eag_id)
                self._subscriptions.append(
                    await mqtt.async_subscribe(self.hass, topic, handler, qos=1)
                )
                self._subscription_topics.append(topic)
                _LOGGER.debug("Subscribed to %s for gateway %s", topic, self.eag_id)
            await async_wait_for_subscriptions(self.hass, self._subscription_topics)
            await self._async_refresh_gateway(resync=True)
            self._started = True
            self._subscriptions.append(
                async_track_time_interval(
                    self.hass, self._async_health_tick, GATEWAY_HEALTH_INTERVAL
                )
            )
        except BaseException:
            await self.async_unload()
            raise
        return True

    @callback
    def _set_gateway_available(self, available: bool) -> None:
        """Notify every entity when gateway health changes, without replaying events."""
        if self._gateway_available == available:
            return
        self._gateway_available = available
        _LOGGER.info(
            "OPUS gateway %s is %s",
            self.eag_id,
            "available" if available else "unavailable",
        )
        async_dispatcher_send(self.hass, f"{SIGNAL_AVAILABILITY_UPDATE}_{self.eag_id}")

    @callback
    def _handle_connection_status(self, connected: bool) -> None:
        """Invalidate outstanding work on disconnect and resync on reconnect."""
        if self._unloaded:
            return
        if not connected:
            self._set_gateway_available(False)
            self._requests.async_cancel_pending("mqtt_unavailable")
            self._cancel_background_work()
        elif self._started:
            self._schedule_gateway_refresh(resync=True)

    @callback
    def _handle_bridge_status(self, msg: ReceiveMessage) -> None:
        """Consume the optional Mosquitto bridge notification topic."""
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        if payload not in ("0", "1"):
            return
        self._bridge_connected = payload == "1"
        if not self._bridge_connected:
            self._set_gateway_available(False)
            self._requests.async_cancel_pending("gateway_unavailable")
            self._cancel_background_work()
        elif self._started:
            self._schedule_gateway_refresh(resync=True)

    @callback
    def _async_health_tick(self, _now) -> None:
        """Probe the gateway independently of quiet or batteryless devices."""
        if mqtt.is_connected(self.hass) and self._bridge_connected is not False:
            self._schedule_gateway_refresh(resync=not self._gateway_available)

    @callback
    def _schedule_gateway_refresh(self, *, resync: bool) -> None:
        if self._unloaded or (
            self._refresh_task
            and not self._refresh_task.done()
            and not self._refresh_task.cancelling()
        ):
            return
        self._refresh_task = self._create_task(
            self._async_refresh_gateway_background(resync=resync)
        )

    async def _async_refresh_gateway_background(self, *, resync: bool) -> None:
        try:
            await self._async_refresh_gateway(resync=resync)
        except (HomeAssistantError, TimeoutError):
            self._set_gateway_available(False)
            self._requests.async_cancel_pending("gateway_unavailable")
            _LOGGER.debug(
                "OPUS gateway %s did not answer its health probe", self.eag_id
            )

    async def _async_refresh_gateway(self, *, resync: bool) -> None:
        """Probe the gateway; give up retrying system info once known unsupported."""
        if self._bridge_connected is False:
            raise request_error("gateway_unavailable", self.eag_id)

        if resync or self._system_info_supported is not False:
            _LOGGER.debug(
                "Probing OPUS gateway %s via %s",
                self.eag_id,
                TOPIC_GET_SYSTEM_INFO.format(base=TOPIC_BASE, eag_id=self.eag_id),
            )
            try:
                self.gateway_info = await self._requests.async_request(
                    TOPIC_GET_SYSTEM_INFO.format(base=TOPIC_BASE, eag_id=self.eag_id),
                    TOPIC_GET_ANSWER_SYSTEM_INFO.format(base=TOPIC_BASE, eag_id=self.eag_id),
                    self.eag_id,
                )
                self._system_info_supported = True
                _LOGGER.debug("OPUS gateway %s responded to the health probe", self.eag_id)
                ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SYSTEM_INFO_UNSUPPORTED)
            except HomeAssistantError as err:
                if self._system_info_supported is not False:
                    _LOGGER.warning(
                        "OPUS gateway %s did not answer get/config/system/info (%s). "
                        "Disabling further automatic system-info probes for this "
                        "gateway to avoid repeating a guaranteed timeout every %s - "
                        "check that your Mosquitto bridge relays this topic in both "
                        "directions if you want gateway metadata in diagnostics. "
                        "Device control and discovery are unaffected. Reloading the "
                        "integration will retry once.",
                        self.eag_id,
                        err,
                        GATEWAY_HEALTH_INTERVAL,
                    )
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        ISSUE_SYSTEM_INFO_UNSUPPORTED,
                        is_fixable=False,
                        is_persistent=False,
                        severity=ir.IssueSeverity.WARNING,
                        translation_key=ISSUE_SYSTEM_INFO_UNSUPPORTED,
                        translation_placeholders={"eag_id": self.eag_id},
                    )
                self._system_info_supported = False

        if self._unloaded or self._bridge_connected is False:
            raise request_error("gateway_unavailable", self.eag_id)
        if resync:
            await async_wait_for_subscriptions(self.hass, self._subscription_topics)
            self._resync_requested_at = monotonic()
            await mqtt.async_publish(
                self.hass,
                TOPIC_GET_DEVICES.format(base=TOPIC_BASE, eag_id=self.eag_id),
                "",
                qos=1,
                retain=False,
            )
            await self._async_request_system_uptime()
            if not self._discovery_timer:
                self._discovery_timer = async_call_later(
                    self.hass, 2, self._finalize_discovery
                )
        self._set_gateway_available(True)

    async def _async_request_system_uptime(self) -> None:
        """Fetch gateway uptime as its own bounded, self-cleaning, non-fatal request."""
        topic = TOPIC_GET_SYSTEM_UPTIME.format(base=TOPIC_BASE, eag_id=self.eag_id)
        answer_topic = TOPIC_GET_ANSWER_SYSTEM_UPTIME.format(
            base=TOPIC_BASE, eag_id=self.eag_id
        )

        @callback
        def handle_uptime(msg: ReceiveMessage) -> None:
            payload = msg.payload
            if isinstance(payload, bytes):
                payload = payload.decode(errors="replace")
            self.gateway_uptime = payload

        try:
            unsub = await mqtt.async_subscribe(
                self.hass, answer_topic, handle_uptime, qos=1
            )
            async_call_later(self.hass, UPTIME_SUBSCRIPTION_TTL, lambda _: unsub())
            await mqtt.async_publish(self.hass, topic, "", qos=1, retain=False)
        except Exception:  # noqa: BLE001 - diagnostic-only, never fatal
            _LOGGER.debug("Could not request OPUS gateway uptime for %s", self.eag_id)

    def _create_task(self, coroutine: Coroutine) -> asyncio.Task:
        """Own every coordinator background operation until unload."""
        task = self.hass.async_create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @callback
    def _cancel_background_work(self) -> None:
        for task in self._tasks:
            task.cancel()
        for device_id, channel_id in list(self._pending_reconciliation_queries):
            self._cancel_reconciliation_queries(device_id, channel_id)
        for pending in (self._pending_telegrams, self._pending_device_streams):
            for cancel in pending.values():
                if cancel:
                    cancel()
            pending.clear()
        self._telegram_data.clear()
        self._telegram_paths.clear()
        self._device_stream_data.clear()
        self._telegram_received_at.clear()
        self._device_stream_received_at.clear()

    async def async_unload(self) -> None:
        """Unload the coordinator and unsubscribe from MQTT."""
        _LOGGER.debug("Unloading Opus GreenNet coordinator for EAG %s", self.eag_id)
        self._unloaded = True
        self._set_gateway_available(False)
        self._requests.async_close()
        self._cancel_background_work()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        if self._discovery_timer:
            self._discovery_timer()
            self._discovery_timer = None
        for device_id, channel_id in list(self._pending_reconciliation_queries):
            self._cancel_reconciliation_queries(device_id, channel_id)
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()
        self._pending_telegrams.clear()
        self._pending_device_streams.clear()
        self._telegram_data.clear()
        self._telegram_paths.clear()
        self._device_stream_data.clear()
        self._telegram_received_at.clear()
        self._device_stream_received_at.clear()
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_SYSTEM_INFO_UNSUPPORTED)

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
                if self.devices.get(device_id) is None:
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

            self._pending_devices.add(device_id)
            if self._discovery_timer:
                self._discovery_timer()
            self._discovery_timer = async_call_later(
                self.hass, 2, self._finalize_discovery
            )
        except Exception:
            _LOGGER.exception("Error handling device property message")

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

            device = self.devices.get(device_id)
            payload = (
                msg.payload.decode()
                if isinstance(msg.payload, bytes)
                else str(msg.payload)
            )

            if device is not None and property_path in _FAST_PATH_PROPERTIES:
                value = self._parse_value(payload)
                if property_path == "batteryLevel":
                    device.battery_level = EnOceanDevice.parse_battery_level(value)
                else:
                    dbm = EnOceanDevice._parse_number(value, integer=True)
                    device.dbm = int(dbm) if dbm is not None else None
                signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
                async_dispatcher_send(self.hass, signal, device)
                return

            if device_id not in self._device_stream_data:
                self._device_stream_data[device_id] = {"deviceId": device_id}
            self._set_nested_property(
                self._device_stream_data[device_id], property_path, payload
            )

            if (
                device_id in self._pending_device_streams
                and self._pending_device_streams[device_id]
            ):
                self._pending_device_streams[device_id]()

            @callback
            def finalize_callback(_now, did=device_id):
                self._finalize_device_stream(did)

            self._pending_device_streams[device_id] = async_call_later(
                self.hass, DEVICE_STREAM_FINALIZE_DELAY, finalize_callback
            )
        except Exception:
            _LOGGER.exception("Error handling device stream message")

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
        cached_data = self._device_data.setdefault(device_id, {"deviceId": device_id})
        self._merge_device_data(cached_data, stream_data)
        if device is None or any(
            key in stream_data for key in ("eeps", "friendlyId", "manufacturer")
        ):
            self._pending_devices.add(device_id)
            if not self._discovery_timer:
                self._discovery_timer = async_call_later(
                    self.hass, 2, self._finalize_discovery
                )
            if device is None:
                return

        functions = self._device_state_functions(stream_data)
        if functions:
            self._log_latency_finalized(
                "stream/device", device_id, received_at, finalized_at, len(functions)
            )
            if self._has_operational_state(functions):
                for channel_id in self._channels_from_functions(functions):
                    self._cancel_reconciliation_queries(device_id, channel_id)
                device.last_command_error = None
            device.update_from_telegram({"functions": functions})

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
        prefix = f"{TOPIC_BASE}/{self.eag_id}/getAnswer/devices"
        if not (
            msg.topic == prefix
            or re.fullmatch(re.escape(prefix) + r"/[^/]+", msg.topic)
        ):
            return
        try:
            self._log_raw_mqtt_message("getAnswer/devices", msg)
            payload = msg.payload
            if isinstance(payload, bytes):
                payload = payload.decode()
            data = json.loads(payload)
            if isinstance(data, dict) and "header" in data:
                decode_response(payload)

            if isinstance(data, list):
                devices_list = data
            elif isinstance(data, dict):
                devices_list = data.get("devices", [data])
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

            if self._discovery_timer:
                self._discovery_timer()
            self._discovery_timer = async_call_later(
                self.hass, 2, self._finalize_discovery
            )
        except json.JSONDecodeError:
            pass
        except ValueError:
            pass
        except Exception:
            _LOGGER.exception("Error handling getAnswer/devices")

    @callback
    def _handle_put_answer_state(self, msg: ReceiveMessage) -> None:
        """Handle the protocol's asynchronous command acknowledgement."""
        if getattr(msg, "retain", False):
            return
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
        error = "Gateway rejected the state command"
        try:
            response = decode_response(payload, require_status=True)
            status = int(response["header"]["httpStatus"])
        except ValueError as err:
            error = str(err)

        device = self.get_device(device_id)
        if status is not None and 200 <= status < 300:
            _LOGGER.debug(
                "OPUS command accepted for %s with HTTP status %d", device_id, status
            )
            if device is not None and device.last_command_error is not None:
                device.last_command_error = None
                signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
                async_dispatcher_send(self.hass, signal, device)
            return

        _LOGGER.warning("OPUS command failed for %s: %s", device_id, error)
        if device is None:
            return
        device.last_command_error = error
        self._cancel_reconciliation_queries(device_id)
        signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
        async_dispatcher_send(self.hass, signal, device)

    # ──────────────────────────────────────────────────────────────────────
    # Shared helpers
    # ──────────────────────────────────────────────────────────────────────

    def _log_raw_mqtt_message(self, source: str, msg: ReceiveMessage) -> None:
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        payload = msg.payload
        payload_text = (
            payload.decode("utf-8", errors="replace")
            if isinstance(payload, bytes)
            else str(payload)
        )
        if len(payload_text) > RAW_MQTT_DEBUG_PAYLOAD_LIMIT:
            payload_text = (
                f"{payload_text[:RAW_MQTT_DEBUG_PAYLOAD_LIMIT]}..."
                f" [truncated {len(payload_text)} chars]"
            )
        _LOGGER.debug("Raw OPUS MQTT %s: topic=%s payload=%r", source, msg.topic, payload_text)

    def _duration_ms(self, start: float | None, end: float) -> str:
        if start is None:
            return "unknown"
        return f"{(end - start) * 1000:.1f}"

    def _log_latency_received(self, source, device_id, property_path, received_at) -> None:
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        _LOGGER.debug(
            "OPUS update latency received: source=%s device_id=%s path=%s received_at=%.6f",
            source, device_id, property_path, received_at,
        )

    def _log_latency_finalized(self, source, device_id, received_at, finalized_at, function_count) -> None:
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        _LOGGER.debug(
            "OPUS update latency finalized: source=%s device_id=%s functions=%d receive_to_finalize_ms=%s",
            source, device_id, function_count, self._duration_ms(received_at, finalized_at),
        )

    def _mark_and_log_dispatch(self, device, source, signal, received_at, finalized_at) -> None:
        dispatched_at = monotonic()
        device.last_update_source = source
        device.last_update_received_monotonic = received_at
        device.last_update_finalized_monotonic = finalized_at
        device.last_update_dispatched_monotonic = dispatched_at
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        _LOGGER.debug(
            "OPUS update latency dispatch: source=%s device_id=%s friendly_id=%s signal=%s "
            "receive_to_dispatch_ms=%s finalize_to_dispatch_ms=%s",
            source, device.device_id, device.friendly_id, signal,
            self._duration_ms(received_at, dispatched_at),
            self._duration_ms(finalized_at, dispatched_at),
        )

    def _apply_known_device_state_property(
        self,
        device_id: str,
        property_path: str,
        payload: str,
        received_at: float | None = None,
    ) -> bool:
        """Apply stream/devices state updates immediately for known devices."""
        container = next(
            (c for c in INDEXED_STATE_CONTAINERS if property_path.startswith(f"{c}/")),
            None,
        )
        if container is None:
            return False

        device = self.devices.get(device_id)
        if device is None:
            return False

        path_parts = property_path.split("/")
        state_key: str | None = None
        value: Any = None

        if len(path_parts) == 3 and path_parts[1].isdigit():
            entries = self._device_data.get(device_id, {}).get(container, [])
            index = int(path_parts[1])
            if not isinstance(entries, list) or index >= len(entries):
                return False
            entry = entries[index]
            if not isinstance(entry, dict) or "key" not in entry or "value" not in entry:
                return False
            state_key = entry.get("key")
            value = entry.get("value")
        elif len(path_parts) >= 2:
            state_key = path_parts[1]
            value = self._parse_value(payload)

        if state_key not in KNOWN_STATE_KEYS:
            return False

        if state_key != KEY_ROTATION_TIME:
            self._cancel_reconciliation_queries(device_id)
            device.last_command_error = None
        device.update_from_telegram({"functions": [{"key": state_key, "value": value}]})

        signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
        now = received_at or monotonic()
        self._log_latency_finalized(f"stream/devices/{container}", device_id, received_at, now, 1)
        self._mark_and_log_dispatch(device, f"stream/devices/{container}", signal, received_at, received_at)
        async_dispatcher_send(self.hass, signal, device)
        return True

    def _cancel_reconciliation_queries(
        self, device_id: str, channel_id: int | None = None
    ) -> None:
        keys = [
            key
            for key in self._pending_reconciliation_queries
            if key[0] == device_id and (channel_id is None or key[1] == channel_id)
        ]
        for key in keys:
            for cancel in self._pending_reconciliation_queries.pop(key, []):
                cancel()

    def _schedule_reconciliation_queries(self, device_id: str, channel_id: int) -> None:
        key = (device_id, channel_id)
        self._cancel_reconciliation_queries(device_id, channel_id)
        self._pending_reconciliation_queries[key] = []
        for delay in OUTBOUND_STATE_RECONCILIATION_DELAYS:

            @callback
            def query_callback(_now, did=device_id, channel=channel_id, seconds=delay):
                _LOGGER.debug(
                    "Querying OPUS status for %s channel %s %.0fs after command",
                    did, channel, seconds,
                )
                if not self.available:
                    return
                self._create_task(self._async_reconcile_status(did, channel))

            self._pending_reconciliation_queries[key].append(
                async_call_later(self.hass, delay, query_callback)
            )

    async def _async_reconcile_status(self, device_id: str, channel_id: int) -> None:
        try:
            await self.async_query_device_status(device_id, channel_id)
        except HomeAssistantError:
            _LOGGER.debug("Could not reconcile OPUS state for %s", device_id)

    @staticmethod
    def _channels_from_functions(functions: list[dict[str, Any]]) -> set[int]:
        default = OpusGreenNetCoordinator._channel_from_functions(functions)
        channels = set()
        for function in functions:
            if function.get("key") not in KNOWN_STATE_KEYS:
                continue
            channel = EnOceanDevice._parse_number(
                function.get("channel", default), minimum=0, integer=True
            )
            if channel is not None:
                channels.add(int(channel))
        return channels

    @staticmethod
    def _channel_from_functions(functions: list[dict[str, Any]]) -> int | None:
        for function in functions:
            if function.get("key") != KEY_CHANNEL:
                continue
            value = EnOceanDevice._parse_number(function.get("value"), minimum=0, integer=True)
            return int(value) if value is not None else None
        return 0

    def _set_nested_property(self, data: dict, path: str, value: str) -> None:
        parts = path.split("/")
        if len(parts) > 16 or any(
            not part or (part.isdigit() and int(part) > 255) for part in parts
        ):
            raise ValueError("Invalid or oversized MQTT property path")
        current = data
        for i, part in enumerate(parts[:-1]):
            if parts[i + 1].isdigit():
                if part not in current:
                    current[part] = []
                current = current[part]
            elif part.isdigit():
                idx = int(part)
                while len(current) <= idx:
                    current.append({})
                current = current[idx]
            else:
                if part not in current:
                    current[part] = {}
                current = current[part]
        final_key = parts[-1]
        if final_key.isdigit():
            idx = int(final_key)
            while len(current) <= idx:
                current.append(None)
            current[idx] = self._parse_value(value)
        else:
            current[final_key] = self._parse_value(value)

    def _parse_value(self, value: str) -> Any:
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

    @staticmethod
    def _merge_device_data(target: dict, delta: dict) -> None:
        for key, value in delta.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                OpusGreenNetCoordinator._merge_device_data(target[key], value)
            elif isinstance(value, list) and isinstance(target.get(key), list):
                for index, entry in enumerate(value):
                    if index >= len(target[key]):
                        target[key].append(entry)
                    elif isinstance(entry, dict) and isinstance(target[key][index], dict):
                        OpusGreenNetCoordinator._merge_device_data(target[key][index], entry)
                    else:
                        target[key][index] = entry
            else:
                target[key] = value

    # ──────────────────────────────────────────────────────────────────────
    # Device discovery finalization
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _finalize_discovery(self, *args) -> None:
        """Finalize device discovery after receiving all properties."""
        self._discovery_timer = None
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
            existing_device = self.devices.get(device_id)

            eeps: list[dict[str, Any]] = []
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
                software_revision=str(data.get("softwareRevision", "")),
                hardware_revision=str(data.get("hardwareRevision", "")),
                dbm=EnOceanDevice._parse_number(data.get("dbm"), integer=True),
                battery_level=EnOceanDevice.parse_battery_level(data.get("batteryLevel")),
            )

            rotation_functions: list[dict[str, Any]] = []
            if existing_device is not None:
                device.channels = existing_device.channels
                device.update_from_telegram({"functions": []})
                device.profile = existing_device.profile
                device.last_update_source = existing_device.last_update_source
                device.last_update_received_monotonic = existing_device.last_update_received_monotonic
                device.last_update_finalized_monotonic = existing_device.last_update_finalized_monotonic
                device.last_update_dispatched_monotonic = existing_device.last_update_dispatched_monotonic
                device.last_command_error = existing_device.last_command_error
                if self._resync_requested_at is not None and (
                    existing_device.last_update_received_monotonic is None
                    or existing_device.last_update_received_monotonic <= self._resync_requested_at
                ):
                    self._apply_initial_state(device, data)
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
                friendly_id, device_id,
                [eep.get("eep") if isinstance(eep, dict) else eep for eep in eeps],
                device.entity_type,
            )

            if is_new or was_incomplete:
                async_dispatcher_send(self.hass, f"{SIGNAL_DEVICE_DISCOVERED}_{self.eag_id}", device)
            else:
                async_dispatcher_send(
                    self.hass, f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}", device
                )
        except Exception:
            _LOGGER.exception("Error creating device from data")

    @staticmethod
    def _device_state_functions(data: dict) -> list[dict[str, Any]]:
        """Read cached state from function arrays or an indexed key/value container."""
        state = data.get("state", {})
        if isinstance(state, dict):
            functions = state.get("functions", [])
            if isinstance(functions, dict):
                functions = [
                    functions[index]
                    for index in sorted(functions, key=lambda x: int(x) if str(x).isdigit() else x)
                ]
            if isinstance(functions, list):
                complete = [
                    function
                    for function in functions
                    if isinstance(function, dict)
                    and isinstance(function.get("key"), str)
                    and "value" in function
                ]
                if complete:
                    return complete

        for container in INDEXED_STATE_CONTAINERS:
            entries = data.get(container)
            if isinstance(entries, list):
                complete = [
                    entry
                    for entry in entries
                    if isinstance(entry, dict)
                    and entry.get("key") in KNOWN_STATE_KEYS
                    and "value" in entry
                ]
                if complete:
                    return complete
            elif isinstance(entries, dict):
                complete = [
                    {"key": key, "value": value}
                    for key, value in entries.items()
                    if key in KNOWN_STATE_KEYS
                ]
                if complete:
                    return complete
        return []

    @staticmethod
    def _has_operational_state(functions: list[dict[str, Any]]) -> bool:
        """Return True only for functions that confirm an actual, known state."""
        return any(
            function.get("key") in KNOWN_STATE_KEYS
            and function.get("key") != KEY_ROTATION_TIME
            and not (
                isinstance(function.get("value"), str)
                and function.get("value").strip().lower() == UNKNOWN_VALUE_SENTINEL
            )
            for function in functions
        )

    def _apply_initial_state(self, device: EnOceanDevice, data: dict) -> None:
        functions = [
            function
            for function in self._device_state_functions(data)
            if function.get("key") not in BUTTON_KEYS
        ]
        if functions:
            device.update_from_telegram({"functions": functions})
            device.last_update_source = "discovery"

    # ──────────────────────────────────────────────────────────────────────
    # Telegram messages (stream/telegram - raw radio traffic)
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _handle_telegram_property_message(self, msg: ReceiveMessage) -> None:
        """Handle incoming telegram property messages from flattened MQTT structure."""
        if self._unloaded or getattr(msg, "retain", False):
            return
        try:
            self._log_raw_mqtt_message("stream/telegram", msg)
            match = TELEGRAM_TOPIC_PATTERN.fullmatch(msg.topic)
            if not match:
                return
            eag_id, device_id, property_path = match.groups()
            if eag_id != self.eag_id:
                return

            payload = (
                msg.payload.decode() if isinstance(msg.payload, bytes) else str(msg.payload)
            )

            if property_path in (None, "from", "to"):
                document = json.loads(payload)
                if not isinstance(document, dict):
                    return
                document = document.get("telegram", document)
                if not isinstance(document, dict):
                    return
                if isinstance(document.get("state"), dict):
                    document = {**document, **document["state"]}
                self._finalize_telegram(device_id)
                self._telegram_data[device_id] = (
                    {property_path: document} if property_path else document
                )
                self._telegram_received_at[device_id] = monotonic()
                self._finalize_telegram(device_id)
                return

            existing = self._telegram_data.get(device_id)
            if existing:
                paths = self._telegram_paths.get(device_id, set())
                effective = existing.get("from") or existing.get("to") or existing
                functions = effective.get("functions", [])
                has_complete = isinstance(functions, list) and any(
                    isinstance(function, dict) and "key" in function and "value" in function
                    for function in functions
                )
                direction_changed = (
                    property_path.startswith("from/") and "to" in existing
                ) or (property_path.startswith("to/") and "from" in existing)
                if direction_changed or (property_path in paths and has_complete):
                    self._finalize_telegram(device_id)

            received_at = monotonic()
            self._telegram_received_at.setdefault(device_id, received_at)
            self._log_latency_received("stream/telegram", device_id, property_path, received_at)

            if device_id not in self._telegram_data:
                self._telegram_data[device_id] = {"deviceId": device_id}
            self._telegram_paths.setdefault(device_id, set()).add(property_path)
            self._set_nested_property(self._telegram_data[device_id], property_path, payload)

            if device_id in self._pending_telegrams and self._pending_telegrams[device_id]:
                self._pending_telegrams[device_id]()

            @callback
            def finalize_callback(_now, did=device_id):
                self._finalize_telegram(did)

            self._pending_telegrams[device_id] = async_call_later(
                self.hass, TELEGRAM_FINALIZE_DELAY, finalize_callback
            )
        except Exception:
            _LOGGER.exception("Error handling telegram property message")

    @callback
    def _finalize_telegram(self, device_id: str) -> None:
        """Finalize telegram processing after receiving all properties."""
        if device_id not in self._telegram_data:
            return

        telegram_data = self._telegram_data.pop(device_id)
        if cancel := self._pending_telegrams.pop(device_id, None):
            cancel()
        self._telegram_paths.pop(device_id, None)
        received_at = self._telegram_received_at.pop(device_id, None)
        finalized_at = monotonic()

        from_data = telegram_data.get("from", {})
        to_data = telegram_data.get("to", {})
        if not isinstance(from_data, dict) or not isinstance(to_data, dict):
            return

        effective_data = from_data or to_data or telegram_data
        direction = effective_data.get("direction") or telegram_data.get("direction")
        is_outbound_command = direction == "to" or (to_data and not from_data)

        if from_data and is_outbound_command:
            return
        if is_outbound_command and self._command_waiters.get(device_id):
            return

        friendly_id = effective_data.get("friendlyId") or telegram_data.get("friendlyId") or device_id

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
            func for func in functions
            if isinstance(func.get("key"), str) and func.get("value") is not None
        ]
        functions = complete_functions
        if not functions:
            return

        if is_outbound_command:
            functions = [
                func for func in functions
                if func.get("key") in KNOWN_STATE_KEYS or func.get("key") == KEY_CHANNEL
            ]
            if not any(function.get("key") in KNOWN_STATE_KEYS for function in functions):
                return

        if not is_outbound_command and self._has_operational_state(functions):
            for channel_id in self._channels_from_functions(functions):
                self._cancel_reconciliation_queries(device_id, channel_id)

        update_source = "stream/telegram/to" if is_outbound_command else "stream/telegram/from"
        self._log_latency_finalized(update_source, device_id, received_at, finalized_at, len(functions))

        telegram_info = effective_data.get("telegramInfo") or telegram_data.get("telegramInfo", {})
        telegram = {
            "deviceId": device_id,
            "friendlyId": friendly_id,
            "functions": functions,
            "timestamp": effective_data.get("timestamp") or telegram_data.get("timestamp"),
            "telegramInfo": telegram_info if isinstance(telegram_info, dict) else {},
        }

        device = self.devices.get(device_id)
        if device is None:
            device = EnOceanDevice(
                device_id=device_id, friendly_id=friendly_id, eeps=effective_data.get("eeps", [])
            )
            self.devices[device_id] = device
            if device.entity_type is not None:
                async_dispatcher_send(self.hass, f"{SIGNAL_DEVICE_DISCOVERED}_{self.eag_id}", device)

        device.update_from_telegram(telegram)
        if not is_outbound_command and self._has_operational_state(functions):
            device.last_command_error = None

        signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
        self._mark_and_log_dispatch(device, update_source, signal, received_at, finalized_at)
        async_dispatcher_send(self.hass, signal, device)

        if is_outbound_command and self._has_operational_state(functions):
            for channel_id in self._channels_from_functions(functions):
                self._schedule_reconciliation_queries(device_id, channel_id)

    # ──────────────────────────────────────────────────────────────────────
    # Command sending
    # ──────────────────────────────────────────────────────────────────────

    async def async_send_command(self, device_id: str, functions: list[dict[str, Any]]) -> None:
        """Send a command to a device and wait for the gateway's acknowledgement."""
        topic = TOPIC_PUT_STATE.format(base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id)
        payload = json.dumps({"state": {"functions": functions}})
        _LOGGER.debug("Sending command to %s: %s", topic, payload)

        if not self.available:
            raise request_error("gateway_unavailable", device_id)
        device = self.get_device(device_id)
        channels = self._channels_from_functions(functions)
        revisions = (
            {
                channel_id: getattr(device.channels.get(channel_id), "state_revision", None)
                for channel_id in channels
            }
            if device
            else {}
        )
        self._command_waiters[device_id] = self._command_waiters.get(device_id, 0) + 1
        try:
            await self._requests.async_request(
                topic,
                f"{TOPIC_BASE}/{self.eag_id}/putAnswer/devices/{device_id}/state",
                device_id,
                payload,
                require_status=True,
                is_available=lambda: self.available,
            )
            if device is not None and self._has_operational_state(functions):
                for channel_id in channels:
                    if getattr(device.channels.get(channel_id), "state_revision", None) == revisions[channel_id]:
                        self._schedule_reconciliation_queries(device_id, channel_id)
        finally:
            remaining = self._command_waiters[device_id] - 1
            if remaining:
                self._command_waiters[device_id] = remaining
            else:
                self._command_waiters.pop(device_id, None)

    def _with_channel_if_needed(
        self, device_id: str, functions: list[dict[str, Any]], channel: int
    ) -> list[dict[str, Any]]:
        device = self.get_device(device_id)
        if channel > 0 or (device is not None and device.channel_count > 1):
            return [{"key": KEY_CHANNEL, "value": str(channel)}, *functions]
        return functions

    async def async_turn_on(
        self, device_id: str, channel: int = 0, brightness: int | None = None, is_dimmable: bool = False
    ) -> None:
        if brightness is not None:
            functions = [{"key": "dimValue", "value": str(brightness)}]
        elif is_dimmable:
            functions = [{"key": "dimValue", "value": "100"}]
        else:
            functions = [{"key": "switch", "value": "on"}]
        await self.async_send_command(device_id, self._with_channel_if_needed(device_id, functions, channel))

    async def async_turn_off(self, device_id: str, channel: int = 0, is_dimmable: bool = False) -> None:
        functions = [{"key": "dimValue", "value": "0"}] if is_dimmable else [{"key": "switch", "value": "off"}]
        await self.async_send_command(device_id, self._with_channel_if_needed(device_id, functions, channel))

    async def async_set_cover_position(self, device_id: str, position: int, channel: int = 0) -> None:
        functions = [{"key": "position", "value": str(position)}]
        await self.async_send_command(device_id, self._with_channel_if_needed(device_id, functions, channel))

    async def async_set_cover_tilt(self, device_id: str, tilt: int, channel: int = 0) -> None:
        functions = [{"key": "angle", "value": str(tilt)}]
        await self.async_send_command(device_id, self._with_channel_if_needed(device_id, functions, channel))

    async def async_stop_cover(self, device_id: str, channel: int = 0) -> None:
        """Stop cover movement.

        EEP D2-05-xx defines a dedicated stop function - key "stop", value
        "true" - separate from "position" (which only accepts 0-100 or
        "unknown").
        """
        functions = [{"key": KEY_STOP, "value": "true"}]
        functions = self._with_channel_if_needed(device_id, functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_query_device_status(self, device_id: str, channel: int = 0) -> None:
        functions = [{"key": "query", "value": "status"}]
        await self.async_send_command(device_id, self._with_channel_if_needed(device_id, functions, channel))

    async def async_set_climate_setpoint(self, device_id: str, temperature: float) -> None:
        await self.async_send_command(device_id, [{"key": "temperatureSetpoint", "value": str(temperature)}])

    async def async_set_climate_mode(self, device_id: str, mode: str) -> None:
        await self.async_send_command(device_id, [{"key": "heaterMode", "value": mode}])

    async def async_query_climate_status(self, device_id: str) -> None:
        await self.async_query_device_status(device_id)

    # ──────────────────────────────────────────────────────────────────────
    # Ported: HOPPE window handle access control (D2-06-40)
    # ──────────────────────────────────────────────────────────────────────

    async def async_set_window_handle_lock(self, device_id: str, *, locked: bool) -> None:
        """Allow or deny operation of a HOPPE window handle."""
        if not self.available:
            raise request_error("gateway_unavailable", device_id)
        topic = TOPIC_WINDOW_HANDLE_ACCESS.format(
            base=TOPIC_BASE, eag_id=self.eag_id, device_id=device_id
        )
        payload = LOCK_COMMAND_NOT_ALLOWED if locked else LOCK_COMMAND_ALLOWED
        _LOGGER.debug("Sending HOPPE window handle command to %s: %s", topic, payload)
        await mqtt.async_publish(self.hass, topic, payload, qos=1, retain=False)

    # ──────────────────────────────────────────────────────────────────────
    # Device profile / configuration (ReCom API)
    # ──────────────────────────────────────────────────────────────────────

    async def async_get_device_profile(self, device_id: str) -> None:
        profile = await self._async_device_request(
            device_id, TOPIC_GET_DEVICE_PROFILE, TOPIC_GET_ANSWER_DEVICE_PROFILE
        )
        if device := self.get_device(device_id):
            device.profile = profile

    async def _async_device_request(
        self,
        device_id: str,
        topic_pattern: str,
        answer_pattern: str,
        payload: str = "",
        *,
        require_status: bool = False,
    ) -> dict[str, Any]:
        if not self.available:
            raise request_error("gateway_unavailable", device_id)
        values = {"base": TOPIC_BASE, "eag_id": self.eag_id, "device_id": device_id}
        return await self._requests.async_request(
            topic_pattern.format(**values),
            answer_pattern.format(**values),
            device_id,
            payload,
            require_status=require_status,
            is_available=lambda: self.available,
        )

    async def async_get_device_configuration(self, device_id: str) -> dict[str, Any]:
        return await self._async_device_request(
            device_id, TOPIC_GET_DEVICE_CONFIGURATION, TOPIC_GET_ANSWER_DEVICE_CONFIGURATION
        )

    async def async_set_device_configuration(self, device_id: str, config: dict[str, Any]) -> None:
        await self._async_device_request(
            device_id,
            TOPIC_PUT_DEVICE_CONFIGURATION,
            TOPIC_PUT_ANSWER_DEVICE_CONFIGURATION,
            json.dumps(config),
            require_status=True,
        )

    async def async_get_device_parameters(self, device_id: str) -> dict[str, Any]:
        return await self._async_device_request(
            device_id, TOPIC_GET_DEVICE_PARAMETERS, TOPIC_GET_ANSWER_DEVICE_PARAMETERS
        )

    # ──────────────────────────────────────────────────────────────────────
    # Device lookup helpers
    # ──────────────────────────────────────────────────────────────────────

    def get_device(self, device_id: str) -> EnOceanDevice | None:
        return self.devices.get(device_id)

    def get_devices_by_type(self, entity_type: str) -> list[EnOceanDevice]:
        return [device for device in self.devices.values() if device.entity_type == entity_type]
