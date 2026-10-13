"""Data coordinator for Opus GreenNet Bridge integration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt import ReceiveMessage
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    BUTTON_KEYS,
    COVER_MOVEMENT_GRACE_SECONDS,
    COVER_RECONCILIATION_DELAYS,
    DOMAIN,
    INDEXED_STATE_CONTAINERS,
    KEY_CHANNEL,
    KEY_QUERY,
    KEY_ROTATION_TIME,
    KEY_STOP,
    KEY_VERTICAL_MOVEMENT_TIME,
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
    TOPIC_PUT_ANSWER_DEVICE_CONFIGURATION,
    TOPIC_PUT_DEVICE_CONFIGURATION,
    TOPIC_PUT_STATE,
    TOPIC_SUB_DEVICE_STREAM_ALL,
    TOPIC_SUB_DEVICES_ALL,
    TOPIC_SUB_PUT_ANSWER_STATE,
    TOPIC_SUB_TELEGRAM_FROM_ALL,
)
from .enocean_device import EnOceanDevice
from .mqtt_transport import (
    PRIORITY_BACKGROUND,
    PRIORITY_COMMAND,
    PRIORITY_QUERY,
    REQUEST_TIMEOUT,
    SETUP_REQUEST_TIMEOUT,
    SETUP_SUBSCRIPTION_TIMEOUT,
    MQTTRequestManager,
    async_get_gateway_uptime,
    async_wait_for_subscriptions,
    decode_response,
    gateway_uptime_value,
    request_error,
)

_LOGGER = logging.getLogger(__name__)

# Dispatcher signals
SIGNAL_DEVICE_REMOVED = f"{DOMAIN}_device_removed"
SIGNAL_DEVICE_DISCOVERED = f"{DOMAIN}_device_discovered"
SIGNAL_DEVICE_STATE_UPDATE = f"{DOMAIN}_device_state_update"
SIGNAL_AVAILABILITY_UPDATE = f"{DOMAIN}_availability_update"

FAST_PATH_PROPERTIES = frozenset({"batteryLevel", "dbm"})
DEVICE_STREAM_FINALIZE_DELAY = 0.02
TELEGRAM_FINALIZE_DELAY = 0.15
RAW_MQTT_DEBUG_PAYLOAD_LIMIT = 500
OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)
GATEWAY_HEALTH_INTERVAL = timedelta(seconds=60)
# Each broker/bridge reconnect re-requests the full device snapshot so
# entities catch up without live telegrams. A flapping connection must not
# re-trigger that multi-thousand-message snapshot every few seconds: within
# this window after the previous reconnect, only the health probe runs and
# the retained replay refreshes entity state.
RECONNECT_RESYNC_DEBOUNCE_SECONDS = 60.0
# stream/# holds tens of thousands of retained telemetry topics. QoS 0
# avoids one PUBACK per replayed message while periodic telegrams make a
# rare lost message self-healing; request/response topics stay at QoS 1.
STREAM_SUBSCRIPTION_QOS = 0

# Regex to parse device topics (plural - initial full state at boot)
# EnOcean/{EAG}/stream/devices/{DeviceID}/{property}
DEVICE_TOPIC_PATTERN = re.compile(r"EnOcean/([^/]+)/stream/devices/([^/]+)/(.+)")

# Regex to parse telegram topics
# EnOcean/{EAG}/stream/telegram/{DeviceID}/{property}
TELEGRAM_TOPIC_PATTERN = re.compile(
    r"EnOcean/([^/]+)/stream/telegram/([^/]+)(?:/(.+))?"
)

# Regex to parse device stream topics (singular - live deltas)
# EnOcean/{EAG}/stream/device/{DeviceID}/{property}
DEVICE_STREAM_TOPIC_PATTERN = re.compile(r"EnOcean/([^/]+)/stream/device/([^/]+)/(.+)")
PUT_ANSWER_STATE_TOPIC_PATTERN = re.compile(
    r"EnOcean/([^/]+)/putAnswer/devices/([^/]+)/state"
)


@dataclass
class CoverMovement:
    """One channel's replaceable travel estimate and cancellation handle."""

    start_position: float | None
    target: int
    started_at: float
    travel: float
    cancel: Callable[[], None] | None = None
    accepted: bool = True
    elapsed: bool = False

    @property
    def ends_at(self) -> float:
        return self.started_at + self.travel + COVER_MOVEMENT_GRACE_SECONDS


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
        self._cover_movements: dict[tuple[str, int], CoverMovement] = {}
        self._pending_reconciliation_fields: dict[tuple[str, int], set[str]] = {}
        self._feedback_revisions: dict[tuple[str, int, str], int] = {}
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
        self._reconciling: set[tuple[str, int]] = set()
        self._control_topics: list[str] = []
        self._stream_topics: list[str] = []
        self._last_reconnect_at: float | None = None
        # Gateway info
        self.gateway_info: dict[str, Any] = {}
        self.gateway_uptime: str | None = None

    @callback
    def async_forget_device(self, device_id: str) -> None:
        """Discard only this child's state and queued work, without MQTT writes."""
        for did, channel_id in list(self._cover_movements):
            if did == device_id:
                self._clear_cover_movement(did, channel_id, notify=False)
        self.devices.pop(device_id, None)
        self._pending_devices.discard(device_id)
        for cache in (
            self._device_data,
            self._telegram_data,
            self._device_stream_data,
            self._telegram_received_at,
            self._device_stream_received_at,
            self._telegram_paths,
        ):
            cache.pop(device_id, None)
        for timers in (self._pending_telegrams, self._pending_device_streams):
            if cancel := timers.pop(device_id, None):
                cancel()
        self._cancel_reconciliation_queries(device_id)
        for key in list(self._feedback_revisions):
            if key[0] == device_id:
                self._feedback_revisions.pop(key)
        self._requests.async_cancel_device(device_id)
        async_dispatcher_send(
            self.hass, f"{SIGNAL_DEVICE_REMOVED}_{self.eag_id}", device_id
        )

    @property
    def available(self) -> bool:
        """Require both the broker connection and a responsive OPUS gateway."""
        return (
            not self._unloaded
            and self._gateway_available
            and self._bridge_connected is not False
            and mqtt.is_connected(self.hass)
        )

    def _control_subscriptions(self) -> tuple[tuple[str, Callable, int], ...]:
        """Small, non-retained answer/status topics needed for the probe."""
        return (
            ("opus_greennet/{eag_id}/bridge/status", self._handle_bridge_status, 1),
            (TOPIC_GET_ANSWER_SYSTEM_UPTIME, self._handle_system_uptime, 1),
            (TOPIC_GET_ANSWER_SYSTEM_INFO, self._handle_system_info, 1),
            (TOPIC_SUB_PUT_ANSWER_STATE, self._handle_put_answer_state, 1),
        )

    def _stream_subscriptions(self) -> tuple[tuple[str, Callable, int], ...]:
        """Heavy wildcard topics, ordered by importance for discovery.

        stream/# replays a retained snapshot of up to ~30,000 topics. Each
        filter is subscribed and SUBACKed before the next one is requested,
        so the broker never has to replay everything concurrently. The three
        telemetry wildcards use QoS 0 to keep the retained replay cheap.
        """
        return (
            (TOPIC_GET_ANSWER_DEVICES, self._handle_get_answer_devices, 1),
            (
                TOPIC_SUB_DEVICES_ALL,
                self._handle_device_property_message,
                STREAM_SUBSCRIPTION_QOS,
            ),
            (
                TOPIC_SUB_DEVICE_STREAM_ALL,
                self._handle_device_stream_message,
                STREAM_SUBSCRIPTION_QOS,
            ),
            (
                TOPIC_SUB_TELEGRAM_FROM_ALL,
                self._handle_telegram_property_message,
                STREAM_SUBSCRIPTION_QOS,
            ),
        )

    async def _async_subscribe_group(
        self,
        group: tuple[tuple[str, Callable, int], ...],
        target: list[str],
        *,
        staggered: bool,
        required: bool = True,
    ) -> None:
        """Subscribe missing topics of one group, optionally one SUBACK at a time.

        SUBACK waits always use the same QoS as the subscription itself.
        ``required=False`` treats a missing SUBACK as advisory: telemetry
        listeners are registered with the subscribe call, so messages flow
        even while the confirmation is still outstanding.
        """
        for pattern, handler, qos in group:
            topic = pattern.format(base=TOPIC_BASE, eag_id=self.eag_id)
            if topic in target:
                continue
            self._subscriptions.append(
                await mqtt.async_subscribe(self.hass, topic, handler, qos=qos)
            )
            target.append(topic)
            self._subscription_topics.append(topic)
            if staggered:
                await async_wait_for_subscriptions(
                    self.hass,
                    [topic],
                    timeout=SETUP_SUBSCRIPTION_TIMEOUT,
                    qos=qos,
                    required=required,
                )
                _LOGGER.debug("OPUS subscription ready: %s", topic)
        if not staggered:
            # Wait per QoS level: the done callback fires per topic/QoS pair.
            by_qos: dict[int, list[str]] = {}
            for pattern, _, qos in group:
                topic = pattern.format(base=TOPIC_BASE, eag_id=self.eag_id)
                if topic in target:
                    by_qos.setdefault(qos, []).append(topic)
            for qos, topics in by_qos.items():
                await async_wait_for_subscriptions(
                    self.hass,
                    topics,
                    timeout=SETUP_SUBSCRIPTION_TIMEOUT,
                    qos=qos,
                    required=required,
                )

    @callback
    def _register_answer_routes(self) -> None:
        """Route exact answers through the already active wildcard listeners."""
        for pattern in (TOPIC_GET_ANSWER_SYSTEM_UPTIME, TOPIC_SUB_PUT_ANSWER_STATE):
            self._requests.async_add_route_pattern(
                pattern.format(base=TOPIC_BASE, eag_id=self.eag_id)
            )

    async def async_setup(self) -> bool:
        """Probe the gateway quickly; load the retained snapshot in background.

        Phase 1 (blocking, part of the config entry setup):
          subscribe only the light control/answer topics, wait for their
          SUBACKs and probe the uptime endpoint with setup-specific bounds.
          No stream/# wildcard is active yet, so the retained flood cannot
          delay the probe or the SUBACKs.
        Phase 2 (background task):
          subscribe the heavy stream/# wildcards one by one, then request a
          fresh device snapshot. Entities are created via dispatcher signals
          when discovery finalizes, so setup does not need to wait for this.
        """
        self._subscriptions.append(
            mqtt.async_subscribe_connection_status(
                self.hass, self._handle_connection_status
            )
        )
        try:
            await self._async_subscribe_group(
                self._control_subscriptions(), self._control_topics, staggered=False
            )
            self._register_answer_routes()
            uptime_response = await async_get_gateway_uptime(
                self._requests,
                self.eag_id,
                priority=PRIORITY_COMMAND,
                timeout=SETUP_REQUEST_TIMEOUT,
                subscribe_timeout=SETUP_SUBSCRIPTION_TIMEOUT,
            )
            self.gateway_uptime = gateway_uptime_value(uptime_response)
            self._started = True
            self._subscriptions.append(
                async_track_time_interval(
                    self.hass, self._async_health_tick, GATEWAY_HEALTH_INTERVAL
                )
            )
            # Phase 2: stream subscriptions + snapshot, decoupled from setup.
            self._schedule_gateway_refresh(resync=True)
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

    def _reconnect_requires_snapshot(self) -> bool:
        """Decide whether a reconnect may re-request the full snapshot.

        HA replays every retained stream topic on each broker reconnect, so
        entities refresh anyway. Re-requesting the gateway's snapshot after
        every quick reconnect of a flapping connection would overload the
        gateway and the broker with tens of thousands of messages.
        """
        now = monotonic()
        previous = self._last_reconnect_at
        self._last_reconnect_at = now
        return previous is None or now - previous >= RECONNECT_RESYNC_DEBOUNCE_SECONDS

    @callback
    def _handle_connection_status(self, connected: bool) -> None:
        """Invalidate outstanding work on disconnect and resync on reconnect."""
        if self._unloaded:
            return
        if not connected:
            self._set_gateway_available(False)
            # HA resubscribes after reconnect; until those SUBACKs arrive the
            # persistent wildcards cannot be trusted for answer routing.
            self._requests.async_clear_route_patterns()
            self._requests.async_cancel_pending("mqtt_unavailable")
            self._cancel_background_work()
        elif self._started:
            self._schedule_gateway_refresh(resync=self._reconnect_requires_snapshot())

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
            self._schedule_gateway_refresh(resync=self._reconnect_requires_snapshot())

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
            self._async_refresh_gateway_background(resync=resync),
            # HA emits its connected callback before queuing resubscriptions.
            # Wait until that callback returns before checking SUBACK readiness.
            eager_start=False,
        )

    async def _async_refresh_gateway_background(self, *, resync: bool) -> None:
        try:
            await self._async_refresh_gateway(resync=resync)
        except HomeAssistantError, TimeoutError:
            self._set_gateway_available(False)
            self._requests.async_cancel_pending("gateway_unavailable")
            _LOGGER.debug(
                "OPUS gateway %s did not answer its health probe", self.eag_id
            )

    async def _async_refresh_gateway(self, *, resync: bool) -> None:
        if self._bridge_connected is False:
            raise request_error("gateway_unavailable", self.eag_id)
        # A quick reconnect can interrupt phase two before its subscriptions
        # and first snapshot finish. Debounce only a completed discovery setup.
        resync = (
            resync
            or self._resync_requested_at is None
            or len(self._stream_topics) < len(self._stream_subscriptions())
        )
        # Always re-establish answer routing: HA resubscribes after every
        # broker reconnect, and already-subscribed topics are skipped cheaply.
        # This also resumes an interrupted phase 2.
        await self._async_subscribe_group(
            self._control_subscriptions(), self._control_topics, staggered=False
        )
        self._register_answer_routes()
        if resync:
            # The heavy stream wildcards follow one SUBACK at a time. Their
            # SUBACKs are advisory: a slow confirmation must not fail the
            # health probe or keep the gateway marked unavailable.
            await self._async_subscribe_group(
                self._stream_subscriptions(),
                self._stream_topics,
                staggered=True,
                required=False,
            )
        # A resync coincides with a retained replay; allow a wider deadline.
        uptime_response = await async_get_gateway_uptime(
            self._requests,
            self.eag_id,
            priority=PRIORITY_QUERY,
            timeout=SETUP_REQUEST_TIMEOUT if resync else REQUEST_TIMEOUT,
            subscribe_timeout=SETUP_SUBSCRIPTION_TIMEOUT,
        )
        self.gateway_uptime = gateway_uptime_value(uptime_response)
        if self._unloaded or self._bridge_connected is False:
            raise request_error("gateway_unavailable", self.eag_id)
        if resync:
            self._resync_requested_at = monotonic()
            await mqtt.async_publish(
                self.hass,
                TOPIC_GET_DEVICES.format(base=TOPIC_BASE, eag_id=self.eag_id),
                "",
                qos=1,
                retain=False,
            )
            if not self._discovery_timer:
                self._discovery_timer = async_call_later(
                    self.hass, 2, self._finalize_discovery
                )
        self._set_gateway_available(True)
        if resync:
            # Some firmware answers uptime and discovery but never system/info.
            # Request optional metadata without awaiting an application response.
            try:
                await mqtt.async_publish(
                    self.hass,
                    TOPIC_GET_SYSTEM_INFO.format(base=TOPIC_BASE, eag_id=self.eag_id),
                    "",
                    qos=1,
                    retain=False,
                )
            except HomeAssistantError:
                _LOGGER.debug("Could not request optional OPUS system information")

    def _create_task(
        self, coroutine: Coroutine, *, eager_start: bool = True
    ) -> asyncio.Task:
        """Own every coordinator background operation until unload."""
        task = self.hass.async_create_task(coroutine, eager_start=eager_start)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @callback
    def _cancel_background_work(self) -> None:
        for device_id, channel_id in list(self._cover_movements):
            self._clear_cover_movement(device_id, channel_id, notify=False)
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
        for cancel in self._pending_telegrams.values():
            if cancel:
                cancel()
        for cancel in self._pending_device_streams.values():
            if cancel:
                cancel()
        for device_id, channel_id in list(self._pending_reconciliation_queries):
            self._cancel_reconciliation_queries(device_id, channel_id)
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()
        self._subscription_topics.clear()
        self._control_topics.clear()
        self._stream_topics.clear()
        self._requests.async_clear_route_patterns()
        self._pending_telegrams.clear()
        self._pending_device_streams.clear()
        self._telegram_data.clear()
        self._telegram_paths.clear()
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
            if not getattr(msg, "retain", False):
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

            if self.get_device(device_id) is not None and re.match(
                r"(?:states|transmitModes|configuration/parameters)/\d+/", property_path
            ):
                self._buffer_device_stream_property(
                    device_id, property_path, payload, received_at
                )
                return

            if self._apply_known_device_state_property(
                device_id, property_path, payload, received_at
            ):
                return

            self._pending_devices.add(device_id)

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
            if not getattr(msg, "retain", False):
                self._log_latency_received(
                    "stream/device", device_id, property_path, received_at
                )

            payload = (
                msg.payload.decode()
                if isinstance(msg.payload, bytes)
                else str(msg.payload)
            )
            if self._apply_scalar_telemetry(
                device_id, property_path, payload, received_at
            ):
                return
            self._buffer_device_stream_property(
                device_id, property_path, payload, received_at
            )

        except Exception as err:
            _LOGGER.exception("Error handling device stream message: %s", err)

    def _apply_scalar_telemetry(
        self, device_id: str, property_path: str, payload: str, received_at: float
    ) -> bool:
        """Apply standalone battery/RSSI readings without a debounce timer."""
        device = self.devices.get(device_id)
        if device is None or property_path not in FAST_PATH_PROPERTIES:
            return False
        value = self._parse_value(payload)
        if property_path == "batteryLevel":
            value = EnOceanDevice._normalize_battery_level(value)
        if value != "notAvailable":
            parsed = EnOceanDevice._parse_number(
                value,
                minimum=0 if property_path == "batteryLevel" else None,
                maximum=100 if property_path == "batteryLevel" else None,
                integer=property_path == "dbm",
            )
            if parsed is None:
                return True
            value = parsed
        cached = self._device_data.setdefault(device_id, {"deviceId": device_id})
        cached[property_path] = value
        # A buffered older scalar must not overwrite this newer reading later.
        self._device_stream_data.get(device_id, {}).pop(property_path, None)
        if property_path == "dbm":
            previous = device.dbm
            device.dbm = None if value == "notAvailable" else int(value)
            changed = previous != device.dbm
        else:
            channel = device.get_or_create_channel()
            previous = channel.battery_level
            device.update_from_telegram(
                {"functions": [{"key": "batteryLevel", "value": value}]}
            )
            changed = previous != channel.battery_level
        if changed:
            signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
            self._mark_and_log_dispatch(
                device, "stream/device", signal, received_at, received_at
            )
            async_dispatcher_send(self.hass, signal, device)
        return True

    def _buffer_device_stream_property(
        self, device_id: str, property_path: str, payload: str, received_at: float
    ) -> None:
        """Collect indexed state metadata before applying its value and channel."""
        self._device_stream_received_at.setdefault(device_id, received_at)
        stream_data = self._device_stream_data.setdefault(
            device_id, {"deviceId": device_id}
        )
        self._set_nested_property(stream_data, property_path, payload)
        if cancel := self._pending_device_streams.get(device_id):
            cancel()

        @callback
        def finalize_callback(_now):
            self._finalize_device_stream(device_id)

        self._pending_device_streams[device_id] = async_call_later(
            self.hass, DEVICE_STREAM_FINALIZE_DELAY, finalize_callback
        )

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

        # Indexed device-model deltas may contain only states/N/value. Resolve
        # their unchanged key/channel metadata from the accumulated snapshot.
        state_delta = {
            key: value for key, value in stream_data.items() if key != "configuration"
        }
        for container in INDEXED_STATE_CONTAINERS:
            delta = stream_data.get(container)
            cached = cached_data.get(container)
            if isinstance(delta, list) and isinstance(cached, list):
                if container == "transmitModes" and device.is_smoke_detector:
                    cached = self._rwm_transmit_modes(cached)
                state_delta[container] = self._indexed_changed_functions(delta, cached)
        functions = self._device_state_functions(state_delta)

        configuration = stream_data.get("configuration")
        cached_configuration = cached_data.get("configuration")
        if isinstance(configuration, dict) and isinstance(cached_configuration, dict):
            parameters = configuration.get("parameters")
            cached_parameters = cached_configuration.get("parameters")
            if isinstance(parameters, list) and isinstance(cached_parameters, list):
                parameters = self._indexed_changed_functions(
                    parameters,
                    cached_parameters,
                    default_channel=cached_configuration.get("channel", 0),
                )
                configuration = {**configuration, "parameters": parameters}
            functions.extend(
                self._configuration_state_functions(
                    {
                        "configuration": configuration,
                    }
                )
            )

        if functions:
            self._log_latency_finalized(
                "stream/device", device_id, received_at, finalized_at, len(functions)
            )
            if self._record_confirmed_feedback(device_id, functions):
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
        # RPC answers are responses to current requests, not retained snapshots.
        # Replaying an old answer can restore stale state or rediscover a device
        # that the gateway no longer reports. Retained stream/devices discovery
        # remains supported by its separate handler.
        if getattr(msg, "retain", False):
            return
        # The wildcard also receives profile/configuration responses, which are
        # owned by request waiters and must never enter device discovery.
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
        self._requests.async_route(msg)
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
                "OPUS command accepted for %s with HTTP status %d",
                device_id,
                status,
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
            if isinstance(data, dict) and data != self.gateway_info:
                self.gateway_info = data
                _LOGGER.debug(
                    "Updated system information for OPUS gateway %s", self.eag_id
                )
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as err:
            _LOGGER.debug("Could not parse system info: %s", err)

    @callback
    def _handle_system_uptime(self, msg: ReceiveMessage) -> None:
        """Handle gateway uptime response."""
        self._requests.async_route(msg)
        try:
            data = decode_response(msg.payload, require_status=True)
            self.gateway_uptime = gateway_uptime_value(data)
            _LOGGER.debug("Gateway uptime: %s", self.gateway_uptime)
        except ValueError as err:
            _LOGGER.debug("Could not parse system uptime: %s", err)

    # ──────────────────────────────────────────────────────────────────────
    # Shared helpers
    # ──────────────────────────────────────────────────────────────────────

    def _log_raw_mqtt_message(self, source: str, msg: ReceiveMessage) -> None:
        """Log raw OPUS MQTT message details when debug logging is enabled."""
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        if source in ("stream/devices", "stream/device") and getattr(
            msg, "retain", False
        ):
            # A startup replay can contain tens of thousands of properties.
            # Discovery/finalization logs summarize these; retain live details.
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
        if state_key in BUTTON_KEYS:
            # Device-model state is cached and may be retained. Only incoming
            # radio telegrams represent a new rocker occurrence.
            return True
        if state_key not in KNOWN_STATE_KEYS:
            return False

        _, device = device_entry
        value = self._parse_value(payload)
        if self._record_confirmed_feedback(
            device_id, [{"key": state_key, "value": value}]
        ):
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

    def _dispatch_cover_estimate(self, device_id: str) -> None:
        if (device := self.get_device(device_id)) is not None:
            async_dispatcher_send(
                self.hass,
                f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}",
                device,
            )

    def _clear_cover_movement(
        self, device_id: str, channel_id: int, *, notify: bool = True
    ) -> None:
        movement = self._cover_movements.pop((device_id, channel_id), None)
        if movement is None:
            return
        if movement.cancel is not None:
            movement.cancel()
        if (device := self.get_device(device_id)) is not None:
            device.get_or_create_channel(channel_id).movement = None
        if notify:
            self._dispatch_cover_estimate(device_id)

    def _start_cover_movement(
        self, device: EnOceanDevice, channel_id: int, target: int
    ) -> CoverMovement | None:
        channel = device.get_or_create_channel(channel_id)
        key = (device.device_id, channel_id)
        previous = self._cover_movements.get(key)
        if previous is not None and previous.target == target:
            # An outbound echo must not extend the same movement's deadline.
            # A repeated local request must also suppress the entity's target
            # assignment after its ACK, just like the first request does.
            channel.state_revision += 1
            return previous
        current = channel.position
        now = monotonic()
        if previous is not None:
            current = previous.start_position
            if current is not None:
                progress = min(
                    1.0, max(0.0, (now - previous.started_at) / previous.travel)
                )
                current += (previous.target - current) * progress
        self._clear_cover_movement(device.device_id, channel_id, notify=False)
        full_time = channel.vertical_movement_time
        if full_time is None or full_time <= 0:
            return None
        travel = (
            full_time if current is None else abs(target - current) / 100 * full_time
        )
        if travel == 0:
            self._dispatch_cover_estimate(device.device_id)
            return None
        # A replacement must stop old status queries before waiting for its ACK.
        # Keep other unconfirmed fields (e.g. tilt) for the replacement schedule.
        pending = self._pending_reconciliation_fields.get(key, set())
        self._cancel_reconciliation_queries(device.device_id, channel_id)
        if pending:
            self._pending_reconciliation_fields[key] = pending
            self._pending_reconciliation_queries[key] = []
        channel.movement = (
            "closing"
            if (target > 50 if current is None else target > current)
            else "opening"
        )
        # Prevent an entity awaiting its command ACK from replacing the last
        # reported position with the target while the cover is still travelling.
        channel.state_revision += 1
        movement = CoverMovement(current, target, now, travel)
        self._cover_movements[key] = movement

        @callback
        def finished(_now):
            self._finish_cover_movement(device.device_id, channel_id, movement)

        movement.cancel = async_call_later(
            self.hass, travel + COVER_MOVEMENT_GRACE_SECONDS, finished
        )
        self._dispatch_cover_estimate(device.device_id)
        return movement

    def _finish_cover_movement(
        self, device_id: str, channel_id: int, movement: CoverMovement
    ) -> None:
        if self._cover_movements.get((device_id, channel_id)) is not movement:
            return
        movement.elapsed = True
        # Very short travel can end before the request's acknowledgement timeout.
        # Keep the estimate cancellable and never assume an unaccepted target.
        if not movement.accepted:
            return
        device = self.get_device(device_id)
        self._clear_cover_movement(device_id, channel_id, notify=False)
        if device is not None:
            channel = device.get_or_create_channel(channel_id)
            channel.position = movement.target  # Assumed, not physical feedback.
            channel.state_revision += 1
            self._dispatch_cover_estimate(device_id)

    def _start_cover_commands(
        self, device: EnOceanDevice | None, functions: list[dict[str, Any]]
    ) -> dict[int, CoverMovement]:
        started = {}
        if device is None or device.entity_type != "cover":
            return started
        default = self._channel_from_functions(functions)
        for function in functions:
            channel = EnOceanDevice._parse_number(
                function.get("channel", default), minimum=0, integer=True
            )
            if channel is None:
                continue
            channel_id = int(channel)
            if function.get("key") == KEY_STOP and function.get("value") == "true":
                self._clear_cover_movement(device.device_id, channel_id)
                self._cancel_reconciliation_queries(device.device_id, channel_id)
            elif function.get("key") == "position":
                target = EnOceanDevice._parse_number(
                    function.get("value"), minimum=0, maximum=100, integer=True
                )
                if target is not None:
                    movement = self._start_cover_movement(
                        device, channel_id, int(target)
                    )
                    if movement is not None:
                        started[channel_id] = movement
        return started

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
            self._pending_reconciliation_fields.pop(key, None)
            for cancel in self._pending_reconciliation_queries.pop(key, []):
                cancel()

    def _schedule_reconciliation_queries(
        self, device_id: str, channel_id: int, *, fields: set[str] | None = None
    ) -> None:
        """Schedule delayed status checks after an optimistic state update."""
        key = (device_id, channel_id)
        # Position and tilt commands can overlap. A later command must not
        # discard an earlier field whose final reading is still unknown.
        expected = self._pending_reconciliation_fields.get(key, set()).union(
            fields or ()
        )
        self._cancel_reconciliation_queries(device_id, channel_id)
        self._pending_reconciliation_queries[key] = []
        if expected:
            self._pending_reconciliation_fields[key] = expected

        delays = OUTBOUND_STATE_RECONCILIATION_DELAYS
        if "position" in expected and (movement := self._cover_movements.get(key)):
            remaining = max(0.0, movement.ends_at - monotonic())
            delays = tuple(remaining + delay for delay in COVER_RECONCILIATION_DELAYS)
        for delay in delays:

            @callback
            def query_callback(_now, did=device_id, channel=channel_id, seconds=delay):
                _LOGGER.debug(
                    "Querying OPUS status for %s channel %s %.0fs after command",
                    did,
                    channel,
                    seconds,
                )
                if not self.available or (did, channel) in self._reconciling:
                    # Coalesce: one queued background query per channel is
                    # enough; never pile up behind a command burst.
                    return
                self._create_task(self._async_reconcile_status(did, channel))

            self._pending_reconciliation_queries[key].append(
                async_call_later(self.hass, delay, query_callback)
            )

    async def _async_reconcile_status(self, device_id: str, channel_id: int) -> None:
        if self.get_device(device_id) is None:
            return
        key = (device_id, channel_id)
        self._reconciling.add(key)
        try:
            await self.async_query_device_status(device_id, channel_id, background=True)
        except HomeAssistantError:
            _LOGGER.debug("Could not reconcile OPUS state for %s", device_id)
        finally:
            self._reconciling.discard(key)

    @staticmethod
    def _channel_from_functions(functions: list[dict[str, Any]]) -> int | None:
        """Return the telegram channel selector, defaulting to channel zero."""
        for function in functions:
            if function.get("key") != KEY_CHANNEL:
                continue
            value = EnOceanDevice._parse_number(
                function.get("value"), minimum=0, integer=True
            )
            return int(value) if value is not None else None
        return 0

    def _set_nested_property(self, data: dict, path: str, value: str) -> None:
        """Set a nested property in a dict using a path like 'eeps/0/eep'."""
        parts = path.split("/")
        if len(parts) > 16 or any(
            not part or (part.isdigit() and int(part) > 255) for part in parts
        ):
            raise ValueError("Invalid or oversized MQTT property path")
        # Gateways can mix named state updates with an indexed snapshot. Keep
        # the snapshot's layout and update its matching function in place.
        if (
            len(parts) == 2
            and parts[0] == "states"
            and not parts[1].isdigit()
            and isinstance(data.get("states"), list)
        ):
            for function in data["states"]:
                if isinstance(function, dict) and function.get("key") == parts[1]:
                    function["value"] = self._parse_value(value)
                    return
            data["states"].append({"key": parts[1], "value": self._parse_value(value)})
            return
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
        if value.startswith('"'):
            try:
                decoded = json.loads(value)
            except ValueError:
                pass
            else:
                if isinstance(decoded, str):
                    value = decoded
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
        """Preserve metadata while assembling separately arriving device deltas."""
        for key, value in delta.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                OpusGreenNetCoordinator._merge_device_data(target[key], value)
            elif isinstance(value, list) and isinstance(target.get(key), list):
                for index, entry in enumerate(value):
                    if index >= len(target[key]):
                        target[key].append(entry)
                    elif isinstance(entry, dict) and isinstance(
                        target[key][index], dict
                    ):
                        OpusGreenNetCoordinator._merge_device_data(
                            target[key][index], entry
                        )
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
                dbm=EnOceanDevice._parse_number(data.get("dbm"), integer=True),
            )

            # Preserve existing channel state or apply initial state from discovery
            rotation_functions = []
            if existing_device is not None:
                device.channels = existing_device.channels
                # Discovery refreshes metadata/state, never a previous rocker
                # occurrence. Clear transients before notifying existing entities.
                device.update_from_telegram({"functions": []})
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
                if self._resync_requested_at is not None and (
                    existing_device.last_update_received_monotonic is None
                    or existing_device.last_update_received_monotonic
                    <= self._resync_requested_at
                ):
                    self._apply_initial_state(device, data)
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
            else:
                async_dispatcher_send(
                    self.hass,
                    f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}",
                    device,
                )

        except Exception as err:
            _LOGGER.exception("Error creating device from data: %s", err)

    @staticmethod
    def _indexed_changed_functions(
        delta: list, cached: list, *, default_channel: Any = 0
    ) -> list[dict[str, Any]]:
        """Attach cached channel context to changed indexed values only."""
        changed_indices = {
            index
            for index, function in enumerate(delta)
            if isinstance(function, dict) and ("value" in function or "key" in function)
        }
        changed = []
        channel = default_channel
        for index, function in enumerate(cached):
            if not isinstance(function, dict):
                continue
            if function.get("key") == KEY_CHANNEL:
                channel = function.get("value")
            elif index in changed_indices:
                changed.append({"channel": channel, **function})
        return changed

    @staticmethod
    def _configuration_state_functions(data: dict) -> list[dict[str, Any]]:
        """Read reported cover movement times, never parameter defaults."""
        configuration = data.get("configuration")
        if not isinstance(configuration, dict):
            return []
        parameters = configuration.get("parameters")
        if not isinstance(parameters, list):
            return []
        channel = configuration.get("channel", 0)
        functions = []
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            if parameter.get("key") == KEY_CHANNEL:
                channel = parameter.get("value")
            elif (
                parameter.get("key") in (KEY_ROTATION_TIME, KEY_VERTICAL_MOVEMENT_TIME)
                and "value" in parameter
            ):
                functions.append({"channel": channel, **parameter})
        return functions

    @staticmethod
    def _device_state_functions(data: dict) -> list[dict[str, Any]]:
        """Read function arrays and both OPUS device-state representations."""
        state = data.get("state", {})
        states = data.get("states", {})
        sources = [state.get("functions", [])] if isinstance(state, dict) else []
        sources.append(states)
        state_functions = []
        for functions in sources:
            if isinstance(functions, dict):
                functions = [
                    functions[index]
                    for index in sorted(
                        functions,
                        key=lambda key: (
                            (0, int(key)) if str(key).isdigit() else (1, str(key))
                        ),
                    )
                ]
            if isinstance(functions, list):
                complete = [
                    function
                    for function in functions
                    if isinstance(function, dict)
                    and isinstance(function.get("key"), str)
                    and function["key"] not in BUTTON_KEYS
                    and "value" in function
                ]
                if complete:
                    state_functions = complete
                    break
        if not state_functions and isinstance(states, dict):
            state_functions = [
                {"key": key, "value": value}
                for key, value in states.items()
                if (key in KNOWN_STATE_KEYS or key == KEY_CHANNEL)
                and key not in BUTTON_KEYS
            ]
        modes = data.get("transmitModes", [])
        eeps = data.get("eeps", [])
        if (
            isinstance(eeps, list)
            and any(
                isinstance(eep, dict) and eep.get("eep") == "F6-05-02" for eep in eeps
            )
            and isinstance(modes, list)
        ):
            modes = OpusGreenNetCoordinator._rwm_transmit_modes(modes)
        if isinstance(modes, list):
            state_functions.extend(
                function
                for function in modes
                if isinstance(function, dict)
                and function.get("key") in ("alarm", "smokeAlarm", "batteryLow")
                and "value" in function
            )
        if "batteryLevel" in data:
            state_functions.append(
                {"key": "batteryLevel", "value": data["batteryLevel"]}
            )
        return state_functions + OpusGreenNetCoordinator._configuration_state_functions(
            data
        )

    @staticmethod
    def _rwm_transmit_modes(modes: list) -> list:
        """Resolve the RWM's documented value-only slots, scoped to F6-05-02."""
        keys = ("smokeAlarm", "batteryLow")
        resolved = []
        for index, mode in enumerate(modes):
            if not isinstance(mode, dict):
                resolved.append(mode)
                continue
            entry = dict(mode)
            if index < len(keys) and entry.get("key") in (None, ""):
                entry["key"] = keys[index]
            resolved.append(entry)
        return resolved

    @staticmethod
    def _has_operational_state(functions: list[dict[str, Any]]) -> bool:
        """Require usable actuator feedback, not interim values or metadata."""
        return bool(OpusGreenNetCoordinator._confirmed_fields_by_channel(functions))

    @staticmethod
    def _confirmed_fields_by_channel(
        functions: list[dict[str, Any]],
    ) -> dict[int, set[str]]:
        """Validate the fields that can confirm an actuator command."""
        default = OpusGreenNetCoordinator._channel_from_functions(functions)
        confirmed: dict[int, set[str]] = {}
        for function in functions:
            key, value = function.get("key"), function.get("value")
            if key in ("dimValue", "position", "angle", "temperatureSetpoint"):
                maximum = 40 if key == "temperatureSetpoint" else 100
                valid = (
                    EnOceanDevice._parse_number(
                        value,
                        minimum=0,
                        maximum=maximum,
                        integer=key != "temperatureSetpoint",
                    )
                    is not None
                )
            elif key == "switch":
                valid = value in ("on", "off")
            elif key == "heaterMode":
                valid = value in ("heating", "on", "off", "autoOff")
            else:
                continue
            channel = EnOceanDevice._parse_number(
                function.get("channel", default), minimum=0, integer=True
            )
            if valid and channel is not None:
                confirmed.setdefault(int(channel), set()).add(key)
        return confirmed

    @staticmethod
    def _command_fields_by_channel(
        functions: list[dict[str, Any]],
        device: EnOceanDevice | None = None,
    ) -> dict[int, set[str]]:
        """Stop needs final position/tilt feedback, not an estimated position."""
        expected = OpusGreenNetCoordinator._confirmed_fields_by_channel(functions)
        default = OpusGreenNetCoordinator._channel_from_functions(functions)
        for function in functions:
            if function.get("key") != KEY_STOP or function.get("value") != "true":
                continue
            channel = EnOceanDevice._parse_number(
                function.get("channel", default), minimum=0, integer=True
            )
            if channel is None:
                continue
            channel_id = int(channel)
            fields = expected.setdefault(channel_id, set())
            fields.add("position")
            if device is not None and device.supports_tilt_for_channel(channel_id):
                fields.add("angle")
        return expected

    def _record_confirmed_feedback(
        self, device_id: str, functions: list[dict[str, Any]]
    ) -> bool:
        """Confirm only reported fields on their own channel, including during ACK."""
        confirmed = self._confirmed_fields_by_channel(functions)
        for channel_id, fields in confirmed.items():
            if "position" in fields:
                self._clear_cover_movement(device_id, channel_id, notify=False)
            for field in fields:
                key = (device_id, channel_id, field)
                self._feedback_revisions[key] = self._feedback_revisions.get(key, 0) + 1
            pending_key = (device_id, channel_id)
            expected = self._pending_reconciliation_fields.get(pending_key)
            if expected is not None:
                expected.difference_update(fields)
            if not expected:
                self._cancel_reconciliation_queries(device_id, channel_id)
        return bool(confirmed)

    def _apply_initial_state(self, device: EnOceanDevice, data: dict) -> None:
        """Apply initial state from device discovery data."""
        _LOGGER.debug(
            "INITIAL STATE: device=%s data_keys=%s",
            device.friendly_id,
            list(data.keys()),
        )
        functions = [
            function
            for function in self._device_state_functions(data)
            if function.get("key") not in BUTTON_KEYS
        ]

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
        # Radio telegrams represent occurrences. Retained replays must not fire
        # rocker events or replace freshly confirmed state after a reconnect.
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
                msg.payload.decode()
                if isinstance(msg.payload, bytes)
                else str(msg.payload)
            )

            if property_path in (None, "from", "to"):
                # OPUS documents complete JSON at /to; accept the equivalent
                # complete inbound shape as well as flattened property topics.
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
                    isinstance(function, dict)
                    and "key" in function
                    and "value" in function
                    for function in functions
                )
                direction_changed = (
                    property_path.startswith("from/") and "to" in existing
                ) or (property_path.startswith("to/") and "from" in existing)
                # A repeated endpoint starts the next flattened frame. Flushing
                # before overwriting preserves rapid press/release and channel
                # reports even when their spacing is below the debounce delay.
                if direction_changed or (property_path in paths and has_complete):
                    self._finalize_telegram(device_id)

            received_at = monotonic()
            self._telegram_received_at.setdefault(device_id, received_at)
            self._log_latency_received(
                "stream/telegram", device_id, property_path, received_at
            )

            if device_id not in self._telegram_data:
                self._telegram_data[device_id] = {"deviceId": device_id}
            self._telegram_paths.setdefault(device_id, set()).add(property_path)
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
        if cancel := self._pending_telegrams.pop(device_id, None):
            cancel()
        self._telegram_paths.pop(device_id, None)
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
        if not isinstance(from_data, dict) or not isinstance(to_data, dict):
            return

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
        if is_outbound_command and self._command_waiters.get(device_id):
            # Entities may optimistically update only after their awaited PUT
            # acknowledgement succeeds. An outbound echo is not that success.
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
            if isinstance(func.get("key"), str) and func.get("value") is not None
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
                if func.get("key") in KNOWN_STATE_KEYS
                or func.get("key") in (KEY_CHANNEL, KEY_STOP)
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
                function.get("key") in KNOWN_STATE_KEYS
                or function.get("key") == KEY_STOP
                for function in functions
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
        if not is_outbound_command:
            self._record_confirmed_feedback(device_id, functions)

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
        if not isinstance(telegram["telegramInfo"], dict):
            telegram["telegramInfo"] = {}

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
        if is_outbound_command:
            estimated = self._start_cover_commands(device, functions)
            default_channel = self._channel_from_functions(functions)
            telegram["functions"] = [
                function
                for function in functions
                if not (
                    function.get("key") == "position"
                    and EnOceanDevice._parse_number(
                        function.get("channel", default_channel),
                        minimum=0,
                        integer=True,
                    )
                    in estimated
                )
            ]
        device.update_from_telegram(telegram)
        if not is_outbound_command and self._has_operational_state(functions):
            device.last_command_error = None

        # Notify listeners of state update
        signal = f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device_id}"
        self._mark_and_log_dispatch(
            device, update_source, signal, received_at, finalized_at
        )
        async_dispatcher_send(self.hass, signal, device)

        if is_outbound_command:
            for channel_id, fields in self._command_fields_by_channel(
                functions, device
            ).items():
                self._schedule_reconciliation_queries(
                    device_id, channel_id, fields=fields
                )

    # ──────────────────────────────────────────────────────────────────────
    # Command sending
    # ──────────────────────────────────────────────────────────────────────

    async def async_send_command(
        self,
        device_id: str,
        functions: list[dict[str, Any]],
        *,
        background: bool = False,
    ) -> None:
        """Send a command to a device using JSON state message.

        All requests pass the transport's global send queue (one in flight).
        ``background`` marks delayed reconciliation queries, which are only
        dispatched when no user command or interactive query is waiting.
        """
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

        if not self.available:
            raise request_error("gateway_unavailable", device_id)
        device = self.get_device(device_id)
        fields_by_channel = self._command_fields_by_channel(functions, device)
        revisions: dict[tuple[int, str], int] = {}
        started_movements: dict[int, CoverMovement] = {}

        @callback
        def snapshot_feedback() -> None:
            # Run inside the request manager's endpoint lock, immediately before
            # publish. Feedback for an earlier queued command cannot confirm this one.
            started_movements.update(self._start_cover_commands(device, functions))
            for movement in started_movements.values():
                movement.accepted = False
            revisions.update(
                {
                    (channel_id, field): self._feedback_revisions.get(
                        (device_id, channel_id, field), 0
                    )
                    for channel_id, fields in fields_by_channel.items()
                    for field in fields
                }
            )

        # Control commands overtake queued status queries/refreshes so that a
        # burst of reconciliation traffic never delays a user action.
        is_query = any(function.get("key") == KEY_QUERY for function in functions)
        if background:
            priority = PRIORITY_BACKGROUND
        elif is_query:
            priority = PRIORITY_QUERY
        else:
            priority = PRIORITY_COMMAND

        self._command_waiters[device_id] = self._command_waiters.get(device_id, 0) + 1
        try:
            try:
                await self._requests.async_request(
                    topic,
                    f"{TOPIC_BASE}/{self.eag_id}/putAnswer/devices/{device_id}/state",
                    device_id,
                    payload,
                    require_status=True,
                    is_available=lambda: self.available,
                    before_publish=snapshot_feedback,
                    priority=priority,
                )
            except HomeAssistantError as err:
                if err.translation_key != "request_timeout":
                    raise
                # A missing acknowledgement only fails this entity action.
                # Gateway availability is driven exclusively by MQTT/bridge
                # connection state and the health probe, never by one command.
                _LOGGER.warning(
                    "OPUS device %s did not acknowledge a %s within the timeout",
                    device_id,
                    "status query" if is_query else "command",
                )
                raise request_error("request_timeout", device_id) from err
            if device is not None:
                for channel_id, fields in fields_by_channel.items():
                    unconfirmed = {
                        field
                        for field in fields
                        if self._feedback_revisions.get(
                            (device_id, channel_id, field), 0
                        )
                        == revisions[channel_id, field]
                    }
                    if unconfirmed:
                        self._schedule_reconciliation_queries(
                            device_id, channel_id, fields=unconfirmed
                        )
                for channel_id, movement in started_movements.items():
                    movement.accepted = True
                    if movement.elapsed:
                        self._finish_cover_movement(device_id, channel_id, movement)
        except BaseException:
            for channel_id, movement in started_movements.items():
                if self._cover_movements.get((device_id, channel_id)) is movement:
                    self._clear_cover_movement(device_id, channel_id)
                    self._cancel_reconciliation_queries(device_id, channel_id)
            raise
        finally:
            remaining = self._command_waiters[device_id] - 1
            if remaining:
                self._command_waiters[device_id] = remaining
            else:
                self._command_waiters.pop(device_id, None)

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
        """Set OPUS position (0 = open, 100 = closed)."""
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
        self._clear_cover_movement(device_id, channel)
        self._cancel_reconciliation_queries(device_id, channel)
        functions = [{"key": KEY_STOP, "value": "true"}]
        functions = self._with_channel_if_needed(device_id, functions, channel)
        await self.async_send_command(device_id, functions)

    async def async_query_device_status(
        self,
        device_id: str,
        channel: int = 0,
        *,
        background: bool = False,
    ) -> None:
        """Query the current device status."""
        functions = [{"key": KEY_QUERY, "value": "status"}]
        functions = self._with_channel_if_needed(device_id, functions, channel)
        await self.async_send_command(device_id, functions, background=background)

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
        """Request and cache a validated profile with bounded subscription ownership."""
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
        """Request one device object or acknowledgement and surface protocol errors."""
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
            priority=PRIORITY_QUERY,
        )

    async def async_get_device_configuration(self, device_id: str) -> dict[str, Any]:
        """Read device configuration, raising an actionable error on failure."""
        return await self._async_device_request(
            device_id,
            TOPIC_GET_DEVICE_CONFIGURATION,
            TOPIC_GET_ANSWER_DEVICE_CONFIGURATION,
        )

    async def async_set_device_configuration(
        self, device_id: str, config: dict[str, Any]
    ) -> None:
        """Write configuration and wait for the gateway's acknowledgement."""
        await self._async_device_request(
            device_id,
            TOPIC_PUT_DEVICE_CONFIGURATION,
            TOPIC_PUT_ANSWER_DEVICE_CONFIGURATION,
            json.dumps(config),
            require_status=True,
        )

    async def async_get_device_parameters(self, device_id: str) -> dict[str, Any]:
        """Read a validated device-parameter response."""
        return await self._async_device_request(
            device_id,
            TOPIC_GET_DEVICE_PARAMETERS,
            TOPIC_GET_ANSWER_DEVICE_PARAMETERS,
        )

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
