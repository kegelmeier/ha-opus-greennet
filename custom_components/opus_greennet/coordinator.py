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
  uptime) whose old stream connection could otherwise stay half open. A
  gateway of the earlier generation that fails several probes in a row is
  also asked, at most every ten minutes and without credentials, whether a
  WebUI 2026 gateway answers at its address now (it was updated); the user is
  then asked for a login.
* Commands are HTTP PUTs. The gateway acknowledges them synchronously; one
  radio request is in flight at a time, user commands first.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from time import monotonic, time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

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
    COVER_END_STOP_GRACE_SECONDS,
    COVER_MOVEMENT_GRACE_SECONDS,
    COVER_RECONCILIATION_DELAYS,
    DOMAIN,
    KEY_ANGLE,
    KEY_CHANNEL,
    KEY_DIMMER,
    KEY_LOCKING_MODE,
    KEY_POSITION,
    KEY_QUERY,
    KEY_RAMPING_MODE,
    KEY_REPEATER_STATE,
    KEY_REPOSITIONING,
    KEY_ROTATION_TIME,
    KEY_ROUTING_TIME,
    KEY_STOP,
    KEY_UNLOCK,
    KEY_VERTICAL_MOVEMENT_TIME,
    RAMPING_MODE_FADE,
    RAMPING_MODE_SWITCH,
    REPOSITIONING_FIRST_DOWN,
    REPOSITIONING_FIRST_UP,
    ROUTING_OFF,
    ROUTING_ON,
    ROUTING_ON_SECONDS,
    SETTING_CACHE_GUARD_SECONDS,
    UNLOCK_ALLOWED,
    UNLOCK_NOT_ALLOWED,
    VALUE_UNKNOWN,
)
from .discovery import async_gateway_address
from .enocean_device import DeviceProfile, EnOceanDevice
from .wire import (
    JsonObject,
    WireFunction,
    WireMapping,
    as_object,
    json_body_problem,
    object_items,
)

_LOGGER = logging.getLogger(__name__)

# Dispatcher signals
SIGNAL_DEVICE_REMOVED = f"{DOMAIN}_device_removed"
SIGNAL_DEVICE_DISCOVERED = f"{DOMAIN}_device_discovered"

# Gateway answer to a state command.
SOFT_SMART_ACK_STATUS = 201
# The gateway stored the command as the device's target state; it answers the
# device's next request with it (HOPPE SecureConnect unlock permission).
TARGET_STATE_STATUS = 202


class CommandDelivery(StrEnum):
    """How the gateway handled an accepted state command."""

    SENT = "sent"  # 200: the telegram went out over the radio.
    # 201 (SoftSmartAck): queued until the sleeping device wakes up and asks
    # for it; 202: stored as the device's target state for its next request.
    # The device has not received anything yet.
    DEFERRED = "deferred"


SIGNAL_DEVICE_STATE_UPDATE = f"{DOMAIN}_device_state_update"
SIGNAL_AVAILABILITY_UPDATE = f"{DOMAIN}_availability_update"
SIGNAL_GATEWAY_UPTIME_UPDATE = f"{DOMAIN}_gateway_uptime_update"
SIGNAL_GATEWAY_INFO_UPDATE = f"{DOMAIN}_gateway_info_update"

GATEWAY_HEALTH_INTERVAL = timedelta(seconds=60)
# A gateway of the earlier generation that fails this many health probes in a
# row is asked whether a WebUI 2026 gateway answers at its address now (it was
# updated). The question is repeated at most this often; it sends no
# credentials and no radio request.
GENERATION_CHECK_AFTER_FAILURES = 3
GENERATION_CHECK_INTERVAL = 600.0
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
# Radio requests (and the feedback that confirmed a command) kept for the
# diagnostics download, oldest dropped first. Memory only, never persisted.
RADIO_JOURNAL_SIZE = 300
# Longest gateway text and string value kept in one journal entry.
JOURNAL_TEXT_LENGTH = 200
JOURNAL_VALUE_LENGTH = 64
JOURNAL_VALUE_COUNT = 20
_PRIORITY_NAMES = {
    PRIORITY_COMMAND: "command",
    PRIORITY_QUERY: "query",
    PRIORITY_BACKGROUND: "background",
}


def _journal_values(items: object) -> dict[str, object]:
    """Return ``{key: value}`` of ``[{"key": ..., "value": ...}]`` for the journal.

    Only what the integration sends itself is of interest, so scalars are kept
    (strings shortened) and any other value is replaced by its type name. A
    value of a malformed list is skipped.
    """
    values: dict[str, object] = {}
    for item in object_items(items)[:JOURNAL_VALUE_COUNT]:
        key = item.get("key")
        if not isinstance(key, str):
            continue
        value = item.get("value")
        if isinstance(value, str):
            values[key] = value[:JOURNAL_VALUE_LENGTH]
        elif isinstance(value, bool | int | float) or value is None:
            values[key] = value
        else:
            values[key] = f"<{type(value).__name__}>"
    return values


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


def _is_timed_parameter(function: WireFunction) -> bool:
    """Return whether a command sets a cover movement or rotation duration.

    The device list reports these times in seconds; the D2-05-00 "Set
    parameters" command of another controller uses milliseconds. Only a
    positive number has a unit; zero and ``noRotation`` mean no rotation in
    both and stay usable.
    """
    if function.get("key") not in (KEY_ROTATION_TIME, KEY_VERTICAL_MOVEMENT_TIME):
        return False
    number = EnOceanDevice._parse_number(function.get("value"))
    return number is not None and number > 0


