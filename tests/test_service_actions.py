"""Service actions through Home Assistant's real service registry.

Covers the quality-scale rules action-setup and action-exceptions: actions are
registered in ``async_setup``, validate their target config entry and device,
and raise translated exceptions instead of failing silently.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.opus_greennet import (
    ATTR_DEVICE_ID,
    _register_services,
)
from custom_components.opus_greennet.const import DOMAIN
from tests.ha_helpers import configure_bridge, wait_for_entity

DEVICE = {
    "deviceId": "AABB1122",
    "friendlyId": "Desk switch",
    "eeps": [{"eep": "D2-01-00"}],
    "states": [{"key": "switch", "value": "off"}],
}


@pytest.fixture
def answering_transport(mqtt_transport):
    """Let the simulated bridge answer configuration and parameter reads."""
    original_publish = mqtt_transport.publish
    mqtt_transport.answers = {
        "configuration": {"header": {"httpStatus": 200}, "configuration": {"a": 1}},
        "parameters": {"header": {"httpStatus": 200}, "parameters": [{"k": "v"}]},
    }

    async def publish(hass, topic, payload, **kwargs):
        if "/get/devices/" in topic and mqtt_transport.reply:
            mqtt_transport.published.append((topic, payload))
            leaf = topic.rsplit("/", 1)[-1]
            if (answer := mqtt_transport.answers.get(leaf)) is not None:
                mqtt_transport.receive(topic.replace("/get/", "/getAnswer/"), answer)
            return
        await original_publish(hass, topic, payload, **kwargs)

    with patch("homeassistant.components.mqtt.async_publish", publish):
        yield mqtt_transport


async def _setup_entry(hass: HomeAssistant, transport, eag_id: str = "AABB0011"):
    transport.devices = [DEVICE]
    result = await configure_bridge(hass, eag_id)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await wait_for_entity(hass, "switch", f"{eag_id}_AABB1122")
    return result["result"]


async def test_actions_are_registered_before_any_entry_loads(
    hass: HomeAssistant, mqtt_transport
):
    """action-setup: actions exist after async_setup, without a loaded entry."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    for service in (
        "get_device_configuration",
        "set_device_configuration",
        "get_device_parameters",
        "reload_entry",
    ):
        assert hass.services.has_service(DOMAIN, service)
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            DOMAIN,
            "get_device_configuration",
            {"device_id": "AABB1122"},
            blocking=True,
            return_response=True,
        )
    assert error.value.translation_key == "no_loaded_config_entry"


async def test_get_and_set_configuration_and_parameters(
    hass: HomeAssistant, answering_transport
):
    entry = await _setup_entry(hass, answering_transport)

    configuration = await hass.services.async_call(
        DOMAIN,
        "get_device_configuration",
        {"device_id": "AABB1122", "config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert configuration["configuration"] == {"a": 1}

    parameters = await hass.services.async_call(
        DOMAIN,
        "get_device_parameters",
        {"device_id": "AABB1122"},
        blocking=True,
        return_response=True,
    )
    assert parameters["parameters"] == [{"k": "v"}]

    await hass.services.async_call(
        DOMAIN,
        "set_device_configuration",
        {"device_id": "AABB1122", "configuration": {"b": 2}},
        blocking=True,
    )
    assert (
        "EnOcean/AABB0011/put/devices/AABB1122/configuration",
        '{"b": 2}',
    ) in answering_transport.published


async def test_unknown_device_is_a_validation_error_without_publish(
    hass: HomeAssistant, answering_transport
):
    await _setup_entry(hass, answering_transport)
    answering_transport.published.clear()
    for service, data in (
        ("get_device_configuration", {"device_id": "DEADBEEF"}),
        ("get_device_parameters", {"device_id": "DEADBEEF"}),
        ("set_device_configuration", {"device_id": "DEADBEEF", "configuration": {}}),
    ):
        with pytest.raises(ServiceValidationError) as error:
            await hass.services.async_call(
                DOMAIN,
                service,
                data,
                blocking=True,
                return_response=service != "set_device_configuration",
            )
        assert error.value.translation_key == "device_not_found"
        assert error.value.translation_placeholders == {"device_id": "DEADBEEF"}
    assert answering_transport.published == []


async def test_rejected_read_surfaces_translated_error(
    hass: HomeAssistant, answering_transport
):
    await _setup_entry(hass, answering_transport)
    answering_transport.answers["configuration"] = {"header": {"httpStatus": 404}}
    with pytest.raises(HomeAssistantError) as error:
        await hass.services.async_call(
            DOMAIN,
            "get_device_configuration",
            {"device_id": "AABB1122"},
            blocking=True,
            return_response=True,
        )
    assert error.value.translation_key == "request_rejected"


async def test_unknown_and_unloaded_entries_are_validation_errors(
    hass: HomeAssistant, answering_transport
):
    entry = await _setup_entry(hass, answering_transport)
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            DOMAIN,
            "get_device_parameters",
            {"device_id": "AABB1122", "config_entry_id": "missing"},
            blocking=True,
            return_response=True,
        )
    assert error.value.translation_key == "config_entry_not_found"
    assert error.value.translation_placeholders == {"config_entry_id": "missing"}

    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            DOMAIN,
            "reload_entry",
            {"config_entry_id": entry.entry_id},
            blocking=True,
        )
    assert error.value.translation_key == "config_entry_not_loaded"
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED


