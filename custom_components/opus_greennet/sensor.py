"""Sensor platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID, DEFAULT_CHANNEL
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
    OpusGreenNetCoordinator,
)
from .enocean_device import EnOceanChannel, EnOceanDevice
from .entity import OpusGreenNetEntity, migrate_legacy_entity_suffix

# Sensor state is pushed by the coordinator.
PARALLEL_UPDATES = 0


# Numeric channel readings exposed by OpusGreenNetMeasurementSensor.
MEASUREMENT_READINGS: Final[dict[str, Callable[[EnOceanChannel], float | None]]] = {
    "illuminance": lambda channel: channel.illuminance,
    "supply_voltage": lambda channel: channel.supply_voltage,
    "battery_level": lambda channel: channel.battery_level,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]
    added_unique_ids: set[str] = set()

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
                    gateway_device_id=gateway_device_id,
                    device=device,
                )
            )

            # Feed temperature (D1-4B-05 Valve Area only)
            if device.primary_eep == "D1-4B-05":
                entities.append(
                    OpusGreenNetFeedTemperatureSensor(
                        coordinator=coordinator,
                        eag_id=eag_id,
                        gateway_device_id=gateway_device_id,
                        device=device,
                    )
                )

            # Energy consumption (D1-4B-07 Electro Heating only)
            if device.primary_eep == "D1-4B-07":
                entities.append(
                    OpusGreenNetPowerConsumptionSensor(
                        coordinator=coordinator,
                        eag_id=eag_id,
                        gateway_device_id=gateway_device_id,
                        device=device,
                    )
                )

        if device.is_presence_detector:
            if f"{eag_id}_{device.device_id}_illuminance" not in added_unique_ids:
                migrate_legacy_entity_suffix(
                    hass,
                    entry.entry_id,
                    "sensor",
                    f"{eag_id}_{device.device_id}",
                    "illumination",
                    "illuminance",
                )
            for suffix, device_class, unit in (
                ("illuminance", SensorDeviceClass.ILLUMINANCE, LIGHT_LUX),
                (
                    "supply_voltage",
                    SensorDeviceClass.VOLTAGE,
                    UnitOfElectricPotential.VOLT,
                ),
                ("battery_level", SensorDeviceClass.BATTERY, UnitOfRatio.PERCENTAGE),
            ):
                entities.append(
                    OpusGreenNetMeasurementSensor(
                        coordinator,
                        eag_id,
                        gateway_device_id,
                        device,
                        suffix,
                        device_class,
                        unit,
                    )
                )

        if device.is_window_handle:
            entities.append(
                OpusGreenNetHandleSensor(
                    coordinator, eag_id, gateway_device_id, device, "handle_state"
                )
            )
            if device.has_autolock:
                entities.append(
                    OpusGreenNetHandleSensor(
                        coordinator, eag_id, gateway_device_id, device, "unlock_request"
                    )
                )

        # Signal strength sensor (all devices with dbm data)
        entities.append(
            OpusGreenNetSignalStrengthSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                gateway_device_id=gateway_device_id,
                device=device,
            )
        )

        new_entities = [
            entity for entity in entities if entity.unique_id not in added_unique_ids
        ]
        added_unique_ids.update(
            entity.unique_id for entity in new_entities if entity.unique_id is not None
        )

        if new_entities:
            async_add_entities(new_entities)

    @callback
    def async_forget_sensors(device_id: str) -> None:
        prefix = f"{eag_id}_{device_id}_"
        added_unique_ids.difference_update(
            [
                unique_id
                for unique_id in added_unique_ids
                if unique_id.startswith(prefix)
            ]
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_DEVICE_REMOVED}_{eag_id}", async_forget_sensors
        )
    )

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


class OpusGreenNetBaseSensor(OpusGreenNetEntity, SensorEntity):
    """Base class for Opus GreenNet sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        suffix: str,
        translation_key: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{suffix}"
        self._attr_translation_key = translation_key


class OpusGreenNetHumiditySensor(OpusGreenNetBaseSensor):
    """Humidity sensor for HeatArea devices."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfRatio.PERCENTAGE

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the humidity sensor."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, "humidity", "humidity"
        )

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
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the feed temperature sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "feed_temperature",
            "feed_temperature",
        )

    @property
    def native_value(self) -> float | None:
        """Return the feed temperature value."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.feed_temperature if channel else None


class OpusGreenNetPowerConsumptionSensor(OpusGreenNetBaseSensor):
    """Power consumption sensor for Electro Heating Area devices."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the energy consumption sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "energy_consumption",
            "power_consumption",
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
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the signal strength sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "signal_strength",
            "signal_strength",
        )
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> int | None:
        """Return the signal strength value."""
        if self._device.dbm is not None:
            return self._device.dbm
        return None


class OpusGreenNetMeasurementSensor(OpusGreenNetBaseSensor):
    """A finite, validated numeric reading from a sensor's default channel."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        suffix: str,
        device_class: SensorDeviceClass,
        unit: str,
    ) -> None:
        super().__init__(coordinator, eag_id, gateway_device_id, device, suffix, suffix)
        # Typed accessor instead of getattr(); an unknown suffix fails at setup.
        self._read_value = MEASUREMENT_READINGS[suffix]
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> float | None:
        """Return the parsed value, or unknown before the first valid reading."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return self._read_value(channel) if channel is not None else None


class OpusGreenNetHandleSensor(OpusGreenNetBaseSensor):
    """Handle position or the independent reported unlock request."""

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        suffix: str,
    ) -> None:
        super().__init__(coordinator, eag_id, gateway_device_id, device, suffix, suffix)
        self._suffix = suffix
        if suffix == "handle_state":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["open", "closed", "tilted"]
        elif suffix == "unlock_request":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["requested", "not_requested"]

    @property
    def native_value(self) -> str | None:
        """Return the reported value independently of other handle attributes."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel is None:
            return None
        if self._suffix == "unlock_request":
            if channel.unlock_request is None:
                return None
            return "requested" if channel.unlock_request else "not_requested"
        if self._suffix == "handle_state":
            return channel.handle_state
        return None
