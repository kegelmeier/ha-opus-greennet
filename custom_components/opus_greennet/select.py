"""Select platform: settings with a fixed set of values of D2-01 switches."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import (
    CONF_EAG_ID,
    DEFAULT_CHANNEL,
    KEY_DIMMER_MODE,
    KEY_FUNCTION_EXTERNAL_INPUT,
    KEY_LED_CHANNEL,
    KEY_LED_COLOR,
    KEY_OPERATION_MODE,
    KEY_REPEATER_STATE,
    KEY_ROUTING_TIME,
    REPEATER_EEPS,
    ROUTING_EEPS,
    ROUTING_OFF,
    ROUTING_ON,
    SWITCH_EEPS,
)
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity
from .readings import COMMANDS, CommandSpec

# Writes are serialized by the coordinator's radio queue.
PARALLEL_UPDATES = 0

# Values of the gateway's parameter catalog (GET .../parameters), read from one
# D2-01-01 and one D2-01-11 switch.
OPERATION_MODES = ("bottomOn", "timer", "toggle", "topOn")
LED_COLORS = ("all", "green", "off", "red")
LED_CHANNELS = ("channel1", "channel2")
REPEATER_STATES = ("off", "level_1", "level_2")
# Dimming principle of D2-01-03 dimmers (catalog of firmware 1.21.34).
DIMMER_MODES = ("phaseControlLower", "phaseControlUpper")
ROUTING_STATES = (ROUTING_OFF, ROUTING_ON)


@dataclass(frozen=True, slots=True, eq=False)
class DeviceSelect:
    """One setting with a fixed set of values, per EEP the catalog was read from."""

    translation_key: str
    parameter: str
    attribute: str
    # Gateway values the catalog lists for each EEP.
    values: Mapping[str, tuple[str, ...]]
    # Only the universal dimmer 563.076 has the setting (decided by product ID).
    universal_dimmer_only: bool = False

    def values_for(self, device: EnOceanDevice) -> tuple[str, ...] | None:
        """Return the values of the device, or None if it has no such setting."""
        if self.universal_dimmer_only and not device.is_universal_dimmer:
            return None
        for eep in device.eep_names:
            if eep in self.values:
                return self.values[eep]
        return None


# Catalog parameters: values as the gateway lists them in GET .../parameters.
CATALOG_SELECTS: tuple[DeviceSelect, ...] = (
    DeviceSelect(
        "operation_mode",
        KEY_OPERATION_MODE,
        "operation_mode",
        dict.fromkeys(SWITCH_EEPS, OPERATION_MODES),
    ),
    DeviceSelect(
        "led_color", KEY_LED_COLOR, "led_color", dict.fromkeys(SWITCH_EEPS, LED_COLORS)
    ),
    DeviceSelect(
        "led_channel", KEY_LED_CHANNEL, "led_channel", {"D2-01-11": LED_CHANNELS}
    ),
    DeviceSelect(
        "dimmer_mode",
        KEY_DIMMER_MODE,
        "dimmer_mode",
        {"D2-01-03": DIMMER_MODES},
        universal_dimmer_only=True,
    ),
    DeviceSelect(
        "function_external_input",
        KEY_FUNCTION_EXTERNAL_INPUT,
        "function_external_input",
        {
            "D2-01-01": ("channel1", "inactive"),
            "D2-01-11": ("channel1", "channel2", "channelsAll", "inactive"),
        },
    ),
)
# Not in the catalog as such. Routing: "on" stands for any routing time the
# device reports and is written as the smallest one.
EXTRA_SELECTS: tuple[DeviceSelect, ...] = (
    DeviceSelect(
        "routing",
        KEY_ROUTING_TIME,
        "routing",
        dict.fromkeys(ROUTING_EEPS, ROUTING_STATES),
    ),
    # Device resource /repeaterState, written and read back there.
    DeviceSelect(
        "repeater",
        KEY_REPEATER_STATE,
        "repeater",
        dict.fromkeys(REPEATER_EEPS, REPEATER_STATES),
    ),
)
DEVICE_SELECTS = CATALOG_SELECTS + EXTRA_SELECTS


def option_slug(value: str) -> str:
    """Return the option name Home Assistant shows: ``bottomOn`` -> ``bottom_on``."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", value).lower()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the selectable settings of switches."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_selects(device: EnOceanDevice) -> None:
        entities: list[SelectEntity] = [
            OpusGreenNetDeviceSelect(
                coordinator, eag_id, gateway_device_id, device, select, values
            )
            for select in DEVICE_SELECTS
            if (values := select.values_for(device)) is not None
        ]
        entities.extend(
            OpusGreenNetCommandSelect(
                coordinator, eag_id, gateway_device_id, device, command
            )
            for eep in device.eep_names
            for command in COMMANDS.get(eep, ())
            if command.kind == "select"
        )
        async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}", async_add_selects
        )
    )
    for device in coordinator.devices.values():
        async_add_selects(device)


