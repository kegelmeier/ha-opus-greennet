"""Sensor platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import (
    CONF_EAG_ID,
    DEFAULT_CHANNEL,
    HANDLE_CLOSED,
    HANDLE_OPEN,
    HANDLE_TILT,
    UNLOCK_NOT_REQUESTED,
    UNLOCK_REQUESTED,
)
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    OpusGreenNetCoordinator,
)
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity


HOPPE_WINDOW_HANDLE_EEPS = frozenset(
    {
        "D2-06-40",
        "F6-10-00",
        "D2-03-10",
    }
)

HOPPE_ELOCK_EEP = "D2-06-40"


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

        if device.primary_eep == "A5-07-03":
            entities.extend(
                (
                    OpusGreenNetIlluminanceSensor(
                        coordinator, eag_id, gateway_device_id, device
                    ),
                    OpusGreenNetSupplyVoltageSensor(
                        coordinator, eag_id, gateway_device_id, device
                    ),
                    OpusGreenNetBatteryLevelSensor(
                        coordinator, eag_id, gateway_device_id, device
                    ),
                )
            )

        # All supported HOPPE window handles expose a passive handle state.
        if device.primary_eep in HOPPE_WINDOW_HANDLE_EEPS:
            entities.append(
                OpusGreenNetHandleStateSensor(
                    coordinator,
                    eag_id,
                    gateway_device_id,
                    device,
                )
            )

        # The unlock-button status only exists for the eLock variant.
        if device.primary_eep == HOPPE_ELOCK_EEP:
            entities.append(
                OpusGreenNetUnlockRequestSensor(
                    coordinator,
                    eag_id,
                    gateway_device_id,
                    device,
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
            entity
            for entity in entities
            if entity.unique_id is None or entity.unique_id not in added_unique_ids
        ]
        added_unique_ids.update(
            entity.unique_id for entity in new_entities if entity.unique_id is not None
        )

        if new_entities:
            async_add_entities(new_entities)

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


class OpusGreenNetIlluminanceSensor(OpusGreenNetBaseSensor):
    """Illuminance reported by an OPUS SMS detector."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = LIGHT_LUX

    def __init__(self, coordinator, eag_id, gateway_device_id, device):
        """Initialize the illuminance sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "illumination",
            "illuminance",
        )

    @property
    def native_value(self) -> float | None:
        """Return illuminance in lux."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.illumination if channel else None


class OpusGreenNetSupplyVoltageSensor(OpusGreenNetBaseSensor):
    """Supply voltage reported by an OPUS SMS detector."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, eag_id, gateway_device_id, device):
        """Initialize the supply-voltage sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "supply_voltage",
            "supply_voltage",
        )

    @property
    def native_value(self) -> float | None:
        """Return supply voltage."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.supply_voltage if channel else None


class OpusGreenNetBatteryLevelSensor(OpusGreenNetBaseSensor):
    """Battery level reported by an OPUS SMS detector."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, eag_id, gateway_device_id, device):
        """Initialize the battery-level sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "battery_level",
            "battery_level",
        )

    @property
    def native_value(self) -> int | None:
        """Return battery percentage."""
        return self._device.battery_level


class OpusGreenNetHandleStateSensor(OpusGreenNetBaseSensor):
    """Position state reported by a HOPPE window handle."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [HANDLE_CLOSED, HANDLE_OPEN, HANDLE_TILT]

    def __init__(self, coordinator, eag_id, gateway_device_id, device):
        """Initialize the window handle state sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "handle_state",
            "handle_state",
        )

    @property
    def native_value(self) -> str | None:
        """Return the reported window-handle position."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.handle_state if channel else None


class OpusGreenNetUnlockRequestSensor(OpusGreenNetBaseSensor):
    """Unlock-button request reported by a HOPPE window handle."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [UNLOCK_NOT_REQUESTED, UNLOCK_REQUESTED]

    def __init__(self, coordinator, eag_id, gateway_device_id, device):
        """Initialize the unlock request sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "unlock_request",
            "unlock_request",
        )

    @property
    def native_value(self) -> str | None:
        """Return whether the handle button requests an unlock."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.unlock_state if channel else None


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