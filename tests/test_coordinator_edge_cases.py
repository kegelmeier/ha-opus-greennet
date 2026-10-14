"""Defensive coordinator paths: foreign topics, malformed input and lifecycle.

Each test asserts the observable outcome (state, published topics, logged
message or raised error), not only that a line runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import coordinator as coordinator_module
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.test_coordinator_transport import UPTIME_RESPONSE, Broker

LOGGER = "custom_components.opus_greennet"


@pytest.fixture
def coord(monkeypatch):
    coordinator = OpusGreenNetCoordinator(MagicMock(), "AABB0011")
    monkeypatch.setattr(f"{coordinator_module.__name__}.async_call_later", MagicMock())
    return coordinator


@pytest.fixture
def broker(monkeypatch):
    broker = Broker()
    module = "custom_components.opus_greennet.mqtt_transport.mqtt"
    monkeypatch.setattr(f"{module}.is_connected", lambda hass: broker.connected)
    monkeypatch.setattr(f"{module}.async_subscribe", broker.subscribe)
    monkeypatch.setattr(f"{module}.async_on_subscribe_done", broker.on_subscribe_done)
    monkeypatch.setattr(f"{module}.async_publish", broker.publish)
    return broker


@pytest.fixture
def live(broker):
    hass = MagicMock()
    hass.async_create_task.side_effect = asyncio.create_task
    coordinator = OpusGreenNetCoordinator(hass, "AABB0011")
    coordinator._gateway_available = True
    return coordinator


def msg(topic: str, payload: object = "", *, retain: bool = False):
    if not isinstance(payload, str | bytes):
        payload = json.dumps(payload)
    return SimpleNamespace(topic=topic, payload=payload, retain=retain)


# ── Foreign or malformed topics are ignored ─────────────────────────────────


@pytest.mark.parametrize(
    ("handler", "topic"),
    [
        ("_handle_device_property_message", "EnOcean/AABB0011/other/DEV1/x"),
        ("_handle_device_property_message", "EnOcean/FFFF0000/stream/devices/D/x"),
        ("_handle_device_stream_message", "EnOcean/AABB0011/other/DEV1/x"),
        ("_handle_device_stream_message", "EnOcean/FFFF0000/stream/device/D/x"),
        ("_handle_telegram_property_message", "EnOcean/AABB0011/other"),
        (
            "_handle_telegram_property_message",
            "EnOcean/FFFF0000/stream/telegram/D/from",
        ),
        ("_handle_put_answer_state", "EnOcean/FFFF0000/putAnswer/devices/D/state"),
        ("_handle_put_answer_state", "EnOcean/AABB0011/putAnswer/other"),
    ],
)
def test_foreign_topics_change_nothing(coord, handler, topic) -> None:
    getattr(coord, handler)(msg(topic, '{"header": {"httpStatus": 500}}'))
    assert coord.devices == {}
    assert coord._device_data == {}
    assert coord._device_stream_data == {}
    assert coord._telegram_data == {}


def test_retained_put_answer_is_ignored(coord) -> None:
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-00"}])
    coord.devices["DEV1"] = device
    coord._handle_put_answer_state(
        msg(
            "EnOcean/AABB0011/putAnswer/devices/DEV1/state",
            {"header": {"httpStatus": 500}},
            retain=True,
        )
    )
    assert device.last_command_error is None


def test_rejection_for_unknown_device_is_only_logged(coord, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        coord._handle_put_answer_state(
            msg(
                "EnOcean/AABB0011/putAnswer/devices/GHOST/state",
                {"header": {"httpStatus": 500}},
            )
        )
    assert "OPUS command failed for GHOST" in caplog.text
    assert coord.devices == {}


@pytest.mark.parametrize(
    "handler", ["_handle_device_property_message", "_handle_device_stream_message"]
)
def test_handler_errors_are_logged_not_raised(coord, handler, caplog) -> None:
    topic = (
        "EnOcean/AABB0011/stream/devices/DEV1/x"
        if handler == "_handle_device_property_message"
        else "EnOcean/AABB0011/stream/device/DEV1/x"
    )
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        # A path deeper than the accepted bound raises inside the handler.
        getattr(coord, handler)(msg(topic + "/a" * 20, "1"))
    assert "Error handling device" in caplog.text


# ── Gateway system answers ───────────────────────────────────────────────────


def test_system_info_accepts_bytes_and_ignores_garbage(coord, caplog) -> None:
    coord._handle_system_info(msg("t", json.dumps({"model": "EAG"}).encode()))
    assert coord.gateway_info == {"model": "EAG"}
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        coord._handle_system_info(msg("t", "not json"))
    assert coord.gateway_info == {"model": "EAG"}
    assert "Could not parse system info" in caplog.text


def test_invalid_uptime_answer_keeps_previous_value(coord, caplog) -> None:
    coord.gateway_uptime = "10"
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        coord._handle_system_uptime(msg("t", {"header": {"httpStatus": 500}}))
    assert coord.gateway_uptime == "10"
    assert "Could not parse system uptime" in caplog.text


# ── Debug logging paths ──────────────────────────────────────────────────────


def test_debug_logging_truncates_raw_payloads(coord, caplog) -> None:
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-00"}])
    big = json.dumps({"functions": [{"key": "switch", "value": "on"}], "x": "y" * 5000})
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        coord._handle_telegram_property_message(
            msg("EnOcean/AABB0011/stream/telegram/DEV1/from", big.encode())
        )
    assert "truncated" in caplog.text
    assert "OPUS update latency dispatch" in caplog.text
    assert coord.devices["DEV1"].channels[0].is_on is True


def test_debug_logging_skips_retained_device_model_replay(coord, caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        coord._handle_device_property_message(
            msg("EnOcean/AABB0011/stream/devices/DEV1/friendlyId", "Desk", retain=True)
        )
    assert "Raw OPUS MQTT stream/devices" not in caplog.text
    assert coord._device_data["DEV1"]["friendlyId"] == "Desk"


def test_debug_logging_of_finalized_stream_update(coord, caplog) -> None:
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-00"}])
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for path, payload in (
            ("states/0/key", "switch"),
            ("states/0/value", "on"),
        ):
            coord._handle_device_stream_message(
                msg(f"EnOcean/AABB0011/stream/device/DEV1/{path}", payload)
            )
        coord._finalize_device_stream("DEV1")
    assert "OPUS update latency finalized" in caplog.text
    assert coord.devices["DEV1"].channels[0].is_on is True
    # A second finalisation without buffered data is a no-op.
    coord._finalize_device_stream("DEV1")


# ── Flattened property paths ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "path"),
    [
        ({}, "eeps/0/1/x"),
        ({}, "0/x"),
        ({"a": [1]}, "a/b/c"),
        ({"a": {"x": 1}}, "a/5"),
    ],
)
def test_conflicting_paths_raise_value_error(coord, data, path) -> None:
    with pytest.raises(ValueError, match="conflicts"):
        coord._set_nested_property(data, path, "1")


def test_named_state_update_appends_to_indexed_snapshot(coord) -> None:
    data = {"states": [{"key": "switch", "value": "off"}]}
    coord._set_nested_property(data, "states/switch", "on")
    coord._set_nested_property(data, "states/dimValue", "40")
    assert data["states"] == [
        {"key": "switch", "value": "on"},
        {"key": "dimValue", "value": 40},
    ]


def test_index_below_object_with_enough_entries_is_text_key(coord) -> None:
    data = {"a": {"x": 1, "y": 2}}
    coord._set_nested_property(data, "a/1", "3")
    assert data["a"] == {"x": 1, "y": 2, "1": 3}


def test_quoted_values_are_unwrapped_once(coord) -> None:
    assert coord._parse_value('"42"') == 42
    assert coord._parse_value('"broken') == '"broken'
    assert coord._parse_value("TRUE") is True


# ── Gateway health and lifecycle ─────────────────────────────────────────────


def test_connection_status_after_unload_is_ignored(coord) -> None:
    coord._unloaded = True
    coord._set_gateway_available = MagicMock()
    coord._handle_connection_status(False)
    coord._set_gateway_available.assert_not_called()


def test_reconnect_before_start_does_not_refresh(coord) -> None:
    coord._schedule_gateway_refresh = MagicMock()
    coord._handle_connection_status(True)
    coord._schedule_gateway_refresh.assert_not_called()


@pytest.mark.parametrize("payload", [b"1", "1"])
def test_bridge_online_after_start_refreshes(coord, payload) -> None:
    coord._started = True
    coord._schedule_gateway_refresh = MagicMock()
    coord._handle_bridge_status(msg("status", payload))
    assert coord._bridge_connected is True
    coord._schedule_gateway_refresh.assert_called_once()


def test_unknown_bridge_status_payload_is_ignored(coord) -> None:
    coord._handle_bridge_status(msg("status", "maybe"))
    assert coord._bridge_connected is None


def test_health_tick_skips_probe_while_bridge_reports_offline(
    coord, monkeypatch
) -> None:
    monkeypatch.setattr(coordinator_module.mqtt, "is_connected", lambda hass: True)
    coord._schedule_gateway_refresh = MagicMock()
    coord._bridge_connected = False
    coord._async_health_tick(None)
    coord._schedule_gateway_refresh.assert_not_called()
    coord._bridge_connected = True
    coord._async_health_tick(None)
    coord._schedule_gateway_refresh.assert_called_once_with(resync=True)


def test_refresh_is_not_scheduled_twice_or_after_unload(coord) -> None:
    coord._create_task = MagicMock()
    running = MagicMock()
    running.done.return_value = False
    running.cancelling.return_value = 0
    coord._refresh_task = running
    coord._schedule_gateway_refresh(resync=False)
    coord._create_task.assert_not_called()
    coord._refresh_task = None
    coord._unloaded = True
    coord._schedule_gateway_refresh(resync=False)
    coord._create_task.assert_not_called()


async def test_failed_background_probe_marks_gateway_unavailable(live) -> None:
    live._async_refresh_gateway = AsyncMock(side_effect=TimeoutError)
    live._requests.async_cancel_pending = MagicMock()
    await live._async_refresh_gateway_background(resync=False)
    assert live._gateway_available is False
    live._requests.async_cancel_pending.assert_called_once_with("gateway_unavailable")


async def test_refresh_rejects_bridge_reported_offline(live) -> None:
    live._bridge_connected = False
    with pytest.raises(HomeAssistantError) as error:
        await live._async_refresh_gateway(resync=False)
    assert error.value.translation_key == "gateway_unavailable"


async def test_bridge_going_offline_during_probe_is_not_reported_available(
    broker, live
) -> None:
    broker.auto_respond = False
    live._gateway_available = False
    live._resync_requested_at = 1.0
    live._stream_topics = [""] * 4
    refresh = asyncio.create_task(live._async_refresh_gateway(resync=False))
    for _ in range(10):
        await asyncio.sleep(0)
    live._bridge_connected = False
    broker.receive("EnOcean/AABB0011/getAnswer/config/system/uptime", UPTIME_RESPONSE)
    with pytest.raises(HomeAssistantError):
        await refresh
    assert live._gateway_available is False
    await live.async_unload()


async def test_optional_system_info_failure_does_not_fail_refresh(
    broker, live, monkeypatch, caplog
) -> None:
    broker.response = UPTIME_RESPONSE
    real_publish = broker.publish

    async def publish(hass, topic, payload, **kwargs):
        if topic.endswith("/get/config/system/info"):
            raise HomeAssistantError("broker rejected publish")
        await real_publish(hass, topic, payload, **kwargs)

    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.mqtt.async_publish", publish
    )
    monkeypatch.setattr(f"{coordinator_module.__name__}.async_call_later", MagicMock())
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await live._async_refresh_gateway(resync=True)
    assert live.available is True
    assert "Could not request optional OPUS system information" in caplog.text
    await live.async_unload()


async def test_device_requests_fail_fast_while_gateway_unavailable(live, broker):
    live._gateway_available = False
    with pytest.raises(HomeAssistantError) as error:
        await live.async_get_device_configuration("DEV1")
    assert error.value.translation_key == "gateway_unavailable"
    assert broker.published == []


async def test_profile_answer_is_cached_on_the_device(live, broker) -> None:
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-00"}])
    live.devices["DEV1"] = device
    broker.response = {"header": {"httpStatus": 200}, "profile": {"eep": "D2-01-00"}}
    await live.async_get_device_profile("DEV1")
    assert device.profile == broker.response
    await live.async_unload()
    broker.assert_clean()


def test_devices_by_type(coord) -> None:
    coord.devices = {
        "A": EnOceanDevice("A", "Switch", [{"eep": "D2-01-00"}]),
        "B": EnOceanDevice("B", "Cover", [{"eep": "D2-05-00"}]),
    }
    assert [device.device_id for device in coord.get_devices_by_type("cover")] == ["B"]


def test_forgetting_a_device_drops_its_feedback_and_movement(coord) -> None:
    device = EnOceanDevice("DEV1", "Cover", [{"eep": "D2-05-00"}])
    coord.devices["DEV1"] = device
    cancel = MagicMock()
    coord._feedback_revisions[("DEV1", 0)] = 3
    coord._feedback_revisions[("OTHER", 0)] = 1
    coord._pending_telegrams["DEV1"] = cancel
    coord.async_forget_device("DEV1")
    assert "DEV1" not in coord.devices
    assert ("DEV1", 0) not in coord._feedback_revisions
    assert coord._feedback_revisions == {("OTHER", 0): 1}
    cancel.assert_called_once()


# ── Static wire helpers with malformed containers ───────────────────────────


def test_indexed_changes_skip_non_object_cache_entries() -> None:
    changed = OpusGreenNetCoordinator._indexed_changed_functions(
        [None, {"value": "on"}],
        ["junk", {"key": "switch", "value": "on"}],
    )
    assert changed == [{"channel": 0, "key": "switch", "value": "on"}]


@pytest.mark.parametrize(
    "configuration",
    [
        None,
        {"parameters": "not-a-list"},
        {"parameters": ["junk", {"key": "rotationTime", "value": 2}]},
    ],
)
def test_configuration_functions_tolerate_malformed_parameters(configuration):
    functions = OpusGreenNetCoordinator._configuration_state_functions(
        {"configuration": configuration}
    )
    expected = (
        [{"channel": 0, "key": "rotationTime", "value": 2}]
        if isinstance(configuration, dict)
        and isinstance(configuration.get("parameters"), list)
        else []
    )
    assert functions == expected


def test_merge_replaces_non_object_list_entries() -> None:
    target = {"states": [{"key": "a"}, "old"]}
    OpusGreenNetCoordinator._merge_device_data(target, {"states": [None, "new"]})
    assert target["states"][1] == "new"


def test_text_eeps_in_indexed_snapshot_are_accepted(coord) -> None:
    coord._create_device_from_data(
        "DEV1",
        {"deviceId": "DEV1", "friendlyId": "Desk", "eeps": {"0": "D2-01-00"}},
    )
    assert coord.devices["DEV1"].eeps == [{"eep": "D2-01-00"}]
    assert coord.devices["DEV1"].entity_type == "switch"


def test_device_creation_errors_are_logged(coord, caplog, monkeypatch) -> None:
    monkeypatch.setattr(
        OpusGreenNetCoordinator,
        "_find_device_by_id",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        coord._create_device_from_data("DEV1", {"deviceId": "DEV1"})
    assert "Error creating device from data" in caplog.text
    assert coord.devices == {}


# ── Reconciliation queries ───────────────────────────────────────────────────


async def test_reconciliation_for_forgotten_device_is_skipped(coord) -> None:
    coord.async_query_device_status = AsyncMock()
    await coord._async_reconcile_status("GHOST", 0)
    coord.async_query_device_status.assert_not_awaited()


async def test_failed_reconciliation_is_logged_at_debug(coord, caplog) -> None:
    coord.devices["DEV1"] = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-00"}])
    coord.async_query_device_status = AsyncMock(
        side_effect=HomeAssistantError("timeout")
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await coord._async_reconcile_status("DEV1", 0)
    assert "Could not reconcile OPUS state for DEV1" in caplog.text
    assert coord._reconciling == set()


def test_reconciliation_timer_does_nothing_while_unavailable(coord, monkeypatch):
    timers = []
    monkeypatch.setattr(
        f"{coordinator_module.__name__}.async_call_later",
        lambda hass, delay, action: timers.append(action) or MagicMock(),
    )
    coord._create_task = MagicMock()
    coord._schedule_reconciliation_queries("DEV1", 0)
    assert len(timers) == 2
    timers[0](None)
    coord._create_task.assert_not_called()
    assert coord._reconcile_needed == set()


# ── Unload releases every pending timer ──────────────────────────────────────


async def test_unload_cancels_discovery_and_debounce_timers(coord) -> None:
    discovery, telegram, stream = MagicMock(), MagicMock(), MagicMock()
    coord._discovery_timer = discovery
    coord._pending_telegrams["DEV1"] = telegram
    coord._pending_device_streams["DEV1"] = stream
    await coord.async_unload()
    discovery.assert_called_once()
    telegram.assert_called()
    stream.assert_called()
    assert coord._discovery_timer is None
    assert coord._pending_telegrams == {}
    assert coord._pending_device_streams == {}
    assert coord.available is False


# ── Quality-scale rule log-when-unavailable ──────────────────────────────────


def test_unavailability_is_logged_once_and_recovery_once(coord, caplog) -> None:
    coord._gateway_available = True
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(3):
            coord._set_gateway_available(False)
        for _ in range(3):
            coord._set_gateway_available(True)
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("OPUS gateway AABB0011 is")
    ]
    assert messages == [
        "OPUS gateway AABB0011 is unavailable",
        "OPUS gateway AABB0011 is available",
    ]
    assert all(record.levelno == logging.INFO for record in caplog.records)
