"""Shared entities for the Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN
from .coordinator import SIGNAL_DEVICE_STATE_UPDATE, OpusGreenNetCoordinator
from .diagnostics import log_entity_state_write
from .enocean_device import EnOceanDevice

_LOGGER = logging.getLogger(__name__)


class OpusGreenNetEntity(Entity):
    """Base class for entities owned by an Opus GreenNet gateway."""

    _attr_has_entity_name = True

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
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DEVICE_STATE_UPDATE}_{self._eag_id}_{self._device.device_id}",
                self._handle_state_update,
            )
        )

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
