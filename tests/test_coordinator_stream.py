"""Coordinator lifecycle: setup, event stream, availability and health."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

MODULE = "custom_components.opus_greennet.coordinator"
SWITCH = "050BF714"
COVER = "0507C89C"


@pytest.fixture(autouse=True)
def fast_retries():
    with patch(
        "custom_components.opus_greennet.coordinator.STREAM_BACKOFF_MAX",
        0.02,
    ):
        yield


def _switch(value: str = "off") -> dict:
    return device_object(SWITCH, "D2-01-01", states=[{"key": "switch", "value": value}])


def _coordinator(entry: ConfigEntry) -> OpusGreenNetCoordinator:
    return entry.runtime_data.coordinator


async def _probe(coordinator: OpusGreenNetCoordinator) -> None:
    """Run one health probe as the minute timer does."""
    coordinator._async_health_tick(dt_util.utcnow())  # noqa: SLF001
    assert coordinator._probe_task is not None  # noqa: SLF001
    await coordinator._probe_task  # noqa: SLF001


async def test_setup_reads_identity_uptime_and_devices(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch("on")]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    assert coordinator.available
    assert coordinator.gateway_uptime == "3600"
    assert coordinator.gateway_info["version"] == "OPUS-GW test"
    assert [request[:2] for request in gateway.requests[:4]] == [
        ("GET", "/system/info"),  # config flow
        ("GET", "/system/info"),
        ("GET", "/system/uptime"),
        ("GET", "/devices/stream"),
    ]
    # State comes from the stream's device list; one GET /devices follows for
    # the configuration the streamed list does not carry.
    await wait_until(lambda: len(gateway.requests_to("GET", "/devices")) == 1)
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    assert hass.states.get(entity_id).state == "on"


async def test_new_stream_resynchronises_state(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch("off")]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")

    # The stream ends; the reconnect starts with the current device list.
    gateway.devices = [_switch("on")]
    await gateway.close_streams()
    await wait_until(lambda: hass.states.get(entity_id).state == "on")
    assert _coordinator(entry).available
    assert gateway.stream_count == 1


async def test_device_list_snapshot_does_not_replay_button_presses(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    rocker = device_object(
        "ROCKER01",
        "F6-02-01",
        states=[{"key": "buttonAI", "value": "pressed"}],
    )
    gateway.devices = [rocker]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "event", "AABB0011_ROCKER01")
    assert hass.states.get(entity_id).state == "unknown"
    await gateway.close_streams()
    await gateway.wait_for_stream()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unknown"

    await gateway.push(telegram("ROCKER01", [{"key": "buttonAI", "value": "pressed"}]))
    await wait_until(lambda: hass.states.get(entity_id).state != "unknown")
    state = hass.states.get(entity_id)
    assert state.attributes["event_type"] == "buttonAI_pressed"
    assert _coordinator(entry).devices["ROCKER01"].channels[0].last_button is None


async def test_gateway_outage_marks_entities_unavailable_until_it_returns(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch("off")]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")

    await gateway.stop()
    await wait_until(lambda: hass.states.get(entity_id).state == "unavailable")
    assert not _coordinator(entry).available

    gateway.devices = [_switch("on")]
    await gateway.start()
    await wait_until(lambda: hass.states.get(entity_id).state == "on")
    assert _coordinator(entry).available


async def test_stream_auth_failure_starts_reauth_once(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    gateway.accept_auth = False
    await gateway.close_streams()
    await wait_until(lambda: not _coordinator(entry).available)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    # No further stream attempts with rejected credentials.
    attempts = len(gateway.requests_to("GET", "/devices/stream"))
    await _probe(_coordinator(entry))
    assert len(gateway.requests_to("GET", "/devices/stream")) == attempts
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1


async def test_health_probe_failure_restarts_stream(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    gateway.status["/system/uptime"] = 500
    streams = len(gateway.requests_to("GET", "/devices/stream"))
    await _probe(coordinator)
    assert not coordinator.available
    # The stream was replaced; the new one makes the gateway available again.
    await wait_until(
        lambda: len(gateway.requests_to("GET", "/devices/stream")) > streams
    )
    await wait_until(lambda: coordinator.available)

    # A later successful probe keeps the gateway available.
    del gateway.status["/system/uptime"]
    await _probe(coordinator)
    assert coordinator.available


async def test_health_probe_restores_availability(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    coordinator._set_gateway_available(False)  # noqa: SLF001
    await _probe(coordinator)
    assert coordinator.available


async def test_gateway_restart_reopens_stream(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    streams = len(gateway.requests_to("GET", "/devices/stream"))
    gateway.uptime = 5
    await _probe(_coordinator(entry))
    await wait_until(
        lambda: len(gateway.requests_to("GET", "/devices/stream")) > streams
    )
    assert _coordinator(entry).gateway_uptime == "5"


async def test_health_probe_auth_failure_starts_reauth(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    gateway.status["/system/uptime"] = 401
    await _probe(_coordinator(entry))
    assert not _coordinator(entry).available
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_periodic_device_list_refresh_updates_metadata_only(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    cover = device_object(
        COVER,
        "D2-05-02",
        states=[{"key": "position", "value": 10}],
        configuration={"parameters": [{"key": "verticalMovementTime", "value": 30.0}]},
        dbm=-70,
    )
    gateway.devices = [cover]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    channel = coordinator.devices[COVER].channels[0]
    assert (channel.position, channel.vertical_movement_time) == (10, 30.0)

    gateway.devices = [
        {
            **cover,
            "states": [{"key": "position", "value": 90}],
            "configuration": {
                "parameters": [{"key": "verticalMovementTime", "value": 45.0}]
            },
            "dbm": None,
            "batteryLevel": "80%",
        },
        device_object("NEWDEV01", "D2-01-01"),
    ]
    coordinator._async_refresh_tick(dt_util.utcnow())  # noqa: SLF001
    assert coordinator._refresh_task is not None  # noqa: SLF001
    await coordinator._refresh_task  # noqa: SLF001
    channel = coordinator.devices[COVER].channels[0]
    # Live state stays with the stream; configuration and battery refresh.
    assert channel.position == 10
    assert channel.vertical_movement_time == 45.0
    assert channel.battery_level == 80
    assert coordinator.devices[COVER].dbm == -70
    await wait_for_entity(hass, "switch", "AABB0011_NEWDEV01")


async def test_device_list_refresh_failures_are_ignored(
    hass: HomeAssistant, gateway: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    gateway.status["/devices"] = 500
    with caplog.at_level(logging.DEBUG):
        await coordinator._async_refresh_device_list()  # noqa: SLF001
    assert "Could not refresh the OPUS device list" in caplog.text
    coordinator._set_gateway_available(False)  # noqa: SLF001
    requests = len(gateway.requests)
    await coordinator._async_refresh_device_list()  # noqa: SLF001
    assert len(gateway.requests) == requests


async def test_unknown_device_is_looked_up_once(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = []
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    report = telegram("LATE0001", [{"key": "switch", "value": "on"}])

    await gateway.push(report)
    await hass.async_block_till_done()
    await gateway.push(report)
    await hass.async_block_till_done()
    assert len(gateway.requests_to("GET", "/devices/LATE0001")) == 1
    assert "LATE0001" not in coordinator.devices

    # The device appears on the gateway later; a new lookup finds it after
    # the retry interval.
    gateway.devices = [
        device_object("LATE0001", "D2-01-01", states=[{"key": "switch", "value": "on"}])
    ]
    with patch(
        "custom_components.opus_greennet.coordinator.UNKNOWN_DEVICE_RETRY_SECONDS", 0
    ):
        await gateway.push(report)
        await hass.async_block_till_done()
    entity_id = await wait_for_entity(hass, "switch", "AABB0011_LATE0001")
    assert hass.states.get(entity_id).state == "on"


async def test_stream_document_types(
    hass: HomeAssistant, gateway: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    gateway.devices = [_switch("off")]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")

    with caplog.at_level(logging.DEBUG):
        await gateway.push({"header": {"content": "systemStatus"}})
        await gateway.push({"header": {"content": "telegram"}, "telegram": []})
        await gateway.push(
            {"header": {"content": "telegram"}, "telegram": {"deviceId": SWITCH}}
        )
        await gateway.push(telegram(SWITCH, [{"key": "switch"}]))
        await gateway.push(
            {
                "header": {"content": "device"},
                "device": _switch("on"),
            }
        )
    await wait_until(lambda: hass.states.get(entity_id).state == "on")
    assert "Ignoring OPUS stream document of type 'systemStatus'" in caplog.text
    assert coordinator.available


async def test_unexpected_error_reconnects_the_stream(
    hass: HomeAssistant, gateway: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    streams = len(gateway.requests_to("GET", "/devices/stream"))
    with patch.object(
        coordinator, "_handle_telegram", side_effect=RuntimeError("boom")
    ):
        await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "on"}]))
        await wait_until(
            lambda: len(gateway.requests_to("GET", "/devices/stream")) > streams
        )
    assert "Unexpected error in the OPUS event stream" in caplog.text
    await wait_until(lambda: coordinator.available)


async def test_unload_stops_stream_and_timers(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not coordinator.available
    assert not coordinator._tasks  # noqa: SLF001
    # The server notices the closed connection when it writes next.
    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "on"}]))
    await wait_until(lambda: gateway.stream_count == 0)
    assert not coordinator._timers  # noqa: SLF001


async def test_gateway_identity_change_is_rejected(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    gateway.eurid = "11223344"
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


# ── Phase R4: stream lifecycle ─────────────────────────────────────────────


async def test_gateway_closing_every_stream_after_snapshot_is_no_tight_loop(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Finding B-2 with a real HTTP server: bounded attempts, stays available."""
    gateway.devices = [_switch("on")]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    with (
        patch(f"{MODULE}.STREAM_BACKOFF_INITIAL", 0.05),
        patch(f"{MODULE}.STREAM_BACKOFF_MAX", 0.2),
        patch(f"{MODULE}.STREAM_BACKOFF_JITTER", 0.0),
    ):
        gateway.close_after_snapshot = True
        await gateway.close_streams()
        before = len(gateway.requests_to("GET", "/devices/stream"))
        await asyncio.sleep(0.6)
        attempts = len(gateway.requests_to("GET", "/devices/stream")) - before
    # Pauses 0.05, 0.1, 0.2, 0.2, ...: at most five attempts in 0.6 s (one
    # more as tolerance). Before R4 every attempt followed without a pause.
    assert 1 <= attempts <= 6
    assert coordinator.available
    assert hass.states.get(entity_id).state == "on"


