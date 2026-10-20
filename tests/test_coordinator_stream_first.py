"""Phase R5: stream-first start and fresh metadata of the REST coordinator.

Plan ``Implementations-Plan-Rest-API.md`` section 7.2 (E7) and findings B-8
(device list read twice at setup) and B-5 (the 30 min list replaced the
signal strength and last-seen time of a newer telegram).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.opus_greennet.api import (
    OpusAuthError,
    OpusConnectionError,
    OpusTimeoutError,
)
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import (
    FIRST_DEVICE_LIST_TIMEOUT,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import (
    configure_bridge,
    setup_bridge,
    wait_for_entity,
    wait_until,
)

MODULE = "custom_components.opus_greennet.coordinator"
SWITCH = "050BF714"
DEVICES = {"header": {"content": "devices"}, "devices": []}


def _switch(value: str = "off", **fields: object) -> dict:
    return device_object(
        SWITCH, "D2-01-01", states=[{"key": "switch", "value": value}], **fields
    )


# ── Stream first with a real HTTP gateway ─────────────────────────────────


async def test_setup_takes_state_from_the_stream(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """State comes from the stream's list; one GET /devices adds configuration."""
    gateway.devices = [_switch("on")]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    assert hass.states.get(entity_id).state == "on"
    await wait_until(lambda: len(gateway.requests_to("GET", "/devices")) == 1)
    assert len(gateway.requests_to("GET", "/devices/stream")) == 1
    coordinator = entry.runtime_data.coordinator
    assert coordinator.devices[SWITCH].last_update_source == "device list"
    assert coordinator.first_device_list_seconds is not None
    assert 0 <= coordinator.first_device_list_seconds < FIRST_DEVICE_LIST_TIMEOUT


