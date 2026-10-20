"""Tests for integration setup, gateway registry, and services."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr

from custom_components.opus_greennet import (
    ATTR_DEVICE_ID,
    DOMAIN,
    OpusGreenNetRuntimeData,
    _loaded_entry,
    _register_services,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.opus_greennet.api import (
    OpusConnectionError,
    OpusGatewayClient,
    OpusResponseError,
    OpusTimeoutError,
)
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge

EAG_ID = "AABB0011"
ENTRY_DATA = {
    "eag_id": EAG_ID,
    "host": "192.0.2.10",
    "port": 8080,
    "username": "admin",
    "password": "secret",
}


def _entry(data: dict[str, Any]) -> ConfigEntry:
    return config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title=f"Opus GreenNet ({EAG_ID})",
        data=data,
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=EAG_ID,
        discovery_keys={},
        subentries_data=None,
    )


def _gateway_entry(gateway: FakeGateway, **overrides: Any) -> ConfigEntry:
    return _entry({"eag_id": EAG_ID, **gateway.config, **overrides})


async def _add(hass: HomeAssistant, entry: ConfigEntry) -> ConfigEntry:
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    return entry


def _mock_setup(coordinator: MagicMock):
    """Patch the coordinator and HTTP session for setups with a mocked hass."""
    return (
        patch(
            "custom_components.opus_greennet.OpusGreenNetCoordinator",
            return_value=coordinator,
        ),
        patch("custom_components.opus_greennet.async_get_clientsession"),
    )


# ── setup with the real gateway API ────────────────────────────────────────


async def test_setup_creates_gateway_registry_device(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.system_info = {"version": "OPUS-GW 2.4.1"}
    entry = await setup_bridge(hass, gateway)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, OpusGreenNetRuntimeData)
    registry = dr.async_get(hass)
    gateway_device = registry.async_get(entry.runtime_data.gateway_device_id)
    assert gateway_device is not None
    assert gateway_device.identifiers == {(DOMAIN, EAG_ID)}
    assert gateway_device.config_entry_id == entry.entry_id
    assert gateway_device.manufacturer == "OPUS"
    assert gateway_device.model == "GreenNet Bridge"
    assert gateway_device.name == f"Opus GreenNet ({EAG_ID})"
    assert gateway_device.serial_number == EAG_ID
    assert gateway_device.sw_version == "OPUS-GW 2.4.1"


@pytest.mark.parametrize("version", [None, 241, ["2.4.1"]])
async def test_gateway_without_text_version_has_no_sw_version(
    hass: HomeAssistant, gateway: FakeGateway, version: object
) -> None:
    gateway.system_info = {"version": version}
    entry = await setup_bridge(hass, gateway)
    gateway_device = dr.async_get(hass).async_get(entry.runtime_data.gateway_device_id)
    assert gateway_device is not None
    assert gateway_device.sw_version is None


async def test_setup_loads_devices_before_platforms(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object("DEV1", "D2-01-01", states=[{"key": "switch", "value": "on"}])
    ]
    entry = await _add(hass, _gateway_entry(gateway))
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data.coordinator
    assert coordinator.available
    assert coordinator.client.host == gateway.host
    assert coordinator.client.port == gateway.port
    assert coordinator.devices["DEV1"].channels[0].is_on is True


async def test_entry_defaults_for_port_and_username(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Entries without port and username use the gateway defaults."""
    created: list[tuple[Any, ...]] = []

    def client(session, host, port, username, password):
        created.append((host, port, username, password))
        return OpusGatewayClient(session, host, gateway.port, username, password)

    entry = _entry(
        {"eag_id": EAG_ID, "host": gateway.host, "password": gateway.password}
    )
    with patch("custom_components.opus_greennet.OpusGatewayClient", client):
        await _add(hass, entry)
    assert created == [(gateway.host, 8080, "admin", gateway.password)]
    assert entry.state is ConfigEntryState.LOADED


