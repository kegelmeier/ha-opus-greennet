"""Shared entities for the Opus GreenNet Bridge integration.

Base: kegelmeier v0.3.3b0. Adds generic, description-driven base classes
(OpusGreenNetDescriptiveBinarySensor / ...Sensor) so new devices never
require touching platform files - only entity_descriptions.py.

FIX: the description object from entity_descriptions.py must NEVER be
assigned to `self.entity_description`. That attribute name is reserved by
Home Assistant's own `homeassistant.helpers.entity.Entity` base class, which
internally reads `self.entity_description.translation_placeholders`,
`.has_entity_name`, etc. as part of computing the entity's display name.
Our lightweight OpusEntityDescription dataclass is not a HA EntityDescription
and does not have those fields, which crashed entity setup with
`AttributeError: 'OpusSensorDescription' object has no attribute
'translation_placeholders'` for every description-driven entity. Fixed by
storing our descriptor under the private name `_opus_description` instead.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_registry import EntityRegistry

from .const import DOMAIN, EEP_MAPPINGS
from .coordinator import (
    SIGNAL_AVAILABILITY_UPDATE,
    SIGNAL_DEVICE_STATE_UPDATE,
    OpusGreenNetCoordinator,
)
from .diagnostics import log_entity_state_write
from .entity_descriptions import OpusEntityDescription
from .enocean_device import EnOceanChannel, EnOceanDevice

_LOGGER = logging.getLogger(__name__)


@callback
def migrate_legacy_multichannel_entity(
    entity_registry: EntityRegistry,
    entity_domain: str,
    eag_id: str,
    device: EnOceanDevice,
) -> None:
    """Migrate or remove a stale single-channel registry entry."""
    if device.channel_count <= 1:
        return

    legacy_unique_id = f"{eag_id}_{device.device_id}"
    channel_zero_unique_id = f"{legacy_unique_id}_ch0"
    legacy_entity_id = entity_registry.async_get_entity_id(
        entity_domain, DOMAIN, legacy_unique_id
    )
    if legacy_entity_id is None:
        return

    channel_zero_entity_id = entity_registry.async_get_entity_id(
        entity_domain, DOMAIN, channel_zero_unique_id
    )
    if channel_zero_entity_id is not None:
        entity_registry.async_remove(legacy_entity_id)
        _LOGGER.info(
            "Removed obsolete aggregate entity %s; channel 0 is %s",
            legacy_entity_id,
            channel_zero_entity_id,
        )
        return

    entity_registry.async_update_entity(
        legacy_entity_id, new_unique_id=channel_zero_unique_id
    )
    _LOGGER.info(
        "Migrated legacy entity %s to multi-channel unique ID %s",
        legacy_entity_id,
        channel_zero_unique_id,
    )


def model_name_for(device: EnOceanDevice) -> str:
    """Return a human-friendly model name, falling back to the raw EEP."""
    eep = device.primary_eep
    if eep and eep in EEP_MAPPINGS:
        return EEP_MAPPINGS[eep][1]
    return eep or "Unknown"


class OpusGreenNetEntity(Entity):
    """Base class for entities owned by an Opus GreenNet gateway."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        channel_id: int = 0,
    ) -> None:
        """Initialize common entity state."""
        self._coordinator = coordinator
        self._eag_id = eag_id
        self._gateway_device_id = gateway_device_id
        self._device = device
        self._channel_id = channel_id

    def _channel_state_snapshot(self) -> tuple[EnOceanChannel | None, int]:
        """Capture the channel before awaiting a command acknowledgement."""
        channel = self._device.channels.get(self._channel_id)
        return channel, channel.state_revision if channel is not None else 0

    def _channel_state_matches(
        self, snapshot: tuple[EnOceanChannel | None, int]
    ) -> bool:
        """Only estimate state when no newer channel update arrived while waiting."""
        channel = self._device.channels.get(self._channel_id)
        return channel is snapshot[0] and (
            channel is None or channel.state_revision == snapshot[1]
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._eag_id}_{self._device.device_id}")},
            name=self._device.friendly_id or self._device.device_id,
            manufacturer=self._device.manufacturer or "OPUS / EnOcean",
            model=model_name_for(self._device),
            serial_number=self._device.device_id,
            sw_version=self._device.software_revision or None,
            hw_version=self._device.hardware_revision or None,
            via_device_id=self._gateway_device_id,
        )

    @property
    def available(self) -> bool:
        """Return whether the coordinator considers the gateway reachable."""
        return self._coordinator.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates for this stable device identifier."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DEVICE_STATE_UPDATE}_{self._eag_id}_{self._device.device_id}",
                self._handle_state_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_AVAILABILITY_UPDATE}_{self._eag_id}",
                self._handle_availability_update,
            )
        )

    @callback
    def _handle_availability_update(self) -> None:
        """Refresh availability without interpreting cached data as a new event."""
        self.async_write_ha_state()

    @callback
    def _handle_state_update(self, device: EnOceanDevice) -> None:
        """Handle a coordinator state update."""
        self._device = device
        log_entity_state_write(
            _LOGGER,
            self.entity_id or self._attr_unique_id,
            self._device,
            self._channel_id,
        )
        self.async_write_ha_state()


class _OpusGreenNetDescriptiveEntity(OpusGreenNetEntity):
    """Common wiring for entities driven by entity_descriptions.py.

    IMPORTANT: our descriptor is stored as `_opus_description`, never as
    `entity_description` - that name is reserved by Home Assistant's own
    Entity base class (see module docstring for the crash this caused).
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        description: OpusEntityDescription,
    ) -> None:
        """Initialize a description-driven entity."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)
        self._opus_description = description
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{description.key}"
        self._attr_translation_key = description.translation_key
        self._attr_entity_category = description.entity_category
        self._attr_entity_registry_enabled_default = description.enabled_by_default


class OpusGreenNetDescriptiveBinarySensor(
    _OpusGreenNetDescriptiveEntity, BinarySensorEntity
):
    """Generic binary_sensor entity driven by an OpusBinarySensorDescription."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialize and apply the description's device class."""
        super().__init__(*args, **kwargs)
        self._attr_device_class = self._opus_description.device_class

    @property
    def is_on(self) -> bool | None:
        """Return the description's value_fn result for the current device."""
        return self._opus_description.value_fn(self._device)


class OpusGreenNetDescriptiveSensor(_OpusGreenNetDescriptiveEntity, SensorEntity):
    """Generic sensor entity driven by an OpusSensorDescription."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialize and apply the description's sensor attributes."""
        super().__init__(*args, **kwargs)
        self._attr_device_class = self._opus_description.device_class
        self._attr_state_class = self._opus_description.state_class
        self._attr_native_unit_of_measurement = (
            self._opus_description.native_unit_of_measurement
        )
        if self._opus_description.options is not None:
            self._attr_options = list(self._opus_description.options)

    @property
    def native_value(self):
        """Return the description's value_fn result for the current device."""
        return self._opus_description.value_fn(self._device)
