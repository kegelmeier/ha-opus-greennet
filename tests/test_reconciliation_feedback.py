"""Only usable device feedback can cancel delayed command status checks."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)

COMMAND_TOPIC = "EnOcean/AABB0011/put/devices/DEV1/state"
ANSWER_TOPIC = "EnOcean/AABB0011/putAnswer/devices/DEV1/state"
MQTT_SHAPES = (
    "telegram_json",
    "telegram_flat",
    "device_flat",
    "devices_flat",
    "device_indexed",
    "devices_indexed",
)


@pytest.fixture
def timers(monkeypatch):
    """Capture real scheduled callbacks and their cancellation handles."""
    scheduled = []

    def call_later(hass, delay, callback):
        cancel = MagicMock()
        scheduled.append((delay, callback, cancel))
        return cancel

    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_call_later", call_later
    )
    return scheduled


@pytest.fixture
async def feedback_coordinator(connected_coordinator, timers):
    """Keep the production request manager, state parsers and timer callbacks."""
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Cover", [{"eep": "D2-05-02"}]
    )
    try:
        yield connected_coordinator
    finally:
        await connected_coordinator.async_unload()


def receive_feedback(coordinator, functions, *, shape="telegram_json"):
    """Send protocol messages through each supported live MQTT parser."""
    base = "EnOcean/AABB0011/stream"

    def message(topic, value):
        return SimpleNamespace(
            topic=topic,
            payload=value if isinstance(value, str) else json.dumps(value),
            retain=False,
        )

    if shape == "telegram_json":
        coordinator._handle_telegram_property_message(
            message(f"{base}/telegram/DEV1/from", {"functions": functions})
        )
        return
    if shape == "telegram_flat":
        handler = coordinator._handle_telegram_property_message
        prefix = f"{base}/telegram/DEV1"
        handler(message(f"{prefix}/direction", "from"))
        for index, function in enumerate(functions):
            for key, value in function.items():
                handler(message(f"{prefix}/functions/{index}/{key}", value))
        coordinator._finalize_telegram("DEV1")
        return

    stream, structure = shape.split("_")
    handler = (
        coordinator._handle_device_stream_message
        if stream == "device"
        else coordinator._handle_device_property_message
    )
    for index, function in enumerate(functions):
        if structure == "flat":
            handler(
                message(
                    f"{base}/{stream}/DEV1/states/{function['key']}",
                    function["value"],
                )
            )
        else:
            for key, value in function.items():
                handler(message(f"{base}/{stream}/DEV1/states/{index}/{key}", value))
    if stream == "device" or structure == "indexed":
        coordinator._finalize_device_stream("DEV1")


def query_timers(timers):
    """Exclude parser debounce callbacks from the scheduled status checks."""
    return [timer for timer in timers if timer[0] in (5, 20)]


async def fire_timer(coordinator, timer):
    """Run an uncancelled callback and its owned request task to completion."""
    _, callback, cancel = timer
    assert not cancel.called
    callback(None)
    if coordinator._tasks:
        await asyncio.gather(*coordinator._tasks)


def assert_status_queries(broker, count, *, channel=0):
    """Inspect published protocol requests rather than mocked helper calls."""
    queries = []
    for topic, payload, _ in broker.published:
        functions = json.loads(payload)["state"]["functions"]
        if {"key": "query", "value": "status"} in functions:
            assert topic == COMMAND_TOPIC
            expected = [{"key": "query", "value": "status"}]
            if channel:
                expected.insert(0, {"key": "channel", "value": str(channel)})
            assert functions == expected
            queries.append(functions)
    assert len(queries) == count


@pytest.mark.parametrize(
    "value",
    [
        "unknown",
        "invalid",
        "notAvailable",
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
        False,
        -1,
        101,
        50.5,
    ],
)
async def test_unusable_report_before_ack_and_between_checks_preserves_queries(
    broker, feedback_coordinator, timers, value
):
    """Interim or malformed positions cannot suppress either 5s or 20s checks."""
    coord = feedback_coordinator
    broker.auto_respond = False
    task = asyncio.create_task(coord.async_set_cover_position("DEV1", 77))
    try:
        await asyncio.sleep(0)
        assert len(broker.published) == 1
        assert not task.done()
        receive_feedback(coord, [{"key": "position", "value": value}])
        broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
        await task
        pending = query_timers(timers)
        assert [delay for delay, _, _ in pending] == [5, 20]

        broker.auto_respond = True
        await fire_timer(coord, pending[0])
        assert_status_queries(broker, 1)
        receive_feedback(coord, [{"key": "position", "value": value}])
        await fire_timer(coord, pending[1])
        assert_status_queries(broker, 2)
        assert len(query_timers(timers)) == 2
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("shape", MQTT_SHAPES)
async def test_every_live_mqtt_shape_requires_numeric_position_feedback(
    broker, feedback_coordinator, timers, shape
):
    """Both stream families and full/flattened telegrams obey the same rules."""
    coord = feedback_coordinator
    await coord.async_set_cover_position("DEV1", 77)
    pending = query_timers(timers)
    receive_feedback(coord, [{"key": "position", "value": "unknown"}], shape=shape)
    await fire_timer(coord, pending[0])
    assert_status_queries(broker, 1)
    receive_feedback(coord, [{"key": "position", "value": 77}], shape=shape)
    assert all(cancel.called for _, _, cancel in pending)
    assert coord.devices["DEV1"].channels[0].position == 77
    assert_status_queries(broker, 1)


@pytest.mark.parametrize(
    ("key", "command_value", "invalid_value"),
    [
        ("dimValue", 10, -1),
        ("dimValue", 10, 101),
        ("dimValue", 10, True),
        ("angle", 30, -1),
        ("angle", 30, 101),
        ("temperatureSetpoint", 21, -1),
        ("temperatureSetpoint", 21, 41),
        ("temperatureSetpoint", 21, True),
        ("switch", "on", "unknown"),
        ("switch", "on", True),
        ("heaterMode", "heating", "notAvailable"),
        ("heaterMode", "heating", True),
    ],
)
async def test_invalid_actuator_fields_cannot_cancel_checks(
    broker, feedback_coordinator, timers, key, command_value, invalid_value
):
    """Numeric bounds and explicit enum values apply to every command field."""
    coord = feedback_coordinator
    await coord.async_send_command("DEV1", [{"key": key, "value": command_value}])
    receive_feedback(coord, [{"key": key, "value": invalid_value}])
    pending = query_timers(timers)
    assert [delay for delay, _, _ in pending] == [5, 20]
    for timer in pending:
        await fire_timer(coord, timer)
    assert_status_queries(broker, 2)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("position", 0),
        ("position", 100),
        ("dimValue", 0),
        ("dimValue", 100),
        ("angle", 0),
        ("angle", 100),
        ("temperatureSetpoint", 0),
        ("temperatureSetpoint", 40),
        ("temperatureSetpoint", 20.5),
        ("switch", "off"),
        ("heaterMode", "autoOff"),
    ],
)
async def test_valid_actuator_feedback_before_ack_avoids_redundant_queries(
    broker, feedback_coordinator, timers, key, value
):
    """Usable reports, including zero and boundary values, confirm a pending PUT."""
    coord = feedback_coordinator
    broker.auto_respond = False
    task = asyncio.create_task(
        coord.async_send_command("DEV1", [{"key": key, "value": value}])
    )
    try:
        await asyncio.sleep(0)
        assert len(broker.published) == 1
        receive_feedback(coord, [{"key": key, "value": value}])
        broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
        await task
        assert query_timers(timers) == []
        assert_status_queries(broker, 0)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_feedback_confirms_only_its_own_channel_and_field(
    broker, feedback_coordinator, timers
):
    """An angle report cannot confirm position, nor a different channel's value."""
    coord = feedback_coordinator
    await coord.async_send_command(
        "DEV1",
        [
            {"key": "position", "value": 77, "channel": 0},
            {"key": "angle", "value": 30, "channel": 0},
            {"key": "position", "value": 66, "channel": 1},
        ],
    )
    zero, one = query_timers(timers)[:2], query_timers(timers)[2:]
    assert len(zero) == len(one) == 2
    receive_feedback(
        coord,
        [
            {"key": "position", "value": "unknown", "channel": 0},
            {"key": "angle", "value": 30, "channel": 0},
        ],
    )
    assert all(not cancel.called for _, _, cancel in [*zero, *one])
    receive_feedback(coord, [{"key": "position", "value": 66, "channel": 1}])
    assert all(cancel.called for _, _, cancel in one)
    assert all(not cancel.called for _, _, cancel in zero)
    await fire_timer(coord, zero[0])
    assert_status_queries(broker, 1)
    receive_feedback(coord, [{"key": "position", "value": 77, "channel": 0}])
    assert all(cancel.called for _, _, cancel in zero)


