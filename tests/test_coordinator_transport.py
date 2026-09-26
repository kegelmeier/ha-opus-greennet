"""Exercise MQTT handshakes, acknowledgements, and lifecycle ownership."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.mqtt_transport import (
    MQTTRequestManager,
    async_probe_gateway,
)


class Broker:
    """A deterministic broker with separate SUBACK and application responses."""

    def __init__(self):
        self.connected = True
        self.auto_ack = True
        self.auto_respond = True
        self.response = {"header": {"httpStatus": 200}}
        self.subscriptions = {}
        self.acknowledgements = {}
        self.published = []
        self.events = []

    async def subscribe(self, hass, topic, callback, qos=1):
        self.subscriptions.setdefault(topic, []).append(callback)
        self.events.append(("subscribe", topic))

        def unsubscribe():
            self.subscriptions[topic].remove(callback)

        return unsubscribe

    def on_subscribe_done(self, hass, topic, qos, callback):
        self.acknowledgements.setdefault(topic, []).append(callback)
        if self.auto_ack:
            callback()
            self.events.append(("suback", topic))

        def unsubscribe():
            self.acknowledgements[topic].remove(callback)

        return unsubscribe

    async def publish(self, hass, topic, payload, **kwargs):
        self.published.append((topic, payload, kwargs))
        self.events.append(("publish", topic))
        if self.auto_respond:
            answer = topic.replace("/get/", "/getAnswer/").replace(
                "/put/", "/putAnswer/"
            )
            self.receive(answer, self.response)

    def receive(self, topic, payload, *, retain=False):
        if not isinstance(payload, (str, bytes)):
            payload = json.dumps(payload)
        message = SimpleNamespace(topic=topic, payload=payload, retain=retain)
        for callback in list(self.subscriptions.get(topic, [])):
            callback(message)

    def assert_clean(self):
        assert all(not callbacks for callbacks in self.subscriptions.values())
        assert all(not callbacks for callbacks in self.acknowledgements.values())


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
def connected_coordinator(broker):
    hass = MagicMock()
    hass.async_create_task.side_effect = asyncio.create_task
    coord = OpusGreenNetCoordinator(hass, "AABB0011")
    coord._gateway_available = True
    return coord


@pytest.mark.parametrize("status", [200, 201])
async def test_command_requires_accepted_gateway_ack(
    broker, connected_coordinator, status
):
    broker.response = {"header": {"httpStatus": status}}
    await connected_coordinator.async_turn_on("DEV1")
    topic, payload, options = broker.published[0]
    assert topic == "EnOcean/AABB0011/put/devices/DEV1/state"
    assert json.loads(payload) == {
        "state": {"functions": [{"key": "switch", "value": "on"}]}
    }
    assert options == {"qos": 1, "retain": False}
    assert [event[0] for event in broker.events] == ["subscribe", "suback", "publish"]
    broker.assert_clean()


@pytest.mark.parametrize(
    "response",
    [
        {"header": {"httpStatus": 400}},
        {"header": {"httpStatus": 500}},
        {"header": {"httpStatus": True}},
        {"header": {"httpStatus": 200.9}},
        {"wrong": "shape"},
        [],
        "not json",
    ],
)
async def test_rejected_or_malformed_ack_fails_action(
    broker, connected_coordinator, response
):
    broker.response = response
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_rejected"
    broker.assert_clean()


async def test_subscription_handshake_precedes_command(broker, connected_coordinator):
    broker.auto_ack = False
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await asyncio.sleep(0)
    assert broker.published == []
    callbacks = broker.acknowledgements["EnOcean/AABB0011/putAnswer/devices/DEV1/state"]
    callbacks[0]()
    await task
    assert len(broker.published) == 1
    broker.assert_clean()


async def test_no_ack_times_out_without_subscription_leak(
    broker, connected_coordinator, monkeypatch
):
    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01
    )
    broker.auto_respond = False
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_turn_on("DEV1")
    assert raised.value.translation_key == "request_timeout"
    broker.assert_clean()


async def test_retained_ack_does_not_complete_command(broker, connected_coordinator):
    broker.auto_respond = False
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await asyncio.sleep(0)
    answer = "EnOcean/AABB0011/putAnswer/devices/DEV1/state"
    broker.receive(answer, {"header": {"httpStatus": 200}}, retain=True)
    await asyncio.sleep(0)
    assert not task.done()
    broker.receive(answer, {"header": {"httpStatus": 200}})
    await task
    broker.assert_clean()


async def test_same_device_commands_are_serialized(broker, connected_coordinator):
    broker.auto_respond = False
    first = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    second = asyncio.create_task(connected_coordinator.async_turn_off("DEV1"))
    await asyncio.sleep(0)
    assert len(broker.published) == 1
    answer = "EnOcean/AABB0011/putAnswer/devices/DEV1/state"
    broker.receive(answer, {"header": {"httpStatus": 200}})
    await first
    await asyncio.sleep(0)
    assert len(broker.published) == 2
    assert not second.done()
    broker.receive(answer, {"header": {"httpStatus": 201}})
    await second
    broker.assert_clean()


async def test_bridge_loss_cancels_active_and_queued_commands(
    broker, connected_coordinator
):
    broker.auto_respond = False
    first = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    second = asyncio.create_task(connected_coordinator.async_turn_off("DEV1"))
    await asyncio.sleep(0)
    connected_coordinator._handle_bridge_status(SimpleNamespace(payload="0"))
    # Even if the bridge immediately recovers, an older queued operation must fail.
    connected_coordinator._bridge_connected = True
    connected_coordinator._gateway_available = True
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, HomeAssistantError) for result in results)
    assert len(broker.published) == 1
    broker.assert_clean()


@pytest.mark.parametrize(
    "operation",
    [
        "async_get_device_configuration",
        "async_get_device_parameters",
        "async_get_device_profile",
    ],
)
async def test_unload_cancels_request_and_subscription(
    broker, connected_coordinator, operation
):
    broker.auto_respond = False
    task = asyncio.create_task(getattr(connected_coordinator, operation)("DEV1"))
    await asyncio.sleep(0)
    await connected_coordinator.async_unload()
    with pytest.raises(HomeAssistantError) as raised:
        await task
    assert raised.value.translation_key == "request_cancelled"
    broker.assert_clean()


async def test_unload_during_suback_wait_cleans_both_waiters(
    broker, connected_coordinator
):
    broker.auto_ack = False
    task = asyncio.create_task(
        connected_coordinator.async_get_device_parameters("DEV1")
    )
    await asyncio.sleep(0)
    await connected_coordinator.async_unload()
    with pytest.raises(HomeAssistantError) as raised:
        await task
    assert raised.value.translation_key == "request_cancelled"
    assert not broker.published
    broker.assert_clean()


@pytest.mark.parametrize(
    "broker_connected,gateway_available", [(False, True), (True, False)]
)
async def test_offline_commands_are_never_published(
    broker, connected_coordinator, broker_connected, gateway_available
):
    broker.connected = broker_connected
    connected_coordinator._gateway_available = gateway_available
    with pytest.raises(HomeAssistantError):
        await connected_coordinator.async_turn_on("DEV1")
    assert not broker.published


async def test_configuration_write_requires_ack(broker, connected_coordinator):
    broker.response = {"header": {"httpStatus": 400}}
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_set_device_configuration(
            "DEV1", {"repeater": 1}
        )
    assert raised.value.translation_key == "request_rejected"
    assert broker.published[0][0].endswith("/put/devices/DEV1/configuration")
    broker.assert_clean()


@pytest.mark.parametrize(
    "response", [[], "broken json", {"header": {"httpStatus": 404}}]
)
async def test_read_response_validation(broker, connected_coordinator, response):
    broker.response = response
    with pytest.raises(HomeAssistantError) as raised:
        await connected_coordinator.async_get_device_parameters("DEV1")
    assert raised.value.translation_key == "request_rejected"
    broker.assert_clean()


async def test_probe_verifies_gateway_and_cleans_subscription(broker):
    broker.response = {"gateway": "OPUS-IQ-DOT"}
    result = await async_probe_gateway(MagicMock(), "AABB0011")
    assert result == broker.response
    assert broker.published[0][0] == "EnOcean/AABB0011/get/config/system/info"
    broker.assert_clean()


async def test_publish_exception_is_propagated_and_cleaned(
    broker, connected_coordinator, monkeypatch
):
    async def fail(*args, **kwargs):
        raise RuntimeError("MQTT publish failed")

    monkeypatch.setattr(
        "custom_components.opus_greennet.mqtt_transport.mqtt.async_publish", fail
    )
    with pytest.raises(RuntimeError, match="MQTT publish failed"):
        await connected_coordinator.async_turn_on("DEV1")
    broker.assert_clean()


async def test_closed_manager_does_not_resubscribe(broker):
    manager = MQTTRequestManager(MagicMock())
    manager.async_close()
    with pytest.raises(HomeAssistantError) as raised:
        await manager.async_request("request", "answer", "DEV1")
    assert raised.value.translation_key == "request_cancelled"
    assert not broker.events


async def test_accepted_local_command_reconciles_even_without_echo(
    broker, connected_coordinator, monkeypatch
):
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Switch", [{"eep": "D2-01-11"}]
    )
    schedule = MagicMock()
    monkeypatch.setattr(
        connected_coordinator, "_schedule_reconciliation_queries", schedule
    )
    await connected_coordinator.async_turn_on("DEV1", channel=1)
    schedule.assert_called_once_with("DEV1", 1)
    schedule.reset_mock()
    await connected_coordinator.async_query_device_status("DEV1", channel=1)
    schedule.assert_not_called()


async def test_report_before_ack_does_not_schedule_redundant_query(
    broker, connected_coordinator, monkeypatch
):
    device = EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-11"}])
    connected_coordinator.devices["DEV1"] = device
    broker.auto_respond = False
    schedule = MagicMock()
    monkeypatch.setattr(
        connected_coordinator, "_schedule_reconciliation_queries", schedule
    )
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1", channel=1))
    await asyncio.sleep(0)
    device.update_from_telegram(
        {"functions": [{"key": "switch", "value": "on", "channel": 1}]}
    )
    broker.receive(
        "EnOcean/AABB0011/putAnswer/devices/DEV1/state", {"header": {"httpStatus": 200}}
    )
    await task
    schedule.assert_not_called()


async def test_fast_outage_after_suback_never_publishes_old_request(
    broker, connected_coordinator
):
    broker.auto_ack = False
    task = asyncio.create_task(connected_coordinator.async_turn_on("DEV1"))
    await asyncio.sleep(0)
    broker.acknowledgements["EnOcean/AABB0011/putAnswer/devices/DEV1/state"][0]()
    connected_coordinator._handle_bridge_status(SimpleNamespace(payload="0"))
    connected_coordinator._bridge_connected = True
    connected_coordinator._gateway_available = True
    with pytest.raises(HomeAssistantError):
        await task
    assert not broker.published
    broker.assert_clean()
