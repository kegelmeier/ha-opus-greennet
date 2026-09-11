"""Event platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import (
    BUTTON_KEYS,
    BUTTON_VALUE_PRESSED,
    BUTTON_VALUE_RELEASED,
    CONF_EAG_ID,
)
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    OpusGreenNetCoordinator,
)
from .diagnostics import log_entity_state_write
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity

# Events are pushed by the coordinator.
PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

# One event type per (button, action) pair, e.g. "buttonA0_pressed".
EVENT_TYPES: list[str] = [
    f"{button}_{action}"
    for button in BUTTON_KEYS
    for action in (BUTTON_VALUE_PRESSED, BUTTON_VALUE_RELEASED)
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet event entities from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
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
                gateway_device_id=gateway_device_id,
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


class OpusGreenNetEvent(OpusGreenNetEntity, EventEntity):
    """Representation of an Opus GreenNet rocker switch event."""

    _attr_has_entity_name = True
    _attr_event_types = EVENT_TYPES

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)

        self._attr_unique_id = f"{eag_id}_{device.device_id}"
        self._attr_name = None  # Use device name

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
                self._device.device_id,
            )
            return

        self._trigger_event(event_type, {"button": button, "action": action})
        log_entity_state_write(
            _LOGGER,
            self.entity_id or self._attr_unique_id,
            self._device,
            0,
        )
        self.async_write_ha_state()