@pytest.mark.parametrize(
    "function",
    [
        {"key": "rotationTime", "value": 0},
        {"key": "localControl", "value": "on"},
        {"key": "lockingMode", "value": "unblock"},
        {"key": "stop", "value": "true"},
        {"key": "channel", "value": 0},
    ],
)
async def test_metadata_or_stop_receipt_does_not_confirm_position(
    broker, feedback_coordinator, timers, function
):
    """A received stop or configuration flag still leaves the position unknown."""
    coord = feedback_coordinator
    await coord.async_stop_cover("DEV1")
    receive_feedback(coord, [function])
    pending = query_timers(timers)
    assert [delay for delay, _, _ in pending] == [5, 20]
    for timer in pending:
        await fire_timer(coord, timer)
    assert_status_queries(broker, 2)


@pytest.mark.parametrize("rotation_time", [0, 1])
async def test_stop_waits_for_angle_only_when_target_channel_supports_tilt(
    broker, feedback_coordinator, timers, rotation_time
):
    """A shutter needs position; a tiltable channel also needs usable angle."""
    coord = feedback_coordinator
    device = coord.devices["DEV1"]
    device.get_or_create_channel(0).rotation_time = 1 - rotation_time
    device.get_or_create_channel(1).rotation_time = rotation_time
    await coord.async_stop_cover("DEV1", channel=1)
    pending = query_timers(timers)
    receive_feedback(
        coord,
        [
            {"key": "position", "value": 77, "channel": 1},
            {"key": "angle", "value": "unknown", "channel": 1},
        ],
    )
    if rotation_time == 0:
        assert all(cancel.called for _, _, cancel in pending)
        assert_status_queries(broker, 0)
    else:
        for timer in pending:
            await fire_timer(coord, timer)
        assert_status_queries(broker, 2, channel=1)
        receive_feedback(coord, [{"key": "angle", "value": 30, "channel": 1}])
        assert all(cancel.called for _, _, cancel in pending)


