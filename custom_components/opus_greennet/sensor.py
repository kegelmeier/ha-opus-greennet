"""Sensor platform for Opus GreenNet Bridge integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfEnergy,
    UnitOfTemperature,
)
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
    """Set up Opus GreenNet sensors from a config entry."""
    coordinator: OpusGreenNetCoordinator = hass.data[DOMAIN][entry.entry_id]
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_sensors(device: EnOceanDevice) -> None:
        """Add sensor entities for a discovered device."""
        entities: list[SensorEntity] = []

        # Climate devices get humidity, feed temperature, and energy sensors
        if device.is_climate:
            # Humidity sensor (all HeatArea types)
            entities.append(
                OpusGreenNetHumiditySensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    device=device,
                )
            )

            # Feed temperature (D1-4B-05 Valve Area only)
            if device.primary_eep == "D1-4B-05":
                entities.append(
                    OpusGreenNetFeedTemperatureSensor(
                        coordinator=coordinator,
                        eag_id=eag_id,
                        device=device,
                    )
                )

            # Energy consumption (D1-4B-07 Electro Heating only)
            if device.primary_eep == "D1-4B-07":
                entities.append(
                    OpusGreenNetEnergyConsumptionSensor(
                        coordinator=coordinator,
                        eag_id=eag_id,
                        device=device,
                    )
                )

        # Signal strength sensor (all devices with dbm data)
        entities.append(
            OpusGreenNetSignalStrengthSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                device=device,
            )
        )

        if entities:
            async_add_entities(entities)

    # Listen for new device discoveries
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}",
            async_add_sensors,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_sensors(device)


class OpusGreenNetBaseSensor(SensorEntity):
    """Base class for Opus GreenNet sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
        suffix: str,
        name: str,
    ) -> None:
        """Initialize the sensor."""
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


class OpusGreenNetHumiditySensor(OpusGreenNetBaseSensor):
    """Humidity sensor for HeatArea devices."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the humidity sensor."""
        super().__init__(coordinator, eag_id, device, "humidity", "Humidity")

    @property
    def native_value(self) -> float | None:
        """Return the humidity value."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.humidity if channel else None


class OpusGreenNetFeedTemperatureSensor(OpusGreenNetBaseSensor):
    """Feed temperature sensor for Valve Area (D1-4B-05) devices."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the feed temperature sensor."""
        super().__init__(
            coordinator, eag_id, device, "feed_temperature", "Feed temperature"
        )

    @property
    def native_value(self) -> float | None:
        """Return the feed temperature value."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.feed_temperature if channel else None


class OpusGreenNetEnergyConsumptionSensor(OpusGreenNetBaseSensor):
    """Energy consumption sensor for Electro Heating Area (D1-4B-07) devices."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "kW"

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the energy consumption sensor."""
        super().__init__(
            coordinator, eag_id, device, "energy_consumption", "Energy consumption"
        )

    @property
    def native_value(self) -> float | None:
        """Return the energy consumption value."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.energy_consumption if channel else None


class OpusGreenNetSignalStrengthSensor(OpusGreenNetBaseSensor):
    """Signal strength sensor for all devices."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the signal strength sensor."""
        super().__init__(
            coordinator, eag_id, device, "signal_strength", "Signal strength"
        )

    @property
    def native_value(self) -> int | None:
        """Return the signal strength value."""
        if self._device.dbm:
            return self._device.dbm
        return None
