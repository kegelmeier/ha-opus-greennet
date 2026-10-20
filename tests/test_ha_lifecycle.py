"""Integration behavior through Home Assistant's public managers.

Every test talks HTTP to the in-process fake gateway: entries are created by
the real config flow, entities by the real platforms and service calls go
through Home Assistant's service registry. Config flow details and the stream
mechanics are covered in ``test_config_flow.py`` and
``test_coordinator_stream.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.api import OpusTimeoutError
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

SWITCH = "AABB1122"
OTHER_SWITCH = "AABB3344"
SWITCH_UID = f"AABB0011_{SWITCH}"


@pytest.fixture(autouse=True)
def fast_retries() -> Iterator[None]:
    with patch(
        "custom_components.opus_greennet.coordinator.STREAM_BACKOFF_MAX",
        0.02,
    ):
        yield


def _switch(device_id: str = SWITCH, value: str = "off") -> dict:
    return device_object(
        device_id,
        "D2-01-00",
        friendly_id="Desk switch",
        states=[{"key": "switch", "value": value}],
    )


def _coordinator(entry: config_entries.ConfigEntry) -> OpusGreenNetCoordinator:
    return entry.runtime_data.coordinator


async def test_setup_creates_and_unloads_entry(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """The flow manager creates a real entry, gateway, services and platforms."""
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert dr.async_get(hass).async_get(entry.runtime_data.gateway_device_id)
    assert hass.services.has_service(DOMAIN, "get_device_configuration")
    entity_id = await wait_for_entity(hass, "switch", SWITCH_UID)
    assert hass.states.get(entity_id).state == "off"
    coordinator = _coordinator(entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED
    assert hass.states.get(entity_id).state == "unavailable"
    assert not coordinator.available
    assert not coordinator._tasks  # noqa: SLF001
    # The gateway notices the closed stream at its next write.
    await gateway.push({"header": {"content": "keepalive"}})
    await wait_until(lambda: gateway.stream_count == 0)
    requests = len(gateway.requests)
    await asyncio.sleep(0.05)
    assert len(gateway.requests) == requests


async def test_setup_accepts_gateway_info_without_optional_fields(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """A system info without version or EURID still loads the known gateway."""
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    gateway.responses["/system/info"] = {
        "header": {"httpStatus": 200, "content": "systemInfo"},
        "systemInfo": {},
    }
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert _coordinator(entry).gateway_info == {}
    gateway_device = dr.async_get(hass).async_get(entry.runtime_data.gateway_device_id)
    assert gateway_device is not None
    assert gateway_device.sw_version is None
    entity_id = await wait_for_entity(hass, "switch", SWITCH_UID)
    assert hass.states.get(entity_id).state == "off"


async def test_device_list_configuration_disables_tilt_for_roller_shutter(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Firmware configuration parameters identify a shutter without tilt support."""
    gateway.devices = [
        device_object(
            SWITCH,
            "D2-05-02",
            friendly_id="Living room shutter",
            configuration={"parameters": [{"key": "rotationTime", "value": 0.0}]},
            states=[
                {"key": "position", "value": 78.0},
                {"key": "angle", "value": "unknown"},
                {"key": "lockingMode", "value": "unblock"},
            ],
        )
    ]
    await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "cover", SWITCH_UID)
    state = hass.states.get(entity_id)
    assert state.state == "open"
    assert state.attributes["current_position"] == 22
    # open, close, set position and stop; no tilt features.
    assert state.attributes["supported_features"] == 15
    assert "current_tilt_position" not in state.attributes