class OpusGreenNetDeviceSelect(OpusGreenNetEntity, SelectEntity):
    """A setting stored on the switch that has a fixed set of values.

    The value comes from the device configuration, which the integration reads
    at setup and with every device list refresh; a change made by another
    controller shows up with the next refresh. A value the catalog does not
    list is shown as unknown and is never written back. A new value is adopted
    only after the device confirmed it when it was read back.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    # Disabled for new registry entries only; existing entries keep their setting.
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        select: DeviceSelect,
        values: tuple[str, ...],
    ) -> None:
        """Initialize the select."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, DEFAULT_CHANNEL
        )
        self._select = select
        self._values = {option_slug(value): value for value in values}
        self._attr_translation_key = select.translation_key
        self._attr_options = list(self._values)
        # The translation key is part of the unique ID: never rename it.
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{select.translation_key}"

    @property
    def available(self) -> bool:
        """Return whether the gateway is reachable and reported the setting.

        A setting the device never reported is never written.
        """
        channel = self._device.channels.get(self._channel_id)
        return (
            super().available
            and channel is not None
            and getattr(channel, self._select.attribute) is not None
        )

    @property
    def current_option(self) -> str | None:
        """Return the configured option, or None while it is unknown or unlisted."""
        channel = self._device.channels.get(self._channel_id)
        if channel is None:
            return None
        value: str | None = getattr(channel, self._select.attribute)
        if value is None or value not in self._values.values():
            return None
        return option_slug(value)

    async def async_select_option(self, option: str) -> None:
        """Write the option to the switch and read it back."""
        await self._coordinator.async_set_device_option(
            self._device.device_id, self._select.parameter, self._values[option]
        )


class OpusGreenNetCommandSelect(OpusGreenNetEntity, SelectEntity):
    """A command of a profile with a fixed set of values.

    The option shown is what the device last reported. A command only changes
    it when the device reports the new value: the gateway accepting the command
    does not mean the device carried it out.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        command: CommandSpec,
    ) -> None:
        """Initialize the select."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, DEFAULT_CHANNEL
        )
        self._command = command
        self._reported = {option_slug(shown): shown for shown, _ in command.options}
        self._wire = {option_slug(shown): wire for shown, wire in command.options}
        self._attr_translation_key = command.suffix
        self._attr_options = list(self._wire)
        if command.config:
            self._attr_entity_category = EntityCategory.CONFIG
            self._attr_entity_registry_enabled_default = False
        # The translation key is part of the unique ID: never rename it.
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{command.suffix}"

    @property
    def current_option(self) -> str | None:
        """Return the reported option, or None while it is unknown or unlisted."""
        channel = self._device.channels.get(self._channel_id)
        if channel is None:
            return None
        value = channel.readings.get(self._command.key)
        if not isinstance(value, str):
            return None
        slug = option_slug(value)
        return slug if self._reported.get(slug) == value else None

    async def async_select_option(self, option: str) -> None:
        """Send the command; the shown option follows the device report."""
        await self._coordinator.async_send_command(
            self._device.device_id,
            [{"key": self._command.key, "value": self._wire[option]}],
        )
