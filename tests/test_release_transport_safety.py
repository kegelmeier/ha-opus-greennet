"""Fault recovery checks for the large-installation transport release."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.mqtt_transport import MQTTRequestManager
from tests.test_coordinator_transport import UPTIME_RESPONSE
from tests.test_coordinator_transport import broker as broker
from tests.test_coordinator_transport import (
    connected_coordinator as connected_coordinator,
)


async def test_reconnect_finishes_interrupted_stream_discovery(
    broker, connected_coordinator, monkeypatch
):
    """Debouncing a reconnect must not abandon an incomplete startup."""
    broker.response = UPTIME_RESPONSE
    coord = connected_coordinator
    coord._started = True
    delay_stream_ack = True
    original_suback = broker.on_subscribe_done

    def subscription_done(hass, topic, qos, callback):
        if delay_stream_ack and topic.endswith("/stream/devices/#"):
            return lambda: None
        return original_suback(hass, topic, qos, callback)

    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.mqtt.async_on_subscribe_done",
        subscription_done,
    )
    try:
        coord._handle_connection_status(True)
        first_refresh = coord._refresh_task
        async with asyncio.timeout(1):
            while "EnOcean/AABB0011/stream/devices/#" not in coord._stream_topics:
                await asyncio.sleep(0)
        coord._handle_connection_status(False)
        await asyncio.gather(first_refresh, return_exceptions=True)

        # Reconnect inside the debounce window while phase two is incomplete.
        delay_stream_ack = False
        coord._handle_connection_status(True)
        await asyncio.wait_for(coord._refresh_task, 1)
        assert "EnOcean/AABB0011/stream/device/#" in coord._stream_topics
        assert "EnOcean/AABB0011/stream/telegram/#" in coord._stream_topics
        assert any(topic.endswith("/get/devices") for topic, _, _ in broker.published)
        assert coord.available is True
    finally:
        await coord.async_unload()
    broker.assert_clean()


async def test_disconnect_discards_commands_queued_for_other_devices(
    broker, connected_coordinator
):
    broker.auto_respond = False
    coord = connected_coordinator
    tasks = [asyncio.create_task(coord.async_turn_on(f"DEV{i}")) for i in range(3)]
    try:
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(broker.published) == 1
        coord._handle_connection_status(False)
        coord._gateway_available = True
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), 1
        )
        assert all(isinstance(result, HomeAssistantError) for result in results)
        assert len(broker.published) == 1
        assert not coord._requests._waiters
        assert not coord._requests._send_lock.locked()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    broker.assert_clean()


async def test_timeout_releases_global_slot_for_next_device(broker):
    broker.auto_respond = False
    manager = MQTTRequestManager(MagicMock())
    first = asyncio.create_task(
        manager.async_request("get/DEV1", "getAnswer/DEV1", "DEV1", timeout=0.02)
    )
    second = asyncio.create_task(
        manager.async_request("get/DEV2", "getAnswer/DEV2", "DEV2", timeout=0.1)
    )
    try:
        with pytest.raises(HomeAssistantError) as raised:
            await first
        assert raised.value.translation_key == "request_timeout"
        await asyncio.sleep(0)
        assert [topic for topic, _, _ in broker.published] == ["get/DEV1", "get/DEV2"]
        broker.receive("getAnswer/DEV2", {"header": {"httpStatus": 200}})
        await second
        assert not manager._send_lock.locked()
        assert not manager._waiters
    finally:
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
    broker.assert_clean()


async def test_persistent_answer_route_rejects_retained_ack(broker):
    broker.auto_respond = False
    manager = MQTTRequestManager(MagicMock())
    manager.async_add_route_pattern("getAnswer/+")
    task = asyncio.create_task(
        manager.async_request("get/DEV1", "getAnswer/DEV1", "DEV1", timeout=0.1)
    )
    try:
        await asyncio.sleep(0)
        for topic, retained in [("getAnswer/DEV1", True), ("getAnswer/DEV2", False)]:
            manager.async_route(
                SimpleNamespace(
                    topic=topic,
                    payload='{"header":{"httpStatus":200}}',
                    retain=retained,
                )
            )
        await asyncio.sleep(0)
        assert not task.done()
        manager.async_route(
            SimpleNamespace(
                topic="getAnswer/DEV1",
                payload='{"header":{"httpStatus":200}}',
                retain=False,
            )
        )
        await task
        assert not manager._routes
        assert not manager._waiters
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    broker.assert_clean()


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
