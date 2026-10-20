"""EnOcean device representation for Opus GreenNet Bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import ClassVar

from .const import (
    A5_04_TEMPERATURE_RANGES,
    BUTTON_KEYS,
    DEFAULT_CHANNEL,
    EEP_MAPPINGS,
    KEY_ACTUATOR_DEACTIVATED,
    KEY_ACTUATOR_LOW_BATTERY,
    KEY_ACTUATOR_NOT_RESPONDING,
    KEY_ANGLE,
    KEY_BUTTON_MOTION,
    KEY_BUTTON_NO_MOTION,
    KEY_CHANNEL,
    KEY_CIRCUIT_IN_USE,
    KEY_CONTACT,
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
    KEY_VERTICAL_MOVEMENT_TIME,
    KEY_WINDOW_OPEN,
    OPUS_MOTION_DETECTOR_EEPS,
    STATE_ON,
)
from .wire import (
    JsonObject,
    WireMapping,
    as_object,
    object_items,
    string_key,
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
    vertical_movement_time: float | None = None
    movement: str | None = None  # Estimated opening/closing, never physical feedback
    local_control: bool | None = None
    energy: float | None = None
    power: float | None = None
    liquid_detected: bool | None = None
    contact: bool | None = None
    smoke_alarm: bool | None = None
    battery_low: bool | None = None
    battery_level: float | None = None
    motion: bool | None = None
    illuminance: float | None = None
    supply_voltage: float | None = None
    handle_state: str | None = None
    is_locked: bool | None = None
    unlock_request: bool | None = None
    mechanics_fault: bool | None = None
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


@dataclass(frozen=True, eq=False)
class ChannelStateSnapshot:
    """Channel identity and revision captured before a command is awaited."""

    channel_id: int
    channel: EnOceanChannel | None
    revision: int


@dataclass
class EnOceanDevice:
    """Represents an EnOcean device from the gateway."""

    device_id: str
    friendly_id: str
    # EEP entries keep their wire representation; unknown fields are preserved.
    eeps: list[JsonObject] = field(default_factory=list)
    manufacturer: str = ""
    physical_device: str = ""
    first_seen: str = ""
    last_seen: str = ""
    last_update_source: str = ""
    last_update_received_monotonic: float | None = field(
        default=None, repr=False, compare=False
    )
    last_update_dispatched_monotonic: float | None = field(
        default=None, repr=False, compare=False
    )
    dbm: int | None = None
    last_command_error: str | None = None
    channels: dict[int, EnOceanChannel] = field(default_factory=dict)

    # Fields a gateway device list owns. ``apply_listed_metadata`` replaces
    # them in place; every other field is runtime state of this integration
    # (channels, diagnostics) and survives a list (REST phase 02, E6).
    LISTED_METADATA_FIELDS: ClassVar[tuple[str, ...]] = (
        "friendly_id",
        "eeps",
        "manufacturer",
        "physical_device",
        "first_seen",
        "last_seen",
        "dbm",
    )

    def apply_listed_metadata(
        self, listed: EnOceanDevice, *, keep_signal: bool = False
    ) -> None:
        """Take the gateway metadata of a newer list entry in place.

        The device object stays the same (REST phase 02, finding B-4), so
        entities and running commands keep a valid reference, and no runtime
        field can be forgotten in a copy. A missing signal strength or
        last-seen time keeps the stored value; ``keep_signal`` keeps both when
        a newer telegram reported them (finding B-5).
        """
        self.friendly_id = listed.friendly_id
        self.eeps = listed.eeps
        self.manufacturer = listed.manufacturer
        self.physical_device = listed.physical_device
        self.first_seen = listed.first_seen
        if listed.last_seen and not keep_signal:
            self.last_seen = listed.last_seen
        if listed.dbm is not None and not keep_signal:
            self.dbm = listed.dbm

    @property
    def primary_eep(self) -> str | None:
        """Get the primary EEP for this device."""
        if self.eeps:
            return string_key(self.eeps[0], "eep")
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
    def supports_ramping_mode(self) -> bool:
        """Return whether dim commands accept a ramping mode (D2-01 dimmers)."""
        eep = self.primary_eep
        return self.is_dimmable and eep is not None and eep.startswith("D2-01-")

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
    def is_smoke_detector(self) -> bool:
        """Return whether this is an OPUS RWM smoke detector."""
        return self.primary_eep == "F6-05-02"

    @property
    def is_presence_detector(self) -> bool:
        """Return whether this is a supported SMS presence detector."""
        return self.primary_eep in ("A5-07-01", "A5-07-03")

    @property
    def _eep_names(self) -> set[str]:
        """Return all announced EEPs, not only the primary one."""
        return {
            name
            for eep in self.eeps
            if isinstance(eep, dict) and (name := string_key(eep, "eep")) is not None
        }

    @property
    def is_opus_motion_detector(self) -> bool:
        """Return whether this is an OPUS motion detector (F6-02-01 + A5-04-01)."""
        return OPUS_MOTION_DETECTOR_EEPS <= self._eep_names

    @property
    def _a5_04_eep(self) -> str | None:
        """Return the announced A5-04 temperature/humidity profile, if any."""
        return next(
            (eep for eep in A5_04_TEMPERATURE_RANGES if eep in self._eep_names), None
        )

    @property
    def is_temperature_humidity_sensor(self) -> bool:
        """Return whether the device announces an A5-04 temperature/humidity profile."""
        return self._a5_04_eep is not None

    @property
    def is_contact_sensor(self) -> bool:
        """Return whether this is a D5-00-01 single input contact."""
        return self.primary_eep == "D5-00-01"

    @property
    def is_window_handle(self) -> bool:
        """Return whether this profile reports HOPPE handle positions."""
        return self.primary_eep in ("D2-06-40", "F6-10-00", "D2-03-10")

    @property
    def has_autolock(self) -> bool:
        """Return whether this handle reports SecureConnect lock status."""
        return self.primary_eep == "D2-06-40"

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
        return eep_type_map.get(eep) if eep is not None else None

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

    # Command estimates ------------------------------------------------------
    #
    # An accepted command acknowledgement is not device feedback. After the
    # acknowledgement the commanded value is assumed, but only if the channel
    # received no newer update while the command was awaited: the same channel
    # object must still be in place and its ``state_revision`` must be
    # unchanged. Feedback, discovery state and the coordinator's cover travel
    # estimate all advance the revision, so they always win over an estimate.
    # Estimates deliberately do not advance the revision themselves.

    def snapshot_channel_state(self, channel_id: int) -> ChannelStateSnapshot:
        """Capture a channel before awaiting a command acknowledgement."""
        channel = self.channels.get(channel_id)
        return ChannelStateSnapshot(
            channel_id=channel_id,
            channel=channel,
            revision=channel.state_revision if channel is not None else 0,
        )

    def channel_state_unchanged(self, snapshot: ChannelStateSnapshot) -> bool:
        """Return whether no newer channel update arrived since the snapshot."""
        channel = self.channels.get(snapshot.channel_id)
        return channel is snapshot.channel and (
            channel is None or channel.state_revision == snapshot.revision
        )

    def _estimate_channel(
        self, snapshot: ChannelStateSnapshot
    ) -> EnOceanChannel | None:
        if not self.channel_state_unchanged(snapshot):
            return None
        return self.get_or_create_channel(snapshot.channel_id)

    def apply_switch_estimate(
        self, snapshot: ChannelStateSnapshot, is_on: bool
    ) -> bool:
        """Assume an acknowledged switch command; return whether it applied."""
        if (channel := self._estimate_channel(snapshot)) is None:
            return False
        channel.is_on = is_on
        return True

    def apply_dim_estimate(
        self, snapshot: ChannelStateSnapshot, dim_value: int
    ) -> bool:
        """Assume an acknowledged ``dimValue`` (0-100); return whether it applied."""
        if (channel := self._estimate_channel(snapshot)) is None:
            return False
        channel.brightness = dim_value
        channel.is_on = dim_value > 0
        return True

    def apply_cover_position_estimate(
        self, snapshot: ChannelStateSnapshot, position: int
    ) -> bool:
        """Assume an acknowledged OPUS position (0 = open); return whether applied."""
        if (channel := self._estimate_channel(snapshot)) is None:
            return False
        channel.position = position
        return True

    def apply_cover_tilt_estimate(
        self, snapshot: ChannelStateSnapshot, angle: int
    ) -> bool:
        """Assume an acknowledged tilt angle; return whether it applied."""
        if (channel := self._estimate_channel(snapshot)) is None:
            return False
        channel.angle = angle
        return True

    @staticmethod
    def _parse_number(
        value: object,
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

    @classmethod
    def _parse_integer(
        cls,
        value: object,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> int | None:
        """Parse a finite integral protocol number (e.g. "50", 50, 50.0)."""
        number = cls._parse_number(
            value, minimum=minimum, maximum=maximum, integer=True
        )
        return int(number) if number is not None else None

    @classmethod
    def _parse_float(
        cls,
        value: object,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float | None:
        """Parse a finite protocol number as float."""
        number = cls._parse_number(value, minimum=minimum, maximum=maximum)
        return float(number) if number is not None else None

    def _parse_channel_id(self, value: object) -> int | None:
        """Reject malformed channel selectors instead of updating channel zero."""
        return self._parse_integer(value, minimum=0)

    @classmethod
    def _float_reading(
        cls,
        current: float | None,
        value: object,
        minimum: float | None = None,
        maximum: float | None = None,
        *,
        allow_unavailable: bool = False,
    ) -> float | None:
        """Return the new reading: None for notAvailable, current if malformed."""
        if allow_unavailable and value == "notAvailable":
            return None
        number = cls._parse_float(value, minimum=minimum, maximum=maximum)
        return current if number is None else number

    @classmethod
    def _integer_reading(
        cls,
        current: int | None,
        value: object,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> int | None:
        """Return the new integral reading, or current if malformed."""
        number = cls._parse_integer(value, minimum=minimum, maximum=maximum)
        return current if number is None else number

    @staticmethod
    def _parse_boolean(value: object) -> bool | None:
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
    def _parse_enum_boolean(value: object, on: str, off: str) -> bool | None:
        """Keep missing or unrecognized detector states unknown."""
        if value == on:
            return True
        if value == off:
            return False
        return None

    @staticmethod
    def _parse_detector_boolean(
        value: object, active: str, inactive: str
    ) -> bool | None:
        """Accept explicit detector enums or booleans, never arbitrary truthiness."""
        if isinstance(value, str):
            value = value.strip()
        parsed = EnOceanDevice._parse_enum_boolean(value, active, inactive)
        return parsed if parsed is not None else EnOceanDevice._parse_boolean(value)

    @staticmethod
    def _normalize_battery_level(value: object) -> object:
        """Accept an optional percent suffix before the shared numeric validation."""
        if isinstance(value, str):
            return value.strip().removesuffix("%").strip()
        return value

    def clear_button_occurrence(self) -> None:
        """Forget the transient button press carried by the last telegram.

        Button fields describe a single telegram occurrence. They must not be
        observed again by later dispatches (RSSI, metadata, PUT answers, ...).
        """
        for channel in self.channels.values():
            channel.last_button = None
            channel.last_button_action = None

    def update_from_telegram(self, telegram: WireMapping) -> None:
        """Update device state from a telegram message.

        Only ``functions`` entries that are JSON objects are applied; entries
        of other types and unknown keys are ignored.
        """
        raw_functions = telegram.get("functions", [])
        single = as_object(raw_functions)
        functions = [single] if single is not None else object_items(raw_functions)

        # Button fields describe only this telegram, including metadata-only updates.
        self.clear_button_occurrence()

        # Determine default channel from telegram-level channel function.
        default_channel_id: int | None = DEFAULT_CHANNEL
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
                # Release telegrams carry no button, so only presses set motion.
                if value == "pressed" and self.is_opus_motion_detector:
                    if key == KEY_BUTTON_MOTION:
                        channel.motion = True
                    elif key == KEY_BUTTON_NO_MOTION:
                        channel.motion = False

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
                channel.position = self._integer_reading(
                    channel.position, value, 0, 100
                )

            elif key == KEY_ANGLE:
                channel.angle = self._integer_reading(channel.angle, value, 0, 100)

            elif key == KEY_VERTICAL_MOVEMENT_TIME:
                channel.vertical_movement_time = self._float_reading(
                    channel.vertical_movement_time, value, minimum=0
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
                channel.energy = self._float_reading(channel.energy, value)

            elif key == KEY_POWER:
                channel.power = self._float_reading(channel.power, value)

            elif key == KEY_LIQUID_DETECTED:
                liquid_detected = self._parse_boolean(value)
                if liquid_detected is not None:
                    channel.liquid_detected = liquid_detected

            elif key == KEY_CONTACT and self.is_contact_sensor:
                channel.contact = self._parse_enum_boolean(value, "open", "closed")

            elif key == "smokeAlarm" or (key == "alarm" and self.is_smoke_detector):
                channel.smoke_alarm = (
                    value == "on"
                    if value in ("on", "off")
                    else self._parse_detector_boolean(value, "alarm", "idle")
                )

            elif key == "batteryLow":
                channel.battery_low = self._parse_detector_boolean(value, "low", "ok")
            elif key == "batteryLevel":
                channel.battery_level = self._float_reading(
                    channel.battery_level,
                    self._normalize_battery_level(value),
                    0,
                    100,
                    allow_unavailable=True,
                )

            elif key == "motionDetector" or (
                key == "motionDetected" and self.is_presence_detector
            ):
                channel.motion = self._parse_detector_boolean(
                    value, "detected", "noMotion"
                )

            elif key in ("illuminance", "supplyVoltage") or (
                key == "illumination" and self.is_presence_detector
            ):
                if key == "supplyVoltage":
                    channel.supply_voltage = self._float_reading(
                        channel.supply_voltage, value, minimum=0, allow_unavailable=True
                    )
                else:
                    channel.illuminance = self._float_reading(
                        channel.illuminance, value, minimum=0, allow_unavailable=True
                    )

            elif key == "handleState" or (key == "handle" and self.is_window_handle):
                if value == "tilt":
                    value = "tilted"
                channel.handle_state = (
                    value if value in ("open", "closed", "tilted") else None
                )

            elif key == "lock":
                channel.is_locked = self._parse_enum_boolean(
                    value, "locked", "unlocked"
                )

            elif key == "unlockRequest" or (key == "unlock" and self.has_autolock):
                channel.unlock_request = self._parse_detector_boolean(
                    value, "requested", "notRequested"
                )

            elif key == "mechanicsFault" or (key == "mechanics" and self.has_autolock):
                channel.mechanics_fault = self._parse_detector_boolean(
                    value, "error", "ok"
                )

            # Climate keys
            elif key == KEY_TEMPERATURE:
                # Preserve the profile-specific ranges from the sensor beta.
                minimum, maximum = A5_04_TEMPERATURE_RANGES.get(
                    self._a5_04_eep or "", (0, 40)
                )
                channel.temperature = self._float_reading(
                    channel.temperature, value, minimum, maximum, allow_unavailable=True
                )

            elif key == KEY_TEMPERATURE_SETPOINT:
                channel.temperature_setpoint = self._float_reading(
                    channel.temperature_setpoint, value, 0, 40, allow_unavailable=True
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
                channel.humidity = self._float_reading(
                    channel.humidity, value, 0, 100, allow_unavailable=True
                )

            elif key == KEY_WINDOW_OPEN:
                if (parsed := self._parse_boolean(value)) is not None:
                    channel.window_open = parsed

            elif key == KEY_SUMMER_MODE:
                if (parsed := self._parse_boolean(value)) is not None:
                    channel.summer_mode = parsed

            elif key == KEY_FEED_TEMPERATURE:
                channel.feed_temperature = self._float_reading(
                    channel.feed_temperature, value, 0, 80, allow_unavailable=True
                )

            elif key == KEY_THERMAL_MODE:
                if value in ("heating", "cooling"):
                    channel.thermal_mode = value

            elif key == KEY_ENERGY_CONSUMPTION:
                channel.energy_consumption = self._float_reading(
                    channel.energy_consumption, value, 0, 10, allow_unavailable=True
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
        if (timestamp := string_key(telegram, "timestamp")) is not None:
            self.last_seen = timestamp
        if (info := as_object(telegram.get("telegramInfo"))) is not None:
            dbm = self._parse_integer(info.get("dbm"))
            if dbm is not None:
                self.dbm = dbm

    @classmethod
    def from_device_object(cls, device_data: JsonObject) -> EnOceanDevice:
        """Create an EnOceanDevice from a device JSON object.

        Accepts the bare device object or a ``{"device": {...}}`` wrapper.
        Fields of an unexpected type fall back to their empty default.
        """
        device = as_object(device_data.get("device", device_data)) or {}
        dbm = cls._parse_integer(device.get("dbm"))

        def text(key: str) -> str:
            return string_key(device, key) or ""

        return cls(
            device_id=text("deviceId"),
            friendly_id=text("friendlyId"),
            eeps=object_items(device.get("eeps", [])),
            manufacturer=text("manufacturer"),
            physical_device=text("physicalDevice"),
            first_seen=text("firstSeen"),
            last_seen=text("lastSeen"),
            dbm=dbm,
        )
