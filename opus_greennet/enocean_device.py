"""EnOcean device representation for Opus GreenNet Bridge.

Base: kegelmeier v0.3.3b0 (strict numeric/enum parsing, per-channel
`state_revision` for race-condition guards). Extended with fields for the
four ported devices (HOPPE window handles, Jaeger Direkt RWM, OPUS SMS
presence sensor).

FIX (2026-09-23): `mechanics_status` (EEP D2-06-40 "Mechanics Status" byte,
key="mechanics", values "ok"/"error") was referenced by
entity_descriptions.py's mechanics_fault binary_sensor description, but the
field was never actually added to EnOceanChannel in a previously-deployed
copy of this file - causing `AttributeError: 'EnOceanChannel' object has no
attribute 'mechanics_status'` on every state write for the HOPPE eLock
device. Added now, along with its update_from_telegram() parsing branch.

`handle_state` (Handle Status: open/closed/tilt) and `lock_state` (Lock
Status: locked/unlocked) are two independent EEP fields from two different
keys ("handle" vs "lock") - never conflate them. The lock entity must only
ever read `lock_state`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any

from .const import (
    BUTTON_KEYS,
    DEFAULT_CHANNEL,
    EEP_MAPPINGS,
    KEY_ACTUATOR_DEACTIVATED,
    KEY_ACTUATOR_LOW_BATTERY,
    KEY_ACTUATOR_NOT_RESPONDING,
    KEY_ALARM,
    KEY_ANGLE,
    KEY_BATTERY_LOW,
    KEY_CHANNEL,
    KEY_CIRCUIT_IN_USE,
    KEY_DIMMER,
    KEY_ENERGY,
    KEY_ENERGY_CONSUMPTION,
    KEY_FEED_TEMPERATURE,
    KEY_HANDLE,
    KEY_HEATER_MODE,
    KEY_HUMIDITY,
    KEY_ILLUMINATION,
    KEY_LIQUID_DETECTED,
    KEY_LOCAL_CONTROL,
    KEY_LOCK,
    KEY_MECHANICS,
    KEY_MISSING_TEMPERATURE,
    KEY_MOTION_DETECTED,
    KEY_POSITION,
    KEY_POWER,
    KEY_POWER_STATE,
    KEY_ROTATION_TIME,
    KEY_SUMMER_MODE,
    KEY_SUPPLY_VOLTAGE,
    KEY_SWITCH,
    KEY_TEMPERATURE,
    KEY_TEMPERATURE_ORIGIN,
    KEY_TEMPERATURE_SETPOINT,
    KEY_THERMAL_MODE,
    KEY_UNLOCK,
    KEY_WINDOW_OPEN,
    STATE_ON,
)


@dataclass
class EnOceanChannel:
    """Represents a single channel of an EnOcean device."""

    channel_id: int
    state_revision: int = field(default=0, repr=False, compare=False)
    is_on: bool | None = None
    brightness: int | None = None
    position: int | None = None
    angle: int | None = None
    rotation_time: float | None = None
    local_control: bool | None = None
    energy: float | None = None
    power: float | None = None
    liquid_detected: bool | None = None
    # Climate fields
    temperature: float | None = None
    temperature_setpoint: float | None = None
    heater_mode: str | None = None
    humidity: float | None = None
    window_open: bool | None = None
    summer_mode: bool | None = None
    feed_temperature: float | None = None
    thermal_mode: str | None = None
    energy_consumption: float | None = None
    power_state: str | None = None
    temperature_origin: str | None = None
    # Error/warning states
    actuator_deactivated: str | None = None
    actuator_low_battery: str | None = None
    actuator_not_responding: str | None = None
    missing_temperature: str | None = None
    circuit_in_use: str | None = None
    # --- OPUS SMS Anwesenheit (A5-07-03) ------------------------------
    motion_detected: bool | None = None
    illumination: float | None = None
    supply_voltage: float | None = None
    # --- HOPPE window handle (D2-06-40 / F6-10-00 / D2-03-10) ----------
    # NOTE: handle_state (window open/closed/tilt) and lock_state (lock-bolt
    # locked/unlocked) are two INDEPENDENT EEP fields from two different
    # keys ("handle" vs "lock"). The HA lock entity must only ever read
    # lock_state.
    handle_state: str | None = None
    mechanics_status: str | None = None
    lock_state: str | None = None
    unlock_state: str | None = None
    # --- Jaeger Direkt RWM (F6-05-02) ----------------------------------
    smoke_alarm: bool | None = None
    battery_low: bool | None = None
    # Transient rocker-switch state, reset on every update_from_telegram call.
    last_button: str | None = None
    last_button_action: str | None = None


@dataclass
class EnOceanDevice:
    """Represents an EnOcean device from the gateway."""

    device_id: str
    friendly_id: str
    eeps: list[dict[str, Any]] = field(default_factory=list)
    manufacturer: str = ""
    physical_device: str = ""
    first_seen: str = ""
    last_seen: str = ""
    software_revision: str = ""
    hardware_revision: str = ""
    last_update_source: str = ""
    last_update_received_monotonic: float | None = field(
        default=None, repr=False, compare=False
    )
    last_update_finalized_monotonic: float | None = field(
        default=None, repr=False, compare=False
    )
    last_update_dispatched_monotonic: float | None = field(
        default=None, repr=False, compare=False
    )
    dbm: int | None = None
    battery_level: int | None = None
    last_command_error: str | None = None
    channels: dict[int, EnOceanChannel] = field(default_factory=dict)
    profile: dict[str, Any] | None = None

    @property
    def primary_eep(self) -> str | None:
        """Get the primary EEP for this device."""
        if self.eeps:
            return self.eeps[0].get("eep")
        return None

    @property
    def entity_type(self) -> str | None:
        """Determine the primary entity type based on the primary EEP."""
        eep = self.primary_eep
        if eep and eep in EEP_MAPPINGS:
            return EEP_MAPPINGS[eep][0]
        return None

    @property
    def is_dimmable(self) -> bool:
        """Check if this device supports dimming."""
        eep = self.primary_eep
        if eep:
            return eep in [
                "D2-01-02", "D2-01-03", "D2-01-06", "D2-01-07",
                "D2-01-0A", "D2-01-0B", "D2-01-0F", "D2-01-10",
                "D2-01-12", "A5-38-08",
            ]
        return False

    @property
    def is_cover(self) -> bool:
        """Check if this device is a cover/blind."""
        eep = self.primary_eep
        return bool(eep and eep.startswith("D2-05-"))

    def supports_tilt_for_channel(self, channel_id: int = DEFAULT_CHANNEL) -> bool:
        """Use the reported rotation time when available, otherwise the EEP."""
        eep = self.primary_eep
        if eep not in ("D2-05-00", "D2-05-02"):
            return False
        channel = self.channels.get(channel_id)
        return channel is None or channel.rotation_time != 0

    @property
    def is_climate(self) -> bool:
        """Check if this device is a climate/heating device."""
        eep = self.primary_eep
        return eep in ("D1-4B-05", "D1-4B-06", "D1-4B-07") if eep else False

    @property
    def setpoint_step(self) -> float:
        """Return the temperature setpoint step size for this device."""
        if self.primary_eep == "D1-4B-05":
            return 0.5
        return 0.1

    @property
    def channel_count(self) -> int:
        """Determine the number of channels based on EEP."""
        eep = self.primary_eep
        if not eep:
            return 1
        channel_map = {
            "D2-01-04": 2, "D2-01-05": 2, "D2-01-06": 2, "D2-01-07": 2,
            "D2-01-08": 4, "D2-01-09": 4, "D2-01-0A": 4, "D2-01-0B": 4,
            "D2-01-0D": 8, "D2-01-0E": 8, "D2-01-0F": 8, "D2-01-10": 8,
            "D2-01-11": 2, "D2-01-12": 2,
        }
        return channel_map.get(eep, 1)

    def get_or_create_channel(
        self, channel_id: int = DEFAULT_CHANNEL
    ) -> EnOceanChannel:
        """Get or create a channel for this device."""
        if channel_id not in self.channels:
            self.channels[channel_id] = EnOceanChannel(channel_id=channel_id)
        return self.channels[channel_id]

    # ── Strict parsing helpers (v0.3.3b0) ──────────────────────────────────

    @staticmethod
    def _parse_number(
        value: Any,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        integer: bool = False,
    ) -> float | int | None:
        """Parse finite protocol numbers without treating booleans as readings."""
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            number = float(value)
        except (ValueError, OverflowError):
            return None
        if (
            not isfinite(number)
            or (minimum is not None and number < minimum)
            or (maximum is not None and number > maximum)
            or (integer and not number.is_integer())
        ):
            return None
        return int(number) if integer else number

    def _parse_channel_id(self, value: Any) -> int | None:
        """Reject malformed channel selectors instead of updating channel zero."""
        parsed = self._parse_number(value, minimum=0, integer=True)
        return int(parsed) if parsed is not None else None

    def _update_numeric_field(
        self,
        channel: EnOceanChannel,
        attr: str,
        value: Any,
        minimum: float | None = None,
        maximum: float | None = None,
        *,
        integer: bool = False,
        allow_unavailable: bool = False,
    ) -> None:
        """Clear an explicitly unavailable reading and ignore malformed values."""
        if allow_unavailable and value == "notAvailable":
            setattr(channel, attr, None)
        elif (
            number := self._parse_number(
                value, minimum=minimum, maximum=maximum, integer=integer
            )
        ) is not None:
            setattr(channel, attr, number)

    @staticmethod
    def _parse_boolean(value: Any) -> bool | None:
        """Parse an explicit boolean without guessing from malformed values."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
        return None

    @staticmethod
    def parse_battery_level(value: Any) -> int | None:
        """Parse an OPUS battery percentage such as 72 or '72%'."""
        if isinstance(value, str):
            value = value.strip().removesuffix("%").strip()
        parsed = EnOceanDevice._parse_number(value, minimum=0, maximum=100)
        return round(parsed) if parsed is not None else None

    # ── Telegram application ───────────────────────────────────────────────

    def update_from_telegram(self, telegram: dict[str, Any]) -> None:
        """Update device state from a telegram message."""
        functions = telegram.get("functions", [])
        if isinstance(functions, dict):
            functions = [functions]
        if not isinstance(functions, list):
            functions = []
        functions = [func for func in functions if isinstance(func, dict)]

        for channel in self.channels.values():
            channel.last_button = None
            channel.last_button_action = None

        default_channel_id = DEFAULT_CHANNEL
        for func in functions:
            if func.get("key") == KEY_CHANNEL:
                parsed = self._parse_channel_id(func.get("value", DEFAULT_CHANNEL))
                if parsed is not None:
                    default_channel_id = parsed
                break

        for func in functions:
            key = func.get("key")
            if key == KEY_CHANNEL:
                continue

            value = func.get("value")
            channel_id = self._parse_channel_id(func.get("channel", default_channel_id))
            if channel_id is None:
                continue
            channel = self.get_or_create_channel(channel_id)
            channel.state_revision += 1

            if key in BUTTON_KEYS:
                if value in ("pressed", "released"):
                    channel.last_button = key
                    channel.last_button_action = value

            elif key == KEY_SWITCH:
                if value in ("on", "off"):
                    channel.is_on = value == STATE_ON

            elif key == KEY_DIMMER:
                brightness = self._parse_number(value, minimum=0, maximum=100, integer=True)
                if brightness is not None:
                    channel.brightness = int(brightness)
                    channel.is_on = brightness > 0

            elif key == KEY_POSITION:
                self._update_numeric_field(channel, "position", value, 0, 100, integer=True)

            elif key == KEY_ANGLE:
                self._update_numeric_field(channel, "angle", value, 0, 100, integer=True)

            elif key == KEY_ROTATION_TIME:
                if isinstance(value, str) and value.strip() == "noRotation":
                    channel.rotation_time = 0
                else:
                    rotation_time = self._parse_number(value, minimum=0)
                    if rotation_time is not None:
                        channel.rotation_time = rotation_time

            elif key == KEY_LOCAL_CONTROL:
                if value in ("on", "off"):
                    channel.local_control = value == STATE_ON

            elif key == KEY_ENERGY:
                self._update_numeric_field(channel, "energy", value)

            elif key == KEY_POWER:
                self._update_numeric_field(channel, "power", value)

            elif key == KEY_LIQUID_DETECTED:
                liquid_detected = self._parse_boolean(value)
                if liquid_detected is not None:
                    channel.liquid_detected = liquid_detected

            # --- OPUS SMS Anwesenheit (A5-07-03) ---------------------------
            elif key == KEY_MOTION_DETECTED:
                if value == "notAvailable":
                    channel.motion_detected = None
                else:
                    parsed_motion = self._parse_boolean(value)
                    if parsed_motion is not None:
                        channel.motion_detected = parsed_motion

            elif key == KEY_ILLUMINATION:
                self._update_numeric_field(
                    channel, "illumination", value, 0, allow_unavailable=True
                )

            elif key == KEY_SUPPLY_VOLTAGE:
                self._update_numeric_field(
                    channel, "supply_voltage", value, 0, allow_unavailable=True
                )

            # --- HOPPE window handle (Handle Status - window open/tilt) ---
            elif key == KEY_HANDLE:
                if value in ("closed", "open", "tilt"):
                    channel.handle_state = value

            # --- HOPPE window handle (Mechanics Status) --------------------
            elif key == KEY_MECHANICS:
                if value in ("ok", "error"):
                    channel.mechanics_status = value

            # --- HOPPE window handle (Lock Status - lock-bolt, drives the
            # lock entity; NEVER conflate with KEY_HANDLE above) -----------
            elif key == KEY_LOCK:
                if value in ("locked", "unlocked"):
                    channel.lock_state = value

            # --- HOPPE window handle (Unlock Query) ------------------------
            elif key == KEY_UNLOCK:
                if value in ("notRequested", "requested"):
                    channel.unlock_state = value

            # --- Jaeger Direkt RWM (F6-05-02) ------------------------------
            elif key == KEY_ALARM:
                if value in ("on", "off"):
                    channel.smoke_alarm = value == STATE_ON

            elif key == KEY_BATTERY_LOW:
                battery_low = self._parse_boolean(value)
                if battery_low is not None:
                    channel.battery_low = battery_low

            # Climate keys
            elif key == KEY_TEMPERATURE:
                self._update_numeric_field(
                    channel, "temperature", value, 0, 40, allow_unavailable=True
                )
            elif key == KEY_TEMPERATURE_SETPOINT:
                self._update_numeric_field(
                    channel, "temperature_setpoint", value, 0, 40, allow_unavailable=True
                )
            elif key == KEY_HEATER_MODE:
                if value in ("heating", "on", "off", "autoOff", "configIncomplete", "error"):
                    channel.heater_mode = value
            elif key == KEY_HUMIDITY:
                self._update_numeric_field(
                    channel, "humidity", value, 0, 100, allow_unavailable=True
                )
            elif key == KEY_WINDOW_OPEN:
                parsed_bool = self._parse_boolean(value)
                if parsed_bool is not None:
                    channel.window_open = parsed_bool
            elif key == KEY_SUMMER_MODE:
                parsed_bool = self._parse_boolean(value)
                if parsed_bool is not None:
                    channel.summer_mode = parsed_bool
            elif key == KEY_FEED_TEMPERATURE:
                self._update_numeric_field(
                    channel, "feed_temperature", value, 0, 80, allow_unavailable=True
                )
            elif key == KEY_THERMAL_MODE:
                if value in ("heating", "cooling"):
                    channel.thermal_mode = value
            elif key == KEY_ENERGY_CONSUMPTION:
                self._update_numeric_field(
                    channel, "energy_consumption", value, 0, 10, allow_unavailable=True
                )
            elif key == KEY_POWER_STATE:
                if value in ("active", "inactive"):
                    channel.power_state = value
            elif key == KEY_TEMPERATURE_ORIGIN:
                if value in ("external", "internal"):
                    channel.temperature_origin = value

            # Error/warning states
            elif key == KEY_ACTUATOR_DEACTIVATED:
                if value in ("info", "reset"):
                    channel.actuator_deactivated = value
            elif key == KEY_ACTUATOR_LOW_BATTERY:
                if value in ("warning", "reset"):
                    channel.actuator_low_battery = value
            elif key == KEY_ACTUATOR_NOT_RESPONDING:
                if value in ("warning", "error", "reset"):
                    channel.actuator_not_responding = value
            elif key == KEY_MISSING_TEMPERATURE:
                if value in ("info", "warning", "error", "reset"):
                    channel.missing_temperature = value
            elif key == KEY_CIRCUIT_IN_USE:
                if value in ("error", "reset"):
                    channel.circuit_in_use = value

        if isinstance(telegram.get("timestamp"), str):
            self.last_seen = telegram["timestamp"]
        if isinstance(telegram.get("telegramInfo"), dict):
            dbm = self._parse_number(telegram["telegramInfo"].get("dbm"), integer=True)
            if dbm is not None:
                self.dbm = int(dbm)

    @classmethod
    def from_device_object(cls, device_data: dict[str, Any]) -> EnOceanDevice:
        """Create an EnOceanDevice from a device JSON object."""
        device = device_data.get("device", device_data)
        dbm = cls._parse_number(device.get("dbm"), integer=True)
        return cls(
            device_id=device.get("deviceId", ""),
            friendly_id=device.get("friendlyId", ""),
            eeps=device.get("eeps", []),
            manufacturer=device.get("manufacturer", ""),
            physical_device=device.get("physicalDevice", ""),
            first_seen=device.get("firstSeen", ""),
            last_seen=device.get("lastSeen", ""),
            software_revision=str(device.get("softwareRevision", "")),
            hardware_revision=str(device.get("hardwareRevision", "")),
            dbm=int(dbm) if dbm is not None else None,
            battery_level=cls.parse_battery_level(device.get("batteryLevel")),
        )
