"""Central, declarative registry of supplementary entities per device.

This module replaces the historic pattern of hardcoded
``if device.primary_eep == "..."`` branches scattered across binary_sensor.py,
sensor.py, etc. (as found in the fubu2k fork). Every platform file becomes
closed for modification: adding, removing, or changing which entities a
device exposes only ever touches this file (Open/Closed Principle).

A description is "applicable" to a device via either an explicit EEP set or
an arbitrary predicate (for device families such as "all climate devices").
Platform files ask ``descriptions_for(device, platform)`` and instantiate a
single generic entity class per description; no platform-specific knowledge
of individual EEPs lives outside this file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfRatio,
)
from homeassistant.helpers.entity import EntityCategory

from .const import (
    HANDLE_CLOSED,
    HANDLE_OPEN,
    HANDLE_TILT,
    UNLOCK_NOT_REQUESTED,
    UNLOCK_REQUESTED,
)
from .enocean_device import EnOceanChannel, EnOceanDevice

Predicate = Callable[[EnOceanDevice], bool]


def _eeps(*eeps: str) -> Predicate:
    """Return a predicate matching an exact set of primary EEPs."""
    eep_set = frozenset(eeps)
    return lambda device: device.primary_eep in eep_set


def _channel_attr(attr: str, channel_id: int = 0) -> Callable[[EnOceanDevice], Any]:
    """Return a value_fn reading ``attr`` off the device's channel."""

    def _get(device: EnOceanDevice) -> Any:
        channel = device.channels.get(channel_id)
        return getattr(channel, attr, None) if channel else None

    return _get


def _device_attr(attr: str) -> Callable[[EnOceanDevice], Any]:
    """Return a value_fn reading ``attr`` directly off the device."""
    return lambda device: getattr(device, attr, None)


@dataclass(frozen=True, kw_only=True)
class OpusEntityDescription:
    """Shared fields for every declarative entity description."""

    key: str
    translation_key: str
    applies_to: Predicate
    value_fn: Callable[[EnOceanDevice], Any]
    entity_category: EntityCategory | None = None
    enabled_by_default: bool = True


@dataclass(frozen=True, kw_only=True)
class OpusBinarySensorDescription(OpusEntityDescription):
    """Declarative binary_sensor entity."""

    device_class: BinarySensorDeviceClass | None = None


@dataclass(frozen=True, kw_only=True)
class OpusSensorDescription(OpusEntityDescription):
    """Declarative sensor entity."""

    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    native_unit_of_measurement: str | None = None
    options: tuple[str, ...] | None = None


@dataclass(frozen=True, kw_only=True)
class OpusLockDescription(OpusEntityDescription):
    """Declarative lock entity.

    ``value_fn`` returns True when locked, False when unlocked, None when
    unknown. ``lock_fn``/``unlock_fn`` return the coordinator coroutine
    factory used to send the command (bound at entity construction time).
    """

    locked_state: str
    unlocked_state: str
    lock_state_attr: str
    coordinator_method: str  # coordinator attribute name, e.g. "async_set_window_handle_lock"


# ---------------------------------------------------------------------------
# Climate family (existing v0.3.3b0 behaviour, now expressed declaratively)
# ---------------------------------------------------------------------------

IS_CLIMATE: Final[Predicate] = lambda device: device.is_climate
IS_VALVE_AREA: Final[Predicate] = _eeps("D1-4B-05")
IS_COSITHERM_AREA: Final[Predicate] = _eeps("D1-4B-06")

# ---------------------------------------------------------------------------
# Ported device families
# ---------------------------------------------------------------------------

IS_SMS_PRESENCE: Final[Predicate] = _eeps("A5-07-03")
IS_SMOKE_DETECTOR: Final[Predicate] = _eeps("F6-05-02")
HOPPE_ALL_VARIANTS: Final[Predicate] = _eeps("D2-06-40", "F6-10-00", "D2-03-10")
HOPPE_ELOCK_ONLY: Final[Predicate] = _eeps("D2-06-40")


