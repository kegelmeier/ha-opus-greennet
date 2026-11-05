"""Estimated cover travel is separate from command echoes and confirmed state."""

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.coordinator import CoverMovement
from custom_components.opus_greennet.cover import OpusGreenNetCover
from tests.fake_gateway import device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio
from tests.test_reconciliation_feedback import (
    assert_status_queries,
    fire_timer,
    receive_feedback,
)
from tests.test_reconciliation_feedback import (
    feedback_coordinator as feedback_coordinator,
)
from tests.test_reconciliation_feedback import timers as timers


def command_echo(coord, functions):
    """Deliver an outbound command telegram (HomeKit, gateway logic, us)."""
    coord._handle_document(telegram("DEV1", functions, direction="to"))


@pytest.fixture
def motion(feedback_coordinator, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.monotonic", lambda: clock[0]
    )
    channel = feedback_coordinator.devices["DEV1"].get_or_create_channel()
    channel.vertical_movement_time = 25
    channel.position = 100
    return feedback_coordinator, channel, clock


def entity_for(coord):
    entity = OpusGreenNetCover(coord, "AABB0011", "gateway", coord.devices["DEV1"])
    entity.async_write_ha_state = MagicMock()
    return entity


async def test_full_travel_keeps_reported_position_until_fallback(motion, timers):
    coord, channel, _ = motion
    entity = entity_for(coord)
    await entity.async_open_cover()
    assert channel.position == 100
    assert entity.is_opening is True
    assert entity.state == "opening"
    assert [delay for delay, _, _ in timers] == [32, 42, 52]
    timers[0][1](None)
    assert entity.is_opening is False
    assert entity.state == "open"
    assert channel.position == 0
    assert coord._pending_reconciliation_fields["DEV1", 0] == {"position"}
    assert not timers[1][2].called
    assert not timers[2][2].called


async def test_repeated_target_keeps_reported_position_and_original_deadline(motion):
    coord, channel, clock = motion
    entity = entity_for(coord)
    await entity.async_open_cover()
    original = coord._cover_movements["DEV1", 0]
    clock[0] += 5
    await entity.async_open_cover()
    assert channel.position == 100
    assert entity.state == "opening"
    assert coord._cover_movements["DEV1", 0] is original
    assert original.ends_at == 132


@pytest.mark.parametrize(
    "start,target,direction,delays",
    [
        (None, 0, "opening", [32, 42, 52]),
        (None, 100, "closing", [32, 42, 52]),
        (80, 20, "opening", [20, 30, 40]),
        (20, 80, "closing", [20, 30, 40]),
    ],
)
async def test_partial_and_unknown_start_travel(
    motion, timers, start, target, direction, delays
):
    coord, channel, _ = motion
    channel.position = start
    await coord.async_set_cover_position("DEV1", target)
    assert channel.movement == direction
    assert channel.position == start
    assert [delay for delay, _, _ in timers] == delays


@pytest.mark.parametrize(
    "target,grace",
    [(0, 7), (100, 7), (50, 5), (20, 5), (80, 5)],
)
async def test_end_stop_runs_wait_longer_than_partial_runs(
    motion, timers, target, grace
):
    coord, channel, _ = motion
    channel.position = 60
    await coord.async_set_cover_position("DEV1", target)
    travel = abs(60 - target) / 100 * 25
    movement = coord._cover_movements["DEV1", 0]
    assert movement.ends_at == pytest.approx(100 + travel + grace)
    assert timers[0][0] == pytest.approx(travel + grace)


async def test_retarget_uses_elapsed_estimate_and_cancels_old_timer(motion, timers):
    coord, channel, clock = motion
    await coord.async_set_cover_position("DEV1", 0)
    old = timers[:]
    clock[0] += 12.5
    await coord.async_set_cover_position("DEV1", 100)
    assert channel.movement == "closing"
    assert all(cancel.called for _, _, cancel in old)
    assert [delay for delay, _, _ in timers[3:]] == [19.5, 29.5, 39.5]
    replacement = coord._cover_movements["DEV1", 0]
    old[0][1](None)
    assert coord._cover_movements["DEV1", 0] is replacement
    assert channel.movement == "closing"


