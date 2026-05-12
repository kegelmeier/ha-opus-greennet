"""Event platform for Opus GreenNet Bridge integration."""
from __future__ import annotations

import logging

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BUTTON_KEYS,
    BUTTON_VALUE_PRESSED,
    BUTTON_VALUE_RELEASED,
    CONF_EAG_ID,
    DOMAIN,
)
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_STATE_UPDATE,
    OpusGreenNetCoordinator,
)
from .enocean_device import EnOceanDevice

_LOGGER = logging.getLogger(__name__)

# One event type per (button, action) pair, e.g. "buttonA0_pressed".
EVENT_TYPES: list[str] = [
    f"{button}_{action}"
    for button in BUTTON_KEYS
    for action in (BUTTON_VALUE_PRESSED, BUTTON_VALUE_RELEASED)
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet event entities from a config entry."""
    coordinator: OpusGreenNetCoordinator = hass.data[DOMAIN][entry.entry_id]
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_event(device: EnOceanDevice) -> None:
        """Add an event entity for a discovered device."""
        if device.entity_type != "event":
            return

        _LOGGER.debug(
            "Adding event entity for device: %s (%s)",
            device.friendly_id,
            device.device_id,
        )

        entities = [
            OpusGreenNetEvent(
                coordinator=coordinator,
                eag_id=eag_id,
                device=device,
            )
        ]
        async_add_entities(entities)

    # Listen for new device discoveries
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}",
            async_add_event,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_event(device)


class OpusGreenNetEvent(EventEntity):
    """Representation of an Opus GreenNet rocker switch event."""

    _attr_has_entity_name = True
    _attr_event_types = EVENT_TYPES

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the event entity."""
        self._coordinator = coordinator
        self._eag_id = eag_id
        self._device = device
        self._device_key = device.friendly_id or device.device_id

        self._attr_unique_id = f"{eag_id}_{device.device_id}"
        self._attr_name = None  # Use device name

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
        """Handle state update from coordinator - fire event."""
        self._device = device

        channel = device.channels.get(0)
        if not channel:
            return

        button = channel.last_button
        action = channel.last_button_action
        if not button or not action:
            return

        event_type = f"{button}_{action}"
        if event_type not in EVENT_TYPES:
            _LOGGER.debug(
                "Ignoring unknown rocker event %s for device %s",
                event_type,
                self._device_key,
            )
            return

        self._trigger_event(event_type, {"button": button, "action": action})
        self.async_write_ha_state()