async def test_reload_leaves_exactly_one_stream_reader(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    old = _coordinator(entry)
    await gateway.wait_for_stream()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new = _coordinator(entry)
    assert new is not old
    assert not old._tasks  # noqa: SLF001
    # The server notices the closed connection of the old reader when it
    # writes next.
    await wait_until(lambda: gateway.stream_count >= 1)
    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "on"}]))
    await wait_until(lambda: gateway.stream_count == 1)
    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "off"}]))
    assert gateway.stream_count == 1
    assert new.available


async def test_stream_replacement_leaves_exactly_one_stream_reader(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    coordinator = _coordinator(entry)
    await gateway.wait_for_stream()
    streams = len(gateway.requests_to("GET", "/devices/stream"))
    gateway.uptime = 5  # gateway restart seen by the health probe
    await _probe(coordinator)
    await wait_until(
        lambda: len(gateway.requests_to("GET", "/devices/stream")) > streams
    )
    await wait_until(lambda: coordinator._stream_connected)  # noqa: SLF001
    await gateway.push(telegram(SWITCH, [{"key": "switch", "value": "on"}]))
    await wait_until(lambda: gateway.stream_count == 1)
    stream_tasks = [
        task
        for task in coordinator._tasks  # noqa: SLF001
        if task.get_name().endswith(" stream")
    ]
    assert len(stream_tasks) == 1
    assert coordinator.available
