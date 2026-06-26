"""Light platform for Opus GreenNet Bridge integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_EAG_ID, DEFAULT_CHANNEL, DOMAIN
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_STATE_UPDATE,
    OpusGreenNetCoordinator,
)
from .diagnostics import device_diagnostic_attributes, log_entity_state_write
from .enocean_device import EnOceanDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet lights from a config entry."""
    coordinator: OpusGreenNetCoordinator = hass.data[DOMAIN][entry.entry_id]
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_light(device: EnOceanDevice) -> None:
        """Add a light entity for a discovered device."""
        if device.entity_type != "light":
            return

        _LOGGER.debug(
            "Adding light entity for device: %s (%s)",
            device.friendly_id,
            device.device_id,
        )

        # Create entity for each channel
        entities = []
        for channel_id in range(device.channel_count):
            entities.append(
                OpusGreenNetLight(
                    coordinator=coordinator,
                    eag_id=eag_id,
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
            async_add_light,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_light(device)


class OpusGreenNetLight(LightEntity):
    """Representation of an Opus GreenNet light."""

    _attr_has_entity_name = True
    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
        channel_id: int = DEFAULT_CHANNEL,
    ) -> None:
        """Initialize the light."""
        self._coordinator = coordinator
        self._eag_id = eag_id
        self._device = device
        self._channel_id = channel_id
        self._device_key = device.friendly_id or device.device_id

        # Entity attributes
        channel_suffix = f"_ch{channel_id}" if device.channel_count > 1 else ""
        self._attr_unique_id = f"{eag_id}_{device.device_id}{channel_suffix}"

        if device.channel_count > 1:
            self._attr_name = f"Channel {channel_id}"
        else:
            self._attr_name = None  # Use device name

        # Determine capabilities
        if device.is_dimmable:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._eag_id}_{self._device.device_id}")},
            name=self._device.friendly_id or self._device.device_id,
            manufacturer=self._device.manufacturer or "EnOcean",
            model=self._device.primary_eep or "Unknown",
            via_device=(DOMAIN, self._eag_id),
        )

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        channel = self._device.channels.get(self._channel_id)
        return channel.is_on if channel else False

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light."""
        if not self._device.is_dimmable:
            return None
        channel = self._device.channels.get(self._channel_id)
        if channel and channel.brightness is not None:
            # Convert 0-100 to 0-255
            return int(channel.brightness * 255 / 100)
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return True  # Device availability could be tracked via lastSeen

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic state attributes."""
        return device_diagnostic_attributes(self._device)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)

        # Optimistic state update - update immediately before sending MQTT
        channel = self._device.get_or_create_channel(self._channel_id)
        if brightness is not None and self._device.is_dimmable:
            brightness_pct = int(brightness * 100 / 255)
            channel.brightness = brightness_pct
            channel.is_on = brightness_pct > 0
        else:
            channel.is_on = True
            if self._device.is_dimmable:
                channel.brightness = 100
        self.async_write_ha_state()

        if brightness is not None and self._device.is_dimmable:
            # Convert 0-255 to 0-100
            brightness_pct = int(brightness * 100 / 255)
            await self._coordinator.async_turn_on(
                self._device.device_id,
                self._channel_id,
                brightness_pct,
                is_dimmable=self._device.is_dimmable,
            )
        else:
            await self._coordinator.async_turn_on(
                self._device.device_id,
                self._channel_id,
                is_dimmable=self._device.is_dimmable,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        # Optimistic state update - update immediately before sending MQTT
        channel = self._device.get_or_create_channel(self._channel_id)
        channel.is_on = False
        if self._device.is_dimmable:
            channel.brightness = 0
        self.async_write_ha_state()

        await self._coordinator.async_turn_off(
            self._device.device_id,
            self._channel_id,
            is_dimmable=self._device.is_dimmable,
        )

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DEVICE_STATE_UPDATE}_{self._eag_id}_{self._device_key}",
                self._handle_state_update,
            )
        )

    @callback
    def _handle_state_update(self, device: EnOceanDevice) -> None:
        """Handle state update from coordinator."""
        self._device = device
        log_entity_state_write(
            _LOGGER,
            self.entity_id or self._attr_unique_id,
            self._device,
            self._channel_id,
        )
        self.async_write_ha_state()
