"""Phase 4: less superseded background work and fewer redundant state writes."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.coordinator import (
    OUTBOUND_STATE_RECONCILIATION_DELAYS,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio
from tests.test_reconciliation_feedback import (
    fire_timer,
    query_timers,
    receive_feedback,
)
from tests.test_reconciliation_feedback import timers as timers

STATUS_QUERY = {"key": "query", "value": "status"}
STATE_PATH = "/devices/DEV1/state"


@pytest.fixture
async def coord(connected_coordinator, timers):
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Actuator", [{"eep": "D2-01-00"}]
    )
    return connected_coordinator


def published_functions(radio):
    return radio.functions(STATE_PATH)


def status_queries(radio) -> int:
    return sum(STATUS_QUERY in functions for functions in published_functions(radio))


async def drain(coordinator):
    for _ in range(5):
        await asyncio.sleep(0)
    if coordinator._tasks:
        await asyncio.gather(*coordinator._tasks, return_exceptions=True)


# ── A. Reconciliation ─────────────────────────────────────────────────────


def test_reconciliation_delays_are_unchanged():
    assert OUTBOUND_STATE_RECONCILIATION_DELAYS == (5, 20)


async def test_identical_flow_keeps_both_checks(radio, coord, timers):
    """Without feedback, the existing 5 s and 20 s checks are both sent."""
    await coord.async_turn_on("DEV1")
    pending = query_timers(timers)
    assert [delay for delay, _, _ in pending] == [5, 20]
    for timer in pending:
        await fire_timer(coord, timer)
    assert status_queries(radio) == 2


async def test_feedback_drops_check_still_waiting_for_the_send_slot(
    radio, coord, timers
):
    """A queued, unpublished check is dropped once feedback confirms the field."""
    await coord.async_turn_on("DEV1")
    pending = query_timers(timers)
    send_lock = coord._radio_queue
    await send_lock.acquire()  # another request is in flight
    try:
        pending[0][1](None)
        await asyncio.sleep(0)
        assert coord._radio_queue.queue_depth == 1
        receive_feedback(coord, [{"key": "switch", "value": "on"}])
        assert all(cancel.called for _, _, cancel in pending)
    finally:
        send_lock.release()
    await drain(coord)
    assert status_queries(radio) == 0
    assert not coord._reconciling
    assert not coord._radio_queue.queue_depth


async def test_partial_feedback_keeps_queued_check(radio, coord, timers):
    """Feedback for a different field does not drop a still-needed check."""
    await coord.async_turn_on("DEV1")
    pending = query_timers(timers)
    send_lock = coord._radio_queue
    await send_lock.acquire()
    try:
        pending[0][1](None)
        await asyncio.sleep(0)
        receive_feedback(coord, [{"key": "dimValue", "value": 10}])
    finally:
        send_lock.release()
    await drain(coord)
    assert status_queries(radio) == 1


async def test_new_command_supersedes_own_queued_check(radio, coord, timers):
    """A check for an older command, still queued behind a newer one, is dropped."""
    await coord.async_turn_on("DEV1")
    first = query_timers(timers)
    radio.auto_respond = False
    second = asyncio.create_task(coord.async_turn_off("DEV1"))
    await radio.wait_sent(1)
    first[0][1](None)  # 5 s check of the first command fires during the second
    await asyncio.sleep(0)
    assert coord._radio_queue.queue_depth == 1
    radio.respond()
    await second
    radio.auto_respond = True
    await drain(coord)
    assert status_queries(radio) == 0
    new = [timer for timer in query_timers(timers) if timer not in first]
    assert [delay for delay, _, _ in new] == [5, 20]
    await fire_timer(coord, new[0])
    assert status_queries(radio) == 1


async def test_superseded_check_is_revived_by_the_next_timer(radio, coord, timers):
    """A timer firing while the old check is still queued is never lost."""
    await coord.async_turn_on("DEV1")
    first = query_timers(timers)
    send_lock = coord._radio_queue
    await send_lock.acquire()
    try:
        first[0][1](None)
        await asyncio.sleep(0)
        # A new command's checks replace the old ones while the old is queued.
        coord._schedule_reconciliation_queries("DEV1", 0, fields={"switch"})
        new = [timer for timer in query_timers(timers) if timer not in first]
        new[0][1](None)  # coalesced into the queued request
    finally:
        send_lock.release()
    await drain(coord)
    assert status_queries(radio) == 1


async def test_stop_and_user_commands_are_never_deduplicated(
    radio, connected_coordinator
):
    coord = connected_coordinator
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Cover", [{"eep": "D2-05-02"}])
    await asyncio.gather(
        coord.async_stop_cover("DEV1"),
        coord.async_stop_cover("DEV1"),
        coord.async_set_cover_position("DEV1", 40),
        coord.async_set_cover_position("DEV1", 40),
    )
    functions = published_functions(radio)
    assert functions.count([{"key": "stop", "value": "true"}]) == 2
    assert functions.count([{"key": "position", "value": "40"}]) == 2


async def test_unload_with_queued_check_leaves_no_waiters(radio, coord, timers):
    await coord.async_turn_on("DEV1")
    pending = query_timers(timers)
    send_lock = coord._radio_queue
    await send_lock.acquire()
    pending[0][1](None)
    await asyncio.sleep(0)
    await coord.async_unload()
    send_lock.release()
    await drain(coord)
    assert status_queries(radio) == 0
    assert not coord._radio_queue.queue_depth
    assert not coord._reconciling
    assert not coord._reconcile_needed
    assert not coord._tasks


# ── B. Queue ──────────────────────────────────────────────────────────────


async def test_per_device_request_bookkeeping_does_not_accumulate(
    radio, connected_coordinator
):
    coord = connected_coordinator
    for index in range(25):
        await coord.async_turn_on(f"DEV{index}")
    assert coord._command_waiters == {}
    assert not coord._radio_queue.locked()
    assert coord._radio_queue.queue_depth == 0


# ── D. Entity state writes ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fast_retries():
    with patch(
        "custom_components.opus_greennet.coordinator.STREAM_BACKOFF_MAX",
        0.02,
    ):
        yield


@pytest.fixture
def writes():
    counts: dict[str, int] = {}
    original = Entity._async_write_ha_state

    def counting(self):
        counts[self.entity_id] = counts.get(self.entity_id, 0) + 1
        return original(self)

    with patch.object(Entity, "_async_write_ha_state", counting):
        yield counts


def _cover(**fields) -> dict:
    return device_object(
        "AABB1122",
        "D2-05-02",
        friendly_id="Shutter",
        states=[{"key": "position", "value": 40.0}],
        **fields,
    )


async def _cover_setup(hass: HomeAssistant, gateway: FakeGateway):
    gateway.devices = [_cover()]
    entry = await setup_bridge(hass, gateway)
    cover = await wait_for_entity(hass, "cover", "AABB0011_AABB1122")
    return cover, entry.runtime_data.coordinator


async def _dbm(
    hass: HomeAssistant,
    gateway: FakeGateway,
    coordinator,
    value: int,
    device_id: str = "AABB1122",
):
    """Deliver a metadata-only change through the periodic device list refresh."""
    gateway.devices = [
        {**device, "dbm": value} if device["deviceId"] == device_id else device
        for device in gateway.devices
    ]
    coordinator._async_refresh_tick(dt_util.utcnow())
    await coordinator._refresh_task
    await hass.async_block_till_done()
    assert coordinator.devices[device_id].dbm == value


async def test_rssi_only_update_skips_unchanged_cover_write(
    hass: HomeAssistant, gateway: FakeGateway, writes
):
    cover, coordinator = await _cover_setup(hass, gateway)
    # The first dispatch after a non-dispatch write (setup) is always written.
    await _dbm(hass, gateway, coordinator, -60)
    before = writes.get(cover, 0)
    for value in (-61, -62, -63):
        await _dbm(hass, gateway, coordinator, value)
    assert writes.get(cover, 0) == before
    assert coordinator.devices["AABB1122"].dbm == -63
    assert hass.states.get(cover).attributes["current_position"] == 60


async def test_feature_change_is_still_written(
    hass: HomeAssistant, gateway: FakeGateway, writes
):
    cover, _ = await _cover_setup(hass, gateway)
    assert hass.states.get(cover).attributes["supported_features"] & 128
    before = writes.get(cover, 0)
    await gateway.push(telegram("AABB1122", [{"key": "rotationTime", "value": "0"}]))
    await wait_until(
        lambda: not hass.states.get(cover).attributes["supported_features"] & 128
    )
    await hass.async_block_till_done()
    # One write from the dispatch; HA's own registry update may add another.
    assert writes[cover] > before


async def test_state_change_after_skipped_write_is_written(
    hass: HomeAssistant, gateway: FakeGateway, writes
):
    cover, coordinator = await _cover_setup(hass, gateway)
    await _dbm(hass, gateway, coordinator, -70)
    await _dbm(hass, gateway, coordinator, -71)
    await gateway.push(telegram("AABB1122", [{"key": "position", "value": "90"}]))
    await wait_until(
        lambda: hass.states.get(cover).attributes.get("current_position") == 10
    )


async def test_availability_change_is_still_written(
    hass: HomeAssistant, gateway: FakeGateway, writes
):
    cover, _ = await _cover_setup(hass, gateway)
    await gateway.stop()
    await wait_until(lambda: hass.states.get(cover).state == "unavailable")
    await gateway.start()
    await wait_until(lambda: hass.states.get(cover).state == "open")


async def test_identical_button_presses_still_fire_two_events(
    hass: HomeAssistant, gateway: FakeGateway
):
    gateway.devices = [device_object("AABB3344", "F6-02-01", friendly_id="Rocker")]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    entity_id = await wait_for_entity(hass, "event", "AABB0011_AABB3344")
    fired = []
    from custom_components.opus_greennet.event import OpusGreenNetEvent

    original = OpusGreenNetEvent._trigger_event

    def counting(self, event_type, attributes=None):
        fired.append(event_type)
        return original(self, event_type, attributes)

    with patch.object(OpusGreenNetEvent, "_trigger_event", counting):
        for count in (1, 2):
            await gateway.push(
                telegram("AABB3344", [{"key": "buttonA0", "value": "pressed"}])
            )
            await wait_until(lambda count=count: len(fired) == count)
        await _dbm(hass, gateway, coordinator, -60, "AABB3344")
    assert fired == ["buttonA0_pressed", "buttonA0_pressed"]
    assert hass.states.get(entity_id).attributes["event_type"] == "buttonA0_pressed"
