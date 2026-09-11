"""EnOcean device representation for Opus GreenNet Bridge."""

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
    KEY_ANGLE,
    KEY_CHANNEL,
    KEY_CIRCUIT_IN_USE,
    KEY_DIMMER,
    KEY_ENERGY,
    KEY_ENERGY_CONSUMPTION,
    KEY_FEED_TEMPERATURE,
    KEY_HEATER_MODE,
    KEY_HUMIDITY,
    KEY_LIQUID_DETECTED,
    KEY_LOCAL_CONTROL,
    KEY_MISSING_TEMPERATURE,
    KEY_POSITION,
    KEY_POWER,
    KEY_POWER_STATE,
    KEY_ROTATION_TIME,
    KEY_SUMMER_MODE,
    KEY_SWITCH,
    KEY_TEMPERATURE,
    KEY_TEMPERATURE_ORIGIN,
    KEY_TEMPERATURE_SETPOINT,
    KEY_THERMAL_MODE,
    KEY_WINDOW_OPEN,
    STATE_ON,
)


@dataclass
class EnOceanChannel:
    """Represents a single channel of an EnOcean device."""

    channel_id: int
    state_revision: int = field(default=0, repr=False, compare=False)
    is_on: bool | None = None
    brightness: int | None = None  # 0-100 for dimmers
    position: int | None = None  # 0-100 for covers
    angle: int | None = None  # Tilt angle for blinds
    rotation_time: float | None = None  # Zero means the cover has no slat rotation
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
    # Transient rocker-switch state: set only by the most recent telegram and
    # emitted by the event entity. Reset on every update_from_telegram call.
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
        """Determine the entity type based on the primary EEP."""
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
                "D2-01-02",
                "D2-01-03",
                "D2-01-06",
                "D2-01-07",
                "D2-01-0A",
                "D2-01-0B",
                "D2-01-0F",
                "D2-01-10",
                "D2-01-12",
                "A5-38-08",
            ]
        return False

    @property
    def is_cover(self) -> bool:
        """Check if this device is a cover/blind."""
        eep = self.primary_eep
        if eep:
            return eep.startswith("D2-05-")
        return False

    @property
    def supports_tilt(self) -> bool:
        """Check if this cover supports tilt/angle control."""
        return self.supports_tilt_for_channel()

    def supports_tilt_for_channel(self, channel_id: int = DEFAULT_CHANNEL) -> bool:
        """Use the reported rotation time when available, otherwise the EEP."""
        eep = self.primary_eep
        if eep not in ["D2-05-00", "D2-05-02"]:
            return False
        channel = self.channels.get(channel_id)
        return channel is None or channel.rotation_time != 0

    @property
    def is_climate(self) -> bool:
        """Check if this device is a climate/heating device."""
        eep = self.primary_eep
        if eep:
            return eep in ["D1-4B-05", "D1-4B-06", "D1-4B-07"]
        return False

    @property
    def heat_area_type(self) -> str | None:
        """Return the specific heat area type."""
        eep = self.primary_eep
        eep_type_map = {
            "D1-4B-05": "valve",
            "D1-4B-06": "cositherm",
            "D1-4B-07": "electro",
        }
        return eep_type_map.get(eep)

    @property
    def setpoint_step(self) -> float:
        """Return the temperature setpoint step size for this device."""
        if self.primary_eep == "D1-4B-05":
            return 0.5
        return 0.1  # D1-4B-06 and D1-4B-07 both use 0.1

    @property
    def channel_count(self) -> int:
        """Determine the number of channels based on EEP."""
        eep = self.primary_eep
        if not eep:
            return 1

        # Multi-channel actuators
        channel_map = {
            "D2-01-04": 2,
            "D2-01-05": 2,
            "D2-01-06": 2,
            "D2-01-07": 2,
            "D2-01-08": 4,
            "D2-01-09": 4,
            "D2-01-0A": 4,
            "D2-01-0B": 4,
            "D2-01-0D": 8,
            "D2-01-0E": 8,
            "D2-01-0F": 8,
            "D2-01-10": 8,
            # Local Control variants: same channel count as their non-LC counterparts
            # D2-01-11 = 2-ch switch with local control (same as D2-01-04/05)
            # D2-01-12 = 2-ch dimmer with local control (same as D2-01-06/07)
            "D2-01-11": 2,
            "D2-01-12": 2,
        }
        return channel_map.get(eep, 1)

    def get_or_create_channel(
        self, channel_id: int = DEFAULT_CHANNEL
    ) -> EnOceanChannel:
        """Get or create a channel for this device."""
        if channel_id not in self.channels:
            self.channels[channel_id] = EnOceanChannel(channel_id=channel_id)
        return self.channels[channel_id]

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
        except ValueError, OverflowError:
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

    def update_from_telegram(self, telegram: dict[str, Any]) -> None:
        """Update device state from a telegram message."""
        functions = telegram.get("functions", [])
        if isinstance(functions, dict):
            functions = [functions]
        if not isinstance(functions, list):
            functions = []
        functions = [func for func in functions if isinstance(func, dict)]

        # Button fields describe only this telegram, including metadata-only updates.
        for channel in self.channels.values():
            channel.last_button = None
            channel.last_button_action = None

        # Determine default channel from telegram-level channel function.
        default_channel_id = DEFAULT_CHANNEL
        for func in functions:
            if func.get("key") == KEY_CHANNEL:
                default_channel_id = self._parse_channel_id(
                    func.get("value", DEFAULT_CHANNEL)
                )
                break

        # Update channel state from functions
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
                brightness = self._parse_number(
                    value, minimum=0, maximum=100, integer=True
                )
                if brightness is not None:
                    channel.brightness = int(brightness)
                    channel.is_on = brightness > 0

            elif key == KEY_POSITION:
                self._update_numeric_field(
                    channel, "position", value, 0, 100, integer=True
                )

            elif key == KEY_ANGLE:
                self._update_numeric_field(
                    channel, "angle", value, 0, 100, integer=True
                )

            elif key == KEY_ROTATION_TIME:
                # EEP D2-05-00 uses noRotation; OPUS also reports numeric zero.
                # Unknown/noChange values must not erase a known configuration.
                if isinstance(value, str) and value.strip() == "noRotation":
                    channel.rotation_time = 0
                elif isinstance(value, (int, float, str)) and not isinstance(
                    value, bool
                ):
                    try:
                        rotation_time = float(value)
                    except ValueError, OverflowError:
                        continue
                    if isfinite(rotation_time) and rotation_time >= 0:
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

            # Climate keys
            elif key == KEY_TEMPERATURE:
                self._update_numeric_field(
                    channel, "temperature", value, 0, 40, allow_unavailable=True
                )

            elif key == KEY_TEMPERATURE_SETPOINT:
                self._update_numeric_field(
                    channel,
                    "temperature_setpoint",
                    value,
                    0,
                    40,
                    allow_unavailable=True,
                )

            elif key == KEY_HEATER_MODE:
                if value in (
                    "heating",
                    "on",
                    "off",
                    "autoOff",
                    "configIncomplete",
                    "error",
                ):
                    channel.heater_mode = value

            elif key == KEY_HUMIDITY:
                self._update_numeric_field(
                    channel, "humidity", value, 0, 100, allow_unavailable=True
                )

            elif key == KEY_WINDOW_OPEN:
                if (parsed := self._parse_boolean(value)) is not None:
                    channel.window_open = parsed

            elif key == KEY_SUMMER_MODE:
                if (parsed := self._parse_boolean(value)) is not None:
                    channel.summer_mode = parsed

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

        # Update last seen from telegram
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
            dbm=int(dbm) if dbm is not None else None,
        )
