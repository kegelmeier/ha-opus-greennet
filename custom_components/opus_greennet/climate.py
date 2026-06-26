"""Climate platform for Opus GreenNet Bridge integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
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
    """Set up Opus GreenNet climate entities from a config entry."""
    coordinator: OpusGreenNetCoordinator = hass.data[DOMAIN][entry.entry_id]
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_climate(device: EnOceanDevice) -> None:
        """Add a climate entity for a discovered device."""
        if device.entity_type != "climate":
            return

        _LOGGER.debug(
            "Adding climate entity for device: %s (%s)",
            device.friendly_id,
            device.device_id,
        )

        entities = [
            OpusGreenNetClimate(
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
            async_add_climate,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_climate(device)


class OpusGreenNetClimate(ClimateEntity):
    """Representation of an Opus GreenNet HeatArea climate device."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 0
    _attr_max_temp = 40
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _enable_turn_on_off_backwards_compat = False

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the climate entity."""
        self._coordinator = coordinator
        self._eag_id = eag_id
        self._device = device
        self._device_key = device.friendly_id or device.device_id

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
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._eag_id}_{self._device.device_id}")},
            name=self._device.friendly_id or self._device.device_id,
            manufacturer=self._device.manufacturer or "OPUS / EnOcean",
            model=self._device.primary_eep or "Unknown",
            via_device=(DOMAIN, self._eag_id),
        )

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
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if not channel or not channel.heater_mode:
            return HVACMode.OFF

        mode = channel.heater_mode
        if mode in ("heating", "on"):
            if self._device.primary_eep == "D1-4B-06":
                return HVACMode.HEAT_COOL
            return HVACMode.HEAT
        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if not channel or not channel.heater_mode:
            return None

        mode = channel.heater_mode
        if mode in ("heating", "on"):
            # For CosiTherm, check thermalMode for cooling vs heating
            if (
                self._device.primary_eep == "D1-4B-06"
                and channel.thermal_mode == "cooling"
            ):
                return HVACAction.COOLING
            return HVACAction.HEATING
        if mode == "autoOff":
            return HVACAction.IDLE  # Temporarily disabled (window/summer)
        if mode in ("off", "configIncomplete", "error"):
            return HVACAction.OFF
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        attrs: dict[str, Any] = device_diagnostic_attributes(self._device)
        if not channel:
            return attrs

        if channel.window_open is not None:
            attrs["window_open"] = channel.window_open
        if channel.summer_mode is not None:
            attrs["summer_mode"] = channel.summer_mode
        if channel.feed_temperature is not None:
            attrs["feed_temperature"] = channel.feed_temperature
        if channel.thermal_mode is not None:
            attrs["thermal_mode"] = channel.thermal_mode
        if channel.energy_consumption is not None:
            attrs["energy_consumption"] = channel.energy_consumption
        if channel.power_state is not None:
            attrs["power_state"] = channel.power_state
        if channel.temperature_origin is not None:
            attrs["temperature_origin"] = channel.temperature_origin
        if channel.heater_mode is not None:
            attrs["heater_mode"] = channel.heater_mode

        # Error states (only show active errors, not "reset")
        if channel.actuator_deactivated and channel.actuator_deactivated != "reset":
            attrs["actuator_deactivated"] = channel.actuator_deactivated
        if channel.actuator_low_battery and channel.actuator_low_battery != "reset":
            attrs["actuator_low_battery"] = channel.actuator_low_battery
        if (
            channel.actuator_not_responding
            and channel.actuator_not_responding != "reset"
        ):
            attrs["actuator_not_responding"] = channel.actuator_not_responding
        if channel.missing_temperature and channel.missing_temperature != "reset":
            attrs["missing_temperature"] = channel.missing_temperature
        if channel.circuit_in_use and channel.circuit_in_use != "reset":
            attrs["circuit_in_use"] = channel.circuit_in_use

        return attrs

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