BINARY_SENSOR_DESCRIPTIONS: Final[tuple[OpusBinarySensorDescription, ...]] = (
    OpusBinarySensorDescription(
        key="window_open",
        translation_key="window",
        applies_to=IS_CLIMATE,
        value_fn=_channel_attr("window_open"),
        device_class=BinarySensorDeviceClass.WINDOW,
    ),
    OpusBinarySensorDescription(
        key="actuator_not_responding",
        translation_key="actuator_not_responding",
        applies_to=IS_CLIMATE,
        value_fn=lambda d: _problem_value(d, "actuator_not_responding"),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="missing_temperature",
        translation_key="missing_temperature",
        applies_to=IS_CLIMATE,
        value_fn=lambda d: _problem_value(d, "missing_temperature"),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="actuator_low_battery",
        translation_key="actuator_battery",
        applies_to=IS_VALVE_AREA,
        value_fn=lambda d: _problem_value(d, "actuator_low_battery"),
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="actuator_deactivated",
        translation_key="actuator_deactivated",
        applies_to=IS_VALVE_AREA,
        value_fn=lambda d: _problem_value(d, "actuator_deactivated"),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="circuit_in_use",
        translation_key="circuit_in_use",
        applies_to=IS_COSITHERM_AREA,
        value_fn=lambda d: _problem_value(d, "circuit_in_use"),
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusBinarySensorDescription(
        key="liquid_detected",
        translation_key="water_leak",
        applies_to=_eeps("F6-05-01"),
        value_fn=_channel_attr("liquid_detected"),
        device_class=BinarySensorDeviceClass.MOISTURE,
    ),
    # --- new devices by fubu2k
    OpusBinarySensorDescription(
        key="motion",
        translation_key="motion",
        applies_to=IS_SMS_PRESENCE,
        value_fn=_channel_attr("motion_detected"),
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    OpusBinarySensorDescription(
        key="smoke_alarm",
        translation_key="smoke_alarm",
        applies_to=IS_SMOKE_DETECTOR,
        value_fn=_channel_attr("smoke_alarm"),
        device_class=BinarySensorDeviceClass.SMOKE,
    ),
    OpusBinarySensorDescription(
        key="battery_low",
        translation_key="battery_low",
        applies_to=IS_SMOKE_DETECTOR,
        value_fn=_channel_attr("battery_low"),
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


def _problem_value(device: EnOceanDevice, attr: str) -> bool | None:
    """Return True if a diagnostic error/warning field is active ("reset" clears it)."""
    channel = device.channels.get(0)
    if not channel:
        return None
    value = getattr(channel, attr, None)
    return None if value is None else value != "reset"


SENSOR_DESCRIPTIONS: Final[tuple[OpusSensorDescription, ...]] = (
    OpusSensorDescription(
        key="humidity",
        translation_key="humidity",
        applies_to=IS_CLIMATE,
        value_fn=_channel_attr("humidity"),
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
    ),
    OpusSensorDescription(
        key="feed_temperature",
        translation_key="feed_temperature",
        applies_to=IS_VALVE_AREA,
        value_fn=_channel_attr("feed_temperature"),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    OpusSensorDescription(
        key="energy_consumption",
        translation_key="power_consumption",
        applies_to=_eeps("D1-4B-07"),
        value_fn=_channel_attr("energy_consumption"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
    ),
    OpusSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        applies_to=lambda device: True,
        value_fn=_device_attr("dbm"),
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_by_default=False,
    ),
    # --- Ported from fubu2k fork ------------------------------------------
    OpusSensorDescription(
        key="illumination",
        translation_key="illuminance",
        applies_to=IS_SMS_PRESENCE,
        value_fn=_channel_attr("illumination"),
        device_class=SensorDeviceClass.ILLUMINANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=LIGHT_LUX,
    ),
    OpusSensorDescription(
        key="supply_voltage",
        translation_key="supply_voltage",
        applies_to=IS_SMS_PRESENCE,
        value_fn=_channel_attr("supply_voltage"),
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        applies_to=IS_SMS_PRESENCE,
        value_fn=_device_attr("battery_level"),
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OpusSensorDescription(
        key="handle_state",
        translation_key="handle_state",
        applies_to=HOPPE_ALL_VARIANTS,
        value_fn=_channel_attr("handle_state"),
        device_class=SensorDeviceClass.ENUM,
        options=(HANDLE_CLOSED, HANDLE_OPEN, HANDLE_TILT),
    ),
    OpusSensorDescription(
        key="unlock_request",
        translation_key="unlock_request",
        applies_to=HOPPE_ELOCK_ONLY,
        value_fn=_channel_attr("unlock_state"),
        device_class=SensorDeviceClass.ENUM,
        options=(UNLOCK_NOT_REQUESTED, UNLOCK_REQUESTED),
    ),
)


LOCK_DESCRIPTIONS: Final[tuple[OpusLockDescription, ...]] = (
    OpusLockDescription(
        key="window_handle_lock",
        translation_key="window_handle_lock",
        applies_to=HOPPE_ELOCK_ONLY,
        value_fn=_channel_attr("lock_state"),
        locked_state="locked",
        unlocked_state="unlocked",
        lock_state_attr="lock_state",
        coordinator_method="async_set_window_handle_lock",
    ),
)

_REGISTRY: Final[dict[str, tuple[OpusEntityDescription, ...]]] = {
    "binary_sensor": BINARY_SENSOR_DESCRIPTIONS,
    "sensor": SENSOR_DESCRIPTIONS,
    "lock": LOCK_DESCRIPTIONS,
}


def descriptions_for(
    device: EnOceanDevice, platform: str
) -> tuple[OpusEntityDescription, ...]:
    """Return every applicable description for one device on one platform."""
    return tuple(
        description
        for description in _REGISTRY.get(platform, ())
        if description.applies_to(device)
    )
