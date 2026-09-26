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

READ-ONLY NOTE (2026-09-26): The HOPPE AutoLock lock_state is pushed
exclusively by the gateway; Home Assistant must never write back to it.
async_lock() and async_unlock() therefore raise HomeAssistantError
instead of publishing any command or mutating local state.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID, DOMAIN
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .entity import OpusGreenNetEntity
from .entity_descriptions import OpusLockDescription, descriptions_for
from .enocean_device import EnOceanDevice

# The coordinator serializes commands per device; entities receive pushed state.
PARALLEL_UPDATES = 0

_READ_ONLY_MSG = (
    "Der HOPPE-AutoLock-Zustand ist schreibgeschützt und wird ausschließlich "
    "vom OPUS-Gateway aktualisiert. Befehle aus der Home-Assistant-GUI werden "
    "nicht an das Gateway weitergeleitet."
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
    """Read-only representation of the HOPPE AutoLock permission state.

    NOTE: the value_fn reads `channel.lock_state` ("locked"/"unlocked"),
    the EEP D2-06-40 "Lock Status" byte - never `channel.handle_state`
    ("open"/"closed"/"tilt", the "Handle Status" byte). These are two
    independent fields; conflating them was the original bug report.

    Write operations (async_lock / async_unlock) are intentionally blocked:
    the HOPPE gateway does not accept lock commands over the Mosquitto bridge
    without a dedicated outbound bridge rule, and issuing optimistic state
    updates without confirmation is misleading. The entity is a pure state
    mirror of what the gateway reports.
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
        """Reject GUI lock commands - state is controlled by the gateway only."""
        raise HomeAssistantError(_READ_ONLY_MSG)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Reject GUI unlock commands - state is controlled by the gateway only."""
        raise HomeAssistantError(_READ_ONLY_MSG)