def request_error(key: str, device_id: str, reason: str = "") -> HomeAssistantError:
    """Create a translated request error without logging sensitive payloads."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"device_id": device_id, "reason": reason},
    )


def snap_to_step(value: float, step: float, minimum: float = 0) -> float | int:
    """Return ``value`` on the parameter's grid, as the device stores it.

    Raises ``ServiceValidationError`` for NaN or infinity, which would not be
    valid JSON. Whole values of a step of 1 or more are sent as integers.
    """
    if not math.isfinite(value):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_parameter_value",
        )
    exponent = Decimal(str(step)).as_tuple().exponent
    decimals = max(0, -exponent) if isinstance(exponent, int) else 0
    snapped = round(minimum + round((value - minimum) / step) * step, decimals)
    if step >= 1 and float(snapped).is_integer():
        return int(snapped)
    return snapped


def _step_reason(key: str, err: HomeAssistantError) -> str:
    """Return the setting and the gateway's reason of a rejected request."""
    reason = (err.translation_placeholders or {}).get("reason")
    return f"{key}: {reason}" if reason else key


# Older values a confirmed setting keeps for the protection, at most.
_SETTING_GUARD_STALE_VALUES = 4


@dataclass(slots=True)
class SettingGuard:
    """A confirmed setting and the older values the gateway's cache may repeat.

    The device list carries the gateway's cached copy of the configuration. It
    is not necessarily refreshed by a write, so a list that arrives after a
    confirmed write can show the value from before it.
    """

    stale: tuple[float | str, ...]
    written: float | str
    at: float


def _setting_number(value: object) -> float | None:
    """Return a reported setting as a number; ``noRotation`` is zero."""
    if isinstance(value, str) and value.strip() == "noRotation":
        return 0.0
    number = EnOceanDevice._parse_number(value)
    return None if number is None else float(number)


def _same_setting(left: object, right: object) -> bool:
    """Return whether two reports of a setting mean the same value."""
    left_number, right_number = _setting_number(left), _setting_number(right)
    if left_number is not None and right_number is not None:
        return abs(left_number - right_number) <= 1e-6
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and left.strip() == right.strip()
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
    def grace(self) -> float:
        if self.target in (0, 100):
            return COVER_END_STOP_GRACE_SECONDS
        return COVER_MOVEMENT_GRACE_SECONDS

    @property
    def ends_at(self) -> float:
        return self.started_at + self.travel + self.grace


# A run without a matching end report is dropped after this many seconds.
COVER_RUN_TIMEOUT_SECONDS = 300.0
# Reported end positions within this distance of the target finish a run.
COVER_RUN_TARGET_TOLERANCE = 2


@dataclass
class CoverRun:
    """Measured travel of one cover command, from sending to the end report.

    ``seconds`` stays None until the device reports a position at the target.
    A status query sent meanwhile makes the measurement unusable: its answer
    tells the position, not when the blind arrived.
    """

    start_position: int | None
    target: int
    repositioning: str | None
    started_at: float
    seconds: float | None = None
    finished_at: str | None = None
    queried: bool = False

    @property
    def direction(self) -> str:
        """Return up/down, or the reference run direction (OPUS 0 = top)."""
        if self.repositioning == REPOSITIONING_FIRST_UP:
            return "reference_up"
        if self.repositioning == REPOSITIONING_FIRST_DOWN:
            return "reference_down"
        if self.start_position is not None and self.target < self.start_position:
            return "up"
        return "down" if self.start_position is not None else "unknown"

    @property
    def full_travel(self) -> bool:
        """Return whether the run covered the whole height without detour."""
        return self.repositioning in (None, "direct") and {
            self.start_position,
            self.target,
        } == {0, 100}


