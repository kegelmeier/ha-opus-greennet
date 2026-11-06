"""Learn mode of the gateway: let new devices teach in for a fixed time.

While the gateway is in learn mode it accepts the teach-in telegram of a device
in radio range, so the mode must never stay on by accident. This module starts
it for ``LEARN_MODE_SECONDS`` and makes sure it is ended again:

1. a timer restores the normal mode;
2. before the learn mode is requested, a note "started by this integration" is
   saved with the gateway's other bookkeeping (``.storage``), so a restart of
   Home Assistant still knows that the gateway may be in learn mode;
3. unloading the entry and stopping Home Assistant restore the normal mode;
4. at the next setup, and whenever the gateway is reachable again, a note that
   is still there leads to reading the mode and restoring the normal mode.

A learn mode that this integration did not start (for example from the OPUS
app) is never ended by it. The start is sent once and never repeated, whatever
the outcome. The mode is read back before it counts as ended; only restoring the
normal mode, which is idempotent, is tried a limited number of times.

Nothing here is sent unless the user enabled device management in the options.
The request and answer shape of ``/system/receivemode`` is taken from names in
the gateway software, not from a recording (see ``docs/devices.md``).
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from datetime import timedelta
from time import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import OpusApiError
from .const import (
    DOMAIN,
    ISSUE_LEARN_MODE_RESET_FAILED,
    LEARN_MODE_BLOCKED_HOURS,
    LEARN_MODE_SECONDS,
    LEARN_MODE_SHUTDOWN_TIMEOUT,
    RECEIVE_MODE_KEY,
    RECEIVE_MODE_LEARN,
    RECEIVE_MODE_NORMAL,
    RECEIVE_MODE_PATH,
)

if TYPE_CHECKING:
    from .coordinator import OpusGreenNetCoordinator

_LOGGER = logging.getLogger(__name__)

SIGNAL_LEARN_MODE_UPDATE = f"{DOMAIN}_learn_mode_update"
# Key of the note in the bookkeeping file.
_STORAGE_KEY = "learn_mode"


class LearnModeController:
    """Start the learn mode for a fixed time and make sure it ends."""

    def __init__(self, coordinator: OpusGreenNetCoordinator) -> None:
        """Initialize the controller of one gateway."""
        self._coordinator = coordinator
        # What is known about the gateway: True after it reported the learn
        # mode, False after it reported the normal mode, None when the last
        # request left it open. Nothing is polled.
        self.state: bool | None = None
        # Wall-clock time of the start; the note that survives a restart.
        self._started_at: float | None = None
        self._timer: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._recovery: asyncio.Task[None] | None = None

    # ── bookkeeping ────────────────────────────────────────────────────────

    def restore(self, saved: object) -> None:
        """Take the note saved before the last restart, if there is one."""
        note = saved.get(_STORAGE_KEY) if isinstance(saved, dict) else None
        started = note.get("started_at") if isinstance(note, dict) else None
        if (
            isinstance(started, int | float)
            and not isinstance(started, bool)
            and math.isfinite(started)
        ):
            self._started_at = float(started)

    def as_storage(self) -> dict[str, Any]:
        """Return the note as it is saved; empty when nothing was started."""
        if self._started_at is None:
            return {}
        return {_STORAGE_KEY: {"started_at": self._started_at}}

    @property
    def pending(self) -> bool:
        """Return whether a start of this integration is not yet confirmed ended."""
        return self._started_at is not None

    # ── start ──────────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Request the learn mode once and end it after ``LEARN_MODE_SECONDS``."""
        coordinator = self._coordinator
        if not coordinator.device_management_allowed():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_management_disabled",
            )
        if not coordinator.available:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="gateway_unavailable"
            )
        now = dt_util.now()
        end = now + timedelta(seconds=LEARN_MODE_SECONDS)
        if now.hour in LEARN_MODE_BLOCKED_HOURS or end.hour in LEARN_MODE_BLOCKED_HOURS:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="learn_mode_night"
            )
        if self._lock.locked():
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="learn_mode_busy"
            )
        async with self._lock:
            if self._started_at is not None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="learn_mode_active"
                )
            try:
                mode = await self._async_read_mode()
            except OpusApiError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="learn_mode_unreadable"
                ) from err
            if mode is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="learn_mode_unreadable"
                )
            self._set_state(mode == RECEIVE_MODE_LEARN)
            if mode == RECEIVE_MODE_LEARN:
                # Started elsewhere: not ours to end.
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="learn_mode_foreign"
                )
            self._started_at = time()
            if not await coordinator.async_save_bookkeeping():
                self._started_at = None
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="learn_mode_not_saved"
                )
            try:
                await coordinator.client.async_put(
                    RECEIVE_MODE_PATH, {RECEIVE_MODE_KEY: RECEIVE_MODE_LEARN}
                )
            except OpusApiError as err:
                # The gateway may or may not be in learn mode now. The request
                # is not repeated; the reset runs in any case.
                _LOGGER.warning(
                    "Starting the learn mode of gateway %s was not confirmed: %s",
                    coordinator.eag_id,
                    err,
                )
                self._set_state(None)
                self._arm_timer()
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="learn_mode_start_unclear",
                ) from err
            self._set_state(True)
            self._arm_timer()

    # ── end ────────────────────────────────────────────────────────────────

    async def async_stop(self, *, read_first: bool = False) -> bool:
        """Restore the normal mode; return whether the gateway confirmed it.

        With ``read_first`` the mode is read before anything is sent, so a note
        left by an earlier run only changes the gateway if it is still in the
        learn mode. The mode is always read back; a failed attempt is not
        repeated here (the next recovery tries again).
        """
        async with self._lock:
            self._cancel_timer()
            if self._started_at is None:
                return True
            if read_first and await self._async_read_mode_or_none() == (
                RECEIVE_MODE_NORMAL
            ):
                await self._async_finish()
                return True
            try:
                await self._coordinator.client.async_put(
                    RECEIVE_MODE_PATH, {RECEIVE_MODE_KEY: RECEIVE_MODE_NORMAL}
                )
            except OpusApiError as err:
                _LOGGER.warning(
                    "Restoring the normal mode of gateway %s was not confirmed: %s",
                    self._coordinator.eag_id,
                    err,
                )
            if await self._async_read_mode_or_none() == RECEIVE_MODE_NORMAL:
                await self._async_finish()
                return True
            self._set_state(None)
            self._create_reset_issue()
            return False

    async def async_shutdown(self) -> None:
        """End a learn mode of this integration before unloading or stopping."""
        self._cancel_timer()
        if self._recovery is not None and not self._recovery.done():
            self._recovery.cancel()
        if self._started_at is None:
            return
        try:
            async with asyncio.timeout(LEARN_MODE_SHUTDOWN_TIMEOUT):
                await self.async_stop(read_first=False)
        except TimeoutError:
            _LOGGER.warning(
                "The learn mode of gateway %s could not be ended in time; it "
                "is checked again at the next start",
                self._coordinator.eag_id,
            )

    @callback
    def schedule_recovery(self) -> None:
        """Check a note left by an earlier run, unless a start is still timed."""
        if (
            self._started_at is None
            or self._timer is not None
            or (self._recovery is not None and not self._recovery.done())
        ):
            return
        self._recovery = self._coordinator.create_background_task(
            self.async_stop(read_first=True), "learn mode recovery"
        )

    # ── helpers ────────────────────────────────────────────────────────────

    async def _async_read_mode(self) -> str | None:
        """Read the mode; ``None`` when the answer names none of the two modes."""
        document = await self._coordinator.client.async_get(RECEIVE_MODE_PATH)
        mode = document.get(RECEIVE_MODE_KEY)
        if mode in (RECEIVE_MODE_LEARN, RECEIVE_MODE_NORMAL):
            return str(mode)
        return None

    async def _async_read_mode_or_none(self) -> str | None:
        try:
            return await self._async_read_mode()
        except OpusApiError as err:
            _LOGGER.debug("Reading the receive mode failed: %s", err)
            return None

    async def _async_finish(self) -> None:
        """Forget the note after the gateway reported the normal mode."""
        self._started_at = None
        self._set_state(False)
        ir.async_delete_issue(self._coordinator.hass, DOMAIN, self._issue_id())
        await self._coordinator.async_save_bookkeeping()

    def _arm_timer(self) -> None:
        self._cancel_timer()
        self._timer = async_call_later(
            self._coordinator.hass, LEARN_MODE_SECONDS, self._timer_fired
        )

    @callback
    def _timer_fired(self, _now: object) -> None:
        self._timer = None
        self._coordinator.create_background_task(self.async_stop(), "learn mode end")

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    def _set_state(self, state: bool | None) -> None:
        self.state = state
        async_dispatcher_send(
            self._coordinator.hass,
            f"{SIGNAL_LEARN_MODE_UPDATE}_{self._coordinator.eag_id}",
        )

    def _issue_id(self) -> str:
        return f"{ISSUE_LEARN_MODE_RESET_FAILED}_{self._coordinator.eag_id}"

    def _create_reset_issue(self) -> None:
        # Late import: ``entity`` imports the coordinator, which owns this object.
        from .entity import ISSUE_DATA_CONFIG_ENTRY_ID

        entry_id = self._coordinator.config_entry_id
        ir.async_create_issue(
            self._coordinator.hass,
            DOMAIN,
            self._issue_id(),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_LEARN_MODE_RESET_FAILED,
            data=None if entry_id is None else {ISSUE_DATA_CONFIG_ENTRY_ID: entry_id},
        )