async def test_late_outbound_echo_does_not_finish_or_extend_movement(motion, timers):
    coord, channel, clock = motion
    await coord.async_set_cover_position("DEV1", 0)
    movement = coord._cover_movements["DEV1", 0]
    clock[0] += 3
    command_echo(coord, [{"key": "position", "value": 0}])
    assert channel.position == 100
    assert coord._cover_movements["DEV1", 0] is movement
    assert movement.ends_at == 132
    assert [delay for delay, _, _ in timers[-2:]] == [39, 49]


async def test_numeric_feedback_ends_estimate_and_reconciliation(motion, timers):
    coord, channel, _ = motion
    await coord.async_set_cover_position("DEV1", 0)
    pending = timers[:]
    receive_feedback(coord, [{"key": "position", "value": 40}])
    assert channel.position == 40
    assert channel.movement is None
    assert not coord._cover_movements
    assert not coord._pending_reconciliation_queries
    assert all(cancel.called for _, _, cancel in pending)


@pytest.mark.parametrize(
    "value", ["unknown", "notAvailable", None, True, -1, 101, float("nan")]
)
async def test_invalid_feedback_does_not_end_estimate(motion, timers, value):
    coord, channel, _ = motion
    await coord.async_set_cover_position("DEV1", 0)
    receive_feedback(coord, [{"key": "position", "value": value}])
    assert channel.movement == "opening"
    assert not timers[0][2].called
    assert not timers[1][2].called


async def test_feedback_before_ack_prevents_new_delayed_queries(motion, radio, timers):
    coord, channel, _ = motion
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await radio.wait_sent(1)
    receive_feedback(coord, [{"key": "position", "value": 0}])
    radio.respond()
    await task
    assert channel.movement is None
    assert len(timers) == 1
    assert timers[0][2].called


async def test_stop_clears_motion_before_ack_and_preserves_stop_protocol(
    motion, radio, timers
):
    coord, channel, _ = motion
    await coord.async_set_cover_position("DEV1", 0)
    old = timers[:]
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_stop_cover("DEV1"))
    await radio.wait_sent(2)
    assert channel.movement is None
    assert channel.position == 100  # Stop does not invent a final position.
    assert all(cancel.called for _, _, cancel in old)
    assert radio.functions()[-1] == [{"key": "stop", "value": "true"}]
    radio.respond()
    await task
    assert [delay for delay, _, _ in timers[-2:]] == [5, 20]


async def test_rejected_command_does_not_clear_another_channels_motion(
    motion, radio, timers
):
    coord, channel, _ = motion
    other = coord.devices["DEV1"].get_or_create_channel(1)
    other.position = 100
    other.vertical_movement_time = 25
    await coord.async_set_cover_position("DEV1", 0, 1)
    radio.response = {"header": {"httpStatus": 400}}
    with pytest.raises(HomeAssistantError):
        await coord.async_set_cover_position("DEV1", 0, 0)
    assert channel.movement is None
    assert other.movement == "opening"
    assert ("DEV1", 0) not in coord._cover_movements
    assert ("DEV1", 1) in coord._cover_movements
    assert timers[-1][2].called


@pytest.mark.parametrize("operation", ["disconnect", "remove", "unload"])
async def test_cleanup_cancels_motion_and_queries(motion, timers, operation):
    coord, channel, _ = motion
    await coord.async_set_cover_position("DEV1", 0)
    if operation == "disconnect":
        coord._set_gateway_available(False)
    elif operation == "remove":
        coord.async_forget_device("DEV1")
    else:
        await coord.async_unload()
    assert not coord._cover_movements
    assert not coord._pending_reconciliation_queries
    assert channel.movement is None
    assert all(cancel.called for _, _, cancel in timers)


@pytest.mark.parametrize("configured", [None, 0])
async def test_unconfigured_covers_keep_existing_behavior(motion, timers, configured):
    coord, channel, _ = motion
    channel.vertical_movement_time = configured
    entity = entity_for(coord)
    await entity.async_open_cover()
    assert channel.position == 0
    assert entity.is_opening is None
    assert [delay for delay, _, _ in timers] == [5, 20]