@pytest.mark.parametrize("failure", ["unreachable", "stream_error"])
async def test_setup_retries_for_an_unusable_gateway(
    hass: HomeAssistant, gateway: FakeGateway, failure: str
) -> None:
    """A previously configured gateway recovers after a failed setup."""
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await gateway.close_streams()
    streams = len(gateway.requests_to("GET", "/devices/stream"))

    if failure == "unreachable":
        await gateway.stop()
    else:
        gateway.status["/devices/stream"] = 500
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
    # The failed attempt left no stream or timers behind: the stream-first
    # setup made at most its one attempt and no further one afterwards.
    attempts = len(gateway.requests_to("GET", "/devices/stream")) - streams
    assert attempts == (0 if failure == "unreachable" else 1)
    await asyncio.sleep(1.4)
    assert len(gateway.requests_to("GET", "/devices/stream")) == streams + attempts

    if failure == "unreachable":
        await gateway.start()
    else:
        del gateway.status["/devices/stream"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.LOADED
    await gateway.wait_for_stream()
    entity_id = await wait_for_entity(hass, "switch", SWITCH_UID)
    assert hass.states.get(entity_id).state == "off"


async def test_entity_service_and_gateway_outage(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """A discovered entity uses real HA services, registry and availability."""
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    registry = er.async_get(hass)
    entity_id = await wait_for_entity(hass, "switch", SWITCH_UID)
    assert hass.states.get(entity_id).state == "off"
    device = dr.async_get(hass).async_get(registry.async_get(entity_id).device_id)
    assert device.via_device_id == entry.runtime_data.gateway_device_id

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == "on"
    assert gateway.requests_to("PUT", f"/devices/{SWITCH}/state") == [
        {"state": {"functions": [{"key": "switch", "value": "on"}]}}
    ]

    await gateway.stop()
    await wait_until(lambda: hass.states.get(entity_id).state == "unavailable")
    with pytest.raises(HomeAssistantError) as error:
        await _coordinator(entry).async_turn_off(SWITCH)
    assert error.value.translation_key == "gateway_unavailable"

    # The gateway returns with the switch switched off locally meanwhile.
    gateway.devices = [_switch(value="off")]
    await gateway.start()
    await wait_until(lambda: hass.states.get(entity_id).state == "off")
    assert _coordinator(entry).available
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == "on"
    # The command during the outage was never sent.
    assert [
        body["state"]["functions"][0]["value"]
        for body in gateway.requests_to("PUT", f"/devices/{SWITCH}/state")
    ] == ["on", "on"]


@pytest.mark.parametrize(
    ("interruption", "error_key"),
    [("outage", "gateway_unavailable"), ("reload", "request_cancelled")],
)
async def test_pending_entity_command_is_cancelled_on_outage_or_reload(
    hass: HomeAssistant,
    gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
    interruption: str,
    error_key: str,
) -> None:
    """Waiting service calls fail instead of acting on a replaced connection.

    The command queued behind the in-flight one is never sent. The in-flight
    command's answer arrives after the interruption: after an outage the
    gateway did accept it, so it succeeds; after a reload it is discarded.
    """
    gateway.devices = [_switch(), _switch(OTHER_SWITCH)]
    entry = await setup_bridge(hass, gateway)
    first_id = await wait_for_entity(hass, "switch", SWITCH_UID)
    second_id = await wait_for_entity(hass, "switch", f"AABB0011_{OTHER_SWITCH}")
    coordinator = _coordinator(entry)
    release = asyncio.Event()
    in_flight = asyncio.Event()

    async def hold_commands(method: str, path: str, body: object) -> None:
        if method == "PUT":
            in_flight.set()
            await release.wait()

    gateway.on_request = hold_commands
    first = asyncio.create_task(
        hass.services.async_call(
            "switch", "turn_on", {"entity_id": first_id}, blocking=True
        )
    )
    await asyncio.wait_for(in_flight.wait(), 1)
    second = asyncio.create_task(
        hass.services.async_call(
            "switch", "turn_on", {"entity_id": second_id}, blocking=True
        )
    )
    stopping: asyncio.Task[None] | None = None
    try:
        await wait_until(lambda: coordinator._radio_queue.queue_depth == 1)  # noqa: SLF001
        if interruption == "outage":
            # The gateway stops listening; its in-flight answer is still sent.
            stopping = asyncio.create_task(gateway.stop())
            await wait_until(lambda: not coordinator.available)
        else:
            assert await hass.config_entries.async_reload(entry.entry_id)
            assert _coordinator(entry) is not coordinator
            assert not coordinator.available
        assert not second.done()

        release.set()
        failing = (second,) if interruption == "outage" else (first, second)
        if interruption == "outage":
            await asyncio.wait_for(first, 1)
        for pending in failing:
            with pytest.raises(HomeAssistantError) as error:
                await asyncio.wait_for(pending, 1)
            assert error.value.translation_key == error_key
        assert [path for method, path, _ in gateway.requests if method == "PUT"] == [
            f"/devices/{SWITCH}/state"
        ]
    finally:
        release.set()
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        if stopping is not None:
            await stopping

    if interruption == "outage":
        assert hass.states.get(second_id).state == "unavailable"
        gateway.on_request = None
        await gateway.start()
        await wait_until(lambda: hass.states.get(second_id).state == "off")
    else:
        await hass.async_block_till_done()
        assert entry.state is config_entries.ConfigEntryState.LOADED
        assert hass.states.get(second_id).state == "off"
    # Discarding the late answer does not touch removed entities.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("cause", ["stream_end", "outage", "gateway_restart"])
async def test_reconnect_resync_updates_existing_entity_state(
    hass: HomeAssistant, gateway: FakeGateway, cause: str
) -> None:
    """The device list of a new stream updates HA without a live telegram."""
    gateway.devices = [_switch(value="off")]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "switch", SWITCH_UID)
    entity_entry = er.async_get(hass).async_get(entity_id)
    assert hass.states.get(entity_id).state == "off"
    states: list[str] = []

    @callback
    def state_changed(event: Event) -> None:
        if event.data["entity_id"] == entity_id and event.data["new_state"]:
            states.append(event.data["new_state"].state)

    unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)
    try:
        gateway.devices = [_switch(value="on")]
        if cause == "stream_end":
            await gateway.close_streams()
        elif cause == "outage":
            await gateway.stop()
            await wait_until(lambda: hass.states.get(entity_id).state == "unavailable")
            await gateway.start()
        else:
            # A falling uptime reveals a gateway restart; the stream reopens.
            gateway.uptime = 5
            coordinator = _coordinator(entry)
            coordinator._async_health_tick(dt_util.utcnow())  # noqa: SLF001
            assert coordinator._probe_task is not None  # noqa: SLF001
            await coordinator._probe_task  # noqa: SLF001
        await wait_until(lambda: hass.states.get(entity_id).state == "on")
    finally:
        unsubscribe()

    expected = ["unavailable", "on"] if cause == "outage" else ["on"]
    assert states == expected
    # The existing entity was updated, not recreated.
    assert er.async_get(hass).async_get(entity_id) == entity_entry
    assert _coordinator(entry).available
    # Only the new stream stays open (the gateway notices at its next write).
    await gateway.push({"header": {"content": "keepalive"}})
    await wait_until(lambda: gateway.stream_count == 1)


async def test_unacknowledged_entity_command_reports_unknown_outcome(
    hass: HomeAssistant, gateway: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    """A gateway that times out a command reports an unknown outcome."""
    gateway.devices = [_switch()]
    entry = await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "switch", SWITCH_UID)
    client = _coordinator(entry).client
    with (
        patch.object(
            client, "async_put", side_effect=OpusTimeoutError("PUT timed out")
        ),
        caplog.at_level(logging.WARNING),
        pytest.raises(HomeAssistantError) as error,
    ):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )
    assert error.value.translation_key == "request_timeout"
    assert hass.states.get(entity_id).state == "off"
    assert caplog.text.count("did not acknowledge a command within the timeout") == 1
