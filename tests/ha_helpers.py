"""A small MQTT boundary and public HA flow helper for integration tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from time import monotonic

from homeassistant import config_entries
from homeassistant.components import mqtt
from homeassistant.components.mqtt.const import MQTT_CONNECTION_STATE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from paho.mqtt.client import topic_matches_sub

from custom_components.opus_greennet.const import DOMAIN


class MQTTTransport:
    """In-memory MQTT boundary with canned bridge responses and subscriptions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.connected = True
        self.devices: list[dict] = []
        self.published: list[tuple[str, str]] = []
        self.subscriptions: list[tuple[str, Callable]] = []
        self.reply = True

    async def subscribe(self, hass, topic, callback, **kwargs) -> Callable:
        """Capture the real integration's topic subscriptions."""
        subscription = (topic, callback)
        self.subscriptions.append(subscription)

        def unsubscribe() -> None:
            self.subscriptions.remove(subscription)

        return unsubscribe

    def subscribed(self, hass, topic, qos, callback) -> Callable:
        """Simulate broker SUBACK without changing application behavior."""
        return self.hass.loop.call_soon(callback).cancel

    async def publish(self, hass, topic, payload, **kwargs) -> None:
        """Return protocol responses through the subscribed MQTT callbacks."""
        self.published.append((topic, payload))
        if not self.reply:
            return
        if topic.endswith("/get/config/system/info"):
            self.receive(
                topic.replace("/get/", "/getAnswer/"),
                {"model": "GreenNet Bridge", "version": "test"},
            )
        elif topic.endswith("/get/devices"):
            self.receive(topic.replace("/get/", "/getAnswer/"), self.devices)
        elif "/put/" in topic:
            self.receive(
                topic.replace("/put/", "/putAnswer/"),
                {"header": {"httpStatus": 200}},
            )

    def receive(self, topic: str, payload: object) -> None:
        """Deliver a message using Home Assistant's MQTT message type."""
        for subscribed_topic, callback in list(self.subscriptions):
            if topic_matches_sub(subscribed_topic, topic):
                callback(
                    mqtt.ReceiveMessage(
                        topic=topic,
                        payload=payload
                        if isinstance(payload, str | bytes)
                        else json.dumps(payload),
                        qos=1,
                        retain=False,
                        subscribed_topic=subscribed_topic,
                        timestamp=monotonic(),
                    )
                )

    async def set_connected(self, connected: bool) -> None:
        """Emit MQTT's public connectivity signal."""
        self.connected = connected
        async_dispatcher_send(self.hass, MQTT_CONNECTION_STATE, connected)
        await self.hass.async_block_till_done()


async def configure_bridge(hass: HomeAssistant, eag_id: str = "aabb0011") -> dict:
    """Submit a bridge through the actual config flow manager."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"eag_id": eag_id}
    )


async def wait_for_entity(
    hass: HomeAssistant, domain: str, unique_id: str, timeout: float = 6
) -> str:
    """Wait for discovery and entity setup to finish in the real HA registry."""
    registry = er.async_get(hass)
    async with asyncio.timeout(timeout):
        while (
            entity_id := registry.async_get_entity_id(domain, DOMAIN, unique_id)
        ) is None:
            await asyncio.sleep(0.01)
        await hass.async_block_till_done()
    return entity_id
