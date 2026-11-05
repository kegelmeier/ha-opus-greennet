"""Shared fixtures for Opus GreenNet tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import frame
from homeassistant.helpers import issue_registry as ir

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway


class _PlainResolver(aiohttp.ThreadedResolver):
    """Home Assistant's resolver without zeroconf (no network component here)."""

    async def real_close(self) -> None:
        await self.close()


@pytest.fixture(autouse=True)
def _no_dns_in_diagnostics() -> Iterator[None]:
    """Keep the diagnostics download from asking a real resolver."""
    with patch(
        "custom_components.opus_greennet.diagnostics.async_gateway_address",
        AsyncMock(return_value=(None, None, None)),
    ):
        yield


@pytest.fixture(autouse=True)
def _no_webui_2026_detection() -> Iterator[None]:
    """Keep the tests from probing real addresses for the newer generation.

    A gateway that does not answer would otherwise be asked for HTTPS on port
    443. Tests of that path replace this patch with their own endpoint.
    """
    with (
        patch(
            "custom_components.opus_greennet.config_flow.async_detect_webui_2026",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.opus_greennet.async_detect_webui_2026",
            AsyncMock(return_value=None),
        ),
    ):
        yield


@pytest.fixture
async def hass(tmp_path: Path) -> AsyncIterator[HomeAssistant]:
    """Run real Home Assistant managers without starting external integrations.

    Import Home Assistant before the custom integration so its validation
    backend is initialized in the same order as a normal HA startup.
    """
    component_directory = Path(__file__).resolve().parents[1] / "custom_components"
    (tmp_path / "custom_components").symlink_to(component_directory)
    resolver = patch(
        "homeassistant.helpers.aiohttp_client._async_make_resolver",
        side_effect=lambda hass: _PlainResolver(),
    )
    resolver.start()
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    frame.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    dr.async_setup(hass)
    # Home Assistant 2026.11 loads the entity registry only after the config
    # entries are initialized, so initialize them first (older releases do not
    # care about the order).
    await hass.config_entries.async_initialize()
    await asyncio.gather(
        ar.async_load(hass, load_empty=True),
        dr.async_load(hass, load_empty=True),
        er.async_load(hass, load_empty=True),
        ir.async_load(hass, load_empty=True),
    )
    try:
        yield hass
    finally:
        for entry in hass.config_entries.async_entries("opus_greennet"):
            await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_stop(force=True)
        resolver.stop()


@pytest.fixture
async def gateway() -> AsyncIterator[FakeGateway]:
    """Run a fake OPUS gateway on a local port."""
    fake = FakeGateway()
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


@pytest.fixture
def make_device():
    """Factory fixture for creating EnOceanDevice instances."""

    def _make(
        eep: str,
        device_id: str = "AABB1122",
        friendly_id: str = "Test Device",
    ) -> EnOceanDevice:
        return EnOceanDevice(
            device_id=device_id,
            friendly_id=friendly_id,
            eeps=[{"eep": eep}],
        )

    return _make


@pytest.fixture
def make_telegram():
    """Factory fixture for creating telegram dicts."""

    def _make(
        functions: list[dict],
        timestamp: str | None = None,
        dbm: int | None = None,
    ) -> dict:
        telegram: dict = {"functions": functions}
        if timestamp:
            telegram["timestamp"] = timestamp
        if dbm is not None:
            telegram["telegramInfo"] = {"dbm": dbm}
        return telegram

    return _make


@pytest.fixture
def coordinator():
    """Create a coordinator with mocked hass and patched async_send_command."""
    hass = MagicMock()
    c = OpusGreenNetCoordinator(hass, MagicMock(), "AABB0011")
    c.async_send_command = AsyncMock()
    return c
