"""Sensor platform for Opus GreenNet Bridge integration.

Fully generic: every device's sensors come from
entity_descriptions.descriptions_for(device, "sensor"). This platform file
never checks a specific EEP (Open/Closed Principle).
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .entity import OpusGreenNetDescriptiveSensor
from .entity_descriptions import descriptions_for
from .enocean_device import EnOceanDevice

# Sensor state is pushed by the coordinator.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]
    added_unique_ids: set[str] = set()

    @callback
    def async_add_sensors(device: EnOceanDevice) -> None:
        """Add every applicable sensor entity for a discovered device."""
        entities: list[SensorEntity] = [
            OpusGreenNetDescriptiveSensor(
                coordinator, eag_id, gateway_device_id, device, description
            )
            for description in descriptions_for(device, "sensor")
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
            async_add_sensors,
        )
    )

    for device in coordinator.devices.values():
        async_add_sensors(device)