async def test_each_start_reads_the_device_list_once(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    await wait_until(lambda: len(gateway.requests_to("GET", "/devices")) == 2)
    assert len(gateway.requests_to("GET", "/devices/stream")) >= 2


async def test_cover_movement_times_without_configuration_in_the_stream(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Phase 21 hardware finding: the stream list has no configuration block."""
    cover = device_object(
        "0507C89C",
        "D2-05-02",
        states=[{"key": "position", "value": 10}],
        configuration={
            "parameters": [
                {"key": "verticalMovementTime", "value": 25.42},
                {"key": "rotationTime", "value": 0.0},
            ]
        },
    )
    gateway.devices = [cover]
    gateway.stream_without_configuration = True
    entry = await setup_bridge(hass, gateway)
    channel = entry.runtime_data.coordinator.devices["0507C89C"].channels[0]
    await wait_until(lambda: channel.vertical_movement_time == 25.42)
    assert channel.rotation_time == 0.0
    # Live state stays with the stream; the list only added configuration.
    assert channel.position == 10
    # A reconnect snapshot without configuration keeps the movement times.
    await gateway.close_streams()
    await gateway.wait_for_stream()
    await hass.async_block_till_done()
    assert channel.vertical_movement_time == 25.42


async def test_setup_waits_for_a_late_device_list(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch("on")]
    gateway.snapshot_delay = 0.3
    entry = await setup_bridge(hass, gateway)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.first_device_list_seconds >= 0.3
    entity_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    assert hass.states.get(entity_id).state == "on"


async def test_setup_retries_without_a_device_list_in_time(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    gateway.snapshot_delay = 5
    with patch(f"{MODULE}.FIRST_DEVICE_LIST_TIMEOUT", 0.2):
        result = await configure_bridge(hass, gateway)
        await hass.async_block_till_done()
    entry = result["result"]
    assert entry.state is ConfigEntryState.SETUP_RETRY
    # No reauthentication: the gateway answered, it was only slow.
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert gateway.requests_to("GET", "/devices") == []


async def test_setup_retries_when_the_stream_fails(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    gateway.status["/devices/stream"] = 503
    result = await configure_bridge(hass, gateway)
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.state is ConfigEntryState.SETUP_RETRY
    # The first failure ends setup at once; no background retry is left.
    assert len(gateway.requests_to("GET", "/devices/stream")) == 1
    await asyncio.sleep(1.2)
    assert len(gateway.requests_to("GET", "/devices/stream")) == 1


async def test_rejected_stream_at_setup_starts_one_reauth(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    gateway.status["/devices/stream"] = 401
    result = await configure_bridge(hass, gateway)
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_diagnostics_report_the_first_device_list_time(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    result = await async_get_config_entry_diagnostics(hass, entry)
    seconds = result["gateway"]["first_device_list_seconds"]
    assert seconds == entry.runtime_data.coordinator.first_device_list_seconds


async def test_reconnect_starts_with_a_snapshot_and_no_button_event(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Every new stream is a full resynchronisation; snapshots fire no event."""
    rocker = device_object(
        "ROCKER01", "F6-02-01", states=[{"key": "buttonAI", "value": "pressed"}]
    )
    gateway.devices = [_switch("off"), rocker]
    await setup_bridge(hass, gateway)
    switch_id = await wait_for_entity(hass, "switch", f"AABB0011_{SWITCH}")
    event_id = await wait_for_entity(hass, "event", "AABB0011_ROCKER01")
    gateway.devices = [_switch("on"), rocker]
    await gateway.close_streams()
    await wait_until(lambda: hass.states.get(switch_id).state == "on")
    assert hass.states.get(event_id).state == "unknown"
    # A reconnect reads no extra device list; only the one after setup.
    assert len(gateway.requests_to("GET", "/devices")) == 1


# ── Setup sequence with a scripted stream (no network) ────────────────────


@pytest.fixture
async def coord() -> AsyncIterator[OpusGreenNetCoordinator]:
    """A coordinator with a real event loop for futures and mocked timers."""
    hass = MagicMock()
    hass.loop = asyncio.get_running_loop()
    hass.async_create_background_task = lambda coroutine, _name: asyncio.ensure_future(
        coroutine
    )
    coordinator = OpusGreenNetCoordinator(hass, MagicMock(), "AABB0011")
    coordinator.client.async_get = AsyncMock(
        side_effect=lambda path, **_: {
            "/system/info": {"systemInfo": {"eurid": "AABB0011"}},
            "/system/uptime": {"systemUptimeResponse": {"uptime": 10}},
            "/devices": {"devices": []},
        }[path]
    )
    with (
        patch(f"{MODULE}.async_dispatcher_send"),
        patch(f"{MODULE}.async_track_time_interval", return_value=MagicMock()),
        patch(f"{MODULE}.STREAM_BACKOFF_INITIAL", 0.01),
        patch(f"{MODULE}.STREAM_BACKOFF_MAX", 0.01),
    ):
        yield coordinator


def _stream(*documents: dict, error: Exception | None = None, hold: bool = False):
    async def stream() -> AsyncIterator[dict]:
        for document in documents:
            yield document
        if error is not None:
            raise error
        if hold:
            await asyncio.Event().wait()

    return stream


async def test_setup_applies_documents_before_the_list_and_then_resolves(
    coord,
) -> None:
    """A telegram ahead of the list is handled; setup waits for the list."""
    early = telegram(SWITCH, [{"key": "switch", "value": "on"}])
    snapshot = {"header": {"content": "devices"}, "devices": [_switch("off")]}
    coord.client.async_stream = _stream(early, snapshot, hold=True)
    coord._lookup_unknown_device = MagicMock()  # noqa: SLF001
    await coord.async_setup()
    assert coord.available
    assert coord.devices[SWITCH].channels[0].is_on is False
    coord._lookup_unknown_device.assert_called_once_with(SWITCH)  # noqa: SLF001
    await coord.async_unload()


async def test_setup_fails_when_the_stream_ends_before_its_list(coord) -> None:
    coord.client.async_stream = _stream()
    with pytest.raises(OpusConnectionError, match="before its device list"):
        await coord.async_setup()
    await coord.async_unload()


@pytest.mark.parametrize(
    "error", [OpusConnectionError("refused"), OpusTimeoutError("silent")]
)
async def test_setup_fails_with_the_first_stream_error(coord, error) -> None:
    coord.client.async_stream = _stream(error=error)
    with pytest.raises(type(error)):
        await coord.async_setup()
    await coord.async_unload()


async def test_auth_error_at_setup_is_left_to_home_assistant(coord) -> None:
    """Setup raises; the runtime reauth callback is not started a second time."""
    on_auth_failed = MagicMock()
    coord._on_auth_failed = on_auth_failed  # noqa: SLF001
    coord.client.async_stream = _stream(error=OpusAuthError("status 401"))
    with pytest.raises(OpusAuthError):
        await coord.async_setup()
    on_auth_failed.assert_not_called()
    await coord.async_unload()


async def test_setup_times_out_without_a_list(coord) -> None:
    coord.client.async_stream = _stream(hold=True)
    with (
        patch(f"{MODULE}.FIRST_DEVICE_LIST_TIMEOUT", 0.05),
        pytest.raises(OpusTimeoutError, match="no device list"),
    ):
        await coord.async_setup()
    assert coord.first_device_list_seconds is None
    await coord.async_unload()
    assert not coord._tasks  # noqa: SLF001


async def test_failures_after_setup_keep_the_normal_reconnect(coord) -> None:
    """Only setup waits for the first list; later errors only reconnect."""
    attempts = 0

    async def stream() -> AsyncIterator[dict]:
        nonlocal attempts
        attempts += 1
        yield DEVICES
        if attempts == 1:
            raise OpusConnectionError("reset")
        await asyncio.Event().wait()

    coord.client.async_stream = stream
    await coord.async_setup()
    for _ in range(100):
        if attempts >= 2:
            break
        await asyncio.sleep(0.01)
    assert attempts >= 2
    assert coord.available
    await coord.async_unload()


# ── B-5: the 30 min list never replaces newer telegram metadata ───────────


def _metadata_coord() -> OpusGreenNetCoordinator:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coordinator._gateway_available = True  # noqa: SLF001
    with patch(f"{MODULE}.async_dispatcher_send"):
        coordinator._apply_device_object(  # noqa: SLF001
            _switch(lastSeen="2026-10-20T10:00:00", dbm=-80), authoritative=True
        )
    return coordinator


def _report(dbm: int) -> dict:
    return {
        "deviceId": SWITCH,
        "direction": "from",
        "timestamp": "2026-10-20T12:00:00",
        "telegramInfo": {"dbm": dbm},
        "functions": [{"key": "switch", "value": "on"}],
    }


async def test_telegram_during_the_refresh_keeps_its_metadata() -> None:
    coord = _metadata_coord()
    stale = {"devices": [_switch(lastSeen="2026-10-20T11:00:00", dbm=-90)]}

    async def answer(_path: str, **_: object) -> dict:
        # The telegram arrives after the request started, before the answer.
        coord._handle_telegram(_report(-55))  # noqa: SLF001
        return stale

    coord.client.async_get = AsyncMock(side_effect=answer)
    with patch(f"{MODULE}.async_dispatcher_send"):
        await coord._async_refresh_device_list()  # noqa: SLF001
    device = coord.devices[SWITCH]
    assert (device.dbm, device.last_seen) == (-55, "2026-10-20T12:00:00")


async def test_refresh_after_the_telegram_updates_metadata() -> None:
    coord = _metadata_coord()
    with patch(f"{MODULE}.async_dispatcher_send"):
        coord._handle_telegram(_report(-55))  # noqa: SLF001
        coord.client.async_get = AsyncMock(
            return_value={"devices": [_switch(lastSeen="2026-10-20T13:00:00", dbm=-60)]}
        )
        await coord._async_refresh_device_list()  # noqa: SLF001
    device = coord.devices[SWITCH]
    assert (device.dbm, device.last_seen) == (-60, "2026-10-20T13:00:00")


async def test_refresh_without_telegrams_updates_metadata() -> None:
    coord = _metadata_coord()
    coord.client.async_get = AsyncMock(
        return_value={"devices": [_switch(lastSeen="2026-10-20T13:00:00", dbm=-60)]}
    )
    with patch(f"{MODULE}.async_dispatcher_send"):
        await coord._async_refresh_device_list()  # noqa: SLF001
    device = coord.devices[SWITCH]
    assert (device.dbm, device.last_seen) == (-60, "2026-10-20T13:00:00")


async def test_snapshot_metadata_is_not_limited_by_telegrams() -> None:
    """Stream snapshots are ordered with the telegrams; they always apply."""
    coord = _metadata_coord()
    with patch(f"{MODULE}.async_dispatcher_send"):
        coord._handle_telegram(_report(-55))  # noqa: SLF001
        coord._apply_device_list(  # noqa: SLF001
            {"devices": [_switch(lastSeen="2026-10-20T14:00:00", dbm=-70)]},
            authoritative=True,
        )
    device = coord.devices[SWITCH]
    assert (device.dbm, device.last_seen) == (-70, "2026-10-20T14:00:00")


async def test_forgotten_device_drops_its_telegram_time() -> None:
    coord = _metadata_coord()
    with patch(f"{MODULE}.async_dispatcher_send"):
        coord._handle_telegram(_report(-55))  # noqa: SLF001
        coord.async_forget_device(SWITCH)
    assert SWITCH not in coord._telegram_metadata_at  # noqa: SLF001