async def test_entry_of_another_domain_is_not_accepted(
    hass: HomeAssistant, answering_transport
):
    await _setup_entry(hass, answering_transport)
    other = config_entries.ConfigEntry(
        data={},
        discovery_keys={},
        domain="other_domain",
        minor_version=1,
        options={},
        source="user",
        subentries_data=None,
        title="Other",
        unique_id=None,
        version=1,
    )
    await hass.config_entries.async_add(other)
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            DOMAIN,
            "get_device_configuration",
            {"device_id": "AABB1122", "config_entry_id": other.entry_id},
            blocking=True,
            return_response=True,
        )
    assert error.value.translation_key == "config_entry_not_found"


async def test_reload_one_or_all_entries(hass: HomeAssistant, answering_transport):
    first = await _setup_entry(hass, answering_transport, "AABB0011")
    second = await _setup_entry(hass, answering_transport, "CCDD0022")
    first_coordinator = first.runtime_data.coordinator
    second_coordinator = second.runtime_data.coordinator

    await hass.services.async_call(
        DOMAIN, "reload_entry", {"config_entry_id": first.entry_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert first.runtime_data.coordinator is not first_coordinator
    assert second.runtime_data.coordinator is second_coordinator

    # With two loaded gateways a device action needs an explicit selection.
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            DOMAIN,
            "get_device_configuration",
            {"device_id": "AABB1122"},
            blocking=True,
            return_response=True,
        )
    assert error.value.translation_key == "config_entry_required"

    first_coordinator = first.runtime_data.coordinator
    await hass.services.async_call(DOMAIN, "reload_entry", {}, blocking=True)
    await hass.async_block_till_done()
    assert first.runtime_data.coordinator is not first_coordinator
    assert second.runtime_data.coordinator is not second_coordinator
    assert first.state is config_entries.ConfigEntryState.LOADED
    assert second.state is config_entries.ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("service", "method"),
    [
        ("get_device_configuration", "async_get_device_configuration"),
        ("get_device_parameters", "async_get_device_parameters"),
    ],
)
async def test_missing_read_result_raises_timeout_error(service, method) -> None:
    """A read that yields no object must not return an empty success."""
    coordinator = MagicMock()
    coordinator.get_device.return_value = object()
    setattr(coordinator, method, AsyncMock(return_value=None))
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.config_entries.async_loaded_entries.return_value = [entry]
    with patch(
        "custom_components.opus_greennet.async_register_admin_service"
    ) as register:
        _register_services(hass)
    handlers = {call.args[2]: call.args[3] for call in register.call_args_list}
    with pytest.raises(HomeAssistantError) as error:
        await handlers[service](SimpleNamespace(data={ATTR_DEVICE_ID: "AABB1122"}))
    assert error.value.translation_key == "device_request_timeout"


def test_services_are_registered_only_once() -> None:
    hass = MagicMock()
    hass.services.has_service.return_value = True
    with patch(
        "custom_components.opus_greennet.async_register_admin_service"
    ) as register:
        _register_services(hass)
    register.assert_not_called()
