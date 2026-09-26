"""Lock platform for the HOPPE eLock window handle (EEP D2-06-40).

RE-ADDED 2026-09-23: an earlier revision replaced this with a native HA
MQTT-discovery entity to avoid a duplicate "ghost entity" bug. That bug's
real cause was fixed at the data-model level (enocean_device.py now reads
the correct `lock` status key instead of `handle`) - it was never actually
caused by using a custom-component entity per se. Native MQTT-discovery
devices are always owned by the `mqtt` integration's device registry and
can never be grouped onto this integration's own device page, which the
user explicitly wants (lock button next to handle_state/unlock_request/
signal_strength on the same "T_Fenstergriff" device). Reverted to a plain
LockEntity, consistent with every other entity in this integration.

Discovery is driven by entity_descriptions.py (LOCK_DESCRIPTIONS), not a
hardcoded EEP check, consistent with binary_sensor.py / sensor.py.

FIX 2026-09-26: HOPPE D2-06-40 is a READ-ONLY source from the HA
perspective. Physical state changes originate at the handle itself and are
pushed by the gateway via MQTT stream topics.  The integration MUST NOT
write back to the gateway when the GUI lock/unlock button is pressed,
because:
  a) the Mosquitto bridge maps stream/# as inbound-only, so any publish
     would silently disappear before reaching the gateway broker; and
  b) the AutoLock feature is intentionally controlled by the physical
     handle, not by home automation.

async_lock / async_unlock now raise HomeAssistantError immediately so the
UI shows a clear "read-only" message and no optimistic local state change
is applied.  The gateway-pushed state via stream/device/#/states/... remains
the single source of truth.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
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

_READ_ONLY_MSG = (
    "Der HOPPE-AutoLock-Zustand ist schreibgeschützt und wird "
    "ausschließlich vom OPUS-Gateway aktualisiert. "
    "Manuelle Bedienung über die GUI ist nicht möglich."
)


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
    """Read-only mirror of the HOPPE D2-06-40 AutoLock permission state.

    NOTE: the value_fn reads `channel.lock_state` ("locked"/"unlocked"),
    the EEP D2-06-40 "Lock Status" byte - never `channel.handle_state`
    ("open"/"closed"/"tilt", the "Handle Status" byte). These are two
    independent fields; conflating them was the original bug report.

    This entity is intentionally read-only. async_lock / async_unlock
    raise HomeAssistantError to prevent any GUI writeback to the gateway.
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
        """Reject GUI write – HOPPE AutoLock state is read-only."""
        raise HomeAssistantError(_READ_ONLY_MSG)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Reject GUI write – HOPPE AutoLock state is read-only."""
        raise HomeAssistantError(_READ_ONLY_MSG)
