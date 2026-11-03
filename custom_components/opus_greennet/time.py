"""Time platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID, DOMAIN
from .coordinator import OpusGreenNetCoordinator

# The value lives in Home Assistant only; nothing is sent to the gateway.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the time entity of the gateway device."""
    async_add_entities(
        [
            OpusGreenNetDailySettingsTime(
                entry.runtime_data.coordinator, entry.data[CONF_EAG_ID]
            )
        ]
    )


class OpusGreenNetDailySettingsTime(TimeEntity, RestoreEntity):
    """Local time of the daily read of the stored settings (gateway device).

    The choice is restored after a restart of Home Assistant. It only moves the
    schedule of the coordinator; whether the read runs at all is decided by the
    switch *Daily settings read*.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "daily_settings_read_time"

    def __init__(self, coordinator: OpusGreenNetCoordinator, eag_id: str) -> None:
        """Initialize the entity for the gateway ``eag_id``."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{eag_id}_daily_settings_read_time"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, eag_id)})

    @property
    def native_value(self) -> time:
        """Return the local time of the daily read."""
        return self._coordinator.daily_settings_time

    async def async_added_to_hass(self) -> None:
        """Restore the time chosen before the restart."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is None:
            return
        try:
            restored = time.fromisoformat(last.state)
        except ValueError:
            return
        self._coordinator.async_set_daily_settings(at=restored)

    async def async_set_value(self, value: time) -> None:
        """Move the daily read to another local time."""
        self._coordinator.async_set_daily_settings(at=value)
        self.async_write_ha_state()
