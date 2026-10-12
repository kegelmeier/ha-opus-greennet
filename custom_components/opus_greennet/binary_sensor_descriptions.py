"""Binary sensor identities and state semantics for supported device profiles."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from .const import DEFAULT_CHANNEL
from .enocean_device import EnOceanDevice


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


def channel_boolean(device: EnOceanDevice, attribute: str) -> bool | None:
    """Preserve an optional boolean, including an unreported unknown state."""
    return getattr(device.channels.get(DEFAULT_CHANNEL), attribute, None)


def diagnostic_value(device: EnOceanDevice, attribute: str) -> bool | None:
    """Preserve HeatArea's existing reset-versus-warning interpretation."""
    value = getattr(device.channels.get(DEFAULT_CHANNEL), attribute, None)
    return None if value is None else value != "reset"


BINARY_SENSOR_DESCRIPTIONS = (
    OpusBinarySensorDescription(
        key="contact",
        translation_key="contact",
        applies_to=lambda device: device.is_contact_sensor,
        value_fn=lambda device: channel_boolean(device, "contact"),
        device_class=BinarySensorDeviceClass.OPENING,
    ),
    OpusBinarySensorDescription(
        key="liquid_detected",
        translation_key="water_leak",
        applies_to=lambda device: device.primary_eep == "F6-05-01",
        value_fn=lambda device: channel_boolean(device, "liquid_detected"),
        device_class=BinarySensorDeviceClass.MOISTURE,
    ),
    OpusBinarySensorDescription(
        key="window_open",
        translation_key="window",
        applies_to=lambda device: device.is_climate,
        value_fn=lambda device: channel_boolean(device, "window_open"),
        device_class=BinarySensorDeviceClass.WINDOW,
    ),
    OpusBinarySensorDescription(
        key="actuator_not_responding",
        translation_key="actuator_not_responding",
        applies_to=lambda device: device.is_climate,
        value_fn=lambda device: diagnostic_value(device, "actuator_not_responding"),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="missing_temperature",
        translation_key="missing_temperature",
        applies_to=lambda device: device.is_climate,
        value_fn=lambda device: diagnostic_value(device, "missing_temperature"),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="actuator_low_battery",
        translation_key="actuator_battery",
        applies_to=lambda device: device.primary_eep == "D1-4B-05",
        value_fn=lambda device: diagnostic_value(device, "actuator_low_battery"),
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="actuator_deactivated",
        translation_key="actuator_deactivated",
        applies_to=lambda device: device.primary_eep == "D1-4B-05",
        value_fn=lambda device: diagnostic_value(device, "actuator_deactivated"),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="circuit_in_use",
        translation_key="circuit_in_use",
        applies_to=lambda device: device.primary_eep == "D1-4B-06",
        value_fn=lambda device: diagnostic_value(device, "circuit_in_use"),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)
