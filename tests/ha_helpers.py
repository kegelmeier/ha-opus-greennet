"""Public Home Assistant flow helpers for integration tests."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import CONF_ALLOW_SETTING_WRITES, DOMAIN
from tests.fake_gateway import FakeGateway


async def configure_bridge(
    hass: HomeAssistant,
    gateway: FakeGateway,
    *,
    allow_writes: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    """Submit a gateway through the actual config flow manager.

    A new entry starts without write access to device settings. Most tests
    write settings, so the helper switches it on after the entry was created;
    ``allow_writes=False`` keeps the default of a real new installation.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**gateway.config, **overrides}
    )
    if allow_writes and result["type"] is FlowResultType.CREATE_ENTRY:
        hass.config_entries.async_update_entry(
            result["result"], options={CONF_ALLOW_SETTING_WRITES: True}
        )
    return result


async def setup_bridge(hass: HomeAssistant, gateway: FakeGateway) -> ConfigEntry:
    """Configure a gateway, wait for its event stream and return the entry."""
    result = await configure_bridge(hass, gateway)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    await gateway.wait_for_stream()
    await hass.async_block_till_done()
    return hass.config_entries.async_entries(DOMAIN)[0]


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


async def wait_until(predicate: Any, timeout: float = 5) -> None:
    """Poll a condition while the event loop keeps running."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)
