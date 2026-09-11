"""Binary sensor platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import CONF_EAG_ID, DEFAULT_CHANNEL
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    OpusGreenNetCoordinator,
)
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity

# Binary sensor state is pushed by the coordinator.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet binary sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_binary_sensors(device: EnOceanDevice) -> None:
        """Add binary sensor entities for a discovered device."""
        entities: list[BinarySensorEntity] = []

        if device.primary_eep == "F6-05-01":
            entities.append(
                OpusGreenNetMoistureSensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    gateway_device_id=gateway_device_id,
                    device=device,
                )
            )

        if not device.is_climate:
            if entities:
                async_add_entities(entities)
            return

        # Window open (all HeatArea types)
        entities.append(
            OpusGreenNetWindowSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                gateway_device_id=gateway_device_id,
                device=device,
            )
        )

        # Actuator not responding (all types)
        entities.append(
            OpusGreenNetProblemSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                gateway_device_id=gateway_device_id,
                device=device,
                suffix="actuator_not_responding",
                translation_key="actuator_not_responding",
                attr_name="actuator_not_responding",
            )
        )

        # Missing temperature (all types)
        entities.append(
            OpusGreenNetProblemSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                gateway_device_id=gateway_device_id,
                device=device,
                suffix="missing_temperature",
                translation_key="missing_temperature",
                attr_name="missing_temperature",
            )
        )

        # Actuator low battery (D1-4B-05 Valve only)
        if device.primary_eep == "D1-4B-05":
            entities.append(
                OpusGreenNetBatterySensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    gateway_device_id=gateway_device_id,
                    device=device,
                )
            )

            # Actuator deactivated (D1-4B-05 Valve only)
            entities.append(
                OpusGreenNetProblemSensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    gateway_device_id=gateway_device_id,
                    device=device,
                    suffix="actuator_deactivated",
                    translation_key="actuator_deactivated",
                    attr_name="actuator_deactivated",
                )
            )

        # Circuit in use (D1-4B-06 CosiTherm only)
        if device.primary_eep == "D1-4B-06":
            entities.append(
                OpusGreenNetProblemSensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    gateway_device_id=gateway_device_id,
                    device=device,
                    suffix="circuit_in_use",
                    translation_key="circuit_in_use",
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


class OpusGreenNetBaseBinarySensor(OpusGreenNetEntity, BinarySensorEntity):
    """Base class for Opus GreenNet binary sensors."""

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
        """Initialize the binary sensor."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{suffix}"
        self._attr_translation_key = translation_key


class OpusGreenNetWindowSensor(OpusGreenNetBaseBinarySensor):
    """Window open binary sensor for HeatArea devices."""

    _attr_device_class = BinarySensorDeviceClass.WINDOW

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the window sensor."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, "window_open", "window"
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if window is open."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel:
            return channel.window_open
        return None


class OpusGreenNetMoistureSensor(OpusGreenNetBaseBinarySensor):
    """Water leak binary sensor for F6-05-01 devices."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the moisture sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "liquid_detected",
            "water_leak",
        )

    @property
    def is_on(self) -> bool | None:
        """Return true when liquid is detected."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel:
            return channel.liquid_detected
        return None


class OpusGreenNetProblemSensor(OpusGreenNetBaseBinarySensor):
    """Problem/error binary sensor for HeatArea devices."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        suffix: str,
        translation_key: str,
        attr_name: str,
    ) -> None:
        """Initialize the problem sensor."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, suffix, translation_key
        )
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
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the battery sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "actuator_low_battery",
            "actuator_battery",
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
