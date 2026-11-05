"""Event platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
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


def event_types(buttons: tuple[str, ...]) -> list[str]:
    """Return one event type per (button, action) pair, e.g. "buttonA0_pressed"."""
    return [
        f"{button}_{action}"
        for button in buttons
        for action in (BUTTON_VALUE_PRESSED, BUTTON_VALUE_RELEASED)
    ]


# Every event type any rocker can report (F6-03-xx: rockers A to D).
EVENT_TYPES: list[str] = event_types(BUTTON_KEYS)


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
        """Add an event entity for a rocker or an actuator's rocker buttons."""
        if device.entity_type != "event" and not device.has_secondary_rocker:
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


# Home Assistant 2026.11 marks EventEntity._async_write_ha_state as @final, and
# OpusGreenNetEntity overrides it to keep the dispatch projection. That is
# safe here: the override runs first in the MRO, only resets the projection
# bookkeeping (which event entities do not use, see _handle_state_update) and
# then calls the final method through super().
class OpusGreenNetEvent(OpusGreenNetEntity, EventEntity):  # type: ignore[misc]
    """Representation of an Opus GreenNet rocker switch event."""

    _attr_has_entity_name = True
    # Rockers are physical push buttons. Registry and frontend metadata only:
    # unique ID, event types, name and enablement stay unchanged, and a
    # user-chosen icon still takes precedence.
    _attr_device_class = EventDeviceClass.BUTTON
    # Button events serve automations: enabled, but hidden from dashboards
    # by default. Applies to new registry entries; a user's choice and
    # existing entries are kept.
    _attr_entity_registry_visible_default = False

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)
        self._attr_event_types = event_types(device.button_keys)
        if device.entity_type == "event":
            # A rocker switch: the event entity is the device itself.
            self._attr_unique_id = f"{eag_id}_{device.device_id}"
            self._attr_name = None  # Use device name
        else:
            # Buttons of an actuator: the plain ID belongs to its switch,
            # light or cover entity.
            self._attr_unique_id = f"{eag_id}_{device.device_id}_buttons"
            self._attr_translation_key = "buttons"

    @callback
    def _handle_state_update(self, device: EnOceanDevice) -> None:
        """Handle state update from coordinator - fire event.

        A telegram's button occurrence sits on the channel the gateway names
        (channel 0 for rockers). Every channel with an occurrence fires its own
        event, lowest channel first; only events of another channel than 0 carry
        a ``channel`` entry, so the data of existing events stays as it was.
        """
        self._device = device

        for channel_id in sorted(device.channels):
            channel = device.channels[channel_id]
            button = channel.last_button
            action = channel.last_button_action
            if not button or not action:
                continue

            event_type = f"{button}_{action}"
            if event_type not in self._attr_event_types:
                _LOGGER.debug(
                    "Ignoring unknown rocker event %s for device %s",
                    event_type,
                    self._device.device_id,
                )
                continue

            data: dict[str, str | int] = {"button": button, "action": action}
            if channel_id != 0:
                data["channel"] = channel_id
            self._trigger_event(event_type, data)
            log_entity_state_write(
                _LOGGER,
                self.entity_id or self._attr_unique_id,
                self._device,
                channel_id,
            )
            self.async_write_ha_state()
