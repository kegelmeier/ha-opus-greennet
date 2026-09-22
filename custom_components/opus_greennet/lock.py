"""Lock platform for Opus GreenNet Bridge integration.

Ported from the fubu2k fork (HOPPE Smart Window Handle, EEP D2-06-40).

Known limitation (carried over intentionally, per product decision): the
OPUS gateway's window-handle access-control command does not reliably change
the physical lock state in the field (confirmed by fubu2k's own testing
notes: "changing Lock-Status is not working"). The entity is ported as-is,
including the v0.3.3b0 optimistic-update guard.

Entity discovery is driven by entity_descriptions.py (LOCK_DESCRIPTIONS), not
by a hardcoded EEP check, consistent with binary_sensor.py / sensor.py.

FIX: the description object is stored as `_opus_description`, never as
`entity_description` - that attribute name is reserved by Home Assistant's
own Entity base class and caused an AttributeError crash on every
description-driven entity (see entity.py module docstring for details).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .entity import OpusGreenNetEntity
from .entity_descriptions import OpusLockDescription, descriptions_for
from .enocean_device import EnOceanDevice

# The coordinator serializes commands per device; entities receive pushed state.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet locks from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]
    added_unique_ids: set[str] = set()

    @callback
    def async_add_locks(device: EnOceanDevice) -> None:
        """Add every applicable lock entity for a discovered device."""
        entities: list[OpusGreenNetWindowHandleLock] = [
            OpusGreenNetWindowHandleLock(
                coordinator, eag_id, gateway_device_id, device, description
            )
            for description in descriptions_for(device, "lock")
        ]
        new_entities = [
            entity for entity in entities if entity.unique_id not in added_unique_ids
        ]
        added_unique_ids.update(entity.unique_id for entity in new_entities)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}",
            async_add_locks,
        )
    )

    for device in coordinator.devices.values():
        async_add_locks(device)


class OpusGreenNetWindowHandleLock(OpusGreenNetEntity, LockEntity):
    """Control whether a HOPPE window handle may be operated."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        description: OpusLockDescription,
    ) -> None:
        """Initialize the HOPPE window handle lock."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)
        self._opus_description = description
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{description.key}"
        self._attr_translation_key = description.translation_key

    @property
    def is_locked(self) -> bool | None:
        """Return whether operation of the window handle is blocked."""
        description = self._opus_description
        state = description.value_fn(self._device)
        if state is None:
            return None
        if state == description.locked_state:
            return True
        if state == description.unlocked_state:
            return False
        return None

    async def async_lock(self, **kwargs: Any) -> None:
        """Block operation of the window handle."""
        description = self._opus_description
        snapshot = self._channel_state_snapshot()
        command = getattr(self._coordinator, description.coordinator_method)
        await command(self._device.device_id, locked=True)
        if not self._channel_state_matches(snapshot):
            return
        channel = self._device.get_or_create_channel(self._channel_id)
        setattr(channel, description.lock_state_attr, description.locked_state)
        channel.state_revision += 1
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Allow operation of the window handle."""
        description = self._opus_description
        snapshot = self._channel_state_snapshot()
        command = getattr(self._coordinator, description.coordinator_method)
        await command(self._device.device_id, locked=False)
        if not self._channel_state_matches(snapshot):
            return
        channel = self._device.get_or_create_channel(self._channel_id)
        setattr(channel, description.lock_state_attr, description.unlocked_state)
        channel.state_revision += 1
        self.async_write_ha_state()