class OpusGreenNetCoordinator:
    """Own the connection to one gateway and the state of its devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: OpusGatewayClient,
        eag_id: str,
        *,
        on_auth_failed: Callable[[], None] | None = None,
        generation_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        """Initialize the coordinator.

        ``generation_check`` is given for a gateway of the earlier generation
        only: it tells whether a gateway of the newer generation answers at
        the address now.
        """
        self.hass = hass
        self.client = client
        self.eag_id = eag_id
        self.devices: dict[str, EnOceanDevice] = {}
        self.gateway_info: JsonObject = {}
        self.gateway_uptime: str | None = None
        # ``(hostname, ipv4, ipv6)`` of the configured gateway host, as far as
        # it resolves (shown by the gateway's diagnostic sensors).
        self.gateway_address: tuple[str | None, str | None, str | None] = (
            None,
            None,
            None,
        )
        self._on_auth_failed = on_auth_failed
        self._generation_check = generation_check
        self._probe_failures = 0
        self._generation_checked_at: float | None = None
        self._auth_failed = False
        self._unloaded = False
        self._gateway_available = False
        self._stream_connected = False
        self._stream_task: asyncio.Task[None] | None = None
        # Incremented for every new stream reader. A replaced reader may still
        # hold documents it parsed before its cancellation takes effect; it
        # drops them instead of applying stale state (phase R4).
        self._stream_generation = 0
        # Opened stream requests and streams that delivered a document since
        # this coordinator was created (diagnostics only; a reload resets them).
        self.stream_attempts = 0
        self.stream_connects = 0
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
        # One settings write (with its read-back) per device at a time.
        self._setting_locks: dict[str, asyncio.Lock] = {}
        self._probe_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        # When ``gateway_info`` was last read from the gateway (ISO, UTC).
        self.gateway_info_read_at: str | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._timers: list[Callable[[], None]] = []
        # One radio request in flight at a time, dispatched by priority.
        self._radio_queue = PriorityLock()
        # What was sent over the radio and how the gateway answered, for the
        # diagnostics download only. Never read to decide anything.
        self._radio_journal: deque[dict[str, object]] = deque(maxlen=RADIO_JOURNAL_SIZE)
        self._radio_journal_seq = 0
        self._device_generations: dict[str, int] = {}
        # Incremented whenever the gateway becomes unavailable: requests queued
        # before an outage are never sent after the gateway returns.
        self._outage_generation = 0
        self._unknown_devices: dict[str, float] = {}
        # Confirmed settings per device, protected against the gateway's cached
        # copy of the configuration in a device list (see ``SettingGuard``).
        self._setting_guards: dict[str, dict[str, SettingGuard]] = {}
        # Counts the configuration writes of a device (one before and one after
        # each), so a settings read that overlapped a write is recognised and
        # dropped instead of replacing the newer value.
        self._setting_writes: dict[str, int] = {}
        # Devices whose settings are being read on request right now.
        self._settings_reading: set[str] = set()
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
        # Gateway profiles per announced EEP combination, read once at setup
        # (GET /devices/<id>/profile, no radio telegram). Devices with the
        # same EEPs share one profile. None marks a profile that could not be
        # read; such devices use the EEP tables.
        self.profiles: dict[tuple[str, ...], DeviceProfile | None] = {}
        # Cover runs being timed and the last completed one per channel.
        self._cover_runs: dict[tuple[str, int], CoverRun] = {}
        self.last_cover_runs: dict[tuple[str, int], CoverRun] = {}

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
        # Entities are created after setup returns, so their type and channel
        # count can follow the gateway profiles read here.
        await self._async_load_profiles()
        # Name resolution can take seconds on a slow network: not part of setup.
        self._create_task(self._async_refresh_gateway_address(), "address lookup")
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
        self.gateway_info_read_at = dt_util.utcnow().isoformat()
        self._log_gateway_versions()

    def _gateway_versions(self) -> tuple[object, object, object]:
        """Return firmware, app and API version as the gateway reported them."""
        info = self.gateway_info
        return info.get("version"), info.get("appVersion"), info.get("apiVersion")

    def _log_gateway_versions(self) -> None:
        """Name the gateway versions in the log (support reports include it)."""
        firmware, app, api = self._gateway_versions()
        _LOGGER.info(
            "OPUS gateway versions: firmware=%s app=%s api=%s", firmware, app, api
        )

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

    def stream_diagnostics(self) -> dict[str, object]:
        """Return cached stream facts for diagnostics.

        Reads counters only: no request, no new stream and no radio telegram.
        ``reconnects`` counts streams after the first one that delivered a
        document; attempts that failed before any document are in ``attempts``.
        """
        return {
            "connected": self._stream_connected,
            "framing_requested": self.client.stream_framing,
            "attempts": self.stream_attempts,
            "connects": self.stream_connects,
            "reconnects": max(self.stream_connects - 1, 0),
            "radio_queue_depth": self._radio_queue.queue_depth,
        }

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
            self.stream_attempts += 1
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
                            self.stream_connects += 1
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
            await self._async_check_generation()
            return
        self._probe_failures = 0
        previous = self.gateway_uptime
        self.gateway_uptime = uptime
        async_dispatcher_send(
            self.hass, f"{SIGNAL_GATEWAY_UPTIME_UPDATE}_{self.eag_id}"
        )
        if previous is not None and float(uptime) < float(previous):
            _LOGGER.info("OPUS gateway %s restarted; reopening its stream", self.eag_id)
            # A firmware update may have happened: offer line framing again.
            self.client.reset_stream_framing()
            self._restart_stream()
        elif self._stream_connected and not self._gateway_available:
            self._set_gateway_available(True)

    async def _async_check_generation(self) -> None:
        """Ask whether the unreachable gateway was updated to WebUI 2026.

        Only after several failed probes in a row and not more often than
        ``GENERATION_CHECK_INTERVAL``, so a gateway that is merely restarting
        is left alone. The answer needs a user login, which the
        reauthentication asks for; nothing is changed before that.
        """
        self._probe_failures += 1
        if (
            self._generation_check is None
            or self._auth_failed
            or self._probe_failures < GENERATION_CHECK_AFTER_FAILURES
        ):
            return
        now = monotonic()
        if (
            self._generation_checked_at is not None
            and now - self._generation_checked_at < GENERATION_CHECK_INTERVAL
        ):
            return
        self._generation_checked_at = now
        if not await self._generation_check():
            return
        _LOGGER.warning(
            "OPUS gateway %s no longer answers as before, but a gateway with "
            "WebUI 2026 does; a user login is needed",
            self.eag_id,
        )
        self._handle_auth_failure()

    @callback
    def _async_refresh_tick(self, _now: datetime) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self._create_task(
                self._async_periodic_refresh(), "device list refresh"
            )

    async def _async_periodic_refresh(self) -> None:
        """Run the 30 min reads: gateway information, then the device list."""
        await self._async_refresh_system_info()
        await self._async_refresh_gateway_address()
        await self._async_refresh_device_list()

    async def _async_refresh_gateway_address(self) -> None:
        """Resolve host name and addresses of the gateway for display.

        A local name lookup, no gateway request. Whatever does not resolve is
        None; the sensors are told only when the result changed.
        """
        address = await async_gateway_address(self.hass, self.client.host)
        if self._unloaded or address == self.gateway_address:
            return
        self.gateway_address = address
        async_dispatcher_send(self.hass, f"{SIGNAL_GATEWAY_INFO_UPDATE}_{self.eag_id}")

    async def _async_refresh_system_info(self) -> None:
        """Read ``/system/info`` again for versions and receive statistics.

        A plain gateway read without radio traffic, once per refresh interval
        and never with the health probe. Fields the gateway leaves out keep
        their last value; a reply for another gateway is ignored.
        """
        if not self.available:
            return
        try:
            document = await self.client.async_get("/system/info")
        except OpusAuthError:
            self._handle_auth_failure()
            return
        except OpusApiError as err:
            _LOGGER.debug("Could not refresh the OPUS gateway information: %s", err)
            return
        info = as_object(document.get("systemInfo")) or {}
        if not info:
            return
        eurid = info.get("eurid")
        if isinstance(eurid, str) and eurid.upper() != self.eag_id:
            _LOGGER.warning(
                "Ignoring the gateway information of %s: it reports another EURID",
                self.client.host,
            )
            return
        versions = self._gateway_versions()
        self.gateway_info = {**self.gateway_info, **info}
        self.gateway_info_read_at = dt_util.utcnow().isoformat()
        if self._gateway_versions() != versions:
            self._log_gateway_versions()
        async_dispatcher_send(self.hass, f"{SIGNAL_GATEWAY_INFO_UPDATE}_{self.eag_id}")

    async def _async_load_profiles(self) -> None:
        """Read one gateway profile per announced EEP combination.

        The profile is static gateway data (no radio telegram). A failure only
        means the EEP tables decide for these devices. Devices paired later
        take a profile read here when their EEPs match, otherwise the tables;
        a reload reads the profiles again.
        """
        samples: dict[tuple[str, ...], str] = {}
        for device in self.devices.values():
            signature = device.eep_names
            if signature and signature not in self.profiles:
                samples.setdefault(signature, device.device_id)
        for signature, device_id in samples.items():
            profile: DeviceProfile | None = None
            try:
                profile = protocol.device_profile(
                    await self.client.async_get(
                        OpusGatewayClient.device_path(device_id, "profile")
                    )
                )
            except OpusAuthError:
                raise
            except OpusApiError as err:
                _LOGGER.debug(
                    "Could not read the OPUS profile of %s (%s): %s",
                    device_id,
                    ", ".join(signature),
                    err,
                )
            self.profiles[signature] = profile
        for device in self.devices.values():
            device.profile = self.profiles.get(device.eep_names)

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
            product_id=protocol.metadata_text(data.get("productId")),
            software_revision=protocol.metadata_text(data.get("softwareRevision")),
            hardware_revision=protocol.metadata_text(data.get("hardwareRevision")),
            first_seen=protocol.metadata_text(data.get("firstSeen")),
            last_seen=protocol.metadata_text(data.get("lastSeen")),
            dbm=EnOceanDevice._parse_integer(data.get("dbm")),
            link_tables=protocol.link_tables(data),
        )
        was_known = existing is not None and bool(existing.eeps)
        if existing is None:
            device = listed
            device.profile = self.profiles.get(device.eep_names)
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
            if device.profile is None:
                device.profile = self.profiles.get(device.eep_names)
            # A device list refreshes metadata and state, never a previous
            # rocker occurrence.
            device.clear_button_occurrence()
        self._record_last_movement_times(device_id, data)
        functions = self._without_stale_settings(
            device_id,
            protocol.device_state_functions(data)
            if authoritative or existing is None
            else protocol.configuration_functions(data),
        )
        if functions:
            device.update_from_telegram({"functions": functions})
            if authoritative or existing is None:
                # The latency log measures from here, not from the device's
                # last telegram.
                device.last_update_source = "device list"
                device.last_update_received_monotonic = monotonic()
                device.last_update_dispatched_monotonic = (
                    device.last_update_received_monotonic
                )
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
        """Record the origin of every position in ``functions``.

        A numeric position records its origin. ``unknown`` keeps the last
        known position (``EnOceanDevice.update_from_telegram``) and so its
        origin as well. Diagnostics only.
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
        for runs in (self._cover_runs, self.last_cover_runs):
            for run_key in [k for k in runs if k[0] == device_id]:
                del runs[run_key]
        self._telegram_metadata_at.pop(device_id, None)
        self.last_vertical_movement_times.pop(device_id, None)
        self._setting_guards.pop(device_id, None)
        self._setting_writes.pop(device_id, None)
        self._settings_reading.discard(device_id)
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
            self._start_cover_runs(device, functions, received_at)
            estimated = self._start_cover_commands(device, functions)
            default = protocol.channel_from_functions(functions)
            applied = [
                function
                for function in functions
                if not (
                    function.get("key") == KEY_POSITION
                    and protocol.function_channel(function, default) in estimated
                )
                # A command target of "unknown" is no report of the device.
                and not (
                    function.get("key") in (KEY_POSITION, KEY_ANGLE)
                    and function.get("value") == VALUE_UNKNOWN
                )
                and not _is_timed_parameter(function)
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
        self,
        device: EnOceanDevice,
        channel_id: int,
        target: int,
        via: int | None = None,
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
        if via is not None:
            # Reference run: first to the end position ``via``, then back.
            first_leg = (
                full_time if current is None else abs(via - current) / 100 * full_time
            )
            travel = first_leg + abs(target - via) / 100 * full_time
        else:
            travel = (
                full_time
                if current is None
                else abs(target - current) / 100 * full_time
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
        first_target = target if via is None else via
        channel.movement = (
            "closing"
            if (first_target > 50 if current is None else first_target > current)
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

        movement.cancel = async_call_later(self.hass, travel + movement.grace, finished)
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
        repositioning = next(
            (
                function.get("value")
                for function in functions
                if function.get("key") == KEY_REPOSITIONING
            ),
            None,
        )
        via = {REPOSITIONING_FIRST_UP: 0, REPOSITIONING_FIRST_DOWN: 100}.get(
            repositioning if isinstance(repositioning, str) else ""
        )
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
                    movement = self._start_cover_movement(
                        device, channel_id, target, via
                    )
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
        self._finish_cover_runs(device_id, functions)
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
                if awaited := sorted(expected & fields):
                    # The device reported what a command of ours was waiting
                    # for: the only evidence beyond the gateway's acknowledgement.
                    self._journal(
                        device_id,
                        "feedback",
                        channel=channel_id,
                        outcome="confirmed",
                        fields=awaited,
                    )
                    _LOGGER.debug(
                        "OPUS device %s channel %s reported %s",
                        device_id,
                        channel_id,
                        awaited,
                    )
                expected.difference_update(fields)
            if not expected:
                self._cancel_reconciliation_queries(device_id, channel_id)
        return bool(confirmed)

    # ──────────────────────────────────────────────────────────────────────
    # Cover run times
    # ──────────────────────────────────────────────────────────────────────

    def _start_cover_runs(
        self,
        device: EnOceanDevice | None,
        functions: Sequence[WireFunction],
        started_at: float,
    ) -> None:
        """Start timing every cover position command in ``functions``.

        A stop ends the timing without a result; a new target replaces it.
        """
        if device is None or device.entity_type != "cover":
            return
        default = protocol.channel_from_functions(functions)
        repositioning = next(
            (
                value
                for function in functions
                if function.get("key") == KEY_REPOSITIONING
                and isinstance(value := function.get("value"), str)
            ),
            None,
        )
        for function in functions:
            channel_id = protocol.function_channel(function, default)
            if channel_id is None:
                continue
            key = (device.device_id, channel_id)
            if function.get("key") == KEY_STOP:
                self._cover_runs.pop(key, None)
            elif (
                function.get("key") == KEY_POSITION
                and (
                    target := EnOceanDevice._parse_integer(
                        function.get("value"), minimum=0, maximum=100
                    )
                )
                is not None
            ):
                channel = device.channels.get(channel_id)
                self._cover_runs[key] = CoverRun(
                    channel.position if channel is not None else None,
                    target,
                    repositioning,
                    started_at,
                )

    def _mark_cover_runs_queried(self, device_id: str, channel_id: int) -> None:
        """A status query answers with the position, not the arrival time."""
        if (run := self._cover_runs.get((device_id, channel_id))) is not None:
            run.queried = True

    def _finish_cover_runs(
        self, device_id: str, functions: Sequence[WireFunction]
    ) -> None:
        """Finish timed runs with a reported position at their target."""
        if not self._cover_runs:
            return
        now = monotonic()
        default = protocol.channel_from_functions(functions)
        for function in functions:
            if function.get("key") != KEY_POSITION:
                continue
            channel_id = protocol.function_channel(function, default)
            position = EnOceanDevice._parse_integer(
                function.get("value"), minimum=0, maximum=100
            )
            if channel_id is None or position is None:
                continue
            key = (device_id, channel_id)
            run = self._cover_runs.get(key)
            if run is None:
                continue
            seconds = now - run.started_at
            if seconds > COVER_RUN_TIMEOUT_SECONDS:
                del self._cover_runs[key]
                continue
            if abs(position - run.target) > COVER_RUN_TARGET_TOLERANCE:
                continue  # an intermediate report, e.g. at a reference end
            del self._cover_runs[key]
            if run.queried:
                _LOGGER.debug(
                    "OPUS cover %s channel %s reached %s %% after a status "
                    "query; no run time recorded",
                    device_id,
                    key[1],
                    position,
                )
                continue
            run.seconds = round(seconds, 2)
            run.finished_at = dt_util.utcnow().isoformat()
            self.last_cover_runs[key] = run
            _LOGGER.info(
                "OPUS cover %s channel %s: %s run from %s %% to %s %% took %.1f s",
                device_id,
                key[1],
                run.direction,
                "?" if run.start_position is None else run.start_position,
                position,
                run.seconds,
            )

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

    def radio_journal(self) -> list[dict[str, object]]:
        """Return the recorded radio requests, oldest first (diagnostics).

        A copy of memory: reading it sends nothing.
        """
        return [dict(entry) for entry in self._radio_journal]

    def _journal(
        self, device_id: str, kind: str, **fields: object
    ) -> dict[str, object]:
        self._radio_journal_seq += 1
        entry: dict[str, object] = {
            "seq": self._radio_journal_seq,
            "at": dt_util.utcnow().isoformat(),
            "device_id": device_id,
            "request": kind,
            **fields,
        }
        self._radio_journal.append(entry)
        return entry

    def _mask_gateway_identifiers(self, text: str) -> str:
        """Hide the gateway's EURID and serial number in a text.

        Gateway texts reach the journal and the debug log verbatim; neither
        identifier belongs in a log or report that is shared for support.
        """
        for identifier in (self.eag_id, self.gateway_info.get("serialNumber")):
            if isinstance(identifier, str) and identifier:
                text = re.sub(
                    re.escape(identifier), "<gateway-id>", text, flags=re.IGNORECASE
                )
        return text

    def _journal_radio_request(
        self,
        device_id: str,
        kind: str,
        priority: int,
        values: Mapping[str, object] | None,
        *,
        waited: float,
        started: float,
        result: JsonObject | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Record how one request that reached the radio slot ended.

        The gateway's answer is not the device's state: ``ok`` only says that
        the gateway accepted the request, ``deferred`` that it stored it for a
        sleeping device. Gateway texts are untrusted data and kept short.
        """
        http_status: int | None = None
        gateway_status: int | None = None
        message: str | None = None
        if error is None:
            outcome = "ok"
            try:
                gateway_status = response_status(result) if result else None
            except ValueError:
                gateway_status = None
            if gateway_status in (SOFT_SMART_ACK_STATUS, TARGET_STATE_STATUS):
                outcome = "deferred"
        elif isinstance(error, asyncio.CancelledError):
            outcome = "cancelled"
        elif isinstance(error, OpusAuthError):
            outcome = "auth_rejected"
        elif isinstance(error, OpusTimeoutError):
            outcome = "timeout"
        elif isinstance(error, OpusConnectionError):
            outcome = "connection_error"
        elif isinstance(error, OpusResponseError):
            outcome = "rejected"
            http_status = error.http_status
            gateway_status = error.gateway_status
            message = self._mask_gateway_identifiers(error.reason)[:JOURNAL_TEXT_LENGTH]
        else:
            outcome = "error"
        if message is None and error is not None:
            message = (
                self._mask_gateway_identifiers(str(error))[:JOURNAL_TEXT_LENGTH] or None
            )
        duration_ms = round((monotonic() - started) * 1000, 1)
        waited_ms = round(waited * 1000, 1)
        priority_name = _PRIORITY_NAMES.get(priority, str(priority))
        entry = self._journal(
            device_id,
            kind,
            priority=priority_name,
            outcome=outcome,
            http_status=http_status,
            gateway_status=gateway_status,
            message=message,
            duration_ms=duration_ms,
            waited_ms=waited_ms,
        )
        if values:
            entry["values"] = dict(values)
        _LOGGER.debug(
            "OPUS radio request: device=%s request=%s priority=%s outcome=%s "
            "http_status=%s gateway_status=%s duration_ms=%s waited_ms=%s "
            "values=%s message=%r",
            device_id,
            kind,
            priority_name,
            outcome,
            http_status,
            gateway_status,
            duration_ms,
            waited_ms,
            dict(values) if values else None,
            message,
        )

    async def _async_radio_request(
        self,
        device_id: str,
        request: Callable[[], Coroutine[object, object, JsonObject]],
        *,
        priority: int,
        kind: str,
        values: Mapping[str, object] | None = None,
        before_send: Callable[[], None] | None = None,
    ) -> JsonObject:
        """Run one radio request while owning the single in-flight slot.

        ``kind`` and ``values`` (what is sent, key and value) are only recorded
        in the diagnostics journal and the debug log.
        """
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
            started = monotonic()
            try:
                result = await request()
            except OpusApiError as err:
                self._journal_radio_request(
                    device_id,
                    kind,
                    priority,
                    values,
                    waited=waited,
                    started=started,
                    error=err,
                )
                raise self._request_error(err, device_id) from err
            except asyncio.CancelledError as err:
                # The request may have reached the gateway: recorded, not undone.
                self._journal_radio_request(
                    device_id,
                    kind,
                    priority,
                    values,
                    waited=waited,
                    started=started,
                    error=err,
                )
                raise
            self._journal_radio_request(
                device_id,
                kind,
                priority,
                values,
                waited=waited,
                started=started,
                result=result,
            )
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
        sent_at: list[float] = []
        body = {"state": {"functions": list(functions)}}
        _LOGGER.debug("Sending command to OPUS device %s: %s", device_id, body)

        is_query = any(function.get("key") == KEY_QUERY for function in functions)

        @callback
        def snapshot_feedback() -> None:
            # Runs while owning the request slot, immediately before sending.
            # Feedback for an earlier queued command cannot confirm this one.
            if background and still_needed is not None and not still_needed():
                raise request_error("request_cancelled", device_id, SUPERSEDED)
            sent_at.append(monotonic())
            if is_query:
                default = protocol.channel_from_functions(functions)
                if default is not None:
                    self._mark_cover_runs_queried(device_id, default)
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
                    kind="status_query" if is_query else "command",
                    values=_journal_values(functions),
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
                if response_status(response)
                in (SOFT_SMART_ACK_STATUS, TARGET_STATE_STATUS)
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
                if delivery is CommandDelivery.SENT and sent_at:
                    self._start_cover_runs(device, functions, sent_at[0])
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
        repositioning: str | None = None,
    ) -> CommandDelivery:
        """Set OPUS position (0 = open, 100 = closed).

        ``repositioning`` (D2-05 "Go to Position and Angle") makes the blind
        drive to an end position first (``firstUp``/``firstDown``) and so
        recalibrates its position tracking; ``direct`` is the default.
        """
        functions: list[WireFunction] = [{"key": "position", "value": str(position)}]
        if repositioning is not None:
            functions.append({"key": KEY_REPOSITIONING, "value": repositioning})
        return await self.async_send_command(
            device_id, self._with_channel_if_needed(device_id, functions, channel)
        )

    async def async_set_handle_unlock(
        self, device_id: str, *, allowed: bool
    ) -> CommandDelivery:
        """Unlock (``allowed``) or lock (``notAllowed``) a HOPPE AutoLock handle.

        The handle sleeps between uses; the gateway then queues the answer
        (201, SoftSmartAck) until the handle wakes up, e.g. when its button
        is pressed.
        """
        functions: list[WireFunction] = [
            {
                "key": KEY_UNLOCK,
                "value": UNLOCK_ALLOWED if allowed else UNLOCK_NOT_ALLOWED,
            }
        ]
        return await self.async_send_command(device_id, functions)

    async def async_set_cover_locking_mode(
        self,
        device_id: str,
        locking_mode: str,
        channel: int = 0,
    ) -> CommandDelivery:
        """Set the blockage of a blind (``block`` or ``unblock``).

        Position and angle keep their profile default ``noChange``.
        """
        functions: list[WireFunction] = [
            {"key": KEY_LOCKING_MODE, "value": locking_mode}
        ]
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
            kind="configuration_read",
        )

    async def async_set_device_configuration(
        self, device_id: str, config: WireMapping
    ) -> None:
        """Write configuration to a device and wait for the acknowledgement."""
        if (problem := json_body_problem(config)) is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_configuration",
                translation_placeholders={"device_id": device_id, "reason": problem},
            )
        path = OpusGatewayClient.device_path(device_id, "configuration")
        configuration = as_object(config.get("configuration")) or {}
        self._note_setting_write(device_id)
        try:
            await self._async_radio_request(
                device_id,
                lambda: self.client.async_put(path, config, timeout=RECOM_TIMEOUT),
                priority=PRIORITY_QUERY,
                kind="configuration_write",
                values=_journal_values(configuration.get("parameters")),
            )
        finally:
            self._note_setting_write(device_id)

    def _note_setting_write(self, device_id: str) -> None:
        """Count a configuration write step of a device (see ``_setting_writes``)."""
        self._setting_writes[device_id] = self._setting_writes.get(device_id, 0) + 1

    async def async_reread_device_settings(self, device_id: str) -> None:
        """Read the settings stored on a device and show them (on request only).

        One ReCom read over the radio, never repeated and never started by the
        integration itself. The values the device reports replace what Home
        Assistant shows, also a value that a device list or an earlier write
        left behind, and end the protection of a confirmed write for these
        settings. A setting the device does not report keeps its known value.
        A press while the same device is being read is ignored. An answer that
        arrives after a write to the device started or finished meanwhile is
        dropped: the write is newer.
        """
        device = self.get_device(device_id)
        if device is None:
            raise request_error("device_not_found", device_id)
        if device_id in self._settings_reading:
            return
        self._settings_reading.add(device_id)
        started = self._setting_writes.get(device_id, 0)
        repeater: JsonObject | None = None
        try:
            document = await self.async_get_device_configuration(device_id)
            if protocol.supports_repeater_state(device):
                # A second, separate read; a failure only leaves the mode as known.
                try:
                    repeater = await self.async_get_repeater_state(device_id)
                except HomeAssistantError as err:
                    _LOGGER.debug(
                        "Repeater mode of device %s not read: %s", device_id, err
                    )
        finally:
            self._settings_reading.discard(device_id)
        if self._setting_writes.get(device_id, 0) != started:
            _LOGGER.debug(
                "Dropped the settings read of device %s: a write overlapped it",
                device_id,
            )
            return
        if self.devices.get(device_id) is not device:
            return
        functions = protocol.configuration_state_functions(document)
        if repeater is not None and (
            function := protocol.repeater_state_function(repeater)
        ):
            functions.append(function)
        if not functions:
            return
        device.update_from_telegram({"functions": functions})
        if guards := self._setting_guards.get(device_id):
            for function in functions:
                if isinstance(key := function.get("key"), str):
                    guards.pop(key, None)
            if not guards:
                self._setting_guards.pop(device_id, None)
        self._dispatch_state(device)

    def _guard_confirmed_setting(
        self,
        device_id: str,
        key: str,
        previous: float | str | None,
        written: float | str,
    ) -> None:
        """Remember what a device list must not bring back after a confirmed write.

        ``previous`` is the value Home Assistant showed before. Earlier values
        of a guard that still runs stay protected, so several changes in a row
        do not let the oldest cached value return.
        """
        guards = self._setting_guards.setdefault(device_id, {})
        older = guards.get(key)
        candidates: list[float | str] = [] if previous is None else [previous]
        if older is not None:
            candidates.extend(older.stale)
        stale: list[float | str] = []
        for value in candidates:
            if not _same_setting(value, written) and not any(
                _same_setting(value, known) for known in stale
            ):
                stale.append(value)
        if not stale:
            guards.pop(key, None)
            if not guards:
                self._setting_guards.pop(device_id, None)
            return
        guards[key] = SettingGuard(
            tuple(stale[:_SETTING_GUARD_STALE_VALUES]), written, monotonic()
        )

    def _without_stale_settings(
        self, device_id: str, functions: list[WireFunction]
    ) -> list[WireFunction]:
        """Drop settings of a device list that repeat the value from before a write.

        A value that differs from the older ones, or equals the written one, is
        the gateway's current copy or a change made elsewhere: it is applied and
        ends the protection of that setting. Everything else is kept unchanged.
        """
        guards = self._setting_guards.get(device_id)
        if not guards:
            return functions
        now = monotonic()
        kept: list[WireFunction] = []
        for function in functions:
            key = function.get("key")
            if not isinstance(key, str):
                kept.append(function)
                continue
            guard = guards.get(key)
            if guard is None:
                kept.append(function)
                continue
            value = function.get("value")
            if now - guard.at <= SETTING_CACHE_GUARD_SECONDS and (
                not _same_setting(value, guard.written)
                and any(_same_setting(value, old) for old in guard.stale)
            ):
                _LOGGER.debug(
                    "Ignored %s of device %s in a device list: it repeats the "
                    "value from before a confirmed write",
                    key,
                    device_id,
                )
                continue
            del guards[key]
            kept.append(function)
        if not guards:
            self._setting_guards.pop(device_id, None)
        return kept

    async def async_set_device_parameter(
        self,
        device_id: str,
        key: str,
        value: float,
        *,
        step: float,
        minimum: float = 0,
    ) -> None:
        """Write one numeric parameter and show it only once the device confirms.

        The value is snapped to the parameter's step first, because the device
        stores it that way. The acknowledgement only means that the gateway
        accepted the request; the stored value is therefore read back. A
        matching value is adopted; a different one is adopted too, because it
        is what the device now holds, and reported. Neither the write nor the
        read-back is repeated automatically.
        """
        written = snap_to_step(value, step, minimum)
        await self._async_write_setting(
            device_id,
            key,
            written,
            read=protocol.configuration_parameter,
            same=lambda stored: abs(stored - written) <= step / 2,
        )

    async def async_set_device_option(
        self, device_id: str, key: str, option: str
    ) -> None:
        """Write one text setting and show it only once the device confirms.

        Same contract as ``async_set_device_parameter``.
        """
        if key in (KEY_REPEATER_STATE, KEY_ROUTING_TIME):
            device = self.get_device(device_id)
            if device is None:
                raise request_error("device_not_found", device_id)
            async with self._setting_locks.setdefault(device_id, asyncio.Lock()):
                if key == KEY_REPEATER_STATE:
                    await self._async_set_repeater_state(device, option)
                else:
                    await self._async_set_routing(device, option)
            return
        await self._async_write_setting(
            device_id,
            key,
            option,
            read=protocol.configuration_option,
            same=lambda stored: stored == option,
        )

    async def _async_write_setting(
        self,
        device_id: str,
        key: str,
        value: float | str,
        *,
        read: Callable[[JsonObject, str], Any],
        same: Callable[[Any], bool],
    ) -> None:
        device = self.get_device(device_id)
        if device is None:
            raise request_error("device_not_found", device_id)
        async with self._setting_locks.setdefault(device_id, asyncio.Lock()):
            if isinstance(value, (int, float)) and (
                conflicts := device.setting_conflicts(key, value=value)
            ):
                # The OPUS config tool warns about these combinations; a write
                # that creates or worsens one is refused before it is sent.
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key=f"setting_conflict_{conflicts[0]}",
                    translation_placeholders={"device_id": device_id},
                )
            document = await self._async_write_and_read_back(device_id, key, value)
            stored = read(document, key)
            if stored is None:
                raise request_error("parameter_not_confirmed", device_id, key)
            previous = device.setting_reading(key)
            device.update_from_telegram({"functions": [{"key": key, "value": stored}]})
            self._guard_confirmed_setting(device_id, key, previous, stored)
            self._dispatch_state(device)
            if not same(stored):
                raise request_error("parameter_differs", device_id, key)

    async def _async_write_and_read_back(
        self, device_id: str, key: str, value: float | str
    ) -> JsonObject:
        """Write one setting, read the configuration back and return it.

        A rejection by the gateway says different things for the two steps: a
        rejected write means the device probably did not get the value
        (``parameter_write_failed``), a rejected read-back means the write was
        accepted and the value may be stored (``parameter_read_back_failed``).
        A read-back that fails otherwise is ``parameter_not_confirmed``; a
        failed write keeps its own message. Neither step is repeated. The
        caller holds the device's setting lock.
        """
        try:
            try:
                await self.async_set_device_configuration(
                    device_id,
                    {"configuration": {"parameters": [{"key": key, "value": value}]}},
                )
            except HomeAssistantError as err:
                # A rejected write: the device probably did not get the value.
                if err.translation_key != "request_rejected":
                    raise
                raise request_error(
                    "parameter_write_failed", device_id, _step_reason(key, err)
                ) from err
            try:
                return await self.async_get_device_configuration(device_id)
            except HomeAssistantError as err:
                # Written, but the outcome could not be read back. A rejected
                # read-back means the write was accepted and may be stored.
                if err.translation_key == "request_rejected":
                    raise request_error(
                        "parameter_read_back_failed",
                        device_id,
                        _step_reason(key, err),
                    ) from err
                raise request_error("parameter_not_confirmed", device_id, key) from err
        finally:
            # The read-back ends the write: a settings read that overlapped it
            # is dropped (see ``_setting_writes``).
            self._note_setting_write(device_id)

    async def _async_set_routing(self, device: EnOceanDevice, option: str) -> None:
        """Switch the routing of a device off or on (read back like any setting).

        "off" is written as the text the catalog lists, "on" as the smallest
        routing time. Nothing is written when the device already shows the
        wanted state; the read-back accepts "off" or any positive time.
        """
        device_id = device.device_id
        if device.setting_reading(KEY_ROUTING_TIME) == option:
            return
        value: float | str = ROUTING_ON_SECONDS if option == ROUTING_ON else ROUTING_OFF
        stored = protocol.configuration_routing(
            await self._async_write_and_read_back(device_id, KEY_ROUTING_TIME, value)
        )
        if stored != option:
            raise request_error("parameter_not_confirmed", device_id, KEY_ROUTING_TIME)
        previous = device.setting_reading(KEY_ROUTING_TIME)
        device.update_from_telegram(
            {"functions": [{"key": KEY_ROUTING_TIME, "value": stored}]}
        )
        self._guard_confirmed_setting(device_id, KEY_ROUTING_TIME, previous, stored)
        self._dispatch_state(device)

    async def async_get_repeater_state(self, device_id: str) -> JsonObject:
        """Read the repeater mode of a device (``GET .../repeaterState``)."""
        path = OpusGatewayClient.device_path(device_id, "repeaterState")
        return await self._async_radio_request(
            device_id,
            lambda: self.client.async_get(path, timeout=RECOM_TIMEOUT),
            priority=PRIORITY_QUERY,
            kind="repeater_state_read",
        )

    async def _async_set_repeater_state(
        self, device: EnOceanDevice, option: str
    ) -> None:
        """Write the repeater mode and adopt it only when the read-back equals it.

        Same contract as the ReCom settings: the acknowledgement means the
        gateway accepted the request, neither step is repeated, and a rejected
        step is reported with its own message.
        """
        device_id = device.device_id
        path = OpusGatewayClient.device_path(device_id, "repeaterState")
        body = {KEY_REPEATER_STATE: option}
        self._note_setting_write(device_id)
        try:
            try:
                await self._async_radio_request(
                    device_id,
                    lambda: self.client.async_put(path, body, timeout=RECOM_TIMEOUT),
                    priority=PRIORITY_QUERY,
                    kind="repeater_state_write",
                    values={KEY_REPEATER_STATE: option},
                )
            except HomeAssistantError as err:
                if err.translation_key != "request_rejected":
                    raise
                raise request_error(
                    "parameter_write_failed",
                    device_id,
                    _step_reason(KEY_REPEATER_STATE, err),
                ) from err
            try:
                document = await self.async_get_repeater_state(device_id)
            except HomeAssistantError as err:
                if err.translation_key != "request_rejected":
                    raise
                raise request_error(
                    "parameter_read_back_failed",
                    device_id,
                    _step_reason(KEY_REPEATER_STATE, err),
                ) from err
        finally:
            self._note_setting_write(device_id)
        function = protocol.repeater_state_function(document)
        if function is None or function["value"] != option:
            raise request_error(
                "parameter_not_confirmed", device_id, KEY_REPEATER_STATE
            )
        previous = device.setting_reading(KEY_REPEATER_STATE)
        device.update_from_telegram({"functions": [function]})
        self._guard_confirmed_setting(device_id, KEY_REPEATER_STATE, previous, option)
        self._dispatch_state(device)

    async def async_get_device_parameters(self, device_id: str) -> JsonObject:
        """Read the configurable parameters a device supports."""
        path = OpusGatewayClient.device_path(device_id, "parameters")
        return await self._async_radio_request(
            device_id,
            lambda: self.client.async_get(path, timeout=RECOM_TIMEOUT),
            priority=PRIORITY_QUERY,
            kind="parameters_read",
        )

    async def async_get_device_link_tables(self, device_id: str) -> JsonObject:
        """Read the link tables stored on a device (read only).

        The wall switches a device listens to and the devices it reports to.
        The gateway's own EURID is masked in the answer. There is deliberately
        no write counterpart.
        """
        path = OpusGatewayClient.device_path(device_id, "linkTables")
        document = await self._async_radio_request(
            device_id,
            lambda: self.client.async_get(path, timeout=RECOM_TIMEOUT),
            priority=PRIORITY_QUERY,
            kind="link_tables_read",
        )
        masked = protocol.mask_gateway_id(document, self.eag_id)
        # mask_gateway_id keeps the type of a JSON object.
        return masked if isinstance(masked, dict) else document


def _is_device_list(document: JsonObject) -> bool:
    """Return whether a stream document is the complete device list."""
    header = as_object(document.get("header")) or {}
    return header.get("content") == "devices"
