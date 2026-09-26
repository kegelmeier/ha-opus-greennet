"""Cover platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
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

# The coordinator serializes commands per device; entities receive pushed state.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet covers from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_cover(device: EnOceanDevice) -> None:
        """Add a cover entity for a discovered device."""
        if device.entity_type != "cover":
            return

        # Create entity for each channel
        entities = []
        for channel_id in range(device.channel_count):
            entities.append(
                OpusGreenNetCover(
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
            async_add_cover,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_cover(device)


class OpusGreenNetCover(OpusGreenNetEntity, CoverEntity):
    """Representation of an Opus GreenNet cover (blinds/shades)."""

    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_device_class = CoverDeviceClass.BLIND

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        channel_id: int = DEFAULT_CHANNEL,
    ) -> None:
        """Initialize the cover."""
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
    def supported_features(self) -> CoverEntityFeature:
        """Return features from the latest cached cover configuration."""
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

        if self._device.supports_tilt_for_channel(self._channel_id):
            features |= CoverEntityFeature.SET_TILT_POSITION

        return features

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
        if not self._device.supports_tilt_for_channel(self._channel_id):
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
    def is_opening(self) -> bool | None:
        """Return if the cover is opening."""
        return None

    @property
    def is_closing(self) -> bool | None:
        """Return if the cover is closing."""
        return None

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        snapshot = self._channel_state_snapshot()
        await self._coordinator.async_set_cover_position(
            self._device.device_id, 0, self._channel_id
        )
        if not self._channel_state_matches(snapshot):
            return
        channel = self._device.get_or_create_channel(self._channel_id)
        channel.position = 0
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        snapshot = self._channel_state_snapshot()
        await self._coordinator.async_set_cover_position(
            self._device.device_id, 100, self._channel_id
        )
        if not self._channel_state_matches(snapshot):
            return
        channel = self._device.get_or_create_channel(self._channel_id)
        channel.position = 100
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self._coordinator.async_stop_cover(
            self._device.device_id, self._channel_id
        )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION)
        if position is not None:
            snapshot = self._channel_state_snapshot()
            # Invert: HA position (0=closed,100=open) → OPUS (0=open,100=closed)
            opus_position = 100 - position
            await self._coordinator.async_set_cover_position(
                self._device.device_id, opus_position, self._channel_id
            )
            if not self._channel_state_matches(snapshot):
                return
            channel = self._device.get_or_create_channel(self._channel_id)
            channel.position = opus_position
            self.async_write_ha_state()

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the cover tilt position."""
        if not self._device.supports_tilt_for_channel(self._channel_id):
            return
        tilt = kwargs.get(ATTR_TILT_POSITION)
        if tilt is not None:
            snapshot = self._channel_state_snapshot()
            await self._coordinator.async_set_cover_tilt(
                self._device.device_id, tilt, self._channel_id
            )
            if not self._channel_state_matches(snapshot):
                return
            channel = self._device.get_or_create_channel(self._channel_id)
            channel.angle = tilt
            self.async_write_ha_state()
