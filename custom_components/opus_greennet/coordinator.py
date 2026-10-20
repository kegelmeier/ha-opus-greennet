"""Coordinator for one OPUS gateway, fed by its REST event stream.

Lifecycle:

* Setup reads the gateway identity and its uptime, then opens the event
  stream and waits for its first document, the complete device list (stream
  first). Every entity is created with current state before the platforms
  start. The streamed list carries no ``configuration`` block, so the device
  list is read once right after setup for cover movement and rotation times
  (never live state).
* The same background task keeps ``GET /devices/stream`` open. Each new
  stream begins with the complete device list (a full resynchronisation) and
  then delivers every telegram the moment the gateway handles it.
* A health probe reads the uptime every minute. It detects an unreachable
  gateway even while the stream is silent, and a gateway restart (falling
  uptime) whose old stream connection could otherwise stay half open.
* Commands are HTTP PUTs. The gateway acknowledges them synchronously; one
  radio request is in flight at a time, user commands first.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable, Coroutine, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from time import monotonic, time

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from . import protocol
from .api import (
    PRIORITY_BACKGROUND,
    PRIORITY_COMMAND,
    PRIORITY_QUERY,
    RECOM_TIMEOUT,
    OpusApiError,
    OpusAuthError,
    OpusConnectionError,
    OpusGatewayClient,
    OpusResponseError,
    OpusTimeoutError,
    PriorityLock,
    gateway_uptime_value,
    response_status,
)
from .const import (
    COVER_MOVEMENT_GRACE_SECONDS,
    COVER_RECONCILIATION_DELAYS,
    DOMAIN,
    KEY_CHANNEL,
    KEY_DIMMER,
    KEY_POSITION,
    KEY_QUERY,
    KEY_RAMPING_MODE,
    KEY_STOP,
    RAMPING_MODE_FADE,
    RAMPING_MODE_SWITCH,
)
from .enocean_device import EnOceanDevice
from .wire import JsonObject, WireFunction, WireMapping, as_object, object_items

_LOGGER = logging.getLogger(__name__)

# Dispatcher signals
SIGNAL_DEVICE_REMOVED = f"{DOMAIN}_device_removed"
SIGNAL_DEVICE_DISCOVERED = f"{DOMAIN}_device_discovered"

# Gateway answer to a state command.
SOFT_SMART_ACK_STATUS = 201


class CommandDelivery(StrEnum):
    """How the gateway handled an accepted state command."""

    SENT = "sent"  # 200: the telegram went out over the radio.
    # 201 (SoftSmartAck): queued until the sleeping device wakes up and asks
    # for it. The device has not received anything yet.
    DEFERRED = "deferred"


SIGNAL_DEVICE_STATE_UPDATE = f"{DOMAIN}_device_state_update"
SIGNAL_AVAILABILITY_UPDATE = f"{DOMAIN}_availability_update"

GATEWAY_HEALTH_INTERVAL = timedelta(seconds=60)
# The stream reports live changes; the periodic device list additionally
# refreshes slowly changing metadata (battery level, signal, movement times)
# and picks up newly paired devices.
DEVICE_LIST_REFRESH_INTERVAL = timedelta(minutes=30)
# Reconnecting the event stream (phase R4, finding B-2). Every new stream
# starts with the complete device list, so each attempt is a full download
# from the gateway (no radio telegram). The pause before the next attempt
# grows exponentially from STREAM_BACKOFF_INITIAL to STREAM_BACKOFF_MAX
# seconds and is lengthened by a random share of up to STREAM_BACKOFF_JITTER,
# so it is never shorter than the nominal value. A pause is taken after every
# stream, also after one that connected: a stream that ended within
# STREAM_MIN_HEALTHY_SECONDS of its first document counts as a failure, so a
# gateway that closes every connection right after the device list cannot
# cause a tight reconnect loop. Only a stream that stayed open longer resets
# the backoff.
STREAM_BACKOFF_INITIAL = 1.0
STREAM_BACKOFF_MAX = 60.0
STREAM_BACKOFF_JITTER = 0.25
STREAM_MIN_HEALTHY_SECONDS = 10.0
# Setup opens the event stream and waits for its first device list instead of
# reading GET /devices first (phase R5, plan section 7.2, findings B-8/E7).
# The list is the first document the gateway writes; it is as large as the
# answer of GET /devices, which has a 10 s request timeout. 30 s leaves room
# for large installations on a slow gateway; without the list by then, setup
# fails and Home Assistant retries it with its own backoff.
FIRST_DEVICE_LIST_TIMEOUT = 30.0
# A telegram from a device missing in the device list triggers one lookup of
# that device; failed lookups are retried after this many seconds.
UNKNOWN_DEVICE_RETRY_SECONDS = 3600.0
OUTBOUND_STATE_RECONCILIATION_DELAYS = (5.0, 20.0)
# The gateway echoes every command it sends as a "to" telegram. The echo of
# this integration's own command can arrive just after the HTTP
# acknowledgement; within this time an echo with the same content is not
# mistaken for a command from another controller.
OWN_COMMAND_ECHO_SECONDS = 2.0
# Internal reason for a background status check dropped before sending.
SUPERSEDED = "superseded"


def stream_retry_delay(failures: int, random_share: float | None = None) -> float:
    """Return the pause in seconds before the next event stream attempt.

    ``failures`` counts the stream attempts since the last healthy stream.
    The nominal pause doubles with every failure up to ``STREAM_BACKOFF_MAX``;
    a random share (0 to 1, drawn when ``random_share`` is None) adds up to
    ``STREAM_BACKOFF_JITTER`` of it, so several Home Assistant instances that
    lost the same gateway do not reconnect in lockstep.
    """
    nominal = min(
        STREAM_BACKOFF_MAX,
        STREAM_BACKOFF_INITIAL * 2.0 ** min(max(failures, 0), 32),
    )
    share = random.random() if random_share is None else random_share
    share = min(max(share, 0.0), 1.0)
    return nominal * (1.0 + STREAM_BACKOFF_JITTER * share)


def request_error(key: str, device_id: str, reason: str = "") -> HomeAssistantError:
    """Create a translated request error without logging sensitive payloads."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"device_id": device_id, "reason": reason},
    )


