"""Read-only readings of device profiles without a dedicated platform model.

Each entry mirrors a profile of the gateway catalog (``GET /profiles/<EEP>``).
Only values the profile reports are listed; commands are not part of this table.
Keys the profile documents with several meanings (for example ``temperature``
of D2-35-00) are left out on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

type ReadingValue = float | str | bool | None

# Values a profile lists for "no usable reading".
NOT_AVAILABLE: Final = ("notAvailable",)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadingSpec:
    """One reading: the gateway key, how to validate it and how to show it."""

    key: str
    suffix: str
    kind: Literal["number", "text", "enum", "bool"]
    minimum: float | None = None
    maximum: float | None = None
    # Gateway values of an enum, shown as lower snake case.
    options: tuple[str, ...] = ()
    true_values: tuple[str, ...] = ()
    false_values: tuple[str, ...] = ()
    # Gateway values that mean "no reading" (the state becomes unknown).
    unavailable: tuple[str, ...] = NOT_AVAILABLE
    unit: str | None = None
    device_class: str | None = None
    measurement: bool = True
    precision: int | None = None
    diagnostic: bool = False
    enabled: bool = True


def number(
    key: str, suffix: str, minimum: float, maximum: float, **kwargs: object
) -> ReadingSpec:
    """Return a numeric reading."""
    return ReadingSpec(
        key=key,
        suffix=suffix,
        kind="number",
        minimum=minimum,
        maximum=maximum,
        **kwargs,  # type: ignore[arg-type]
    )


def text(key: str, suffix: str) -> ReadingSpec:
    """Return a diagnostic text reading, disabled by default."""
    return ReadingSpec(
        key=key, suffix=suffix, kind="text", diagnostic=True, enabled=False
    )


def enum(key: str, suffix: str, *options: str, **kwargs: object) -> ReadingSpec:
    """Return a reading with a fixed set of gateway values."""
    return ReadingSpec(
        key=key,
        suffix=suffix,
        kind="enum",
        options=options,
        **kwargs,  # type: ignore[arg-type]
    )


def flag(
    key: str,
    suffix: str,
    true_values: tuple[str, ...],
    false_values: tuple[str, ...],
    **kwargs: object,
) -> ReadingSpec:
    """Return a two-state reading."""
    return ReadingSpec(
        key=key,
        suffix=suffix,
        kind="bool",
        true_values=true_values,
        false_values=false_values,
        **kwargs,  # type: ignore[arg-type]
    )


CELSIUS: Final = "°C"
PERCENT: Final = "%"
TRUE_FALSE: Final = (("true",), ("false",))
ERROR_RESET: Final = (("error", "warning", "info"), ("reset",))
HEATER_MODE: Final = ("heating", "cooling")


def _clamp_errors(*extra: str) -> tuple[str, ...]:
    return (*NOT_AVAILABLE, *extra)


# Readings a device reports per channel (the telegram carries a ``channel``).
# They are stored per channel and shown as one set of entities for each channel
# that reported them.
CHANNEL_READINGS: Final[dict[str, tuple[ReadingSpec, ...]]] = {
    # CosiTherm heating actuator, 1 to 30 heating circuits.
    "D2-34-10": (
        number(
            "temperature",
            "temperature",
            0,
            40,
            unit=CELSIUS,
            device_class="temperature",
            unavailable=_clamp_errors("unknown"),
        ),
        number(
            "temperatureSetpoint",
            "temperature_setpoint",
            0,
            40,
            unit=CELSIUS,
            device_class="temperature",
            measurement=False,
            unavailable=_clamp_errors("unknown"),
        ),
        enum("heatingCircuitState", "circuit_state", "off", "on"),
        enum("operationMode", "operation_mode", "emergency", "off", "on"),
    ),
}

READINGS: Final[dict[str, tuple[ReadingSpec, ...]]] = {
    # Light, temperature and occupancy sensor (discontinued OPUS sensors).
    "A5-08-01": (
        number(
            "illumination",
            "illuminance",
            0,
            510,
            unit="lx",
            device_class="illuminance",
        ),
        number(
            "temperature",
            "temperature",
            0,
            51,
            unit=CELSIUS,
            device_class="temperature",
        ),
        flag("motionDetected", "motion", *TRUE_FALSE, device_class="motion"),
        flag("occupancyButton", "occupancy_button", ("pressed",), ("released",)),
        number(
            "supplyVoltage",
            "supply_voltage",
            0,
            5.1,
            unit="V",
            device_class="voltage",
            diagnostic=True,
        ),
    ),
    # Harvesting-powered valve actuator (Micropelt, Eurotronic Stella).
    "A5-20-06": (
        number(
            "temperature",
            "temperature",
            0,
            40,
            unit=CELSIUS,
            device_class="temperature",
        ),
        number(
            "temperatureSetpoint",
            "temperature_setpoint",
            0,
            40,
            unit=CELSIUS,
            device_class="temperature",
            measurement=False,
        ),
        number(
            "feedTemperature",
            "feed_temperature",
            0,
            80,
            unit=CELSIUS,
            device_class="temperature",
        ),
        number("valve", "valve_position", 0, 100, unit=PERCENT),
        number(
            "setpointShift",
            "setpoint_shift",
            -5,
            5,
            unit=CELSIUS,
            measurement=False,
            diagnostic=True,
            enabled=False,
        ),
        flag("window", "window", ("open",), ("closed",), device_class="window"),
        flag(
            "actuatorObstructed",
            "actuator_obstructed",
            *TRUE_FALSE,
            device_class="problem",
            diagnostic=True,
        ),
        flag("energyInput", "energy_input", *TRUE_FALSE, diagnostic=True),
        flag(
            "energyStorageCharged",
            "energy_storage_charged",
            *TRUE_FALSE,
            diagnostic=True,
        ),
        flag(
            "radioError",
            "radio_error",
            *TRUE_FALSE,
            device_class="problem",
            diagnostic=True,
        ),
        # "true" = weak signal (-77 dBm or less).
        flag(
            "radioSignalStrength",
            "radio_signal_weak",
            *TRUE_FALSE,
            device_class="problem",
            diagnostic=True,
        ),
    ),
    # Four digital inputs with wake signal and temperature.
    "A5-30-03": (
        flag("digitalInput0", "digital_input_0", ("high",), ("low",)),
        flag("digitalInput1", "digital_input_1", ("high",), ("low",)),
        flag("digitalInput2", "digital_input_2", ("high",), ("low",)),
        flag("digitalInput3", "digital_input_3", ("high",), ("low",)),
        flag("wakeStatus", "wake_signal", ("high",), ("low",), diagnostic=True),
        number(
            "temperature",
            "temperature",
            0,
            40,
            unit=CELSIUS,
            device_class="temperature",
        ),
    ),
    # Room operating panel: the values it reports (message C).
    "D2-11-01": (
        number(
            "temperature",
            "temperature",
            0,
            40,
            unit=CELSIUS,
            device_class="temperature",
        ),
        number(
            "humidity",
            "humidity",
            0,
            100,
            unit=PERCENT,
            device_class="humidity",
        ),
        number(
            "baseSetpoint",
            "base_setpoint",
            15,
            30,
            unit=CELSIUS,
            device_class="temperature",
            measurement=False,
        ),
        enum("fanSpeed", "fan_speed", "auto", "speed0", "speed1", "speed2", "speed3"),
        flag(
            "occupancyState",
            "occupancy",
            ("occupied",),
            ("unoccupied",),
            device_class="occupancy",
        ),
        enum(
            "setpointType",
            "setpoint_type",
            "absolute",
            "relative",
            diagnostic=True,
            enabled=False,
        ),
    ),
    # IoT multisensor.
    "D2-14-41": (
        number(
            "temperature",
            "temperature",
            -40,
            60,
            unit=CELSIUS,
            device_class="temperature",
            unavailable=_clamp_errors("overrange", "underrange", "temperatureError"),
        ),
        number(
            "humidity",
            "humidity",
            0,
            100,
            unit=PERCENT,
            device_class="humidity",
            unavailable=_clamp_errors("humidityError"),
        ),
        number(
            "illumination",
            "illuminance",
            0,
            100000,
            unit="lx",
            device_class="illuminance",
            unavailable=_clamp_errors("illuminationError"),
        ),
        flag("contact", "contact", ("open",), ("closed",), device_class="opening"),
        *(
            number(
                f"acceleration{axis}",
                f"acceleration_{axis.lower()}",
                -2.5,
                2.5,
                unit="g",
                diagnostic=True,
                enabled=False,
                unavailable=_clamp_errors(
                    f"accelerationError{axis}", "overrange", "underrange"
                ),
            )
            for axis in ("X", "Y", "Z")
        ),
        enum(
            "accelerationStatus",
            "acceleration_status",
            "heartbeat",
            "threshold1",
            "threshold2",
            diagnostic=True,
            enabled=False,
        ),
    ),
    # Temperature/humidity sensor with leak detection (OPUS 55 SENS).
    "D2-14-54": (
        number(
            "temperature",
            "temperature",
            0,
            50,
            unit=CELSIUS,
            device_class="temperature",
            unavailable=_clamp_errors("error"),
        ),
        number(
            "humidity",
            "humidity",
            0,
            100,
            unit=PERCENT,
            device_class="humidity",
            unavailable=_clamp_errors("invalid", "notSupported"),
        ),
        flag(
            "liquidDetected",
            "water_leak",
            *TRUE_FALSE,
            device_class="moisture",
            unavailable=_clamp_errors("error"),
        ),
        # "true" = a leak was detected within the last 14 days.
        flag(
            "pastLeakDetection",
            "past_leak_detection",
            *TRUE_FALSE,
            device_class="moisture",
        ),
    ),
    # A.C. current clamps.
    "D2-32-00": (
        number(
            "current1",
            "current_1",
            0,
            4095,
            unit="A",
            device_class="current",
        ),
        flag(
            "powerFailureDetected",
            "power_failure",
            *TRUE_FALSE,
            device_class="problem",
            diagnostic=True,
        ),
    ),
    "D2-32-02": (
        *(
            number(
                f"current{channel}",
                f"current_{channel}",
                0,
                4095,
                unit="A",
                device_class="current",
            )
            for channel in (1, 2, 3)
        ),
        flag(
            "powerFailureDetected",
            "power_failure",
            *TRUE_FALSE,
            device_class="problem",
            diagnostic=True,
        ),
    ),
    # CosiTherm heating actuator: system report. The per-channel values are in
    # CHANNEL_READINGS.
    "D2-34-10": (
        enum("heaterMode", "heater_mode", *HEATER_MODE),
        enum(
            "error",
            "system_error",
            "generalError",
            "heatCircuitError",
            "noError",
            diagnostic=True,
        ),
        number(
            "channelNumber",
            "channel_count",
            1,
            30,
            measurement=False,
            diagnostic=True,
            enabled=False,
        ),
    ),
    # Infrared heating actuator. "temperature" (room or panel surface) and
    # "presence" (the gateway text contradicts itself) are not mapped.
    "D2-35-00": (
        enum("mode", "actuator_mode", "managed", "off", "on", "error"),
        number(
            "temperatureSetpoint",
            "temperature_setpoint",
            5,
            40,
            unit=CELSIUS,
            device_class="temperature",
            measurement=False,
        ),
        number(
            "outdoorTemperature",
            "outdoor_temperature",
            -30,
            60,
            unit=CELSIUS,
            device_class="temperature",
        ),
        flag("window", "window", ("open",), ("closed",), device_class="window"),
        number("nominalValue", "load", 0, 100, unit=PERCENT),
        number("power", "power", 0, 10000, unit="W", device_class="power"),
    ),
    # Moellenhoff actuator (older) and actuator 3.0: status reports.
    "D1-4B-00": (
        enum("heaterMode", "heater_mode", *HEATER_MODE),
        number(
            "hardwareVersion",
            "hardware_version",
            0,
            255,
            measurement=False,
            diagnostic=True,
            enabled=False,
        ),
        number(
            "networkVersion",
            "network_version",
            0,
            65535,
            measurement=False,
            diagnostic=True,
            enabled=False,
        ),
        number(
            "softwareVersion",
            "software_version",
            0,
            65535,
            measurement=False,
            diagnostic=True,
            enabled=False,
        ),
    ),
    # Moellenhoff heat area (older): temperature and setpoint reports. Commands
    # need an identifier key and are not mapped.
    "D1-4B-01": (
        number(
            "temperature",
            "temperature",
            0,
            40,
            unit=CELSIUS,
            device_class="temperature",
        ),
        number(
            "temperatureSetpoint",
            "temperature_setpoint",
            5,
            30,
            unit=CELSIUS,
            device_class="temperature",
            measurement=False,
        ),
    ),
    "D1-4B-02": (
        enum("heaterMode", "heater_mode", *HEATER_MODE),
        text("hardwareVersion", "hardware_version"),
        text("networkVersion", "network_version"),
        text("softwareVersion", "software_version"),
        text("ipAddress", "ip_address"),
        flag(
            "circuitMissmatch",
            "circuit_mismatch",
            *ERROR_RESET,
            device_class="problem",
            diagnostic=True,
        ),
        flag(
            "mhNotFound",
            "mh_not_found",
            *ERROR_RESET,
            device_class="problem",
            diagnostic=True,
        ),
    ),
    # Heat area 3.0 (virtual): extra reports next to the climate values.
    "D1-4B-04": (
        flag(
            "dataMissmatch",
            "data_mismatch",
            *ERROR_RESET,
            device_class="problem",
            diagnostic=True,
        ),
        flag(
            "missingData",
            "missing_data",
            *ERROR_RESET,
            device_class="problem",
            diagnostic=True,
        ),
    ),
    # WirelessMBus virtual device. The raw "payload" is not mapped.
    "D1-4B-10": (
        text("deviceType", "device_type"),
        text("manufacturer", "manufacturer"),
        text("serial", "serial"),
        text("version", "version"),
    ),
    # Pressac power clamp (virtual profile).
    "D1-4B-15": (
        number(
            "totalCurrent", "total_current", 0, 1800, unit="A", device_class="current"
        ),
        number("totalPower", "total_power", 0, 432000, unit="W", device_class="power"),
    ),
    # REG central control unit: reports only; LED, firmware reset and update
    # commands are not mapped.
    "D1-4B-50": (
        enum(
            "ledStatus",
            "led_status",
            "disabled",
            "enabled",
            diagnostic=True,
            enabled=False,
        ),
        number(
            "uptime",
            "uptime",
            0,
            60000,
            unit="d",
            device_class="duration",
            measurement=False,
            diagnostic=True,
            enabled=False,
        ),
    ),
}
