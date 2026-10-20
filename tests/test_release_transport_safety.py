"""Fault recovery checks for the large-installation transport release."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.api import OpusConnectionError
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import device_object
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until
from tests.test_coordinator_commands import bridge as bridge
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio
from tests.test_coordinator_commands import settle, short_timeout, state_path


@pytest.fixture(autouse=True)
def fast_retries():
    with patch(
        "custom_components.opus_greennet.coordinator.STREAM_BACKOFF_MAX",
        0.02,
    ):
        yield


async def test_outage_discards_commands_queued_for_any_device(
    radio, connected_coordinator
):
    """Commands queued behind an in-flight request are never sent after an outage."""
    radio.auto_respond = False
    coord = connected_coordinator
    tasks = [
        asyncio.create_task(coord.async_turn_on(device_id))
        for device_id in ("DEV0", "DEV0", "DEV1", "DEV2")
    ]
    try:
        await settle()
        assert len(radio.sent) == 1
        # The event stream failed; the in-flight request then fails as well.
        coord._set_gateway_available(False)
        radio.respond(OpusConnectionError("connection reset"))
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), 1
        )
        assert [result.translation_key for result in results] == [
            "gateway_unavailable"
        ] * 4
        assert len(radio.sent) == 1
        assert not coord._radio_queue.locked()
        assert coord._radio_queue.queue_depth == 0
        assert not coord._command_waiters
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_timeout_releases_radio_slot_for_next_device(
    gateway, bridge, monkeypatch
):
    short_timeout(monkeypatch, bridge, 0.1)
    gateway.delays[state_path("DEV1")] = 0.3
    first = asyncio.create_task(bridge.async_turn_on("DEV1"))
    await wait_until(lambda: gateway.requests_to("PUT", state_path("DEV1")))
    second = asyncio.create_task(bridge.async_turn_on("DEV2"))
    with pytest.raises(HomeAssistantError) as raised:
        await first
    assert raised.value.translation_key == "request_timeout"
    await second
    assert gateway.requests_to("PUT", state_path("DEV2")) == [
        {"state": {"functions": [{"key": "switch", "value": "on"}]}}
    ]
    assert not bridge._radio_queue.locked()
    assert bridge._radio_queue.queue_depth == 0


async def test_gateway_outage_cancels_cover_estimate_and_status_checks(hass, gateway):
    gateway.devices = [
        device_object(
            "COVER",
            "D2-05-00",
            states=[{"key": "position", "value": 100}],
            configuration={
                "parameters": [{"key": "verticalMovementTime", "value": 25}]
            },
        )
    ]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    entity = await wait_for_entity(hass, "cover", "AABB0011_COVER")
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": entity}, blocking=True
    )
    assert hass.states.get(entity).state == "opening"
    assert coordinator._cover_movements
    assert coordinator._pending_reconciliation_queries

    await gateway.stop()
    await wait_until(lambda: hass.states.get(entity).state == "unavailable")
    assert not coordinator._cover_movements
    assert not coordinator._pending_reconciliation_queries
    assert coordinator.devices["COVER"].channels[0].movement is None

    await gateway.start()
    await wait_until(lambda: hass.states.get(entity).state == "closed")
    # No status check was left behind to fire after the outage.
    assert not coordinator._pending_reconciliation_queries
    assert len(gateway.requests_to("PUT", "/devices/COVER/state")) == 1


@pytest.mark.parametrize("value", [None, "unknown", "notAvailable", 1])
def test_leak_sensor_does_not_invent_a_dry_reading(value):
    device = EnOceanDevice("LEAK1", "Leak detector", [{"eep": "F6-05-01"}])
    device.update_from_telegram(
        {"functions": [{"key": "liquidDetected", "value": value}]}
    )
    assert device.get_or_create_channel().liquid_detected is None


def test_leak_alarm_survives_invalid_reading_and_channel_rediscovery():
    device = EnOceanDevice("LEAK1", "Leak detector", [{"eep": "F6-05-01"}])
    device.update_from_telegram(
        {"functions": [{"key": "liquidDetected", "value": True}]}
    )
    device.update_from_telegram(
        {"functions": [{"key": "liquidDetected", "value": "unknown"}]}
    )
    assert device.get_or_create_channel().liquid_detected is True
