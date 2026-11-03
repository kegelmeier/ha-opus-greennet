"""Binary sensor identities and state semantics for supported device profiles."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from .const import DEFAULT_CHANNEL, ERROR_CODE_NONE
from .enocean_device import EnOceanChannel, EnOceanDevice


@dataclass(frozen=True, kw_only=True)
class OpusBinarySensorDescription:
    """Integration metadata, separate from Home Assistant entity_description."""

    key: str
    translation_key: str
    applies_to: Callable[[EnOceanDevice], bool]
    value_fn: Callable[[EnOceanDevice], bool | None]
    device_class: BinarySensorDeviceClass | None = None
    entity_category: EntityCategory | None = None
    enabled_by_default: bool = True


def virtual_heat_area(device: EnOceanDevice) -> bool:
    """Return whether this is a HeatArea that reports window and timeout states.

    The physical Heat Area 3.0 (D1-4B-03) has its own error reports instead.
    """
    return device.is_climate and device.primary_eep != "D1-4B-03"


def channel_boolean(
    device: EnOceanDevice, read: Callable[[EnOceanChannel], bool | None]
) -> bool | None:
    """Preserve an optional boolean, including an unreported unknown state."""
    channel = device.channels.get(DEFAULT_CHANNEL)
    return read(channel) if channel is not None else None


def diagnostic_value(
    device: EnOceanDevice, read: Callable[[EnOceanChannel], str | None]
) -> bool | None:
    """Preserve HeatArea's existing reset-versus-warning interpretation."""
    channel = device.channels.get(DEFAULT_CHANNEL)
    value = read(channel) if channel is not None else None
    return None if value is None else value != "reset"


def error_code_problem(device: EnOceanDevice) -> bool:
    """Report a problem only for a reported error code.

    The detector sends an error code only when one occurs; without one the
    sensor shows "OK" instead of "unknown".
    """
    channel = device.channels.get(DEFAULT_CHANNEL)
    code = channel.error_code if channel is not None else None
    return code is not None and code != ERROR_CODE_NONE


def liquid_detected(device: EnOceanDevice) -> bool:
    """Report water only after the sensor reported it.

    F6-05-01 sends only on a change, and the gateway lists no state for it, so
    without a report the sensor shows "dry" instead of "unknown".
    """
    return channel_boolean(device, lambda c: c.liquid_detected) is True


BINARY_SENSOR_DESCRIPTIONS = (
    OpusBinarySensorDescription(
        key="liquid_detected",
        translation_key="water_leak",
        applies_to=lambda device: device.primary_eep == "F6-05-01",
        value_fn=liquid_detected,
        device_class=BinarySensorDeviceClass.MOISTURE,
    ),
    OpusBinarySensorDescription(
        key="window_open",
        translation_key="window",
        applies_to=lambda device: virtual_heat_area(device),
        value_fn=lambda device: channel_boolean(device, lambda c: c.window_open),
        device_class=BinarySensorDeviceClass.WINDOW,
    ),
    OpusBinarySensorDescription(
        key="actuator_not_responding",
        translation_key="actuator_not_responding",
        applies_to=lambda device: virtual_heat_area(device),
        value_fn=lambda device: diagnostic_value(
            device, lambda c: c.actuator_not_responding
        ),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="missing_temperature",
        translation_key="missing_temperature",
        applies_to=lambda device: virtual_heat_area(device),
        value_fn=lambda device: diagnostic_value(
            device, lambda c: c.missing_temperature
        ),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="actuator_low_battery",
        translation_key="actuator_battery",
        applies_to=lambda device: device.primary_eep == "D1-4B-05",
        value_fn=lambda device: diagnostic_value(
            device, lambda c: c.actuator_low_battery
        ),
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="actuator_deactivated",
        translation_key="actuator_deactivated",
        applies_to=lambda device: device.primary_eep == "D1-4B-05",
        value_fn=lambda device: diagnostic_value(
            device, lambda c: c.actuator_deactivated
        ),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="circuit_in_use",
        translation_key="circuit_in_use",
        applies_to=lambda device: device.primary_eep == "D1-4B-06",
        value_fn=lambda device: diagnostic_value(device, lambda c: c.circuit_in_use),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Heat Area 3.0 Physical (D1-4B-03): reports from the room control unit.
    OpusBinarySensorDescription(
        key="communication_error",
        translation_key="rcu_communication_error",
        applies_to=lambda device: device.primary_eep == "D1-4B-03",
        value_fn=lambda device: diagnostic_value(
            device, lambda c: c.communication_error
        ),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="heat_circuits_error",
        translation_key="heat_circuits_error",
        applies_to=lambda device: device.primary_eep == "D1-4B-03",
        value_fn=lambda device: diagnostic_value(
            device, lambda c: c.heat_circuits_error
        ),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="rcu_low_battery",
        translation_key="rcu_battery",
        applies_to=lambda device: device.primary_eep == "D1-4B-03",
        value_fn=lambda device: diagnostic_value(device, lambda c: c.rcu_low_battery),
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="rcu_malfunction",
        translation_key="rcu_malfunction",
        applies_to=lambda device: device.primary_eep == "D1-4B-03",
        value_fn=lambda device: diagnostic_value(device, lambda c: c.rcu_malfunction),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="contact",
        translation_key="contact",
        applies_to=lambda device: device.is_contact_sensor,
        value_fn=lambda device: channel_boolean(device, lambda c: c.contact),
        device_class=BinarySensorDeviceClass.OPENING,
    ),
    # On = the actuator's own buttons and inputs may switch it (D2-01
    # "localControl"). A setting, not a state: diagnostic, disabled for new
    # registry entries.
    OpusBinarySensorDescription(
        key="local_control",
        translation_key="local_control",
        applies_to=lambda device: device.has_local_control,
        value_fn=lambda device: channel_boolean(device, lambda c: c.local_control),
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_by_default=False,
    ),
    # A5-07 presence detectors report "noError" or an error code (251-255)
    # in their supply voltage byte.
    OpusBinarySensorDescription(
        key="error_code",
        translation_key="sensor_error",
        applies_to=lambda device: device.reports_error_code,
        value_fn=error_code_problem,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)
