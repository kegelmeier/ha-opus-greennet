"""Lock platform for HOPPE window handles via Opus GreenNet."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID, DEFAULT_CHANNEL, LOCK_LOCKED, LOCK_UNLOCKED
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity

HOPPE_WINDOW_HANDLE_EEP = "D2-06-40"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HOPPE window handle locks from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]
    added_unique_ids: set[str] = set()

    @callback
    def async_add_lock(device: EnOceanDevice) -> None:
        """Add a lock entity for every discovered HOPPE window handle."""
        if device.primary_eep != HOPPE_WINDOW_HANDLE_EEP:
            return

        entity = OpusGreenNetWindowHandleLock(
            coordinator=coordinator,
            eag_id=eag_id,
            gateway_device_id=gateway_device_id,
            device=device,
        )
        if entity.unique_id in added_unique_ids:
            return
        added_unique_ids.add(entity.unique_id)
        async_add_entities([entity])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}",
            async_add_lock,
        )
    )

    for device in coordinator.devices.values():
        async_add_lock(device)


class OpusGreenNetWindowHandleLock(OpusGreenNetEntity, LockEntity):
    """Control whether a HOPPE window handle may be operated."""

    _attr_has_entity_name = True
    _attr_translation_key = "window_handle_lock"

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the HOPPE window handle lock."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)
        self._attr_unique_id = f"{eag_id}_{device.device_id}_lock"

    @property
    def is_locked(self) -> bool | None:
        """Return whether operation of the window handle is blocked."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel is None or channel.lock_state is None:
            return None
        if channel.lock_state == LOCK_LOCKED:
            return True
        if channel.lock_state == LOCK_UNLOCKED:
            return False
        return None

    async def async_lock(self, **kwargs: Any) -> None:
        """Block operation of the window handle."""
        await self._coordinator.async_set_window_handle_lock(
            self._device.device_id, locked=True
        )
        channel = self._device.get_or_create_channel(DEFAULT_CHANNEL)
        channel.lock_state = LOCK_LOCKED
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Allow operation of the window handle."""
        await self._coordinator.async_set_window_handle_lock(
            self._device.device_id, locked=False
        )
        channel = self._device.get_or_create_channel(DEFAULT_CHANNEL)
        channel.lock_state = LOCK_UNLOCKED
        self.async_write_ha_state()
