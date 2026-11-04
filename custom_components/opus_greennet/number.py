"""Number platform: configured times and thresholds of D2-05 and D2-01 devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import LIGHT_LUX, PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import (
    CONF_EAG_ID,
    DEFAULT_CHANNEL,
    DEVICE_SETTINGS,
    KEY_ALIVE_CYCLE,
    KEY_ILLUMINATION_THRESHOLD,
    KEY_LONG_PRESS,
    KEY_LOWER_LIMIT,
    KEY_MAX_HOLDING_TIME,
    KEY_PIR_OFF_TIME,
    KEY_RAMPING_TIME,
    KEY_RAMPING_TIME_1,
    KEY_RAMPING_TIME_2,
    KEY_RAMPING_TIME_3,
    KEY_RAMPING_TIME_SWITCH,
    KEY_RECOM_READY_TIME,
    KEY_ROTATION_TIME,
    KEY_SHORT_PRESS,
    KEY_SWITCH_OFF_WARNING,
    KEY_SWITCH_OFF_WARNING_SIGNAL,
    KEY_TIMER,
    KEY_TIMER_LONG,
    KEY_UPPER_LIMIT,
    KEY_VERTICAL_MOVEMENT_TIME,
)
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity
from .readings import COMMANDS, CommandSpec

# Writes are serialized by the coordinator's radio queue.
PARALLEL_UPDATES = 0

# Range and step the gateway reports for each parameter (GET .../parameters).
MAX_TRAVEL_SECONDS = 655.35
TRAVEL_STEP_SECONDS = 0.01
MAX_HOLDING_SECONDS = 65535
HOLDING_STEP_SECONDS = 1
MAX_RAMPING_SECONDS = 25.5
RAMPING_STEP_SECONDS = 0.1
MAX_RAMP_SECONDS = 6553
MIN_DIMMING_LIMIT_PERCENT = 15
MAX_DIMMING_LIMIT_PERCENT = 80
MAX_PRESS_SECONDS = 2.55
PRESS_STEP_SECONDS = 0.01
MIN_TIMER_SECONDS = 60
MAX_TIMER_SECONDS = 7200
MAX_TIMER_LONG_SECONDS = 18000
MAX_WARNING_SECONDS = 45
MIN_WARNING_SIGNAL_SECONDS = 0.2
MAX_WARNING_SIGNAL_SECONDS = 2
WARNING_SIGNAL_STEP_SECONDS = 0.001
MAX_ILLUMINATION_LUX = 1500

# The parameter catalogs were read from a D2-05-02 blind, a D2-01-03 dimmer and
# a D2-01-01 and a D2-01-11 switch. Other types get no settings until their
# catalog is known.
BLIND_TIME_EEPS = frozenset({"D2-05-02"})
DIMMER_TIME_EEPS = frozenset({"D2-01-03"})
SWITCH_SETTING_EEPS = frozenset({"D2-01-01", "D2-01-11"})
# Only the device list of an A5-07-03 presence detector was read so far.
PRESENCE_SETTING_EEPS = frozenset({"A5-07-03"})


@dataclass(frozen=True, slots=True)
class DeviceTime:
    """One number parameter of a device with the range the gateway reports."""

    translation_key: str
    parameter: str
    attribute: str
    maximum: float = MAX_TRAVEL_SECONDS
    step: float = TRAVEL_STEP_SECONDS
    minimum: float = 0
    # Seconds unless the parameter is a threshold in lux.
    unit: str = UnitOfTime.SECONDS
    device_class: NumberDeviceClass | None = NumberDeviceClass.DURATION
    # Platform type of the devices that have the parameter.
    entity_type: str = "cover"
    # EEPs whose catalog lists the parameter.
    eeps: frozenset[str] = frozenset()
    # True: only the universal dimmer 563.076; False: only the other dimmers of
    # the EEP; None: no restriction. Decided by the product ID of the device.
    universal_dimmer: bool | None = None

    def applies_to(self, device: EnOceanDevice) -> bool:
        """Return whether the device is known to have this parameter."""
        if (
            self.universal_dimmer is not None
            and device.is_universal_dimmer is not self.universal_dimmer
        ):
            return False
        return (
            device.entity_type == self.entity_type
            and not self.eeps.isdisjoint(device.eep_names)
            and device.reports_setting(self.parameter)
        )


BLIND_TIMES: tuple[DeviceTime, ...] = (
    DeviceTime(
        "vertical_movement_time",
        KEY_VERTICAL_MOVEMENT_TIME,
        "vertical_movement_time",
        eeps=BLIND_TIME_EEPS,
    ),
    DeviceTime(
        "rotation_time", KEY_ROTATION_TIME, "rotation_time", eeps=BLIND_TIME_EEPS
    ),
    DeviceTime(
        "max_holding_time",
        KEY_MAX_HOLDING_TIME,
        "max_holding_time",
        maximum=MAX_HOLDING_SECONDS,
        step=HOLDING_STEP_SECONDS,
        eeps=BLIND_TIME_EEPS,
    ),
)

DIMMER_TIMES: tuple[DeviceTime, ...] = (
    DeviceTime(
        "ramping_time",
        KEY_RAMPING_TIME,
        "ramping_time",
        maximum=MAX_RAMPING_SECONDS,
        step=RAMPING_STEP_SECONDS,
        entity_type="light",
        eeps=DIMMER_TIME_EEPS,
        universal_dimmer=False,
    ),
    DeviceTime(
        "short_press_time",
        KEY_SHORT_PRESS,
        "short_press_time",
        maximum=MAX_PRESS_SECONDS,
        step=PRESS_STEP_SECONDS,
        entity_type="light",
        eeps=DIMMER_TIME_EEPS,
        universal_dimmer=False,
    ),
    DeviceTime(
        "long_press_time",
        KEY_LONG_PRESS,
        "long_press_time",
        maximum=MAX_PRESS_SECONDS,
        step=PRESS_STEP_SECONDS,
        entity_type="light",
        eeps=DIMMER_TIME_EEPS,
        universal_dimmer=False,
    ),
    # Parameter set of the universal dimmer 563.076. Both sets belong to the
    # EEP D2-01-03, so the product ID of the device decides which one a dimmer
    # gets: the three numbers above for every other dimmer, these for the 563.076.
    *(
        DeviceTime(
            key,
            parameter,
            attribute,
            maximum=MAX_RAMP_SECONDS,
            step=RAMPING_STEP_SECONDS,
            entity_type="light",
            eeps=DIMMER_TIME_EEPS,
            universal_dimmer=True,
        )
        for key, parameter, attribute in (
            ("ramping_time_1", KEY_RAMPING_TIME_1, "ramping_time_1"),
            ("ramping_time_2", KEY_RAMPING_TIME_2, "ramping_time_2"),
            ("ramping_time_3", KEY_RAMPING_TIME_3, "ramping_time_3"),
            ("ramping_time_switch", KEY_RAMPING_TIME_SWITCH, "ramping_time_switch"),
        )
    ),
    # Dimming limits in percent of the dimmer; the device scales its brightness
    # between them (operating instructions of the 563.076).
    *(
        DeviceTime(
            key,
            parameter,
            attribute,
            minimum=MIN_DIMMING_LIMIT_PERCENT,
            maximum=MAX_DIMMING_LIMIT_PERCENT,
            step=1,
            unit=PERCENTAGE,
            device_class=None,
            entity_type="light",
            eeps=DIMMER_TIME_EEPS,
            universal_dimmer=True,
        )
        for key, parameter, attribute in (
            ("lower_limit", KEY_LOWER_LIMIT, "lower_limit"),
            ("upper_limit", KEY_UPPER_LIMIT, "upper_limit"),
        )
    ),
)

# The settings of switches belong to the device, not to a channel: the
# gateway reports no channel for them, also not for a 2-channel switch.
SWITCH_SETTINGS: tuple[DeviceTime, ...] = (
    DeviceTime(
        "timer",
        KEY_TIMER,
        "timer",
        minimum=MIN_TIMER_SECONDS,
        maximum=MAX_TIMER_SECONDS,
        step=1,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
    DeviceTime(
        "timer_long",
        KEY_TIMER_LONG,
        "timer_long",
        minimum=MIN_TIMER_SECONDS,
        maximum=MAX_TIMER_LONG_SECONDS,
        step=1,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
    DeviceTime(
        "switch_off_warning",
        KEY_SWITCH_OFF_WARNING,
        "switch_off_warning",
        maximum=MAX_WARNING_SECONDS,
        step=1,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
    DeviceTime(
        "switch_off_warning_signal",
        KEY_SWITCH_OFF_WARNING_SIGNAL,
        "switch_off_warning_signal",
        minimum=MIN_WARNING_SIGNAL_SECONDS,
        maximum=MAX_WARNING_SIGNAL_SECONDS,
        step=WARNING_SIGNAL_STEP_SECONDS,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
    DeviceTime(
        "illumination_threshold",
        KEY_ILLUMINATION_THRESHOLD,
        "illumination_threshold",
        maximum=MAX_ILLUMINATION_LUX,
        step=1,
        unit=LIGHT_LUX,
        device_class=NumberDeviceClass.ILLUMINANCE,
        entity_type="switch",
        eeps=SWITCH_SETTING_EEPS,
    ),
)

DEVICE_TIMES = BLIND_TIMES + DIMMER_TIMES + SWITCH_SETTINGS


def _presence_setting(
    translation_key: str, parameter: str, unit: str = UnitOfTime.SECONDS
) -> DeviceTime:
    """Describe a presence detector setting, with the model range of its key."""
    attribute, minimum, maximum = DEVICE_SETTINGS[parameter]
    return DeviceTime(
        translation_key,
        parameter,
        attribute,
        maximum=maximum,
        step=1,
        minimum=minimum,
        unit=unit,
        entity_type="binary_sensor",
        eeps=PRESENCE_SETTING_EEPS,
    )


# Settings of the presence detectors, shown as read-only sensors only. They are
# deliberately not part of DEVICE_TIMES: no number entity is created, so the
# integration offers no way to write them. A battery detector answers a ReCom
# request only while it is ready (its readiness cycle), which is not proven
# for a write.
PRESENCE_SETTINGS: tuple[DeviceTime, ...] = (
    _presence_setting("alive_cycle", KEY_ALIVE_CYCLE, UnitOfTime.MINUTES),
    _presence_setting("pir_off_time", KEY_PIR_OFF_TIME),
    _presence_setting("recom_ready_time", KEY_RECOM_READY_TIME),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the time settings of blinds and dimmers."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_numbers(device: EnOceanDevice) -> None:
        entities: list[NumberEntity] = [
            OpusGreenNetTimeNumber(
                coordinator, eag_id, gateway_device_id, device, device_time
            )
            for device_time in DEVICE_TIMES
            if device_time.applies_to(device)
        ]
        entities.extend(
            OpusGreenNetCommandNumber(
                coordinator, eag_id, gateway_device_id, device, command
            )
            for eep in device.eep_names
            for command in COMMANDS.get(eep, ())
            if command.kind == "number"
        )
        async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}", async_add_numbers
        )
    )
    for device in coordinator.devices.values():
        async_add_numbers(device)


class OpusGreenNetTimeNumber(OpusGreenNetEntity, NumberEntity):
    """A time or threshold stored on the blind, dimmer or switch.

    The value comes from the device configuration, which the integration reads
    at setup and with every device list refresh; a change made by another
    controller shows up with the next refresh. A new value is adopted only
    after the device confirmed it when it was read back.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    # Disabled for new registry entries only; existing entries keep their setting.
    _attr_entity_registry_enabled_default = False
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        device_time: DeviceTime,
    ) -> None:
        """Initialize the number."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, DEFAULT_CHANNEL
        )
        self._device_time = device_time
        self._attr_translation_key = device_time.translation_key
        self._attr_device_class = device_time.device_class
        self._attr_native_unit_of_measurement = device_time.unit
        self._attr_native_min_value = device_time.minimum
        self._attr_native_max_value = device_time.maximum
        self._attr_native_step = device_time.step
        # The translation key is part of the unique ID: never rename it.
        self._attr_unique_id = (
            f"{eag_id}_{device.device_id}_{device_time.translation_key}"
        )

    @property
    def available(self) -> bool:
        """Return whether the gateway is reachable and reported the setting.

        A setting the device never reported is never written.
        """
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the configured time, or None while it is unknown."""
        channel = self._device.channels.get(self._channel_id)
        if channel is None:
            return None
        value: float | None = getattr(channel, self._device_time.attribute)
        return value

    async def async_set_native_value(self, value: float) -> None:
        """Write the time to the device and read it back."""
        await self._coordinator.async_set_device_parameter(
            self._device.device_id,
            self._device_time.parameter,
            value,
            step=self._device_time.step,
            minimum=self._device_time.minimum,
        )


class OpusGreenNetCommandNumber(OpusGreenNetEntity, NumberEntity):
    """A numeric command of a profile, for example the setpoint of an actuator.

    The value shown is what the device last reported. A command only changes it
    when the device reports the new value: the gateway accepting the command
    does not mean the device carried it out.
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        command: CommandSpec,
    ) -> None:
        """Initialize the number."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, DEFAULT_CHANNEL
        )
        self._command = command
        self._attr_translation_key = command.suffix
        self._attr_device_class = (
            NumberDeviceClass(command.device_class) if command.device_class else None
        )
        self._attr_native_unit_of_measurement = command.unit
        self._attr_native_min_value = command.minimum or 0
        self._attr_native_max_value = command.maximum or 0
        self._attr_native_step = command.step or 1
        # The translation key is part of the unique ID: never rename it.
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{command.suffix}"

    @property
    def native_value(self) -> float | None:
        """Return the reported value, or None while it is unknown."""
        channel = self._device.channels.get(self._channel_id)
        if channel is None:
            return None
        value = channel.readings.get(self._command.key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Send the command; the shown value follows the device report."""
        await self._coordinator.async_send_command(
            self._device.device_id,
            [{"key": self._command.key, "value": f"{value:g}"}],
        )