@pytest.mark.parametrize(
    "value", [None, True, -1, float("nan"), float("inf"), "noChange"]
)
def test_invalid_travel_time_keeps_known_configuration(make_device, value):
    device = make_device("D2-05-00")
    channel = device.get_or_create_channel()
    channel.vertical_movement_time = 25
    device.update_from_telegram(
        {"functions": [{"key": "verticalMovementTime", "value": value}]}
    )
    assert channel.vertical_movement_time == 25


async def test_real_ha_cover_shows_motion_and_accepts_configuration_updates(
    hass, gateway
):
    def cover(travel: float) -> dict:
        return device_object(
            "COVER",
            "D2-05-00",
            states=[{"key": "position", "value": 100}],
            configuration={
                "parameters": [{"key": "verticalMovementTime", "value": travel}]
            },
        )

    gateway.devices = [cover(25)]
    entry = await setup_bridge(hass, gateway)
    entity = await wait_for_entity(hass, "cover", "AABB0011_COVER")
    channel = entry.runtime_data.coordinator.devices["COVER"].channels[0]
    assert channel.vertical_movement_time == 25
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": entity}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity).state == "opening"
    assert hass.states.get(entity).attributes["current_position"] == 0
    assert gateway.requests_to("PUT", "/devices/COVER/state") == [
        {"state": {"functions": [{"key": "position", "value": "0"}]}}
    ]
    await gateway.push(telegram("COVER", [{"key": "position", "value": 0}]))
    await wait_until(lambda: hass.states.get(entity).state == "open")
    # A changed movement time arrives with the next device list.
    gateway.devices = [{**cover(40), "states": [{"key": "position", "value": 0}]}]
    await gateway.close_streams()
    await wait_until(lambda: channel.vertical_movement_time == 40)
    assert hass.states.get(entity).state == "open"


async def test_retarget_cancels_old_queries_before_ack(motion, radio, timers):
    coord, channel, clock = motion
    await coord.async_set_cover_position("DEV1", 0)
    old = timers[:]
    clock[0] += 10
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 100))
    await radio.wait_sent(2)
    assert channel.movement == "closing"
    assert all(cancel.called for _, _, cancel in old)
    radio.respond()
    await task
    assert [delay for delay, _, _ in timers[-2:]] == [27, 37]


async def test_queued_target_starts_only_when_sent(motion, radio, timers):
    coord, channel, clock = motion
    radio.auto_respond = False
    first = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await radio.wait_sent(1)
    second = asyncio.create_task(coord.async_set_cover_position("DEV1", 100))
    await asyncio.sleep(0)
    assert channel.movement == "opening"
    assert len(radio.sent) == 1
    clock[0] += 10
    radio.respond()
    await first
    await radio.wait_sent(2)
    assert channel.movement == "closing"
    assert coord._cover_movements["DEV1", 0].started_at == 110
    radio.respond()
    await second


@pytest.mark.parametrize("failure", ["cancel", "timeout"])
async def test_failed_request_clears_estimate(motion, radio, timers, failure):
    coord, channel, _ = motion
    radio.timeout = 0.01
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await radio.wait_sent(1)
    assert channel.movement == "opening"
    if failure == "cancel":
        task.cancel()
    with pytest.raises((asyncio.CancelledError, HomeAssistantError)):
        await task
    assert channel.movement is None
    assert not coord._cover_movements
    assert not coord._pending_reconciliation_queries
    assert timers[0][2].called
    assert not coord._radio_queue.locked()
    assert not coord._command_waiters


async def test_feedback_and_native_commands_are_channel_scoped(motion, timers):
    coord, channel, _ = motion
    other = coord.devices["DEV1"].get_or_create_channel(1)
    other.position = 100
    other.vertical_movement_time = 40
    await coord.async_set_cover_position("DEV1", 0)
    command_echo(
        coord, [{"key": "channel", "value": 1}, {"key": "position", "value": 0}]
    )
    assert other.movement == "opening"
    assert other.position == 100
    assert [delay for delay, _, _ in timers[-3:]] == [47, 57, 67]
    receive_feedback(
        coord,
        [
            {"key": "channel", "value": 1},
            {"key": "position", "value": 0},
        ],
    )
    assert other.movement is None
    assert channel.movement == "opening"
    assert ("DEV1", 0) in coord._pending_reconciliation_queries
    assert ("DEV1", 1) not in coord._pending_reconciliation_queries


