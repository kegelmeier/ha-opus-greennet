"""Binary sensor platform for Opus GreenNet Bridge integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
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
    """Set up Opus GreenNet binary sensors from a config entry."""
    coordinator: OpusGreenNetCoordinator = hass.data[DOMAIN][entry.entry_id]
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_binary_sensors(device: EnOceanDevice) -> None:
        """Add binary sensor entities for a discovered device."""
        if not device.is_climate:
            return

        entities: list[BinarySensorEntity] = []

        # Window open (all HeatArea types)
        entities.append(
            OpusGreenNetWindowSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                device=device,
            )
        )

        # Actuator not responding (all types)
        entities.append(
            OpusGreenNetProblemSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                device=device,
                suffix="actuator_not_responding",
                name="Actuator not responding",
                attr_name="actuator_not_responding",
            )
        )

        # Missing temperature (all types)
        entities.append(
            OpusGreenNetProblemSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                device=device,
                suffix="missing_temperature",
                name="Missing temperature",
                attr_name="missing_temperature",
            )
        )

        # Actuator low battery (D1-4B-05 Valve only)
        if device.primary_eep == "D1-4B-05":
            entities.append(
                OpusGreenNetBatterySensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    device=device,
                )
            )

            # Actuator deactivated (D1-4B-05 Valve only)
            entities.append(
                OpusGreenNetProblemSensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    device=device,
                    suffix="actuator_deactivated",
                    name="Actuator deactivated",
                    attr_name="actuator_deactivated",
                )
            )

        # Circuit in use (D1-4B-06 CosiTherm only)
        if device.primary_eep == "D1-4B-06":
            entities.append(
                OpusGreenNetProblemSensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    device=device,
                    suffix="circuit_in_use",
                    name="Circuit in use",
                    attr_name="circuit_in_use",
                )
            )

        async_add_entities(entities)

    # Listen for new device discoveries
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}",
            async_add_binary_sensors,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_binary_sensors(device)


class OpusGreenNetBaseBinarySensor(BinarySensorEntity):
    """Base class for Opus GreenNet binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
        suffix: str,
        name: str,
    ) -> None:
        """Initialize the binary sensor."""
        self._coordinator = coordinator
        self._eag_id = eag_id
        self._device = device
        self._device_key = device.friendly_id or device.device_id
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{suffix}"
        self._attr_name = name

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
    def available(self) -> bool:
        """Return if entity is available."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic state attributes."""
        return device_diagnostic_attributes(self._device)

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
            DEFAULT_CHANNEL,
        )
        self.async_write_ha_state()


class OpusGreenNetWindowSensor(OpusGreenNetBaseBinarySensor):
    """Window open binary sensor for HeatArea devices."""

    _attr_device_class = BinarySensorDeviceClass.WINDOW

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the window sensor."""
        super().__init__(coordinator, eag_id, device, "window_open", "Window")

    @property
    def is_on(self) -> bool | None:
        """Return true if window is open."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel:
            return channel.window_open
        return None


class OpusGreenNetProblemSensor(OpusGreenNetBaseBinarySensor):
    """Problem/error binary sensor for HeatArea devices."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
        suffix: str,
        name: str,
        attr_name: str,
    ) -> None:
        """Initialize the problem sensor."""
        super().__init__(coordinator, eag_id, device, suffix, name)
        self._attr_name_key = attr_name

    @property
    def is_on(self) -> bool | None:
        """Return true if there is a problem (value is not 'reset' and not None)."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if not channel:
            return None
        value = getattr(channel, self._attr_name_key, None)
        if value is None:
            return None
        # "reset" means the error/warning has been cleared
        return value != "reset"


class OpusGreenNetBatterySensor(OpusGreenNetBaseBinarySensor):
    """Low battery binary sensor for Valve Area (D1-4B-05) devices."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the battery sensor."""
        super().__init__(
            coordinator, eag_id, device, "actuator_low_battery", "Actuator battery"
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if battery is low.

        Note: BinarySensorDeviceClass.BATTERY is_on=True means low battery.
        """
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if not channel:
            return None
        value = channel.actuator_low_battery
        if value is None:
            return None
        return value != "reset"
