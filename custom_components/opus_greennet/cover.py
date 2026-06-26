"""Cover platform for Opus GreenNet Bridge integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntity,
    CoverEntityFeature,
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
    """Set up Opus GreenNet covers from a config entry."""
    coordinator: OpusGreenNetCoordinator = hass.data[DOMAIN][entry.entry_id]
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_cover(device: EnOceanDevice) -> None:
        """Add a cover entity for a discovered device."""
        if device.entity_type != "cover":
            return

        _LOGGER.debug(
            "Adding cover entity for device: %s (%s)",
            device.friendly_id,
            device.device_id,
        )

        # Create entity for each channel
        entities = []
        for channel_id in range(device.channel_count):
            entities.append(
                OpusGreenNetCover(
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
            async_add_cover,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_cover(device)


class OpusGreenNetCover(CoverEntity):
    """Representation of an Opus GreenNet cover (blinds/shades)."""

    _attr_has_entity_name = True
    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
        channel_id: int = DEFAULT_CHANNEL,
    ) -> None:
        """Initialize the cover."""
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

        # Determine supported features
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

        if device.supports_tilt:
            features |= CoverEntityFeature.SET_TILT_POSITION

        self._attr_supported_features = features

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
    def current_cover_position(self) -> int | None:
        """Return current position of cover.

        0 is closed, 100 is fully open.
        OPUS uses inverted scale: 0 = fully open, 100 = fully closed.
        """
        channel = self._device.channels.get(self._channel_id)
        if channel is None or channel.position is None:
            return None
        return 100 - channel.position

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return current tilt position of cover."""
        if not self._device.supports_tilt:
            return None
        channel = self._device.channels.get(self._channel_id)
        return channel.angle if channel else None

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        return False  # Would need to track state transitions

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        return False  # Would need to track state transitions

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic state attributes."""
        return device_diagnostic_attributes(self._device)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        # Optimistic state update - update immediately before sending MQTT
        # OPUS: 0 = fully open
        channel = self._device.get_or_create_channel(self._channel_id)
        channel.position = 0
        self.async_write_ha_state()

        await self._coordinator.async_set_cover_position(
            self._device.device_id, 0, self._channel_id
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        # Optimistic state update - update immediately before sending MQTT
        # OPUS: 100 = fully closed
        channel = self._device.get_or_create_channel(self._channel_id)
        channel.position = 100
        self.async_write_ha_state()

        await self._coordinator.async_set_cover_position(
            self._device.device_id, 100, self._channel_id
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self._coordinator.async_stop_cover(self._device.device_id, self._channel_id)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION)
        if position is not None:
            # Invert: HA position (0=closed,100=open) → OPUS (0=open,100=closed)
            opus_position = 100 - position
            # Optimistic state update - update immediately before sending MQTT
            channel = self._device.get_or_create_channel(self._channel_id)
            channel.position = opus_position
            self.async_write_ha_state()

            await self._coordinator.async_set_cover_position(
                self._device.device_id, opus_position, self._channel_id
            )

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the cover tilt position."""
        tilt = kwargs.get(ATTR_TILT_POSITION)
        if tilt is not None:
            # Optimistic state update - update immediately before sending MQTT
            channel = self._device.get_or_create_channel(self._channel_id)
            channel.angle = tilt
            self.async_write_ha_state()

            await self._coordinator.async_set_cover_tilt(
                self._device.device_id, tilt, self._channel_id
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
