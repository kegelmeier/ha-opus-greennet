"""Edge cases of the MQTT request transport and its priority lock."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import mqtt_transport
from custom_components.opus_greennet.mqtt_transport import (
    MQTTRequestManager,
    PriorityLock,
    async_probe_gateway,
    topic_matches,
)
from tests.test_coordinator_transport import Broker

TOPIC = "EnOcean/AABB0011/get/devices/DEV1/profile"
ANSWER = "EnOcean/AABB0011/getAnswer/devices/DEV1/profile"


@pytest.fixture
def broker(monkeypatch):
    broker = Broker()
    module = "custom_components.opus_greennet.mqtt_transport.mqtt"
    monkeypatch.setattr(f"{module}.is_connected", lambda hass: broker.connected)
    monkeypatch.setattr(f"{module}.async_subscribe", broker.subscribe)
    monkeypatch.setattr(f"{module}.async_on_subscribe_done", broker.on_subscribe_done)
    monkeypatch.setattr(f"{module}.async_publish", broker.publish)
    return broker


@pytest.mark.parametrize(
    ("pattern", "topic", "expected"),
    [
        ("a/+/c", "a/b/c", True),
        ("a/#", "a/b/c", True),
        ("a/b/c", "a/b", False),
        ("a/b", "a/x", False),
        ("a/b", "a/b/c", False),
    ],
)
def test_topic_matches(pattern, topic, expected) -> None:
    assert topic_matches(pattern, topic) is expected


async def test_priority_lock_cancelled_waiter_leaves_queue() -> None:
    lock = PriorityLock()
    await lock.acquire()
    waiter = asyncio.create_task(lock.acquire(5))
    await asyncio.sleep(0)
    assert len(lock._waiters) == 1
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert lock._waiters == []
    lock.release()
    assert lock.locked() is False


async def test_priority_lock_cancellation_after_handover_releases() -> None:
    """A waiter cancelled right after receiving ownership must pass it on."""
    lock = PriorityLock()
    await lock.acquire()
    first = asyncio.create_task(lock.acquire(1))
    second = asyncio.create_task(lock.acquire(2))
    await asyncio.sleep(0)
    lock.release()  # hands ownership to ``first``
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, 1)
    assert lock.locked() is True
    lock.release()
    assert lock.locked() is False


async def test_priority_lock_skips_already_cancelled_waiters() -> None:
    lock = PriorityLock()
    await lock.acquire()
    stale: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    stale.cancel()
    lock._waiters.append((0, -1, stale))
    lock.release()
    assert lock.locked() is False


async def test_request_fails_when_mqtt_disconnects_before_publish(broker) -> None:
    manager = MQTTRequestManager(MagicMock())
    broker.connected = False
    with pytest.raises(HomeAssistantError) as error:
        await manager.async_request(TOPIC, ANSWER, "DEV1")
    assert error.value.translation_key == "mqtt_unavailable"
    assert broker.published == []
    broker.assert_clean()


async def test_request_fails_when_gateway_is_unavailable(broker) -> None:
    manager = MQTTRequestManager(MagicMock())
    with pytest.raises(HomeAssistantError) as error:
        await manager.async_request(TOPIC, ANSWER, "DEV1", is_available=lambda: False)
    assert error.value.translation_key == "gateway_unavailable"
    assert broker.published == []


async def test_response_after_device_removal_is_discarded(broker) -> None:
    """An answer that arrives after the child was forgotten is not returned."""
    manager = MQTTRequestManager(MagicMock())
    broker.auto_respond = False
    request = asyncio.create_task(manager.async_request(TOPIC, ANSWER, "DEV1"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert broker.published
    manager._device_generations["DEV1"] = 1  # removal without failing waiters
    broker.receive(ANSWER, {"header": {"httpStatus": 200}})
    with pytest.raises(HomeAssistantError) as error:
        await request
    assert error.value.translation_key == "request_cancelled"
    broker.assert_clean()


async def test_minimum_send_interval_is_respected(broker, monkeypatch) -> None:
    monkeypatch.setattr(mqtt_transport, "MIN_SEND_INTERVAL", 0.05)
    manager = MQTTRequestManager(MagicMock())
    manager._last_send_done = asyncio.get_running_loop().time()
    started = asyncio.get_running_loop().time()
    await manager.async_request(TOPIC, ANSWER, "DEV1")
    assert asyncio.get_running_loop().time() - started >= 0.04


async def test_probe_keeps_mqtt_unavailable_distinct(broker) -> None:
    broker.connected = False
    with pytest.raises(HomeAssistantError) as error:
        await async_probe_gateway(MagicMock(), "AABB0011")
    assert error.value.translation_key == "mqtt_unavailable"
    broker.assert_clean()
