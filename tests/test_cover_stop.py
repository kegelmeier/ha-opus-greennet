"""Cover stop uses the EEP command and reconciles the resulting position."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.const import KEY_STOP, KNOWN_STATE_KEYS
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice

DEVICE_ID = "AABB1122"
COMMAND_TOPIC = f"EnOcean/AABB0011/put/devices/{DEVICE_ID}/state"
ANSWER_TOPIC = f"EnOcean/AABB0011/putAnswer/devices/{DEVICE_ID}/state"


@pytest.fixture
async def stop_coordinator(hass, mqtt_transport):
    """Use the real request manager with only its network boundary replaced."""
    coordinator = OpusGreenNetCoordinator(hass, "AABB0011")
    coordinator._gateway_available = True
    try:
        yield coordinator
    finally:
        await coordinator.async_unload()


@pytest.fixture
def reconciliation_timers(monkeypatch):
    """Capture delayed callbacks so their resulting requests can be exercised."""
    timers = []

    def call_later(hass, delay, callback):
        timers.append((delay, callback))
        return MagicMock()

    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_call_later", call_later
    )
    return timers


@pytest.mark.parametrize(
    ("eep", "channel", "status"),
    [("D2-05-00", 0, 200), ("D2-05-01", 1, 201), ("D2-05-02", 2, 200)],
)
async def test_stop_wire_command_waits_for_ack_then_queries_same_channel(
    hass,
    mqtt_transport,
    stop_coordinator,
    reconciliation_timers,
    monkeypatch,
    eep,
    channel,
    status,
):
    """The EEP's stop=true string gets two status checks only after acceptance."""
    device = EnOceanDevice(DEVICE_ID, "Cover", [{"eep": eep}])
    device_channel = device.get_or_create_channel(channel)
    device_channel.position = 42
    stop_coordinator.devices[DEVICE_ID] = device
    mqtt_transport.reply = False
    published = asyncio.Event()

    async def publish(*args, **kwargs):
        await mqtt_transport.publish(*args, **kwargs)
        published.set()

    monkeypatch.setattr("homeassistant.components.mqtt.async_publish", publish)
    task = asyncio.create_task(stop_coordinator.async_stop_cover(DEVICE_ID, channel))
    try:
        await asyncio.wait_for(published.wait(), 1)
        selector = [{"key": "channel", "value": str(channel)}] if channel else []
        assert mqtt_transport.published == [
            (
                COMMAND_TOPIC,
                json.dumps(
                    {
                        "state": {
                            "functions": [*selector, {"key": "stop", "value": "true"}]
                        }
                    }
                ),
            )
        ]
        assert not task.done()
        assert not reconciliation_timers

        mqtt_transport.receive(ANSWER_TOPIC, {"header": {"httpStatus": status}})
        await task
        assert [delay for delay, _ in reconciliation_timers] == [5, 20]
        assert device_channel.position == 42
        assert KEY_STOP not in KNOWN_STATE_KEYS

        mqtt_transport.reply = True
        for _, callback in tuple(reconciliation_timers):
            callback(None)
            await hass.async_block_till_done()
        assert len(mqtt_transport.published) == 3
        for topic, payload in mqtt_transport.published[1:]:
            assert topic == COMMAND_TOPIC
            assert json.loads(payload) == {
                "state": {"functions": [*selector, {"key": "query", "value": "status"}]}
            }
        assert len(reconciliation_timers) == 2
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("status", [400, 500])
async def test_rejected_stop_has_no_position_change_or_reconciliation(
    mqtt_transport, stop_coordinator, reconciliation_timers, monkeypatch, status
):
    """A rejected stop cannot claim success or create follow-up commands."""
    device = EnOceanDevice(DEVICE_ID, "Cover", [{"eep": "D2-05-02"}])
    channel = device.get_or_create_channel(1)
    channel.position = 42
    stop_coordinator.devices[DEVICE_ID] = device
    mqtt_transport.reply = False

    async def publish(*args, **kwargs):
        await mqtt_transport.publish(*args, **kwargs)
        mqtt_transport.receive(ANSWER_TOPIC, {"header": {"httpStatus": status}})

    monkeypatch.setattr("homeassistant.components.mqtt.async_publish", publish)
    with pytest.raises(HomeAssistantError) as raised:
        await stop_coordinator.async_stop_cover(DEVICE_ID, 1)
    assert raised.value.translation_key == "request_rejected"
    assert channel.position == 42
    assert len(mqtt_transport.published) == 1
    assert not reconciliation_timers
    assert not mqtt_transport.subscriptions


async def test_offline_stop_is_not_published(
    mqtt_transport, stop_coordinator, reconciliation_timers
):
    """A broker connection alone is insufficient to send a physical stop."""
    stop_coordinator._gateway_available = False
    with pytest.raises(HomeAssistantError) as raised:
        await stop_coordinator.async_stop_cover(DEVICE_ID, 1)
    assert raised.value.translation_key == "gateway_unavailable"
    assert not mqtt_transport.published
    assert not reconciliation_timers
