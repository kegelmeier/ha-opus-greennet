"""Defensive branches of the climate, lock, number and select entities."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.climate import HVACMode

from custom_components.opus_greennet.climate import OpusGreenNetClimate
from custom_components.opus_greennet.coordinator import CommandDelivery
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.lock import OpusGreenNetAutoLock

EAG_ID = "AABB0011"


def _device(eep: str) -> EnOceanDevice:
    return EnOceanDevice(device_id="DEV1", friendly_id="Device", eeps=[{"eep": eep}])


def _coordinator() -> MagicMock:
    coordinator = MagicMock(available=True)
    coordinator.async_set_climate_setpoint = AsyncMock()
    coordinator.async_set_climate_mode = AsyncMock()
    coordinator.async_set_handle_unlock = AsyncMock(
        return_value=CommandDelivery.DEFERRED
    )
    return coordinator


# ── climate ─────────────────────────────────────────────────────────────────


async def test_climate_without_a_temperature_sends_no_setpoint() -> None:
    coordinator = _coordinator()
    entity = OpusGreenNetClimate(coordinator, EAG_ID, "gw", _device("D1-4B-06"))

    await entity.async_set_temperature()

    coordinator.async_set_climate_setpoint.assert_not_awaited()


async def test_climate_mode_without_a_gateway_value_sends_no_command() -> None:
    coordinator = _coordinator()
    entity = OpusGreenNetClimate(coordinator, EAG_ID, "gw", _device("D1-4B-06"))

    await entity.async_set_hvac_mode(HVACMode.AUTO)

    coordinator.async_set_climate_mode.assert_not_awaited()


# ── lock ────────────────────────────────────────────────────────────────────


async def test_a_newer_request_keeps_the_pending_state_of_an_older_one() -> None:
    """An older request that finishes late must not clear the newer request."""
    coordinator = _coordinator()
    device = _device("D2-06-40")
    entity = OpusGreenNetAutoLock(coordinator, EAG_ID, "gw", device)
    entity.async_write_ha_state = MagicMock()
    release = asyncio.Event()

    async def slow_answer(*_args: object, **_kwargs: object) -> CommandDelivery:
        await release.wait()
        return CommandDelivery.DEFERRED

    coordinator.async_set_handle_unlock = AsyncMock(side_effect=slow_answer)
    with patch("custom_components.opus_greennet.lock.LOCK_SETTLE_SECONDS", 0):
        first = asyncio.create_task(entity.async_unlock())
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # the first request is now being sent
        second = asyncio.create_task(entity.async_lock())
        await asyncio.sleep(0)
        assert entity.is_locking
        release.set()
        await asyncio.gather(first, second)

    assert coordinator.async_set_handle_unlock.await_count == 2
    assert not entity.is_locking
    assert not entity.is_unlocking
