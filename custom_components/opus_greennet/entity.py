"""Shared entities for the Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_registry import EntityRegistry

from .const import DOMAIN
from .coordinator import (
    SIGNAL_AVAILABILITY_UPDATE,
    SIGNAL_DEVICE_STATE_UPDATE,
    OpusGreenNetCoordinator,
)
from .diagnostics import log_entity_state_write
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
        legacy_entity_id,
        new_unique_id=channel_zero_unique_id,
    )
    _LOGGER.info(
        "Migrated legacy entity %s to multi-channel unique ID %s",
        legacy_entity_id,
        channel_zero_unique_id,
    )


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
            model=self._device.primary_eep or "Unknown",
            serial_number=self._device.device_id,
            via_device_id=self._gateway_device_id,
        )

    @property
    def available(self) -> bool:
        """Return whether the MQTT transport is connected."""
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
