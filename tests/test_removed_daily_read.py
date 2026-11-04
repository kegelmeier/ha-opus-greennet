"""The entities of the daily settings read of v1.2.0b1 are removed at setup."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet import _remove_daily_read_entities
from custom_components.opus_greennet.const import DOMAIN

EAG_ID = "0123ABCD"


async def test_entities_of_the_daily_read_are_removed(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    removed = [
        registry.async_get_or_create(platform, DOMAIN, f"{EAG_ID}_{suffix}").entity_id
        for platform, suffix in (
            ("switch", "daily_settings_read"),
            ("time", "daily_settings_read_time"),
            ("sensor", "daily_settings_run"),
        )
    ]
    kept = registry.async_get_or_create(
        "sensor", DOMAIN, f"{EAG_ID}_gateway_uptime"
    ).entity_id
    other_gateway = registry.async_get_or_create(
        "switch", DOMAIN, "FFFF0000_daily_settings_read"
    ).entity_id

    _remove_daily_read_entities(hass, EAG_ID)

    assert all(registry.async_get(entity_id) is None for entity_id in removed)
    assert registry.async_get(kept) is not None
    assert registry.async_get(other_gateway) is not None