async def test_later_tilt_command_keeps_unconfirmed_position_checks(
    broker, feedback_coordinator, timers
):
    """Resetting timers for a second field must retain the first field's checks."""
    coord = feedback_coordinator
    await coord.async_set_cover_position("DEV1", 77)
    await coord.async_set_cover_tilt("DEV1", 30)
    previous, current = query_timers(timers)[:2], query_timers(timers)[2:]
    assert len(previous) == len(current) == 2
    assert all(cancel.called for _, _, cancel in previous)
    receive_feedback(
        coord,
        [{"key": "position", "value": "unknown"}, {"key": "angle", "value": 30}],
    )
    for timer in current:
        await fire_timer(coord, timer)
    assert_status_queries(broker, 2)
    receive_feedback(coord, [{"key": "position", "value": 77}])
    assert all(cancel.called for _, _, cancel in current)


async def test_first_queued_commands_feedback_cannot_confirm_second_command(
    broker, feedback_coordinator, timers
):
    """Each command begins its feedback window only when it is actually sent."""
    coord = feedback_coordinator
    broker.auto_respond = False
    first = asyncio.create_task(coord.async_set_cover_position("DEV1", 77, channel=1))
    second = asyncio.create_task(coord.async_set_cover_position("DEV1", 78, channel=1))
    try:
        await asyncio.sleep(0)
        assert len(broker.published) == 1
        receive_feedback(coord, [{"key": "position", "value": 77, "channel": 1}])
        broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
        await first
        await asyncio.sleep(0)
        assert len(broker.published) == 2
        assert not second.done()
        assert query_timers(timers) == []

        broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
        await second
        pending = query_timers(timers)
        assert [delay for delay, _, _ in pending] == [5, 20]
        broker.auto_respond = True
        for timer in pending:
            await fire_timer(coord, timer)
        assert_status_queries(broker, 2, channel=1)
    finally:
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