class GatewayMismatchError(OpusApiError):
    """The configured address answers with a different gateway identity."""


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
    """Own the connection to one gateway and the state of its devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: OpusGatewayClient,
        eag_id: str,
        *,
        on_auth_failed: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.client = client
        self.eag_id = eag_id
        self.devices: dict[str, EnOceanDevice] = {}
        self.gateway_info: JsonObject = {}
        self.gateway_uptime: str | None = None
        self._on_auth_failed = on_auth_failed
        self._auth_failed = False
        self._unloaded = False
        self._gateway_available = False
        self._stream_connected = False
        self._stream_task: asyncio.Task[None] | None = None
        # Incremented for every new stream reader. A replaced reader may still
        # hold documents it parsed before its cancellation takes effect; it
        # drops them instead of applying stale state (phase R4).
        self._stream_generation = 0
        # Resolved by the stream with its first device list during setup, or
        # failed with the error of the first stream attempt (phase R5).
        self._first_device_list: asyncio.Future[None] | None = None
        # Seconds from opening the stream to its first device list at setup
        # (diagnostics; measurement asked for by REST plan R5).
        self.first_device_list_seconds: float | None = None
        # Monotonic time of the last received telegram per device; a device
        # list requested before it never replaces its signal strength and
        # last-seen time (finding B-5).
        self._telegram_metadata_at: dict[str, float] = {}
        self._probe_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._timers: list[Callable[[], None]] = []
        # One radio request in flight at a time, dispatched by priority.
        self._radio_queue = PriorityLock()
        self._device_generations: dict[str, int] = {}
        # Incremented whenever the gateway becomes unavailable: requests queued
        # before an outage are never sent after the gateway returns.
        self._outage_generation = 0
        self._unknown_devices: dict[str, float] = {}
        self._command_waiters: dict[str, int] = {}
        self._sent_commands: dict[str, list[tuple[float, frozenset[object]]]] = {}
        self._cover_movements: dict[tuple[str, int], CoverMovement] = {}
        # Last vertical run duration per device and channel from the device
        # list (diagnostics only, REST phase 02). Never used for estimates,
        # timers or queries.
        self.last_vertical_movement_times: dict[str, dict[int, float]] = {}
        # Origin and value of each cover position at the moment the integration
        # set it (diagnostics only). A position that differs from the recorded
        # value was written by an entity estimate.
        self._position_origins: dict[tuple[str, int], tuple[str, int]] = {}
        self._pending_reconciliation_queries: dict[
            tuple[str, int], list[Callable[[], None]]
        ] = {}
        self._pending_reconciliation_fields: dict[tuple[str, int], set[str]] = {}
        self._feedback_revisions: dict[tuple[str, int, str], int] = {}
        self._reconciling: set[tuple[str, int]] = set()
        # Channels whose delayed status check is still wanted. A queued
        # background check is sent only while its channel is listed here;
        # feedback or a newer command removes it before the request is sent.
        self._reconcile_needed: set[tuple[str, int]] = set()

    # ──────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """Return whether the gateway answers and its event stream is open."""
        return not self._unloaded and self._gateway_available

    async def async_setup(self) -> None:
        """Read identity and uptime, then take the devices from the stream.

        The event stream starts with the complete device list; that list is the
        only snapshot at setup (stream first, phase R5). The same reader keeps
        the stream open afterwards. Raises an ``OpusApiError`` when the gateway
        cannot be used yet: the error of the first stream attempt, or
        ``OpusTimeoutError`` when no device list arrives within
        ``FIRST_DEVICE_LIST_TIMEOUT``.
        """
        info = await self.client.async_get("/system/info")
        self._apply_system_info(info)
        self.gateway_uptime = gateway_uptime_value(
            await self.client.async_get("/system/uptime")
        )
        first = self.hass.loop.create_future()
        self._first_device_list = first
        opened_at = monotonic()
        self._start_stream()
        try:
            async with asyncio.timeout(FIRST_DEVICE_LIST_TIMEOUT):
                await asyncio.shield(first)
        except TimeoutError as err:
            raise OpusTimeoutError(
                "The event stream sent no device list within "
                f"{FIRST_DEVICE_LIST_TIMEOUT:.0f} s"
            ) from err
        finally:
            self._first_device_list = None
            if not first.done():
                first.cancel()
        self.first_device_list_seconds = round(monotonic() - opened_at, 3)
        _LOGGER.debug(
            "OPUS gateway %s sent its first device list after %.3f s",
            self.eag_id,
            self.first_device_list_seconds,
        )
        self._timers.append(
            async_track_time_interval(
                self.hass, self._async_health_tick, GATEWAY_HEALTH_INTERVAL
            )
        )
        self._timers.append(
            async_track_time_interval(
                self.hass, self._async_refresh_tick, DEVICE_LIST_REFRESH_INTERVAL
            )
        )
        # The stream's device list carries no ``configuration`` block on real
        # gateways (OPUS-IQ-DOT 1.21.30; phase 21 hardware diagnostics: 0 of
        # 10 covers had a movement time after a stream-first start). Cover
        # movement and rotation times come only from GET /devices, so the
        # metadata refresh runs once before setup returns, so entities start
        # with their movement times. It is not authoritative: live state stays
        # with the stream, and newer telegram metadata is kept (B-5). A failure
        # is only logged; the 30 min refresh tries again. No radio telegram is
        # sent.
        await self._async_refresh_device_list()

    async def async_unload(self) -> None:
        """Stop all background work and mark the gateway unavailable."""
        _LOGGER.debug("Unloading Opus GreenNet coordinator for EAG %s", self.eag_id)
        self._unloaded = True
        self._set_gateway_available(False)
        for cancel in self._timers:
            cancel()
        self._timers.clear()
        self._cancel_background_work()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

    def _apply_system_info(self, document: JsonObject) -> None:
        """Store the gateway information and verify its identity."""
        info = as_object(document.get("systemInfo")) or {}
        eurid = info.get("eurid")
        if isinstance(eurid, str) and eurid.upper() != self.eag_id:
            raise GatewayMismatchError(
                f"The gateway at {self.client.host} reports EURID {eurid}"
            )
        self.gateway_info = info

    def _create_task(
        self, coroutine: Coroutine[object, object, None], name: str
    ) -> asyncio.Task[None]:
        """Own every background operation until unload."""
        task = self.hass.async_create_background_task(
            coroutine, f"{DOMAIN} {self.eag_id} {name}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @callback
    def _set_gateway_available(self, available: bool) -> None:
        """Notify every entity when gateway health changes."""
        if self._gateway_available == available:
            return
        self._gateway_available = available
        if not available:
            self._outage_generation += 1
            self._cancel_background_work()
        if not self._unloaded:
            _LOGGER.info(
                "OPUS gateway %s is %s",
                self.eag_id,
                "available" if available else "unavailable",
            )
        async_dispatcher_send(self.hass, f"{SIGNAL_AVAILABILITY_UPDATE}_{self.eag_id}")

    @callback
    def _cancel_background_work(self) -> None:
        """Drop estimates and delayed checks that assume a working gateway."""
        for device_id, channel_id in list(self._cover_movements):
            self._clear_cover_movement(device_id, channel_id, notify=False)
        for device_id, channel_id in list(self._pending_reconciliation_queries):
            self._cancel_reconciliation_queries(device_id, channel_id)
        self._reconcile_needed.clear()

    @callback
    def _handle_auth_failure(self) -> None:
        """Stop using rejected credentials and ask the user for new ones once."""
        self._set_gateway_available(False)
        if self._auth_failed:
            return
        self._auth_failed = True
        _LOGGER.warning("OPUS gateway %s rejected the credentials", self.eag_id)
        if self._on_auth_failed is not None:
            self._on_auth_failed()

    # ──────────────────────────────────────────────────────────────────────
    # Event stream
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _start_stream(self) -> None:
        if self._unloaded or self._auth_failed:
            return
        self._stream_generation += 1
        self._stream_task = self._create_task(
            self._async_run_stream(self._stream_generation), "stream"
        )

    @callback
    def _restart_stream(self) -> None:
        """Replace the stream connection, e.g. after a gateway restart."""
        if self._stream_task is not None and not self._stream_task.done():
            self._stream_task.cancel()
        self._stream_connected = False
        self._start_stream()

    def _is_current_stream(self, generation: int) -> bool:
        """Return whether ``generation`` is the reader that may apply state."""
        return not self._unloaded and generation == self._stream_generation

    async def _async_run_stream(self, generation: int) -> None:
        """Keep the event stream open until unload or replacement.

        Only the reader of the current ``generation`` applies documents and
        changes availability. A pause from ``stream_retry_delay`` follows every
        stream; ``failures`` is reset only by a stream that stayed open for at
        least ``STREAM_MIN_HEALTHY_SECONDS``. Rejected credentials end the
        reader without another attempt.
        """
        failures = 0
        while self._is_current_stream(generation):
            connected_at: float | None = None
            try:
                # ``aclosing`` releases the HTTP response as soon as a replaced
                # reader leaves the loop, not only when the generator is
                # garbage collected.
                async with aclosing(self.client.async_stream()) as stream:
                    async for document in stream:
                        if not self._is_current_stream(generation):
                            return
                        if connected_at is None:
                            connected_at = monotonic()
                            self._stream_connected = True
                            _LOGGER.debug(
                                "OPUS event stream connected for %s", self.eag_id
                            )
                        self._handle_document(document)
                        if not self._gateway_available:
                            self._set_gateway_available(True)
                        if _is_device_list(document):
                            self._resolve_first_device_list(None)
                _LOGGER.debug("OPUS event stream for %s ended", self.eag_id)
                self._resolve_first_device_list(
                    OpusConnectionError("The event stream ended before its device list")
                )
            except OpusAuthError as err:
                if self._is_current_stream(generation):
                    self._stream_connected = False
                    if not self._resolve_first_device_list(err):
                        self._handle_auth_failure()
                return
            except OpusApiError as err:
                _LOGGER.debug("OPUS event stream for %s failed: %s", self.eag_id, err)
                self._resolve_first_device_list(err)
            except Exception as err:
                _LOGGER.exception("Unexpected error in the OPUS event stream")
                self._resolve_first_device_list(
                    OpusResponseError(f"Unexpected error in the event stream: {err}")
                )
            if not self._is_current_stream(generation):
                return
            self._stream_connected = False
            if connected_at is None:
                # No document arrived: the gateway cannot be used right now.
                self._set_gateway_available(False)
            elif monotonic() - connected_at >= STREAM_MIN_HEALTHY_SECONDS:
                failures = 0
            else:
                # The gateway answers but closed the stream right away. The
                # entities stay available (the device list just arrived); the
                # attempt still counts towards the backoff (finding B-2).
                _LOGGER.debug(
                    "OPUS event stream for %s closed after less than %s s",
                    self.eag_id,
                    STREAM_MIN_HEALTHY_SECONDS,
                )
            delay = stream_retry_delay(failures)
            failures += 1
            await asyncio.sleep(delay)

    @callback
    def _resolve_first_device_list(self, error: OpusApiError | None) -> bool:
        """Finish a waiting setup; return whether one was waiting.

        Only the setup's first stream attempt reports here. Later attempts,
        after setup finished, keep the normal reconnect behaviour.
        """
        first = self._first_device_list
        if first is None or first.done():
            return False
        if error is None:
            first.set_result(None)
        else:
            first.set_exception(error)
        return True

    @callback
    def _handle_document(self, document: JsonObject) -> None:
        """Apply one event stream document."""
        header = as_object(document.get("header")) or {}
        content = header.get("content")
        if content == "devices":
            self._apply_device_list(document, authoritative=True)
        elif content == "device":
            if (data := as_object(document.get("device"))) is not None:
                # A single changed device is live feedback, unlike a list.
                if isinstance(device_id := data.get("deviceId"), str):
                    self._record_confirmed_feedback(
                        device_id, protocol.device_state_functions(data)
                    )
                self._apply_device_object(
                    data, authoritative=True, position_origin="reported"
                )
        elif content == "telegram":
            if (telegram := as_object(document.get("telegram"))) is not None:
                self._handle_telegram(telegram)
        else:
            _LOGGER.debug("Ignoring OPUS stream document of type %r", content)

    # ──────────────────────────────────────────────────────────────────────
    # Health probe and device list refresh
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _async_health_tick(self, _now: datetime) -> None:
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = self._create_task(self._async_probe(), "health probe")

    async def _async_probe(self) -> None:
        """Check that the gateway answers and detect gateway restarts."""
        try:
            uptime = gateway_uptime_value(await self.client.async_get("/system/uptime"))
        except OpusAuthError:
            self._handle_auth_failure()
            return
        except OpusApiError as err:
            _LOGGER.debug("OPUS gateway %s health probe failed: %s", self.eag_id, err)
            if self._gateway_available:
                self._set_gateway_available(False)
                # The open stream may be half open towards a rebooting gateway.
                self._restart_stream()
            return
        previous = self.gateway_uptime
        self.gateway_uptime = uptime
        if previous is not None and float(uptime) < float(previous):
            _LOGGER.info("OPUS gateway %s restarted; reopening its stream", self.eag_id)
            self._restart_stream()
        elif self._stream_connected and not self._gateway_available:
            self._set_gateway_available(True)

    @callback
    def _async_refresh_tick(self, _now: datetime) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self._create_task(
                self._async_refresh_device_list(), "device list refresh"
            )

    async def _async_refresh_device_list(self) -> None:
        """Refresh metadata and discover newly paired devices."""
        if not self.available:
            return
        requested_at = monotonic()
        try:
            document = await self.client.async_get("/devices")
        except OpusApiError as err:
            _LOGGER.debug("Could not refresh the OPUS device list: %s", err)
            return
        self._apply_device_list(
            document, authoritative=False, requested_at=requested_at
        )

    # ──────────────────────────────────────────────────────────────────────
    # Devices
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _apply_device_list(
        self,
        document: WireMapping,
        *,
        authoritative: bool,
        requested_at: float | None = None,
    ) -> None:
        """Apply a complete device list.

        ``authoritative`` lists (the start of every stream) carry the current
        state. A periodic refresh only updates metadata, because the stream
        may already have delivered newer state; ``requested_at`` is when that
        refresh was requested (finding B-5).
        """
        devices = object_items(document.get("devices"))
        for entry in devices:
            data = as_object(entry.get("device", entry))
            if data is not None:
                self._apply_device_object(
                    data, authoritative=authoritative, requested_at=requested_at
                )
        _LOGGER.debug("Applied OPUS device list with %d devices", len(devices))

    @callback
    def _apply_device_object(
        self,
        data: JsonObject,
        *,
        authoritative: bool,
        requested_at: float | None = None,
        position_origin: str = "device list",
    ) -> None:
        """Create or refresh one device from its gateway object.

        A device list requested at ``requested_at`` keeps the signal strength
        and last-seen time of a telegram received after that moment: the
        telegram is newer than the list (finding B-5).
        """
        device_id = data.get("deviceId")
        if not isinstance(device_id, str) or not device_id:
            return
        self._unknown_devices.pop(device_id, None)
        existing = self.devices.get(device_id)
        listed = EnOceanDevice(
            device_id=device_id,
            friendly_id=protocol.metadata_text(data.get("friendlyId")) or device_id,
            eeps=object_items(data.get("eeps")),
            manufacturer=protocol.metadata_text(data.get("manufacturer")),
            physical_device=protocol.metadata_text(data.get("physicalDevice")),
            first_seen=protocol.metadata_text(data.get("firstSeen")),
            last_seen=protocol.metadata_text(data.get("lastSeen")),
            dbm=EnOceanDevice._parse_integer(data.get("dbm")),
        )
        was_known = existing is not None and bool(existing.eeps)
        if existing is None:
            device = listed
        else:
            # Update in place (REST phase 02, E6/B-4): channels, the last
            # command error and update timestamps stay on the same object that
            # entities and running commands hold.
            device = existing
            telegram_at = self._telegram_metadata_at.get(device_id)
            newer_telegram = (
                requested_at is not None
                and telegram_at is not None
                and telegram_at >= requested_at
            )
            device.apply_listed_metadata(listed, keep_signal=newer_telegram)
            # A device list refreshes metadata and state, never a previous
            # rocker occurrence.
            device.clear_button_occurrence()
        self._record_last_movement_times(device_id, data)
        functions = (
            protocol.device_state_functions(data)
            if authoritative or existing is None
            else protocol.configuration_functions(data)
        )
        if functions:
            device.update_from_telegram({"functions": functions})
            if authoritative or existing is None:
                device.last_update_source = "device list"
                self._note_position_origin(device, functions, position_origin)
        self.devices[device_id] = device
        if not was_known:
            _LOGGER.info(
                "Device discovered: %s (%s) - EEPs: %s - Type: %s",
                device.friendly_id,
                device_id,
                [eep.get("eep") for eep in device.eeps],
                device.entity_type,
            )
            async_dispatcher_send(
                self.hass, f"{SIGNAL_DEVICE_DISCOVERED}_{self.eag_id}", device
            )
        else:
            self._dispatch_state(device)

    @callback
    def _note_position_origin(
        self, device: EnOceanDevice, functions: Sequence[WireFunction], origin: str
    ) -> None:
        """Record the origin of every valid position in ``functions``.

        Only numeric positions count: ``unknown`` leaves the position and its
        recorded origin unchanged. Diagnostics only (REST phase 02).
        """
        default = protocol.channel_from_functions(functions)
        for function in functions:
            if function.get("key") != KEY_POSITION:
                continue
            if (
                EnOceanDevice._parse_number(
                    function.get("value"), minimum=0, maximum=100
                )
                is None
            ):
                continue
            channel_id = protocol.function_channel(function, default)
            channel = (
                device.channels.get(channel_id) if channel_id is not None else None
            )
            if channel_id is None or channel is None or channel.position is None:
                continue
            self._position_origins[(device.device_id, channel_id)] = (
                origin,
                channel.position,
            )

    def position_origin(self, device_id: str, channel_id: int) -> str | None:
        """Return where a channel's current position came from (diagnostics).

        ``device list`` (a snapshot), ``reported`` (device report),
        ``assumed`` (end of a travel estimate) or ``estimated``
        (acknowledged command or a foreign command telegram). None without a
        position. Never used for state, timers or queries.
        """
        device = self.devices.get(device_id)
        channel = device.channels.get(channel_id) if device is not None else None
        if channel is None or channel.position is None:
            return None
        recorded = self._position_origins.get((device_id, channel_id))
        if recorded is None or recorded[1] != channel.position:
            return "estimated"
        return recorded[0]

    @callback
    def _record_last_movement_times(self, device_id: str, data: JsonObject) -> None:
        """Keep the last run durations a device list reports (diagnostics)."""
        if durations := protocol.last_vertical_movement_times(data):
            self.last_vertical_movement_times.setdefault(device_id, {}).update(
                durations
            )

    @callback
    def async_forget_device(self, device_id: str) -> None:
        """Discard only this child's state and queued work, without gateway writes."""
        for did, channel_id in list(self._cover_movements):
            if did == device_id:
                self._clear_cover_movement(did, channel_id, notify=False)
        self.devices.pop(device_id, None)
        self._telegram_metadata_at.pop(device_id, None)
        self.last_vertical_movement_times.pop(device_id, None)
        for origin_key in [k for k in self._position_origins if k[0] == device_id]:
            del self._position_origins[origin_key]
        self._cancel_reconciliation_queries(device_id)
        for key in list(self._feedback_revisions):
            if key[0] == device_id:
                self._feedback_revisions.pop(key)
        # Requests already queued for this device are dropped before sending.
        self._device_generations[device_id] = (
            self._device_generations.get(device_id, 0) + 1
        )
        async_dispatcher_send(
            self.hass, f"{SIGNAL_DEVICE_REMOVED}_{self.eag_id}", device_id
        )

    @callback
    def _lookup_unknown_device(self, device_id: str) -> None:
        """Fetch a device that reported before it appeared in a device list."""
        now = monotonic()
        last = self._unknown_devices.get(device_id)
        if last is not None and now - last < UNKNOWN_DEVICE_RETRY_SECONDS:
            return
        self._unknown_devices[device_id] = now
        self._create_task(self._async_lookup_device(device_id), "device lookup")

    async def _async_lookup_device(self, device_id: str) -> None:
        try:
            document = await self.client.async_get(
                OpusGatewayClient.device_path(device_id)
            )
        except OpusApiError as err:
            _LOGGER.debug(
                "OPUS device %s is not known to the gateway: %s", device_id, err
            )
            return
        if (data := as_object(document.get("device"))) is not None:
            self._apply_device_object(data, authoritative=True)

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

    @callback
    def _dispatch_state(self, device: EnOceanDevice) -> None:
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_DEVICE_STATE_UPDATE}_{self.eag_id}_{device.device_id}",
            device,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Telegrams
    # ──────────────────────────────────────────────────────────────────────

    @callback
    def _handle_telegram(self, telegram: JsonObject) -> None:
        """Apply one radio telegram.

        ``from`` telegrams are device reports. ``to`` telegrams are commands
        the gateway sent: from HomeKit, the gateway's own logic or this
        integration. Commands from elsewhere update state optimistically and
        are verified by delayed status queries; this integration's own
        commands are left to the entity, which applies its estimate after
        the acknowledgement.
        """
        received_at = monotonic()
        device_id = telegram.get("deviceId")
        direction = telegram.get("direction")
        if not isinstance(device_id, str) or direction not in ("from", "to"):
            return
        is_outbound = direction == "to"
        if is_outbound and self._command_waiters.get(device_id):
            return

        raw_functions = telegram.get("functions")
        single = as_object(raw_functions)
        functions: list[WireFunction] = [
            function
            for function in ([single] if single else object_items(raw_functions))
            if isinstance(function.get("key"), str)
            and function.get("value") is not None
        ]
        if is_outbound:
            functions = protocol.outbound_state_functions(functions)
            if not protocol.has_target_state(functions):
                return
            if self._is_own_echo(device_id, functions, received_at):
                return
        if not functions:
            return

        device = self.devices.get(device_id)
        if device is None:
            self._lookup_unknown_device(device_id)
            return

        if not is_outbound:
            self._record_confirmed_feedback(device_id, functions)
        applied = functions
        if is_outbound:
            estimated = self._start_cover_commands(device, functions)
            default = protocol.channel_from_functions(functions)
            applied = [
                function
                for function in functions
                if not (
                    function.get("key") == KEY_POSITION
                    and protocol.function_channel(function, default) in estimated
                )
            ]
        update: dict[str, object] = {"functions": applied}
        if not is_outbound:
            # Only a received telegram tells when the device was last heard
            # and how strong it was; sent telegrams report dbm 0.
            update["timestamp"] = telegram.get("timestamp")
            update["telegramInfo"] = telegram.get("telegramInfo")
            self._telegram_metadata_at[device_id] = received_at
        device.update_from_telegram(update)
        # A foreign command is applied optimistically; a device report is
        # feedback (diagnostics only).
        self._note_position_origin(
            device, applied, "estimated" if is_outbound else "reported"
        )
        if not is_outbound and protocol.has_operational_state(functions):
            device.last_command_error = None

        source = "telegram/to" if is_outbound else "telegram/from"
        self._mark_and_log_dispatch(device, source, received_at, telegram)
        self._dispatch_state(device)
        # Event entities consume button fields synchronously during the
        # dispatch above. Clear them so later dispatches cannot replay them.
        device.clear_button_occurrence()

        if is_outbound:
            for channel_id, fields in protocol.command_fields_by_channel(
                functions, device
            ).items():
                self._schedule_reconciliation_queries(
                    device_id, channel_id, fields=fields
                )

    def _is_own_echo(
        self, device_id: str, functions: Sequence[WireFunction], now: float
    ) -> bool:
        """Consume a recent own command whose echo these functions are."""
        sent = [
            entry for entry in self._sent_commands.get(device_id, []) if entry[0] > now
        ]
        signature = protocol.command_signature(functions)
        match = next((entry for entry in sent if entry[1] == signature), None)
        if match is not None:
            sent.remove(match)
        if sent:
            self._sent_commands[device_id] = sent
        else:
            self._sent_commands.pop(device_id, None)
        return match is not None

    def _mark_and_log_dispatch(
        self,
        device: EnOceanDevice,
        source: str,
        received_at: float,
        telegram: WireMapping,
    ) -> None:
        """Stamp a device update and log the gateway-to-HA latency."""
        device.last_update_source = source
        device.last_update_received_monotonic = received_at
        device.last_update_dispatched_monotonic = monotonic()
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        sent_at = protocol.telegram_timestamp(telegram.get("timestamp"))
        _LOGGER.debug(
            "OPUS %s %s (%s): %s gateway_to_ha_ms=%s",
            source,
            device.friendly_id,
            device.device_id,
            telegram.get("functions"),
            "unknown" if sent_at is None else f"{(time() - sent_at) * 1000:.0f}",
        )

    # ──────────────────────────────────────────────────────────────────────
    # Cover travel estimates
    # ──────────────────────────────────────────────────────────────────────

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
                self._dispatch_state(device)

    def _start_cover_movement(
        self, device: EnOceanDevice, channel_id: int, target: int
    ) -> CoverMovement | None:
        channel = device.get_or_create_channel(channel_id)
        key = (device.device_id, channel_id)
        previous = self._cover_movements.get(key)
        if previous is not None and previous.target == target:
            # An outbound echo must not extend the same movement's deadline.
            # A repeated local request must also suppress the entity's target
            # assignment after its acknowledgement, like the first request.
            channel.state_revision += 1
            return previous
        current: float | None = channel.position
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
            self._dispatch_state(device)
            return None
        # A replacement must stop old status queries before waiting for its
        # acknowledgement. Keep other unconfirmed fields (e.g. tilt).
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
        # Prevent an entity awaiting its acknowledgement from replacing the
        # last reported position with the target while the cover travels.
        channel.state_revision += 1
        movement = CoverMovement(current, target, now, travel)
        self._cover_movements[key] = movement

        @callback
        def finished(_now: datetime) -> None:
            self._finish_cover_movement(device.device_id, channel_id, movement)

        movement.cancel = async_call_later(
            self.hass, travel + COVER_MOVEMENT_GRACE_SECONDS, finished
        )
        self._dispatch_state(device)
        return movement

    def _finish_cover_movement(
        self, device_id: str, channel_id: int, movement: CoverMovement
    ) -> None:
        if self._cover_movements.get((device_id, channel_id)) is not movement:
            return
        movement.elapsed = True
        # Very short travel can end before the acknowledgement arrives. Keep
        # the estimate cancellable and never assume an unaccepted target.
        if not movement.accepted:
            return
        device = self.get_device(device_id)
        self._clear_cover_movement(device_id, channel_id, notify=False)
        if device is not None:
            channel = device.get_or_create_channel(channel_id)
            channel.position = movement.target  # Assumed, not physical feedback.
            channel.state_revision += 1
            self._position_origins[(device_id, channel_id)] = (
                "assumed",
                movement.target,
            )
            self._dispatch_state(device)

    def _start_cover_commands(
        self, device: EnOceanDevice | None, functions: Sequence[WireFunction]
    ) -> dict[int, CoverMovement]:
        started: dict[int, CoverMovement] = {}
        if device is None or device.entity_type != "cover":
            return started
        default = protocol.channel_from_functions(functions)
        for function in functions:
            channel_id = protocol.function_channel(function, default)
            if channel_id is None:
                continue
            if function.get("key") == KEY_STOP and function.get("value") == "true":
                self._clear_cover_movement(device.device_id, channel_id)
                self._cancel_reconciliation_queries(device.device_id, channel_id)
            elif function.get("key") == KEY_POSITION:
                target = EnOceanDevice._parse_integer(
                    function.get("value"), minimum=0, maximum=100
                )
                if target is not None:
                    movement = self._start_cover_movement(device, channel_id, target)
                    if movement is not None:
                        started[channel_id] = movement
        return started

    # ──────────────────────────────────────────────────────────────────────
    # Feedback and delayed status checks
    # ──────────────────────────────────────────────────────────────────────

    def _record_confirmed_feedback(
        self, device_id: str, functions: Sequence[WireFunction]
    ) -> bool:
        """Confirm only reported fields on their own channel."""
        confirmed = protocol.confirmed_fields_by_channel(functions)
        for channel_id, fields in confirmed.items():
            if KEY_POSITION in fields:
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
        # A check already waiting in the queue is superseded as well; it is
        # dropped before sending unless a later timer wants it again.
        self._reconcile_needed.difference_update(
            {
                key
                for key in self._reconcile_needed
                if key[0] == device_id and (channel_id is None or key[1] == channel_id)
            }
        )

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

        delays: tuple[float, ...] = OUTBOUND_STATE_RECONCILIATION_DELAYS
        if KEY_POSITION in expected and (movement := self._cover_movements.get(key)):
            remaining = max(0.0, movement.ends_at - monotonic())
            delays = tuple(remaining + delay for delay in COVER_RECONCILIATION_DELAYS)
        for delay in delays:

            @callback
            def query_callback(
                _now: datetime,
                did: str = device_id,
                channel: int = channel_id,
                seconds: float = delay,
            ) -> None:
                _LOGGER.debug(
                    "Querying OPUS status for %s channel %s %.0fs after command",
                    did,
                    channel,
                    seconds,
                )
                if not self.available:
                    return
                self._reconcile_needed.add((did, channel))
                if (did, channel) in self._reconciling:
                    # Coalesce: one queued background query per channel.
                    return
                self._create_task(
                    self._async_reconcile_status(did, channel), "status check"
                )

            self._pending_reconciliation_queries[key].append(
                async_call_later(self.hass, delay, query_callback)
            )

    async def _async_reconcile_status(self, device_id: str, channel_id: int) -> None:
        if self.get_device(device_id) is None:
            return
        key = (device_id, channel_id)
        self._reconciling.add(key)

        def still_needed() -> bool:
            # Evaluated once, immediately before sending. Consuming the flag
            # lets a timer firing during the in-flight query start a new one.
            if key not in self._reconcile_needed:
                return False
            self._reconcile_needed.discard(key)
            return True

        try:
            await self.async_query_device_status(
                device_id, channel_id, background=True, still_needed=still_needed
            )
        except HomeAssistantError as err:
            if (err.translation_placeholders or {}).get("reason") == SUPERSEDED:
                _LOGGER.debug(
                    "Dropped superseded OPUS status check for %s channel %s",
                    device_id,
                    channel_id,
                )
            else:
                _LOGGER.debug("Could not reconcile OPUS state for %s", device_id)
        finally:
            self._reconciling.discard(key)

    # ──────────────────────────────────────────────────────────────────────
    # Requests
    # ──────────────────────────────────────────────────────────────────────

    def _request_error(self, err: OpusApiError, device_id: str) -> HomeAssistantError:
        """Translate a transport failure for one request."""
        if isinstance(err, OpusAuthError):
            self._handle_auth_failure()
            return request_error("request_rejected", device_id, str(err))
        if isinstance(err, OpusTimeoutError):
            return request_error("request_timeout", device_id)
        if isinstance(err, OpusConnectionError):
            return request_error("gateway_unavailable", device_id)
        reason = err.reason if isinstance(err, OpusResponseError) else str(err)
        return request_error("request_rejected", device_id, reason)

    def _check_request_valid(self, device_id: str, generation: tuple[int, int]) -> None:
        outage, device_generation = generation
        if self._unloaded or device_generation != self._device_generations.get(
            device_id, 0
        ):
            raise request_error("request_cancelled", device_id)
        if not self.available or outage != self._outage_generation:
            raise request_error("gateway_unavailable", device_id)

    def _request_generation(self, device_id: str) -> tuple[int, int]:
        """Identify the device and connection state a request was queued for."""
        return (self._outage_generation, self._device_generations.get(device_id, 0))

    async def _async_radio_request(
        self,
        device_id: str,
        request: Callable[[], Coroutine[object, object, JsonObject]],
        *,
        priority: int,
        before_send: Callable[[], None] | None = None,
    ) -> JsonObject:
        """Run one radio request while owning the single in-flight slot."""
        generation = self._request_generation(device_id)
        self._check_request_valid(device_id, generation)
        queued_at = monotonic()
        await self._radio_queue.acquire(priority)
        try:
            waited = monotonic() - queued_at
            if waited > 1.0:
                _LOGGER.debug(
                    "OPUS request for %s waited %.2fs in the queue "
                    "(priority %d, %d still queued)",
                    device_id,
                    waited,
                    priority,
                    self._radio_queue.queue_depth,
                )
            self._check_request_valid(device_id, generation)
            if before_send is not None:
                before_send()
            try:
                result = await request()
            except OpusApiError as err:
                raise self._request_error(err, device_id) from err
            # The gateway answered: only unload or a removed device make the
            # result meaningless. An outage declared meanwhile does not undo
            # an accepted request.
            if self._unloaded or generation[1] != self._device_generations.get(
                device_id, 0
            ):
                raise request_error("request_cancelled", device_id)
            return result
        finally:
            self._radio_queue.release()

    async def async_send_command(
        self,
        device_id: str,
        functions: Sequence[WireFunction],
        *,
        background: bool = False,
        still_needed: Callable[[], bool] | None = None,
    ) -> CommandDelivery:
        """Send state functions to a device and wait for the acknowledgement.

        ``background`` marks delayed reconciliation queries, which are only
        sent when no user command or interactive query is waiting.
        ``still_needed`` (background only) is checked right before sending; a
        superseded check is dropped without being sent.

        A deferred command (status 201) gets no estimate and no status
        queries: until the device fetches the telegram, a query would be
        deferred as well, and the stream delivers the device's report later.
        """
        device = self.get_device(device_id)
        fields_by_channel = protocol.command_fields_by_channel(functions, device)
        revisions: dict[tuple[int, str], int] = {}
        started_movements: dict[int, CoverMovement] = {}
        body = {"state": {"functions": list(functions)}}
        _LOGGER.debug("Sending command to OPUS device %s: %s", device_id, body)

        @callback
        def snapshot_feedback() -> None:
            # Runs while owning the request slot, immediately before sending.
            # Feedback for an earlier queued command cannot confirm this one.
            if background and still_needed is not None and not still_needed():
                raise request_error("request_cancelled", device_id, SUPERSEDED)
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
                response = await self._async_radio_request(
                    device_id,
                    lambda: self.client.async_put(
                        OpusGatewayClient.device_path(device_id, "state"), body
                    ),
                    priority=priority,
                    before_send=snapshot_feedback,
                )
            except HomeAssistantError as err:
                if err.translation_key == "request_rejected" and device is not None:
                    device.last_command_error = (
                        err.translation_placeholders or {}
                    ).get("reason") or "rejected"
                    self._cancel_reconciliation_queries(device_id)
                    self._dispatch_state(device)
                elif err.translation_key == "request_timeout":
                    # A missing acknowledgement only fails this entity action.
                    _LOGGER.warning(
                        "OPUS device %s did not acknowledge a %s within the timeout",
                        device_id,
                        "status query" if is_query else "command",
                    )
                raise
            delivery = (
                CommandDelivery.DEFERRED
                if response_status(response) == SOFT_SMART_ACK_STATUS
                else CommandDelivery.SENT
            )
            if delivery is CommandDelivery.DEFERRED:
                _LOGGER.debug(
                    "OPUS gateway queued the command for %s until the device "
                    "wakes up (SoftSmartAck)",
                    device_id,
                )
                for channel_id, movement in started_movements.items():
                    if self._cover_movements.get((device_id, channel_id)) is movement:
                        self._clear_cover_movement(device_id, channel_id)
                started_movements.clear()
                fields_by_channel = {}
            if device is not None:
                if device.last_command_error is not None:
                    device.last_command_error = None
                    self._dispatch_state(device)
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
            return delivery
        except BaseException:
            for channel_id, movement in started_movements.items():
                if self._cover_movements.get((device_id, channel_id)) is movement:
                    self._clear_cover_movement(device_id, channel_id)
                    self._cancel_reconciliation_queries(device_id, channel_id)
            raise
        finally:
            self._sent_commands.setdefault(device_id, []).append(
                (
                    monotonic() + OWN_COMMAND_ECHO_SECONDS,
                    protocol.command_signature(functions),
                )
            )
            remaining = self._command_waiters[device_id] - 1
            if remaining:
                self._command_waiters[device_id] = remaining
            else:
                self._command_waiters.pop(device_id, None)

    def _with_channel_if_needed(
        self,
        device_id: str,
        functions: Sequence[WireFunction],
        channel: int,
    ) -> list[WireFunction]:
        """Prepend the mandatory selector for multi-channel actuator commands."""
        device = self.get_device(device_id)
        if channel > 0 or (device is not None and device.channel_count > 1):
            return [{"key": KEY_CHANNEL, "value": str(channel)}, *functions]
        return list(functions)

    def _dim_functions(
        self, device_id: str, dim_value: int, *, transition: bool
    ) -> list[WireFunction]:
        """Return a dim command, with the ramping mode where the device has one."""
        functions: list[WireFunction] = [{"key": KEY_DIMMER, "value": str(dim_value)}]
        device = self.get_device(device_id)
        if device is not None and device.supports_ramping_mode:
            functions.append(
                {
                    "key": KEY_RAMPING_MODE,
                    "value": RAMPING_MODE_FADE if transition else RAMPING_MODE_SWITCH,
                }
            )
        return functions

    async def async_turn_on(
        self,
        device_id: str,
        channel: int = 0,
        brightness: int | None = None,
        is_dimmable: bool = False,
        *,
        transition: bool = False,
    ) -> CommandDelivery:
        """Turn on a switch or light.

        Dimmers use dimValue, not switch (OPUS reference section 5.3).
        ``transition`` keeps the dimmer's own fade instead of switching at once.
        """
        functions: list[WireFunction]
        if brightness is not None or is_dimmable:
            functions = self._dim_functions(
                device_id,
                100 if brightness is None else brightness,
                transition=transition,
            )
        else:
            functions = [{"key": "switch", "value": "on"}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_turn_off(
        self,
        device_id: str,
        channel: int = 0,
        is_dimmable: bool = False,
        *,
        transition: bool = False,
    ) -> CommandDelivery:
        """Turn off a switch or light (dimmers: dimValue 0)."""
        functions: list[WireFunction]
        if is_dimmable:
            functions = self._dim_functions(device_id, 0, transition=transition)
        else:
            functions = [{"key": "switch", "value": "off"}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_set_cover_position(
        self,
        device_id: str,
        position: int,
        channel: int = 0,
    ) -> CommandDelivery:
        """Set OPUS position (0 = open, 100 = closed)."""
        functions: list[WireFunction] = [{"key": "position", "value": str(position)}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_set_cover_tilt(
        self,
        device_id: str,
        tilt: int,
        channel: int = 0,
    ) -> CommandDelivery:
        """Set cover tilt angle."""
        functions: list[WireFunction] = [{"key": "angle", "value": str(tilt)}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_stop_cover(
        self, device_id: str, channel: int = 0
    ) -> CommandDelivery:
        """Stop cover movement."""
        self._clear_cover_movement(device_id, channel)
        self._cancel_reconciliation_queries(device_id, channel)
        functions: list[WireFunction] = [{"key": KEY_STOP, "value": "true"}]
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_query_device_status(
        self,
        device_id: str,
        channel: int = 0,
        *,
        background: bool = False,
        still_needed: Callable[[], bool] | None = None,
    ) -> CommandDelivery:
        """Query the current device status."""
        functions: list[WireFunction] = [{"key": KEY_QUERY, "value": "status"}]
        return await self.async_send_command(
            device_id,
            self._with_channel_if_needed(device_id, functions, channel),
            background=background,
            still_needed=still_needed,
        )

    async def async_set_climate_setpoint(
        self,
        device_id: str,
        temperature: float,
    ) -> CommandDelivery:
        """Set the temperature setpoint for a climate device."""
        return await self.async_send_command(
            device_id, [{"key": "temperatureSetpoint", "value": str(temperature)}]
        )

    async def async_set_climate_mode(
        self,
        device_id: str,
        mode: str,
    ) -> CommandDelivery:
        """Set the heater mode for a climate device."""
        return await self.async_send_command(
            device_id, [{"key": "heaterMode", "value": mode}]
        )

    # ──────────────────────────────────────────────────────────────────────
    # ReCom service requests
    # ──────────────────────────────────────────────────────────────────────

    async def async_get_device_configuration(self, device_id: str) -> JsonObject:
        """Read the configuration stored on a device."""
        path = OpusGatewayClient.device_path(device_id, "configuration")
        return await self._async_radio_request(
            device_id,
            lambda: self.client.async_get(path, timeout=RECOM_TIMEOUT),
            priority=PRIORITY_QUERY,
        )

    async def async_set_device_configuration(
        self, device_id: str, config: WireMapping
    ) -> None:
        """Write configuration to a device and wait for the acknowledgement."""
        path = OpusGatewayClient.device_path(device_id, "configuration")
        await self._async_radio_request(
            device_id,
            lambda: self.client.async_put(path, config, timeout=RECOM_TIMEOUT),
            priority=PRIORITY_QUERY,
        )

    async def async_get_device_parameters(self, device_id: str) -> JsonObject:
        """Read the configurable parameters a device supports."""
        path = OpusGatewayClient.device_path(device_id, "parameters")
        return await self._async_radio_request(
            device_id,
            lambda: self.client.async_get(path, timeout=RECOM_TIMEOUT),
            priority=PRIORITY_QUERY,
        )


def _is_device_list(document: JsonObject) -> bool:
    """Return whether a stream document is the complete device list."""
    header = as_object(document.get("header")) or {}
    return header.get("content") == "devices"