async def test_delayed_queries_are_sent_after_fallback(motion, radio, timers):
    coord, channel, clock = motion
    await coord.async_set_cover_position("DEV1", 0)
    movement, first, second = timers[:]
    clock[0] = 130
    movement[1](None)
    assert channel.movement is None
    assert_status_queries(radio, 0)
    clock[0] = 140
    await fire_timer(coord, first)
    assert_status_queries(radio, 1)
    clock[0] = 150
    await fire_timer(coord, second)
    assert_status_queries(radio, 2)


def test_configured_travel_is_channel_scoped_and_ignores_default_values(motion):
    coord, channel, _ = motion
    device = coord.devices["DEV1"]
    functions = protocol.configuration_state_functions(
        {
            "configuration": {
                "parameters": [
                    {"key": "verticalMovementTime", "defaultValue": 90},
                    {"key": "channel", "value": 1},
                    {"key": "verticalMovementTime", "value": "40"},
                    {"key": "channel", "value": 2},
                    {"key": "verticalMovementTime", "value": "12.5"},
                ]
            }
        }
    )
    device.update_from_telegram({"functions": functions})
    assert channel.vertical_movement_time == 25
    assert device.channels[1].vertical_movement_time == 40
    assert device.channels[2].vertical_movement_time == 12.5


@pytest.mark.parametrize("status", [200, 400])
async def test_short_travel_waits_for_ack_before_assuming_target(
    motion, radio, timers, status
):
    coord, channel, clock = motion
    channel.position = 1
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await radio.wait_sent(1)
    assert timers[0][0] == 7.25
    clock[0] += 8
    timers[0][1](None)
    assert channel.position == 1
    assert channel.movement == "opening"
    radio.respond({"header": {"httpStatus": status}})
    if status == 400:
        with pytest.raises(HomeAssistantError):
            await task
        assert channel.position == 1
        assert not coord._pending_reconciliation_queries
    else:
        await task
        assert channel.position == 0
        assert [delay for delay, _, _ in timers[-2:]] == [10, 20]
    assert channel.movement is None
    assert not coord._cover_movements


# ── A newer estimate must survive the end of an older request ──────────────


def _replace_estimate(coord, target):
    """Replace the running estimate as a newer command would."""
    device = coord.devices["DEV1"]
    movement = coord._start_cover_movement(device, 0, target)  # noqa: SLF001
    assert movement is not None
    return movement


@pytest.mark.parametrize("status", [201, 400])
async def test_older_request_leaves_the_newer_estimate_alone(
    motion, radio, timers, status
):
    coord, channel, _ = motion
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 0))
    await radio.wait_sent(1)
    newer = _replace_estimate(coord, 50)

    radio.respond({"header": {"httpStatus": status}})
    if status == 400:
        with pytest.raises(HomeAssistantError):
            await task
    else:
        await task

    assert coord._cover_movements["DEV1", 0] is newer


async def test_clearing_the_estimate_of_an_unknown_device_is_harmless(motion):
    coord, _, _ = motion
    cancel = MagicMock()
    coord._cover_movements["GONE", 0] = CoverMovement(None, 0, 100.0, 10.0, cancel)

    coord._clear_cover_movement("GONE", 0)

    assert ("GONE", 0) not in coord._cover_movements
    cancel.assert_called_once_with()


async def test_retarget_without_a_known_start_position(motion):
    coord, channel, clock = motion
    channel.position = None
    first = _replace_estimate(coord, 0)
    assert first.start_position is None
    clock[0] += 5

    second = _replace_estimate(coord, 100)

    assert second.target == 100
    assert coord._cover_movements["DEV1", 0] is second


async def test_a_finished_estimate_of_a_removed_device_is_dropped(motion):
    coord, _, _ = motion
    movement = _replace_estimate(coord, 0)
    del coord.devices["DEV1"]

    coord._finish_cover_movement("DEV1", 0, movement)

    assert ("DEV1", 0) not in coord._cover_movements
