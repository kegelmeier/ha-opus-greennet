"""Service actions through Home Assistant's real service registry.

Covers the quality-scale rules action-setup and action-exceptions: actions are
registered in ``async_setup``, validate their target config entry and device,
and raise translated exceptions instead of failing silently.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.opus_greennet import _register_services
from custom_components.opus_greennet.api import OpusTimeoutError
from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import configure_bridge, wait_for_entity, wait_until

DEVICE_ID = "AABB1122"
CONFIGURATION_PATH = f"/devices/{DEVICE_ID}/configuration"
PARAMETERS_PATH = f"/devices/{DEVICE_ID}/parameters"


@pytest.fixture
def answering_gateway(gateway: FakeGateway) -> FakeGateway:
    """Let the gateway answer configuration and parameter reads."""
    gateway.devices = [
        device_object(
            DEVICE_ID,
            "D2-01-00",
            friendly_id="Desk switch",
            states=[{"key": "switch", "value": "off"}],
        )
    ]
    gateway.responses[CONFIGURATION_PATH] = {
        "header": {"httpStatus": 200, "content": "configuration"},
        "configuration": {"a": 1},
    }
    gateway.responses[PARAMETERS_PATH] = {
        "header": {"httpStatus": 200, "content": "parameters"},
        "parameters": [{"k": "v"}],
    }
    return gateway


@pytest.fixture
async def second_gateway(gateway: FakeGateway) -> AsyncIterator[FakeGateway]:
    """Run another gateway with its own EURID and the same device."""
    fake = FakeGateway(eurid="CCDD0022", password="CCDD0022")
    fake.devices = gateway.devices
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


async def _setup_entry(hass: HomeAssistant, gateway: FakeGateway):
    result = await configure_bridge(hass, gateway)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await gateway.wait_for_stream()
    await wait_for_entity(hass, "switch", f"{gateway.eurid}_{DEVICE_ID}")
    return result["result"]


async def _get_configuration(hass: HomeAssistant, **data):
    return await hass.services.async_call(
        DOMAIN,
        "get_device_configuration",
        {"device_id": DEVICE_ID, **data},
        blocking=True,
        return_response=True,
    )


async def test_actions_are_registered_before_any_entry_loads(hass: HomeAssistant):
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
        await _get_configuration(hass)
    assert error.value.translation_key == "no_loaded_config_entry"


async def test_get_and_set_configuration_and_parameters(
    hass: HomeAssistant, answering_gateway: FakeGateway
):
    entry = await _setup_entry(hass, answering_gateway)

    configuration = await _get_configuration(hass, config_entry_id=entry.entry_id)
    assert configuration["configuration"] == {"a": 1}

    parameters = await hass.services.async_call(
        DOMAIN,
        "get_device_parameters",
        {"device_id": DEVICE_ID},
        blocking=True,
        return_response=True,
    )
    assert parameters["parameters"] == [{"k": "v"}]

    await hass.services.async_call(
        DOMAIN,
        "set_device_configuration",
        {"device_id": DEVICE_ID, "configuration": {"b": 2}},
        blocking=True,
    )
    assert answering_gateway.requests_to("GET", CONFIGURATION_PATH) == [None]
    assert answering_gateway.requests_to("GET", PARAMETERS_PATH) == [None]
    assert answering_gateway.requests_to("PUT", CONFIGURATION_PATH) == [{"b": 2}]


async def test_unknown_device_is_a_validation_error_without_request(
    hass: HomeAssistant, answering_gateway: FakeGateway
):
    await _setup_entry(hass, answering_gateway)
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
    assert not [path for _, path, _ in answering_gateway.requests if "DEADBEEF" in path]


async def test_rejected_read_surfaces_translated_error(
    hass: HomeAssistant, answering_gateway: FakeGateway
):
    await _setup_entry(hass, answering_gateway)
    answering_gateway.responses[CONFIGURATION_PATH] = {
        "header": {"httpStatus": 404, "message": "no ReCom answer"}
    }
    with pytest.raises(HomeAssistantError) as error:
        await _get_configuration(hass)
    assert error.value.translation_key == "request_rejected"
    assert error.value.translation_placeholders == {
        "device_id": DEVICE_ID,
        "reason": "status 404: no ReCom answer",
    }


async def test_rejected_write_surfaces_translated_error(
    hass: HomeAssistant, answering_gateway: FakeGateway
):
    await _setup_entry(hass, answering_gateway)
    answering_gateway.status[CONFIGURATION_PATH] = 500
    with pytest.raises(HomeAssistantError) as error:
        await hass.services.async_call(
            DOMAIN,
            "set_device_configuration",
            {"device_id": DEVICE_ID, "configuration": {"b": 2}},
            blocking=True,
        )
    assert error.value.translation_key == "request_rejected"
    assert error.value.translation_placeholders["reason"] == (
        "status 500: forced failure"
    )


async def test_read_without_answer_raises_timeout_error(
    hass: HomeAssistant, answering_gateway: FakeGateway
):
    """A ReCom read that is never answered fails with a translated timeout."""
    entry = await _setup_entry(hass, answering_gateway)
    client = entry.runtime_data.coordinator.client
    with (
        patch.object(
            client,
            "async_get",
            AsyncMock(side_effect=OpusTimeoutError("GET timed out")),
        ),
        pytest.raises(HomeAssistantError) as error,
    ):
        await _get_configuration(hass)
    assert error.value.translation_key == "request_timeout"


async def test_actions_fail_while_the_gateway_is_unavailable(
    hass: HomeAssistant, answering_gateway: FakeGateway
):
    entry = await _setup_entry(hass, answering_gateway)
    coordinator = entry.runtime_data.coordinator
    with patch(
        "custom_components.opus_greennet.coordinator.STREAM_BACKOFF_MAX",
        0.02,
    ):
        await answering_gateway.stop()
        await wait_until(lambda: not coordinator.available)
        requests = len(answering_gateway.requests)
        with pytest.raises(HomeAssistantError) as error:
            await _get_configuration(hass)
        assert error.value.translation_key == "gateway_unavailable"
        assert len(answering_gateway.requests) == requests

        await answering_gateway.start()
        await wait_until(lambda: coordinator.available)
    configuration = await _get_configuration(hass)
    assert configuration["configuration"] == {"a": 1}


async def test_unknown_and_unloaded_entries_are_validation_errors(
    hass: HomeAssistant, answering_gateway: FakeGateway
):
    entry = await _setup_entry(hass, answering_gateway)
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            DOMAIN,
            "get_device_parameters",
            {"device_id": DEVICE_ID, "config_entry_id": "missing"},
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
    hass: HomeAssistant, answering_gateway: FakeGateway
):
    await _setup_entry(hass, answering_gateway)
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
        await _get_configuration(hass, config_entry_id=other.entry_id)
    assert error.value.translation_key == "config_entry_not_found"


async def test_reload_one_or_all_entries(
    hass: HomeAssistant, answering_gateway: FakeGateway, second_gateway: FakeGateway
):
    first = await _setup_entry(hass, answering_gateway)
    second = await _setup_entry(hass, second_gateway)
    first_coordinator = first.runtime_data.coordinator
    second_coordinator = second.runtime_data.coordinator

    await hass.services.async_call(
        DOMAIN, "reload_entry", {"config_entry_id": first.entry_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert first.runtime_data.coordinator is not first_coordinator
    assert not first_coordinator.available
    assert second.runtime_data.coordinator is second_coordinator

    # With two loaded gateways a device action needs an explicit selection.
    with pytest.raises(ServiceValidationError) as error:
        await _get_configuration(hass)
    assert error.value.translation_key == "config_entry_required"
    # The selection decides which gateway is asked.
    await _get_configuration(hass, config_entry_id=second.entry_id)
    assert second_gateway.requests_to("GET", CONFIGURATION_PATH) == [None]
    assert answering_gateway.requests_to("GET", CONFIGURATION_PATH) == []

    first_coordinator = first.runtime_data.coordinator
    await hass.services.async_call(DOMAIN, "reload_entry", {}, blocking=True)
    await hass.async_block_till_done()
    assert first.runtime_data.coordinator is not first_coordinator
    assert second.runtime_data.coordinator is not second_coordinator
    assert first.state is config_entries.ConfigEntryState.LOADED
    assert second.state is config_entries.ConfigEntryState.LOADED


def test_services_are_registered_only_once() -> None:
    hass = MagicMock()
    hass.services.has_service.return_value = True
    with patch(
        "custom_components.opus_greennet.async_register_admin_service"
    ) as register:
        _register_services(hass)
    register.assert_not_called()
