"""Climate platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
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
    """Set up Opus GreenNet climate entities from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_climate(device: EnOceanDevice) -> None:
        """Add a climate entity for a discovered device."""
        if device.entity_type != "climate":
            return

        entities = [
            OpusGreenNetClimate(
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
            async_add_climate,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_climate(device)


class OpusGreenNetClimate(OpusGreenNetEntity, ClimateEntity):
    """Representation of an Opus GreenNet HeatArea climate device."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 0
    _attr_max_temp = 40
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)

        self._attr_unique_id = f"{eag_id}_{device.device_id}"
        self._attr_name = None  # Use device name

        # Set step based on heat area type
        self._attr_target_temperature_step = device.setpoint_step

        # Set HVAC modes based on EEP type
        # D1-4B-06 (CosiTherm) supports thermalMode cooling/heating
        if device.primary_eep == "D1-4B-06":
            self._attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
        else:
            # D1-4B-05 (Valve) and D1-4B-07 (Electro) are heat-only
            self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.temperature if channel else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature setpoint."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.temperature_setpoint if channel else None

    @property
    def current_humidity(self) -> int | None:
        """Return the current humidity."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel and channel.humidity is not None:
            return int(channel.humidity)
        return None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current HVAC mode."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if not channel or not channel.heater_mode:
            return None

        mode = channel.heater_mode
        if mode in ("heating", "on", "autoOff"):
            if self._device.primary_eep == "D1-4B-06":
                return HVACMode.HEAT_COOL
            return HVACMode.HEAT
        if mode == "off":
            return HVACMode.OFF
        return None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if not channel or not channel.heater_mode:
            return None

        mode = channel.heater_mode
        if mode == "autoOff":
            return HVACAction.IDLE  # Temporarily disabled (window/summer)
        if mode == "off":
            return HVACAction.OFF
        if mode in ("heating", "on") and self._device.primary_eep == "D1-4B-07":
            if channel.power_state == "active":
                return HVACAction.HEATING
            if channel.power_state == "inactive":
                return HVACAction.IDLE
        # Enabled modes and seasonal thermalMode do not establish actuator activity.
        return None

    async def async_turn_on(self) -> None:
        """Enable the device's supported operating mode."""
        mode = (
            HVACMode.HEAT_COOL
            if self._device.primary_eep == "D1-4B-06"
            else HVACMode.HEAT
        )
        await self.async_set_hvac_mode(mode)

    async def async_turn_off(self) -> None:
        """Disable the heat area."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is not None:
            await self._coordinator.async_set_climate_setpoint(
                self._device.device_id, temperature
            )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._coordinator.async_set_climate_mode(
                self._device.device_id, "off"
            )
        elif hvac_mode in (HVACMode.HEAT, HVACMode.HEAT_COOL):
            # D1-4B-06 (CosiTherm) uses "on", others use "heating"
            if self._device.primary_eep == "D1-4B-06":
                mode_value = "on"
            else:
                mode_value = "heating"
            await self._coordinator.async_set_climate_mode(
                self._device.device_id, mode_value
            )
