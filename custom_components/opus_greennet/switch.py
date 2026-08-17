"""Switch platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID, DEFAULT_CHANNEL
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    OpusGreenNetCoordinator,
)
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet switches from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_switch(device: EnOceanDevice) -> None:
        """Add a switch entity for a discovered device."""
        if device.entity_type != "switch":
            return

        # Create entity for each channel
        entities = []
        for channel_id in range(device.channel_count):
            entities.append(
                OpusGreenNetSwitch(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    gateway_device_id=gateway_device_id,
                    device=device,
                    channel_id=channel_id,
                )
            )

        async_add_entities(entities)

    # Listen for new device discoveries
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}",
            async_add_switch,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_switch(device)


class OpusGreenNetSwitch(OpusGreenNetEntity, SwitchEntity):
    """Representation of an Opus GreenNet switch."""

    _attr_has_entity_name = True
    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        channel_id: int = DEFAULT_CHANNEL,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, eag_id, gateway_device_id, device, channel_id)

        # Entity attributes
        channel_suffix = f"_ch{channel_id}" if device.channel_count > 1 else ""
        self._attr_unique_id = f"{eag_id}_{device.device_id}{channel_suffix}"

        if device.channel_count > 1:
            self._attr_translation_key = "channel"
            self._attr_translation_placeholders = {"channel": str(channel_id)}
        else:
            self._attr_name = None  # Use device name

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        channel = self._device.channels.get(self._channel_id)
        return channel.is_on if channel else False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._coordinator.async_turn_on(self._device.device_id, self._channel_id)
        channel = self._device.get_or_create_channel(self._channel_id)
        channel.is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._coordinator.async_turn_off(self._device.device_id, self._channel_id)
        channel = self._device.get_or_create_channel(self._channel_id)
        channel.is_on = False
        self.async_write_ha_state()