async def test_entry_without_host_requires_the_gateway_address(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Entries of the former MQTT transport only know the EAG identifier."""
    with pytest.raises(ConfigEntryAuthFailed) as raised:
        await async_setup_entry(hass, _entry({"eag_id": EAG_ID}))
    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "gateway_address_required"
    assert gateway.requests == []


@pytest.mark.parametrize(
    ("configure", "error", "translation_key"),
    [
        (
            lambda gw: setattr(gw, "accept_auth", False),
            ConfigEntryAuthFailed,
            "invalid_auth",
        ),
        (
            lambda gw: setattr(gw, "eurid", "11223344"),
            ConfigEntryAuthFailed,
            "wrong_gateway",
        ),
        (
            lambda gw: gw.status.update({"/devices/stream": 500}),
            ConfigEntryNotReady,
            "gateway_unavailable",
        ),
        (
            lambda gw: setattr(gw, "uptime", "x"),
            ConfigEntryNotReady,
            "gateway_unavailable",
        ),
    ],
    ids=["invalid_auth", "wrong_gateway", "stream_error", "invalid_uptime"],
)
async def test_failed_setup_raises_translated_errors(
    hass: HomeAssistant,
    gateway: FakeGateway,
    configure,
    error: type[Exception],
    translation_key: str,
) -> None:
    configure(gateway)
    unload = AsyncMock(wraps=OpusGreenNetCoordinator.async_unload)

    async def spy(self: OpusGreenNetCoordinator) -> None:
        await unload(self)

    with (
        patch.object(OpusGreenNetCoordinator, "async_unload", spy),
        pytest.raises(error) as raised,
    ):
        await async_setup_entry(hass, _gateway_entry(gateway))
    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == translation_key
    unload.assert_awaited_once()
    # Nothing was left running: at most the one failed stream attempt of the
    # stream-first setup, and no further attempt afterwards.
    attempts = len(gateway.requests_to("GET", "/devices/stream"))
    assert attempts <= 1
    await asyncio.sleep(1.4)
    assert len(gateway.requests_to("GET", "/devices/stream")) == attempts


async def test_unreachable_gateway_is_retried(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = _gateway_entry(gateway)
    await gateway.stop()
    await _add(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize(
    ("configure", "state", "flows"),
    [
        (lambda gw: setattr(gw, "accept_auth", False), "setup_error", ["reauth"]),
        (lambda gw: setattr(gw, "eurid", "11223344"), "setup_error", ["reauth"]),
        (lambda gw: gw.status.update({"/system/info": 503}), "setup_retry", []),
    ],
    ids=["invalid_auth", "wrong_gateway", "gateway_error"],
)
async def test_failed_setup_states_in_home_assistant(
    hass: HomeAssistant, gateway: FakeGateway, configure, state: str, flows: list
) -> None:
    configure(gateway)
    entry = await _add(hass, _gateway_entry(gateway))
    assert entry.state is ConfigEntryState(state)
    progress = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in progress] == flows


async def test_failed_platform_setup_unloads_the_coordinator(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """A failure after the coordinator started must not leave the stream open."""
    entry = _gateway_entry(gateway)
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("platform failure"),
    ):
        await _add(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    coordinator = entry.runtime_data.coordinator
    assert not coordinator.available
    assert not coordinator._tasks  # noqa: SLF001
    assert not coordinator._timers  # noqa: SLF001


async def test_unload_and_reload_with_the_real_gateway(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    first = entry.runtime_data.coordinator
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator is not first
    assert not first.available
    assert not first._tasks  # noqa: SLF001
    assert entry.runtime_data.coordinator.available

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


# ── unit tests with a mocked coordinator ───────────────────────────────────


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


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (OpusConnectionError("Offline"), ConfigEntryNotReady),
        (ValueError("Bug"), ValueError),
    ],
)
async def test_failed_setup_cleans_up_without_masking_programming_errors(
    failure: Exception, expected: type[Exception]
) -> None:
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock(side_effect=failure)
    coordinator.async_unload = AsyncMock()
    entry = SimpleNamespace(data=ENTRY_DATA)
    coordinator_patch, session_patch = _mock_setup(coordinator)
    with coordinator_patch, session_patch, pytest.raises(expected):
        await async_setup_entry(MagicMock(), entry)
    coordinator.async_unload.assert_awaited_once()


async def test_failed_platform_unload_keeps_coordinator_running() -> None:
    coordinator = MagicMock()
    coordinator.async_unload = AsyncMock()
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    assert await async_unload_entry(hass, entry) is False
    coordinator.async_unload.assert_not_awaited()


@pytest.mark.parametrize(
    ("failure", "expected_log"),
    [
        (OpusTimeoutError("GET /system/info timed out"), "GET /system/info timed out"),
        (
            OpusConnectionError("GET /devices failed: Connection refused"),
            "GET /devices failed: Connection refused",
        ),
        (OpusResponseError("status 404: not found", 404), "status 404: not found"),
    ],
)
async def test_failed_setup_logs_the_underlying_cause(
    failure: Exception, expected_log: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Retry loops must show why the gateway was not ready, not only that."""
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock(side_effect=failure)
    coordinator.async_unload = AsyncMock()
    entry = SimpleNamespace(data=ENTRY_DATA)
    coordinator_patch, session_patch = _mock_setup(coordinator)
    with (
        coordinator_patch,
        session_patch,
        caplog.at_level(logging.WARNING, logger="custom_components.opus_greennet"),
        pytest.raises(ConfigEntryNotReady) as raised,
    ):
        await async_setup_entry(MagicMock(), entry)
    assert raised.value.__cause__ is failure
    assert raised.value.translation_key == "gateway_unavailable"
    assert (
        f"OPUS gateway {EAG_ID} is not ready, Home Assistant will retry: {expected_log}"
        in caplog.text
    )


async def test_failed_setup_logs_the_real_gateway_error(
    hass: HomeAssistant, gateway: FakeGateway, caplog: pytest.LogCaptureFixture
) -> None:
    gateway.status["/system/uptime"] = 500
    with (
        caplog.at_level(logging.WARNING, logger="custom_components.opus_greennet"),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, _gateway_entry(gateway))
    assert (
        f"OPUS gateway {EAG_ID} is not ready, Home Assistant will retry: "
        "status 500: forced failure" in caplog.text
    )
