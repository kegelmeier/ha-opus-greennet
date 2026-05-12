"""EnOcean device representation for Opus GreenNet Bridge."""
from __future__ import annotations

from dataclasses import dataclass, field
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
    KEY_LOCAL_CONTROL,
    KEY_MISSING_TEMPERATURE,
    KEY_POSITION,
    KEY_POWER,
    KEY_POWER_STATE,
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
    is_on: bool = False
    brightness: int | None = None  # 0-100 for dimmers
    position: int | None = None  # 0-100 for covers
    angle: int | None = None  # Tilt angle for blinds
    local_control: bool = False
    energy: float | None = None
    power: float | None = None
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
    # consumed once by the event entity. Reset on every update_from_telegram call.
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
    dbm: int = 0
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
        eep = self.primary_eep
        return eep in ["D2-05-00", "D2-05-02"]

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

    def update_from_telegram(self, telegram: dict[str, Any]) -> None:
        """Update device state from a telegram message."""
        functions = telegram.get("functions", [])
        if isinstance(functions, dict):
            functions = [functions]

        # Determine channel from telegram
        channel_id = DEFAULT_CHANNEL
        for func in functions:
            if func.get("key") == KEY_CHANNEL:
                try:
                    channel_id = int(func.get("value", DEFAULT_CHANNEL))
                except (ValueError, TypeError):
                    channel_id = DEFAULT_CHANNEL
                break

        channel = self.get_or_create_channel(channel_id)

        # Reset transient rocker fields so they only reflect the current telegram.
        channel.last_button = None
        channel.last_button_action = None

        # Update channel state from functions
        for func in functions:
            key = func.get("key")
            value = func.get("value")

            if key in BUTTON_KEYS:
                channel.last_button = key
                channel.last_button_action = str(value) if value is not None else None

            elif key == KEY_SWITCH:
                channel.is_on = value == STATE_ON

            elif key == KEY_DIMMER:
                try:
                    channel.brightness = int(value)
                    channel.is_on = channel.brightness > 0
                except (ValueError, TypeError):
                    pass

            elif key == KEY_POSITION:
                try:
                    channel.position = int(value)
                except (ValueError, TypeError):
                    pass

            elif key == KEY_ANGLE:
                try:
                    channel.angle = int(value)
                except (ValueError, TypeError):
                    pass

            elif key == KEY_LOCAL_CONTROL:
                channel.local_control = value == STATE_ON

            elif key == KEY_ENERGY:
                try:
                    channel.energy = float(value)
                except (ValueError, TypeError):
                    pass

            elif key == KEY_POWER:
                try:
                    channel.power = float(value)
                except (ValueError, TypeError):
                    pass

            # Climate keys
            elif key == KEY_TEMPERATURE:
                if value != "notAvailable":
                    try:
                        channel.temperature = float(value)
                    except (ValueError, TypeError):
                        pass

            elif key == KEY_TEMPERATURE_SETPOINT:
                if value != "notAvailable":
                    try:
                        channel.temperature_setpoint = float(value)
                    except (ValueError, TypeError):
                        pass

            elif key == KEY_HEATER_MODE:
                channel.heater_mode = str(value)

            elif key == KEY_HUMIDITY:
                if value != "notAvailable":
                    try:
                        channel.humidity = float(value)
                    except (ValueError, TypeError):
                        pass

            elif key == KEY_WINDOW_OPEN:
                channel.window_open = value == "true" or value is True

            elif key == KEY_SUMMER_MODE:
                channel.summer_mode = value == "true" or value is True

            elif key == KEY_FEED_TEMPERATURE:
                if value != "notAvailable":
                    try:
                        channel.feed_temperature = float(value)
                    except (ValueError, TypeError):
                        pass

            elif key == KEY_THERMAL_MODE:
                channel.thermal_mode = str(value)

            elif key == KEY_ENERGY_CONSUMPTION:
                if value != "notAvailable":
                    try:
                        channel.energy_consumption = float(value)
                    except (ValueError, TypeError):
                        pass

            elif key == KEY_POWER_STATE:
                channel.power_state = str(value)

            elif key == KEY_TEMPERATURE_ORIGIN:
                channel.temperature_origin = str(value)

            # Error/warning states
            elif key == KEY_ACTUATOR_DEACTIVATED:
                channel.actuator_deactivated = str(value)

            elif key == KEY_ACTUATOR_LOW_BATTERY:
                channel.actuator_low_battery = str(value)

            elif key == KEY_ACTUATOR_NOT_RESPONDING:
                channel.actuator_not_responding = str(value)

            elif key == KEY_MISSING_TEMPERATURE:
                channel.missing_temperature = str(value)

            elif key == KEY_CIRCUIT_IN_USE:
                channel.circuit_in_use = str(value)

        # Update last seen from telegram
        if "timestamp" in telegram:
            self.last_seen = telegram["timestamp"]
        if "telegramInfo" in telegram:
            dbm = telegram["telegramInfo"].get("dbm")
            if dbm is not None:
                self.dbm = dbm

    @classmethod
    def from_device_object(cls, device_data: dict[str, Any]) -> EnOceanDevice:
        """Create an EnOceanDevice from a device JSON object."""
        device = device_data.get("device", device_data)

        return cls(
            device_id=device.get("deviceId", ""),
            friendly_id=device.get("friendlyId", ""),
            eeps=device.get("eeps", []),
            manufacturer=device.get("manufacturer", ""),
            physical_device=device.get("physicalDevice", ""),
            first_seen=device.get("firstSeen", ""),
            last_seen=device.get("lastSeen", ""),
            dbm=device.get("dbm", 0),
        )

    def to_device_info(self, eag_id: str) -> dict[str, Any]:
        """Convert to Home Assistant device info dictionary."""
        return {
            "identifiers": {("opus_greennet", f"{eag_id}_{self.device_id}")},
            "name": self.friendly_id or self.device_id,
            "manufacturer": self.manufacturer or "EnOcean",
            "model": self.primary_eep or "Unknown",
            "via_device": ("opus_greennet", eag_id),
        }
