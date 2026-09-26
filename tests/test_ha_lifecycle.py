"""Integration behavior through Home Assistant's public managers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN
from tests.ha_helpers import configure_bridge, wait_for_entity


async def test_config_flow_creates_and_unloads_entry(
    hass: HomeAssistant, mqtt_transport
):
    """The flow manager creates a real entry, gateway, services and platforms."""
    result = await configure_bridge(hass, "  aabb0011  ")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.data == {"eag_id": "AABB0011"}
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert dr.async_get(hass).async_get(entry.runtime_data.gateway_device_id)
    assert hass.services.has_service(DOMAIN, "get_device_configuration")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED
    assert mqtt_transport.subscriptions == []


async def test_config_flow_rejects_invalid_id(hass: HomeAssistant, mqtt_transport):
    """Validation errors are attached to the correct user input field."""
    result = await configure_bridge(hass, "not-a-bridge")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"eag_id": "invalid_eag_id"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_config_flow_rejects_duplicate_id(hass: HomeAssistant, mqtt_transport):
    """Case normalization prevents configuring the same gateway twice."""
    assert (await configure_bridge(hass))["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    result = await configure_bridge(hass, "AABB0011")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_config_flow_reports_disconnected_mqtt(
    hass: HomeAssistant, mqtt_transport
):
    """A configured but disconnected broker keeps the setup form open."""
    mqtt_transport.connected = False
    result = await configure_bridge(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_config_flow_aborts_without_mqtt(hass: HomeAssistant, mqtt_transport):
    """A missing MQTT integration produces the actionable abort reason."""
    with patch(
        "homeassistant.components.mqtt.async_wait_for_mqtt_client",
        new=AsyncMock(return_value=False),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"


async def test_config_flow_reports_unresponsive_gateway(
    hass: HomeAssistant, mqtt_transport
):
    """A connected broker alone cannot validate an incorrect gateway ID."""
    mqtt_transport.reply = False
    with patch("custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01):
        result = await configure_bridge(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "gateway_unavailable"}
    assert hass.config_entries.async_entries(DOMAIN) == []
    assert mqtt_transport.subscriptions == []


async def test_setup_retries_and_cleans_subscriptions_for_unreachable_gateway(
    hass: HomeAssistant, mqtt_transport
):
    """A previously configured gateway can recover after a failed setup."""
    result = await configure_bridge(hass)
    await hass.async_block_till_done()
    entry = result["result"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    mqtt_transport.reply = False
    with patch("custom_components.opus_greennet.mqtt_transport.REQUEST_TIMEOUT", 0.01):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
    assert mqtt_transport.subscriptions == []

    mqtt_transport.reply = True
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_partial_mqtt_setup_failure_releases_previous_subscriptions(
    hass: HomeAssistant, mqtt_transport
):
    """A failure partway through setup must leave no live MQTT callbacks."""
    result = await configure_bridge(hass)
    await hass.async_block_till_done()
    entry = result["result"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    attempts = 0

    async def fail_third_subscription(hass, topic, callback, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise HomeAssistantError("MQTT subscription failed")
        return await mqtt_transport.subscribe(hass, topic, callback, **kwargs)

    with patch(
        "homeassistant.components.mqtt.async_subscribe", fail_third_subscription
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
    assert mqtt_transport.subscriptions == []


async def test_entity_service_and_broker_reconnect(hass: HomeAssistant, mqtt_transport):
    """A discovered entity uses real HA services, registry and availability."""
    mqtt_transport.devices = [
        {
            "deviceId": "AABB1122",
            "friendlyId": "Desk switch",
            "eeps": [{"eep": "D2-01-00"}],
            "states": {"switch": "off"},
        }
    ]
    result = await configure_bridge(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = result["result"]
    registry = er.async_get(hass)

    entity_id = await wait_for_entity(hass, "switch", "AABB0011_AABB1122")
    assert hass.states.get(entity_id).state == "off"
    device = dr.async_get(hass).async_get(registry.async_get(entity_id).device_id)
    assert device.via_device_id == entry.runtime_data.gateway_device_id

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == "on"
    assert any(
        "/put/devices/AABB1122/state" in topic for topic, _ in mqtt_transport.published
    )

    await mqtt_transport.set_connected(False)
    assert hass.states.get(entity_id).state == "unavailable"
    discoveries_before = sum(
        topic.endswith("/get/devices") for topic, _ in mqtt_transport.published
    )
    await mqtt_transport.set_connected(True)
    assert hass.states.get(entity_id).state == "on"
    assert (
        sum(topic.endswith("/get/devices") for topic, _ in mqtt_transport.published)
        == discoveries_before + 1
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert mqtt_transport.subscriptions == []


@pytest.mark.parametrize(
    ("interruption", "error_key"),
    [
        ("broker", "mqtt_unavailable"),
        ("bridge", "gateway_unavailable"),
        ("reload", "request_cancelled"),
    ],
)
async def test_pending_entity_command_is_cancelled_on_disconnect_or_reload(
    hass: HomeAssistant, mqtt_transport, interruption, error_key
):
    """Waiting HA service calls fail promptly without leaving reply subscriptions."""
    mqtt_transport.devices = [
        {
            "deviceId": "AABB1122",
            "friendlyId": "Desk switch",
            "eeps": [{"eep": "D2-01-00"}],
            "states": {"switch": "off"},
        }
    ]
    result = await configure_bridge(hass)
    await hass.async_block_till_done()
    entry = result["result"]
    entity_id = await wait_for_entity(hass, "switch", "AABB0011_AABB1122")
    initial_subscriptions = len(mqtt_transport.subscriptions)
    command_sent = asyncio.Event()

    async def publish_without_command_ack(hass, topic, payload, **kwargs):
        if topic.endswith("/put/devices/AABB1122/state"):
            mqtt_transport.published.append((topic, payload))
            command_sent.set()
            return
        await mqtt_transport.publish(hass, topic, payload, **kwargs)

    with patch(
        "homeassistant.components.mqtt.async_publish", publish_without_command_ack
    ):
        pending_call = asyncio.create_task(
            hass.services.async_call(
                "switch", "turn_on", {"entity_id": entity_id}, blocking=True
            )
        )
        try:
            await asyncio.wait_for(command_sent.wait(), 1)
            assert not pending_call.done()
            if interruption == "broker":
                await mqtt_transport.set_connected(False)
            elif interruption == "bridge":
                mqtt_transport.receive("opus_greennet/AABB0011/bridge/status", "0")
                await hass.async_block_till_done()
            else:
                assert await hass.config_entries.async_reload(entry.entry_id)
            with pytest.raises(HomeAssistantError) as error:
                await asyncio.wait_for(pending_call, 1)
            assert error.value.translation_key == error_key
            assert len(mqtt_transport.subscriptions) == initial_subscriptions
            if interruption != "reload":
                assert hass.states.get(entity_id).state == "unavailable"
                if interruption == "broker":
                    await mqtt_transport.set_connected(True)
                else:
                    mqtt_transport.receive("opus_greennet/AABB0011/bridge/status", "1")
                    await hass.async_block_till_done()
                assert hass.states.get(entity_id).state == "off"
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert mqtt_transport.subscriptions == []
        finally:
            pending_call.cancel()
            await asyncio.gather(pending_call, return_exceptions=True)


async def test_bridge_reconnect_restarts_an_interrupted_health_probe(
    hass: HomeAssistant, mqtt_transport
):
    """A quick offline/online notification must not postpone recovery for a minute."""
    mqtt_transport.devices = [
        {
            "deviceId": "AABB1122",
            "friendlyId": "Desk switch",
            "eeps": [{"eep": "D2-01-00"}],
            "states": {"switch": "off"},
        }
    ]
    await configure_bridge(hass)
    await hass.async_block_till_done()
    entity_id = await wait_for_entity(hass, "switch", "AABB0011_AABB1122")
    probe_sent = asyncio.Event()

    async def delay_first_health_answer(hass, topic, payload, **kwargs):
        if topic.endswith("/get/config/system/info") and not probe_sent.is_set():
            probe_sent.set()
            return
        await mqtt_transport.publish(hass, topic, payload, **kwargs)

    with patch(
        "homeassistant.components.mqtt.async_publish", delay_first_health_answer
    ):
        mqtt_transport.receive("opus_greennet/AABB0011/bridge/status", "1")
        await asyncio.wait_for(probe_sent.wait(), 1)
        mqtt_transport.receive("opus_greennet/AABB0011/bridge/status", "0")
        mqtt_transport.receive("opus_greennet/AABB0011/bridge/status", "1")
        await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"


@pytest.mark.parametrize("connection", ["broker", "bridge"])
async def test_reconnect_snapshot_updates_existing_entity_state(
    hass: HomeAssistant, mqtt_transport, connection
):
    """A fresh discovery snapshot updates HA without needing a live telegram."""
    mqtt_transport.devices = [
        {
            "deviceId": "AABB1122",
            "friendlyId": "Desk switch",
            "eeps": [{"eep": "D2-01-00"}],
            "states": {"switch": "off"},
        }
    ]
    await configure_bridge(hass)
    await hass.async_block_till_done()
    entity_id = await wait_for_entity(hass, "switch", "AABB0011_AABB1122")
    assert hass.states.get(entity_id).state == "off"
    if connection == "broker":
        await mqtt_transport.set_connected(False)
    else:
        mqtt_transport.receive("opus_greennet/AABB0011/bridge/status", "0")
        await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    mqtt_transport.devices[0]["states"]["switch"] = "on"
    updated = asyncio.Event()

    @callback
    def state_changed(event):
        if (
            event.data["entity_id"] == entity_id
            and (state := event.data["new_state"]) is not None
            and state.state == "on"
        ):
            updated.set()

    unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)
    try:
        if connection == "broker":
            await mqtt_transport.set_connected(True)
        else:
            mqtt_transport.receive("opus_greennet/AABB0011/bridge/status", "1")
            await hass.async_block_till_done()
        await asyncio.wait_for(updated.wait(), 6)
        assert hass.states.get(entity_id).state == "on"
    finally:
        unsubscribe()
