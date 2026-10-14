"""Characterise command-driven state changes end to end through the entities.

These tests drive the real entity, coordinator, request manager and parsers
against the deterministic broker. They pin the wire payloads, the optimistic
post-ACK state, the protection against feedback that arrives while a command
awaits its acknowledgement, and the number of Home Assistant state writes.
They were written to pass unchanged before and after the phase 2 ownership
refactoring, so they serve as the before/after comparison.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.coordinator import SIGNAL_DEVICE_STATE_UPDATE
from custom_components.opus_greennet.cover import OpusGreenNetCover
from custom_components.opus_greennet.enocean_device import (
    EnOceanChannel,
    EnOceanDevice,
)
from custom_components.opus_greennet.light import OpusGreenNetLight
from custom_components.opus_greennet.switch import OpusGreenNetSwitch
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)
from tests.test_reconciliation_feedback import ANSWER_TOPIC, COMMAND_TOPIC
from tests.test_reconciliation_feedback import timers as timers

EAG_ID = "AABB0011"
STATE_SIGNAL = f"{SIGNAL_DEVICE_STATE_UPDATE}_{EAG_ID}_DEV1"
CHANNEL_FIELDS = ("is_on", "brightness", "position", "angle", "movement")


@pytest.fixture
def dispatched(monkeypatch):
    """Deliver coordinator state signals to the entities under test."""
    entities: list = []
    signals: list[str] = []

    def send(_hass, signal, *args):
        signals.append(signal)
        if signal == STATE_SIGNAL:
            for entity in entities:
                entity._handle_state_update(*args)

    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send", send
    )
    return SimpleNamespace(entities=entities, signals=signals)


@pytest.fixture
async def coord(connected_coordinator, timers, dispatched):
    try:
        yield connected_coordinator
    finally:
        await connected_coordinator.async_unload()


def add_device(coord, eep: str, channels: dict[int, dict] | None = None):
    device = EnOceanDevice("DEV1", "Device", [{"eep": eep}])
    for channel_id, values in (channels or {}).items():
        device.channels[channel_id] = EnOceanChannel(channel_id=channel_id, **values)
    coord.devices["DEV1"] = device
    return device


def add_entity(coord, dispatched, entity_class, channel_id: int = 0):
    entity = entity_class(
        coord, EAG_ID, "gateway", coord.devices["DEV1"], channel_id=channel_id
    )
    entity.async_write_ha_state = MagicMock()
    dispatched.entities.append(entity)
    return entity


def published_functions(broker) -> list[list[dict]]:
    return [
        json.loads(payload)["state"]["functions"]
        for topic, payload, _ in broker.published
        if topic == COMMAND_TOPIC
    ]


def channel_state(device, channel_id):
    channel = device.channels.get(channel_id)
    if channel is None:
        return None
    return {name: getattr(channel, name) for name in CHANNEL_FIELDS}


def query_delays(timers) -> list[float]:
    return [delay for delay, _, _ in timers if delay in (5, 20)]


def feedback(coord, functions):
    coord._handle_telegram_property_message(
        SimpleNamespace(
            topic=f"EnOcean/{EAG_ID}/stream/telegram/DEV1/from",
            payload=json.dumps({"functions": functions}),
            retain=False,
        )
    )


async def wait_for_publish(broker, count: int = 1) -> None:
    for _ in range(100):
        if len(published_functions(broker)) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError("command was not published")


SWITCH_SEED = {0: {"is_on": False}, 1: {"is_on": True}}
DIMMER_SEED = {0: {"is_on": False, "brightness": 0}, 1: {"is_on": True}}
COVER_SEED = {0: {"position": 50, "angle": 10}, 1: {"position": 40, "angle": 20}}

SUCCESS_CASES = [
    # Single-channel switch: no channel selector.
    (
        "switch-on",
        OpusGreenNetSwitch,
        "D2-01-01",
        SWITCH_SEED,
        0,
        "async_turn_on",
        {},
        [{"key": "switch", "value": "on"}],
        {"is_on": True},
    ),
    (
        "switch-off",
        OpusGreenNetSwitch,
        "D2-01-01",
        {0: {"is_on": True}},
        0,
        "async_turn_off",
        {},
        [{"key": "switch", "value": "off"}],
        {"is_on": False},
    ),
    (
        "switch-creates-missing-channel",
        OpusGreenNetSwitch,
        "D2-01-01",
        {},
        0,
        "async_turn_on",
        {},
        [{"key": "switch", "value": "on"}],
        {"is_on": True},
    ),
    # Multi-channel switch: channel zero is sent explicitly.
    (
        "multichannel-switch-channel-zero",
        OpusGreenNetSwitch,
        "D2-01-04",
        SWITCH_SEED,
        0,
        "async_turn_on",
        {},
        [{"key": "channel", "value": "0"}, {"key": "switch", "value": "on"}],
        {"is_on": True},
    ),
    (
        "multichannel-switch-channel-one",
        OpusGreenNetSwitch,
        "D2-01-04",
        SWITCH_SEED,
        1,
        "async_turn_off",
        {},
        [{"key": "channel", "value": "1"}, {"key": "switch", "value": "off"}],
        {"is_on": False},
    ),
    # Dimmers use dimValue for on/off.
    (
        "dimmer-on-default",
        OpusGreenNetLight,
        "D2-01-02",
        DIMMER_SEED,
        0,
        "async_turn_on",
        {},
        [{"key": "dimValue", "value": "100"}],
        {"is_on": True, "brightness": 100},
    ),
    (
        "dimmer-on-brightness",
        OpusGreenNetLight,
        "D2-01-02",
        DIMMER_SEED,
        0,
        "async_turn_on",
        {"brightness": 128},
        [{"key": "dimValue", "value": "50"}],
        {"is_on": True, "brightness": 50},
    ),
    (
        "dimmer-on-minimum-brightness",
        OpusGreenNetLight,
        "D2-01-02",
        DIMMER_SEED,
        0,
        "async_turn_on",
        {"brightness": 1},
        [{"key": "dimValue", "value": "1"}],
        {"is_on": True, "brightness": 1},
    ),
    (
        "dimmer-on-zero-brightness-turns-off",
        OpusGreenNetLight,
        "D2-01-02",
        {0: {"is_on": True, "brightness": 60}},
        0,
        "async_turn_on",
        {"brightness": 0},
        [{"key": "dimValue", "value": "0"}],
        {"is_on": False, "brightness": 0},
    ),
    (
        "dimmer-off",
        OpusGreenNetLight,
        "D2-01-02",
        {0: {"is_on": True, "brightness": 60}},
        0,
        "async_turn_off",
        {},
        [{"key": "dimValue", "value": "0"}],
        {"is_on": False, "brightness": 0},
    ),
    (
        "multichannel-dimmer-channel-zero",
        OpusGreenNetLight,
        "D2-01-06",
        DIMMER_SEED,
        0,
        "async_turn_on",
        {"brightness": 255},
        [{"key": "channel", "value": "0"}, {"key": "dimValue", "value": "100"}],
        {"is_on": True, "brightness": 100},
    ),
    # Non-dimmable light ignores the requested brightness.
    (
        "switching-light-on-with-brightness",
        OpusGreenNetLight,
        "A5-38-09",
        {0: {"is_on": False}},
        0,
        "async_turn_on",
        {"brightness": 128},
        [{"key": "switch", "value": "on"}],
        {"is_on": True, "brightness": None},
    ),
    (
        "switching-light-off",
        OpusGreenNetLight,
        "A5-38-09",
        {0: {"is_on": True}},
        0,
        "async_turn_off",
        {},
        [{"key": "switch", "value": "off"}],
        {"is_on": False, "brightness": None},
    ),
    # Covers: OPUS 0 = open, 100 = closed; tilt is not inverted.
    (
        "cover-open",
        OpusGreenNetCover,
        "D2-05-00",
        COVER_SEED,
        0,
        "async_open_cover",
        {},
        [{"key": "position", "value": "0"}],
        {"position": 0},
    ),
    (
        "cover-close",
        OpusGreenNetCover,
        "D2-05-00",
        COVER_SEED,
        0,
        "async_close_cover",
        {},
        [{"key": "position", "value": "100"}],
        {"position": 100},
    ),
    (
        "cover-position-inverted",
        OpusGreenNetCover,
        "D2-05-00",
        COVER_SEED,
        0,
        "async_set_cover_position",
        {"position": 30},
        [{"key": "position", "value": "70"}],
        {"position": 70},
    ),
    (
        "cover-tilt",
        OpusGreenNetCover,
        "D2-05-00",
        COVER_SEED,
        0,
        "async_set_cover_tilt_position",
        {"tilt_position": 40},
        [{"key": "angle", "value": "40"}],
        {"angle": 40},
    ),
    (
        "cover-position-channel-one",
        OpusGreenNetCover,
        "D2-05-00",
        COVER_SEED,
        1,
        "async_set_cover_position",
        {"position": 30},
        [{"key": "channel", "value": "1"}, {"key": "position", "value": "70"}],
        {"position": 70},
    ),
]


@pytest.mark.parametrize(
    "entity_class,eep,seed,channel_id,action,kwargs,functions,changes",
    [case[1:] for case in SUCCESS_CASES],
    ids=[case[0] for case in SUCCESS_CASES],
)
async def test_accepted_command_publishes_wire_values_and_estimates_once(
    coord,
    broker,
    timers,
    dispatched,
    entity_class,
    eep,
    seed,
    channel_id,
    action,
    kwargs,
    functions,
    changes,
):
    device = add_device(coord, eep, seed)
    entity = add_entity(coord, dispatched, entity_class, channel_id)
    others = {
        other: channel_state(device, other) for other in seed if other != channel_id
    }
    expected = channel_state(device, channel_id) or {
        name: None for name in CHANNEL_FIELDS
    }
    expected.update(changes)

    await getattr(entity, action)(**kwargs)

    assert published_functions(broker) == [functions]
    assert channel_state(device, channel_id) == expected
    assert {other: channel_state(device, other) for other in others} == others
    # Exactly one write by the commanding entity, no coordinator state dispatch.
    entity.async_write_ha_state.assert_called_once()
    assert dispatched.signals == []
    # Existing reconciliation schedule, no additional query.
    assert query_delays(timers) == [5, 20]
    assert len(broker.published) == 1
    broker.assert_clean()


@pytest.mark.parametrize("channel_id", [0, 1])
async def test_stop_sends_stop_payload_without_estimating_state(
    coord, broker, timers, dispatched, channel_id
):
    device = add_device(coord, "D2-05-00", COVER_SEED)
    entity = add_entity(coord, dispatched, OpusGreenNetCover, channel_id)
    before = {cid: channel_state(device, cid) for cid in COVER_SEED}

    await entity.async_stop_cover()

    expected = [{"key": "stop", "value": "true"}]
    if channel_id:
        expected.insert(0, {"key": "channel", "value": "1"})
    assert published_functions(broker) == [expected]
    assert {cid: channel_state(device, cid) for cid in COVER_SEED} == before
    entity.async_write_ha_state.assert_not_called()
    assert dispatched.signals == []
    assert query_delays(timers) == [5, 20]


FAILURE_CASES = [
    ("rejected", {"header": {"httpStatus": 400}}, "request_rejected"),
    ("malformed", {"wrong": "shape"}, "request_rejected"),
    ("timeout", None, "request_timeout"),
]
FAILING_ACTIONS = [
    (OpusGreenNetSwitch, "D2-01-04", SWITCH_SEED, "async_turn_on", {}),
    (OpusGreenNetLight, "D2-01-02", DIMMER_SEED, "async_turn_on", {}),
    (OpusGreenNetLight, "D2-01-02", DIMMER_SEED, "async_turn_off", {}),
    (
        OpusGreenNetCover,
        "D2-05-00",
        COVER_SEED,
        "async_set_cover_position",
        {"position": 30},
    ),
    (
        OpusGreenNetCover,
        "D2-05-00",
        COVER_SEED,
        "async_set_cover_tilt_position",
        {"tilt_position": 40},
    ),
]


@pytest.mark.parametrize(
    "response,translation_key",
    [case[1:] for case in FAILURE_CASES],
    ids=[case[0] for case in FAILURE_CASES],
)
@pytest.mark.parametrize("entity_class,eep,seed,action,kwargs", FAILING_ACTIONS)
async def test_failed_command_leaves_state_untouched(
    coord,
    broker,
    timers,
    dispatched,
    monkeypatch,
    response,
    translation_key,
    entity_class,
    eep,
    seed,
    action,
    kwargs,
):
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01
    )
    broker.response = response
    device = add_device(coord, eep, seed)
    entity = add_entity(coord, dispatched, entity_class)
    before = {cid: channel_state(device, cid) for cid in seed}

    with pytest.raises(HomeAssistantError) as raised:
        await getattr(entity, action)(**kwargs)

    assert raised.value.translation_key == translation_key
    assert len(published_functions(broker)) == 1  # no automatic retry
    assert {cid: channel_state(device, cid) for cid in seed} == before
    entity.async_write_ha_state.assert_not_called()
    assert query_delays(timers) == []
    assert coord.available is True  # a failed command never changes availability
    broker.assert_clean()


async def test_unavailable_gateway_rejects_without_publishing(
    coord, broker, timers, dispatched
):
    device = add_device(coord, "D2-01-01", {0: {"is_on": False}})
    entity = add_entity(coord, dispatched, OpusGreenNetSwitch)
    coord._gateway_available = False

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_turn_on()

    assert raised.value.translation_key == "gateway_unavailable"
    assert broker.published == []
    assert device.channels[0].is_on is False
    entity.async_write_ha_state.assert_not_called()
    assert timers == []


FEEDBACK_CASES = [
    (
        OpusGreenNetSwitch,
        "D2-01-01",
        {0: {"is_on": False}},
        "async_turn_on",
        {},
        [{"key": "switch", "value": "off"}],
        {"is_on": False},
        {"is_on": True},
    ),
    (
        OpusGreenNetLight,
        "D2-01-02",
        {0: {"is_on": False, "brightness": 0}},
        "async_turn_on",
        {"brightness": 255},
        [{"key": "dimValue", "value": "30"}],
        {"is_on": True, "brightness": 30},
        {"is_on": True, "brightness": 100},
    ),
    (
        OpusGreenNetCover,
        "D2-05-00",
        {0: {"position": 50, "angle": 10}},
        "async_set_cover_position",
        {"position": 30},
        [{"key": "position", "value": "55"}],
        {"position": 55},
        {"position": 70},
    ),
    (
        OpusGreenNetCover,
        "D2-05-00",
        {0: {"position": 50, "angle": 10}},
        "async_set_cover_tilt_position",
        {"tilt_position": 40},
        [{"key": "angle", "value": "15"}],
        {"angle": 15},
        {"angle": 40},
    ),
]


@pytest.mark.parametrize(
    "entity_class,eep,seed,action,kwargs,reported,reported_state,estimated_state",
    FEEDBACK_CASES,
)
async def test_feedback_during_ack_wait_wins_over_estimate(
    coord,
    broker,
    timers,
    dispatched,
    entity_class,
    eep,
    seed,
    action,
    kwargs,
    reported,
    reported_state,
    estimated_state,
):
    del estimated_state
    broker.auto_respond = False
    device = add_device(coord, eep, seed)
    entity = add_entity(coord, dispatched, entity_class)
    expected = channel_state(device, 0)
    expected.update(reported_state)

    task = asyncio.create_task(getattr(entity, action)(**kwargs))
    await wait_for_publish(broker)
    feedback(coord, reported)
    assert entity.async_write_ha_state.call_count == 1
    broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
    await task

    assert channel_state(device, 0) == expected
    # Only the feedback dispatch wrote state; the estimate was suppressed.
    entity.async_write_ha_state.assert_called_once()
    assert dispatched.signals == [STATE_SIGNAL]
    # Confirmed by feedback: no delayed status query was scheduled.
    assert query_delays(timers) == []
    broker.assert_clean()


@pytest.mark.parametrize(
    "entity_class,eep,seed,action,kwargs,reported,reported_state,estimated_state",
    FEEDBACK_CASES,
)
async def test_feedback_before_command_is_replaced_by_estimate(
    coord,
    broker,
    timers,
    dispatched,
    entity_class,
    eep,
    seed,
    action,
    kwargs,
    reported,
    reported_state,
    estimated_state,
):
    device = add_device(coord, eep, seed)
    entity = add_entity(coord, dispatched, entity_class)
    feedback(coord, reported)
    expected = channel_state(device, 0)
    assert {name: expected[name] for name in reported_state} == reported_state
    expected.update(estimated_state)

    await getattr(entity, action)(**kwargs)

    assert channel_state(device, 0) == expected
    assert entity.async_write_ha_state.call_count == 2
    assert dispatched.signals == [STATE_SIGNAL]
    assert query_delays(timers) == [5, 20]


async def test_other_channel_feedback_during_ack_keeps_own_estimate(
    coord, broker, timers, dispatched
):
    broker.auto_respond = False
    device = add_device(coord, "D2-01-04", SWITCH_SEED)
    entity = add_entity(coord, dispatched, OpusGreenNetSwitch, 0)
    sibling = add_entity(coord, dispatched, OpusGreenNetSwitch, 1)

    task = asyncio.create_task(entity.async_turn_on())
    await wait_for_publish(broker)
    feedback(
        coord, [{"key": "channel", "value": "1"}, {"key": "switch", "value": "off"}]
    )
    broker.receive(ANSWER_TOPIC, {"header": {"httpStatus": 200}})
    await task

    assert device.channels[0].is_on is True
    assert device.channels[1].is_on is False
    # Feedback reached both entities once; the estimate wrote only its own.
    assert entity.async_write_ha_state.call_count == 2
    sibling.async_write_ha_state.assert_called_once()
    assert query_delays(timers) == [5, 20]


async def test_cover_travel_estimate_suppresses_target_assignment(
    coord, broker, timers, dispatched, monkeypatch
):
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.monotonic", lambda: 100.0
    )
    device = add_device(
        coord, "D2-05-00", {0: {"position": 100, "vertical_movement_time": 20}}
    )
    entity = add_entity(coord, dispatched, OpusGreenNetCover)

    await entity.async_open_cover()

    channel = device.channels[0]
    assert published_functions(broker) == [[{"key": "position", "value": "0"}]]
    assert channel.position == 100  # last report kept while travelling
    assert channel.movement == "opening"
    assert entity.is_opening is True
    # One write from the coordinator's estimate dispatch, none from the entity.
    entity.async_write_ha_state.assert_called_once()
    assert dispatched.signals == [STATE_SIGNAL]
    assert [delay for delay, _, _ in timers] == [25.0, 35.0, 45.0]


@pytest.mark.parametrize(
    "module", ["entity", "light", "switch", "cover", "climate", "lock"]
)
def test_entity_layer_does_not_import_mqtt(module):
    """Entities delegate commands; they never reach the MQTT layer directly."""
    path = (
        Path(__file__).parents[1] / "custom_components" / "opus_greennet" / module
    ).with_suffix(".py")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    forbidden = {
        name
        for name in imported
        if "mqtt" in name.split(".") or name.startswith("mqtt_transport")
    }
    assert forbidden == set()


# Domain layer: command estimates -------------------------------------------


def _domain_device(eep: str = "D2-01-04") -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Device", [{"eep": eep}])


@pytest.mark.parametrize(
    "apply,value,changes",
    [
        ("apply_switch_estimate", True, {"is_on": True}),
        ("apply_switch_estimate", False, {"is_on": False}),
        ("apply_dim_estimate", 40, {"is_on": True, "brightness": 40}),
        ("apply_dim_estimate", 0, {"is_on": False, "brightness": 0}),
        ("apply_cover_position_estimate", 70, {"position": 70}),
        ("apply_cover_tilt_estimate", 15, {"angle": 15}),
    ],
)
def test_estimate_applies_to_unchanged_channel_without_new_revision(
    apply, value, changes
):
    device = _domain_device()
    channel = device.get_or_create_channel(1)
    channel.state_revision = 7
    other = device.get_or_create_channel(0)
    snapshot = device.snapshot_channel_state(1)
    expected = channel_state(device, 1)
    expected.update(changes)

    assert getattr(device, apply)(snapshot, value) is True

    assert device.channels[1] is channel
    assert channel_state(device, 1) == expected
    assert channel.state_revision == 7  # an estimate is not feedback
    assert channel_state(device, 0) == {name: None for name in CHANNEL_FIELDS}
    assert device.channels[0] is other


def test_estimate_creates_channel_missing_before_and_after_command():
    device = _domain_device()
    snapshot = device.snapshot_channel_state(1)
    assert snapshot.channel is None and snapshot.revision == 0

    assert device.apply_switch_estimate(snapshot, True) is True
    assert device.channels[1].is_on is True
    assert device.channels[1].state_revision == 0


def test_feedback_after_snapshot_suppresses_estimate(make_telegram):
    device = _domain_device()
    device.get_or_create_channel(1).is_on = False
    snapshot = device.snapshot_channel_state(1)
    device.update_from_telegram(
        make_telegram([{"key": "switch", "value": "off", "channel": 1}])
    )

    assert device.channel_state_unchanged(snapshot) is False
    assert device.apply_switch_estimate(snapshot, True) is False
    assert device.channels[1].is_on is False


def test_other_channel_feedback_keeps_snapshot_valid(make_telegram):
    device = _domain_device()
    device.get_or_create_channel(0)
    snapshot = device.snapshot_channel_state(0)
    device.update_from_telegram(
        make_telegram([{"key": "switch", "value": "on", "channel": 1}])
    )

    assert device.channel_state_unchanged(snapshot) is True


def test_channel_created_by_feedback_during_command_suppresses_estimate(
    make_telegram,
):
    device = _domain_device()
    snapshot = device.snapshot_channel_state(0)
    device.update_from_telegram(
        make_telegram([{"key": "switch", "value": "off", "channel": 0}])
    )

    assert device.apply_switch_estimate(snapshot, True) is False
    assert device.channels[0].is_on is False


def test_replaced_channel_object_suppresses_estimate():
    device = _domain_device()
    device.get_or_create_channel(0)
    snapshot = device.snapshot_channel_state(0)
    device.channels[0] = EnOceanChannel(channel_id=0)

    assert device.apply_cover_position_estimate(snapshot, 10) is False
    assert device.channels[0].position is None


def test_snapshot_survives_device_rediscovery_sharing_channels():
    """Rediscovery replaces the device object but keeps the channel mapping."""
    original = _domain_device()
    original.get_or_create_channel(0)
    snapshot = original.snapshot_channel_state(0)
    rediscovered = _domain_device()
    rediscovered.channels = original.channels

    assert rediscovered.apply_switch_estimate(snapshot, True) is True
    assert original.channels[0].is_on is True


def test_snapshot_is_immutable():
    snapshot = _domain_device().snapshot_channel_state(0)
    with pytest.raises(AttributeError):
        snapshot.revision = 1  # type: ignore[misc]
