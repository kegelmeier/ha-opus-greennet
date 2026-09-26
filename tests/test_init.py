"""Tests for integration setup, gateway registry, and services."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)

from custom_components.opus_greennet import (
    ATTR_DEVICE_ID,
    DOMAIN,
    OpusGreenNetRuntimeData,
    _loaded_entry,
    _register_services,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice


@pytest.mark.asyncio
async def test_setup_creates_gateway_registry_device() -> None:
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock()
    coordinator.async_unload = AsyncMock()
    gateway = SimpleNamespace(id="gateway-registry-id")
    registry = MagicMock()
    registry.async_get_or_create.return_value = gateway
    entry = SimpleNamespace(
        entry_id="entry-id",
        data={"eag_id": "AABB0011"},
        title="Opus GreenNet (AABB0011)",
    )
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    with (
        patch(
            "custom_components.opus_greennet.OpusGreenNetCoordinator",
            return_value=coordinator,
        ),
        patch("custom_components.opus_greennet.dr.async_get", return_value=registry),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert isinstance(entry.runtime_data, OpusGreenNetRuntimeData)
    assert entry.runtime_data.gateway_device_id == "gateway-registry-id"
    registry.async_get_or_create.assert_called_once_with(
        config_entry_id="entry-id",
        identifiers={(DOMAIN, "AABB0011")},
        manufacturer="OPUS",
        model="GreenNet Bridge",
        name="Opus GreenNet (AABB0011)",
        serial_number="AABB0011",
    )
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()


@pytest.mark.asyncio
async def test_unload_stops_coordinator_after_platforms() -> None:
    coordinator = MagicMock()
    coordinator.async_unload = AsyncMock()
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await async_unload_entry(hass, entry) is True
    coordinator.async_unload.assert_awaited_once()


def test_loaded_entry_requires_selection_for_multiple_gateways() -> None:
    hass = MagicMock()
    hass.config_entries.async_loaded_entries.return_value = [MagicMock(), MagicMock()]

    with pytest.raises(ServiceValidationError) as error:
        _loaded_entry(hass, None)
    assert error.value.translation_key == "config_entry_required"


@pytest.mark.asyncio
async def test_get_service_returns_response_for_selected_device() -> None:
    coordinator = MagicMock()
    coordinator.get_device.return_value = EnOceanDevice("DEV1", "Switch")
    coordinator.async_get_device_configuration = AsyncMock(
        return_value={"configured": True}
    )
    entry = SimpleNamespace(
        entry_id="entry-id",
        domain=DOMAIN,
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )
    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.config_entries.async_loaded_entries.return_value = [entry]

    with patch(
        "custom_components.opus_greennet.async_register_admin_service"
    ) as register:
        _register_services(hass)

    get_registration = register.call_args_list[0]
    get_handler = get_registration.args[3]
    result = await get_handler(SimpleNamespace(data={ATTR_DEVICE_ID: "DEV1"}))

    assert result == {"configured": True}
    assert get_registration.kwargs["supports_response"] is SupportsResponse.ONLY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (HomeAssistantError("Offline"), ConfigEntryNotReady),
        (ValueError("Bug"), ValueError),
    ],
)
async def test_failed_setup_cleans_up_without_masking_programming_errors(
    failure: Exception, expected: type[Exception]
) -> None:
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock(side_effect=failure)
    coordinator.async_unload = AsyncMock()
    entry = SimpleNamespace(data={"eag_id": "AABB0011"})
    with (
        patch(
            "custom_components.opus_greennet.OpusGreenNetCoordinator",
            return_value=coordinator,
        ),
        pytest.raises(expected),
    ):
        await async_setup_entry(MagicMock(), entry)
    coordinator.async_unload.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_platform_unload_keeps_coordinator_running() -> None:
    coordinator = MagicMock()
    coordinator.async_unload = AsyncMock()
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    assert await async_unload_entry(hass, entry) is False
    coordinator.async_unload.assert_not_awaited()
