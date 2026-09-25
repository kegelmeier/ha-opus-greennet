"""Lock platform for Opus GreenNet Bridge integration.

AutoLock (HOPPE Smart Window Handle, EEP D2-06-40) — display only.

This entity shows the physical lock state reported by the gateway
(lock=locked|unlocked). It intentionally does NOT send any MQTT command:
- async_lock() / async_unlock() raise ServiceValidationError so that the
  user receives a clear "display only" message in the UI instead of a
  silent no-op or an optimistic local state change.
- No coordinator method is called on user action.
- No channel attribute is mutated locally.
- The displayed state is exclusively driven by incoming gateway pushes.

Entity discovery is driven by entity_descriptions.py (LOCK_DESCRIPTIONS).

FIX: the description object is stored as `_opus_description`, never as
`entity_description` — that attribute name is reserved by Home Assistant's
own Entity base class and caused an AttributeError crash on every
description-driven entity (see entity.py module docstring for details).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
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
    """Set up Opus GreenNet AutoLock entities from a config entry."""
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
    """Display the physical AutoLock state of a HOPPE window handle.

    This entity is intentionally read-only. The gateway does not support
    reliable remote lock/unlock commands; active control is therefore not
    implemented. Any attempt to lock or unlock via the UI or a service call
    raises ServiceValidationError with a descriptive message.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        description: OpusLockDescription,
    ) -> None:
        """Initialize the HOPPE window handle AutoLock entity."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)
        self._opus_description = description
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{description.key}"
        self._attr_translation_key = description.translation_key

    @property
    def is_locked(self) -> bool | None:
        """Return whether the window handle is physically locked."""
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
        """Reject lock commands — this entity is display only."""
        raise ServiceValidationError(
            translation_domain="opus_greennet",
            translation_key="autolock_readonly",
        )

    async def async_unlock(self, **kwargs: Any) -> None:
        """Reject unlock commands — this entity is display only."""
        raise ServiceValidationError(
            translation_domain="opus_greennet",
            translation_key="autolock_readonly",
        )
