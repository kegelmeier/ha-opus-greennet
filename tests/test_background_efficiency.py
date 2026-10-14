"""Phase 4: less superseded background work and fewer redundant state writes."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from custom_components.opus_greennet.coordinator import (
    OUTBOUND_STATE_RECONCILIATION_DELAYS,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.ha_helpers import configure_bridge, wait_for_entity
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)
from tests.test_reconciliation_feedback import (
    ANSWER_TOPIC,
    COMMAND_TOPIC,
    fire_timer,
    query_timers,
    receive_feedback,
)
from tests.test_reconciliation_feedback import timers as timers

STATUS_QUERY = {"key": "query", "value": "status"}


@pytest.fixture
async def coord(connected_coordinator, timers):
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Actuator", [{"eep": "D2-01-00"}]
    )
    try:
        yield connected_coordinator
    finally:
        await connected_coordinator.async_unload()


def published_functions(broker):
    return [
        json.loads(payload)["state"]["functions"]
        for topic, payload, _ in broker.published
        if topic == COMMAND_TOPIC
    ]


def status_queries(broker) -> int:
    return sum(STATUS_QUERY in functions for functions in published_functions(broker))


async def drain(coordinator):
    for _ in range(5):
        await asyncio.sleep(0)
    if coordinator._tasks:
        await asyncio.gather(*coordinator._tasks, return_exceptions=True)


# ── A. Reconciliation ─────────────────────────────────────────────────────


def test_reconciliation_delays_are_unchanged():
    assert OUTBOUND_STATE_RECONCILIATION_DELAYS == (5, 20)


async def test_identical_flow_keeps_both_checks(broker, coord, timers):
    """Without feedback, the existing 5 s and 20 s checks are both sent."""
    await coord.async_turn_on("DEV1")
    pending = query_timers(timers)
    assert [delay for delay, _, _ in pending] == [5, 20]
    for timer in pending:
        await fire_timer(coord, timer)
    assert status_queries(broker) == 2


async def test_feedback_drops_check_still_waiting_for_the_send_slot(
    broker, coord, timers
):
    """A queued, unpublished check is dropped once feedback confirms the field."""
    await coord.async_turn_on("DEV1")
    pending = query_timers(timers)
    send_lock = coord._requests._send_lock
    await send_lock.acquire()  # another request is in flight
    try:
        pending[0][1](None)
        await asyncio.sleep(0)
        assert coord._requests.queue_depth == 1
        receive_feedback(coord, [{"key": "switch", "value": "on"}])
        assert all(cancel.called for _, _, cancel in pending)
    finally:
        send_lock.release()
    await drain(coord)
    assert status_queries(broker) == 0
    assert not coord._reconciling
    assert not coord._requests._waiters


async def test_partial_feedback_keeps_queued_check(broker, coord, timers):
    """Feedback for a different field does not drop a still-needed check."""
    await coord.async_turn_on("DEV1")
    pending = query_timers(timers)
    send_lock = coord._requests._send_lock
    await send_lock.acquire()
    try:
        pending[0][1](None)
        await asyncio.sleep(0)
        receive_feedback(coord, [{"key": "dimValue", "value": 10}])
    finally:
        send_lock.release()
    await drain(coord)
    assert status_queries(broker) == 1


async def test_new_command_supersedes_own_queued_check(broker, coord, timers):
    """A check for an older command, still queued behind a newer one, is dropped."""
    await coord.async_turn_on("DEV1")
    first = query_timers(timers)
    broker.auto_respond = False
    second = asyncio.create_task(coord.async_turn_off("DEV1"))
    await asyncio.sleep(0)
    first[0][1](None)  # 5 s check of the first command fires during the second
    await asyncio.sleep(0)
    broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
    await second
    broker.auto_respond = True
    await drain(coord)
    assert status_queries(broker) == 0
    new = [timer for timer in query_timers(timers) if timer not in first]
    assert [delay for delay, _, _ in new] == [5, 20]
    await fire_timer(coord, new[0])
    assert status_queries(broker) == 1


async def test_superseded_check_is_revived_by_the_next_timer(broker, coord, timers):
    """A timer firing while the old check is still queued is never lost."""
    await coord.async_turn_on("DEV1")
    first = query_timers(timers)
    send_lock = coord._requests._send_lock
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
    assert status_queries(broker) == 1


async def test_stop_and_user_commands_are_never_deduplicated(
    broker, connected_coordinator
):
    coord = connected_coordinator
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Cover", [{"eep": "D2-05-02"}])
    try:
        await asyncio.gather(
            coord.async_stop_cover("DEV1"),
            coord.async_stop_cover("DEV1"),
            coord.async_set_cover_position("DEV1", 40),
            coord.async_set_cover_position("DEV1", 40),
        )
        functions = published_functions(broker)
        assert functions.count([{"key": "stop", "value": "true"}]) == 2
        assert functions.count([{"key": "position", "value": "40"}]) == 2
    finally:
        await coord.async_unload()


async def test_unload_with_queued_check_leaves_no_waiters(broker, coord, timers):
    await coord.async_turn_on("DEV1")
    pending = query_timers(timers)
    send_lock = coord._requests._send_lock
    await send_lock.acquire()
    pending[0][1](None)
    await asyncio.sleep(0)
    await coord.async_unload()
    send_lock.release()
    await drain(coord)
    assert status_queries(broker) == 0
    assert not coord._requests._waiters
    assert not coord._reconciling
    assert not coord._reconcile_needed
    assert not coord._tasks


# ── B. Queue ──────────────────────────────────────────────────────────────


async def test_answer_topic_locks_do_not_accumulate(broker, connected_coordinator):
    coord = connected_coordinator
    try:
        for index in range(25):
            await coord.async_turn_on(f"DEV{index}")
        assert coord._requests._locks == {}
    finally:
        await coord.async_unload()


# ── D. Entity state writes ────────────────────────────────────────────────


@pytest.fixture
def writes():
    counts: dict[str, int] = {}
    original = Entity._async_write_ha_state

    def counting(self):
        counts[self.entity_id] = counts.get(self.entity_id, 0) + 1
        return original(self)

    with patch.object(Entity, "_async_write_ha_state", counting):
        yield counts


COVER = {
    "deviceId": "AABB1122",
    "friendlyId": "Shutter",
    "eeps": [{"eep": "D2-05-02"}],
    "states": [{"key": "position", "value": 40.0}],
}


async def _cover_setup(hass, mqtt_transport):
    mqtt_transport.devices = [COVER]
    await configure_bridge(hass)
    await hass.async_block_till_done()
    cover = await wait_for_entity(hass, "cover", "AABB0011_AABB1122")
    entry = hass.config_entries.async_entries("opus_greennet")[0]
    return cover, entry.runtime_data.coordinator


def _dbm(mqtt_transport, value: str):
    mqtt_transport.receive("EnOcean/AABB0011/stream/device/AABB1122/dbm", value)


async def test_rssi_only_update_skips_unchanged_cover_write(
    hass: HomeAssistant, mqtt_transport, writes
):
    cover, coordinator = await _cover_setup(hass, mqtt_transport)
    # The first dispatch after a non-dispatch write (setup) is always written.
    _dbm(mqtt_transport, "-60")
    await hass.async_block_till_done()
    before = writes.get(cover, 0)
    for value in ("-61", "-62", "-63"):
        _dbm(mqtt_transport, value)
        await hass.async_block_till_done()
    assert writes.get(cover, 0) == before
    assert coordinator.devices["AABB1122"].dbm == -63
    assert hass.states.get(cover).attributes["current_position"] == 60


async def test_feature_change_is_still_written(
    hass: HomeAssistant, mqtt_transport, writes
):
    cover, _ = await _cover_setup(hass, mqtt_transport)
    assert hass.states.get(cover).attributes["supported_features"] & 128
    before = writes.get(cover, 0)
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/telegram/AABB1122/from",
        {"functions": [{"key": "rotationTime", "value": "0"}]},
    )
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()
    # One write from the dispatch; HA's own registry update may add another.
    assert writes[cover] > before
    assert not hass.states.get(cover).attributes["supported_features"] & 128


async def test_state_change_after_skipped_write_is_written(
    hass: HomeAssistant, mqtt_transport, writes
):
    cover, _ = await _cover_setup(hass, mqtt_transport)
    _dbm(mqtt_transport, "-70")
    await hass.async_block_till_done()
    mqtt_transport.receive(
        "EnOcean/AABB0011/stream/telegram/AABB1122/from",
        {"functions": [{"key": "position", "value": "90"}]},
    )
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()
    assert hass.states.get(cover).attributes["current_position"] == 10


async def test_availability_change_is_still_written(
    hass: HomeAssistant, mqtt_transport, writes
):
    cover, _ = await _cover_setup(hass, mqtt_transport)
    await mqtt_transport.set_connected(False)
    assert hass.states.get(cover).state == "unavailable"
    await mqtt_transport.set_connected(True)
    for _ in range(50):
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()
        if hass.states.get(cover).state != "unavailable":
            break
    assert hass.states.get(cover).state == "open"


async def test_identical_button_presses_still_fire_two_events(
    hass: HomeAssistant, mqtt_transport
):
    mqtt_transport.devices = [
        {"deviceId": "AABB3344", "friendlyId": "Rocker", "eeps": [{"eep": "F6-02-01"}]}
    ]
    await configure_bridge(hass)
    await hass.async_block_till_done()
    entity_id = await wait_for_entity(hass, "event", "AABB0011_AABB3344")
    fired = []
    from custom_components.opus_greennet.event import OpusGreenNetEvent

    original = OpusGreenNetEvent._trigger_event

    def counting(self, event_type, attributes=None):
        fired.append(event_type)
        return original(self, event_type, attributes)

    with patch.object(OpusGreenNetEvent, "_trigger_event", counting):
        for _ in range(2):
            mqtt_transport.receive(
                "EnOcean/AABB0011/stream/telegram/AABB3344/from",
                {"functions": [{"key": "buttonA0", "value": "pressed"}]},
            )
            await asyncio.sleep(0.3)
            await hass.async_block_till_done()
        _dbm_topic = "EnOcean/AABB0011/stream/device/AABB3344/dbm"
        mqtt_transport.receive(_dbm_topic, "-60")
        await hass.async_block_till_done()
    assert fired == ["buttonA0_pressed", "buttonA0_pressed"]
    assert hass.states.get(entity_id).attributes["event_type"] == "buttonA0_pressed"
