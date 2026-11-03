"""EnOcean device representation for Opus GreenNet Bridge."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import ClassVar, Final

from .const import (
    A5_04_TEMPERATURE_RANGES,
    BUTTON_KEYS,
    D2_01_CHANNEL_COUNTS,
    D2_01_DIMMER_EEPS,
    DEFAULT_CHANNEL,
    DEVICE_OPTIONS,
    DEVICE_SETTING_KEYS,
    DEVICE_SETTINGS,
    EEP_MAPPINGS,
    ERROR_CODE_EEPS,
    ERROR_CODE_NONE,
    GATEWAY_DIMMER_EEPS,
    ILLUMINATION_EEPS,
    ILLUMINATION_MAX_LUX,
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
    KEY_ERROR_CODE,
    KEY_FEED_TEMPERATURE,
    KEY_HEATER_MODE,
    KEY_HUMIDITY,
    KEY_ILLUMINATION,
    KEY_LIQUID_DETECTED,
    KEY_LOCAL_CONTROL,
    KEY_LOCKING_MODE,
    KEY_MAX_HOLDING_TIME,
    KEY_MISSING_TEMPERATURE,
    KEY_POSITION,
    KEY_POWER,
    KEY_POWER_STATE,
    KEY_REPOSITIONING,
    KEY_ROTATION_TIME,
    KEY_ROUTING_TIME,
    KEY_SUMMER_MODE,
    KEY_SWITCH,
    KEY_TEMPERATURE,
    KEY_TEMPERATURE_ORIGIN,
    KEY_TEMPERATURE_SETPOINT,
    KEY_THERMAL_MODE,
    KEY_UNLOCK,
    KEY_VERTICAL_MOVEMENT_TIME,
    KEY_WINDOW_OPEN,
    LOCAL_CONTROL_EEPS,
    LOCKING_MODE_EEPS,
    LOCKING_MODES,
    OPUS_MOTION_DETECTOR_EEPS,
    REPOSITIONING_EEPS,
    ROCKER_2_BUTTON_KEYS,
    ROCKER_EEP_PREFIXES,
    ROUTING_OFF,
    ROUTING_ON,
    SETTING_CONSTRAINTS,
    STATE_ON,
    UNLOCK_ALLOWED,
    UNLOCK_NOT_ALLOWED,
    VALUE_OVER_RANGE,
)
from .wire import (
    JsonObject,
    WireMapping,
    as_object,
    object_items,
    string_key,
)

# Configured blind times: stored settings, no state reports.
BLIND_TIME_KEYS: Final = frozenset(
    {KEY_VERTICAL_MOVEMENT_TIME, KEY_ROTATION_TIME, KEY_MAX_HOLDING_TIME}
)

# Handle positions as the profiles report them, normalised to the entity
# states. HOPPE D2-03-10 reports "tilt", F6-10-00 "up" (both "Window is tilt").
HANDLE_STATES: dict[str, str] = {
    "open": "open",
    "closed": "closed",
    "tilted": "tilted",
    "tilt": "tilted",
    "up": "tilted",
}

_EMPTY_RANGES: Mapping[str, float] = MappingProxyType({})


@dataclass(frozen=True)
class DeviceProfile:
    """What the gateway profile of a device offers (``/devices/<id>/profile``).

    ``to_keys`` are the function keys the device accepts, ``from_keys`` the
    keys it reports. ``channels`` is the number of output channels of the
    ``channel`` key in a ``to`` group (None without such a key).
    ``from_maxima`` holds the upper range limit of numeric ``from`` keys.
    """

    to_keys: frozenset[str] = frozenset()
    from_keys: frozenset[str] = frozenset()
    channels: int | None = None
    from_maxima: Mapping[str, float] = _EMPTY_RANGES

    @property
    def output(self) -> str | None:
        """Return "light" for dimValue, "switch" for switch outputs, else None."""
        if KEY_DIMMER in self.to_keys:
            return "light"
        if KEY_SWITCH in self.to_keys:
            return "switch"
        return None


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
    max_holding_time: float | None = None  # Seconds, D2-05-02 configuration
    # D2-01 configuration, filled from DEVICE_SETTINGS: dimmer ramping and
    # press times (seconds), switch timers and warning (seconds), illumination
    # threshold (lux). Device-wide, kept on the first channel.
    ramping_time: float | None = None
    short_press_time: float | None = None
    long_press_time: float | None = None
    timer: float | None = None
    timer_long: float | None = None
    switch_off_warning: float | None = None
    switch_off_warning_signal: float | None = None
    illumination_threshold: float | None = None
    # A5-07-03 presence detector settings (DEVICE_SETTINGS): alive cycle in
    # minutes, PIR off time and ReCom readiness cycle in seconds.
    alive_cycle: float | None = None
    pir_off_time: float | None = None
    recom_ready_time: float | None = None
    # D2-01 switch settings with fixed values, as reported (DEVICE_OPTIONS).
    operation_mode: str | None = None
    led_color: str | None = None
    led_channel: str | None = None
    function_external_input: str | None = None
    routing: str | None = None  # off or on (any routing time)
    repeater: str | None = None  # off, level_1 or level_2
    movement: str | None = None  # Estimated opening/closing, never physical feedback
    local_control: bool | None = None
    locking_mode: str | None = None  # block, unblock or alarm (D2-05)
    energy: float | None = None
    power: float | None = None
    liquid_detected: bool | None = None
    contact: bool | None = None
    smoke_alarm: bool | None = None
    battery_low: bool | None = None
    battery_level: float | None = None
    motion: bool | None = None
    illuminance: float | None = None
    # True while the sensor reports illumination above its range.
    illuminance_over_range: bool | None = None
    # "noError" or a reported error code (A5-07 supply voltage byte 251-255).
    error_code: str | None = None
    supply_voltage: float | None = None
    handle_state: str | None = None
    is_locked: bool | None = None
    unlock_request: bool | None = None
    # Answer the gateway gives the handle's next unlock request (its stored
    # target state): True = allowed (green), False = not allowed (red).
    unlock_allowed: bool | None = None
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


@dataclass(frozen=True, slots=True)
class LinkedDevice:
    """One entry of a link table: a device and how it is linked.

    ``parameters`` are the entry's settings as the gateway reports them
    (rocker, central on/off, channel), values as text.
    """

    device_id: str
    eep: str
    parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class LinkTables:
    """What a device listens to (inbound) and reports to (outbound).

    Read only: the integration never writes link tables.
    """

    inbound: tuple[LinkedDevice, ...] = ()
    outbound: tuple[LinkedDevice, ...] = ()


@dataclass
class EnOceanDevice:
    """Represents an EnOcean device from the gateway."""

    device_id: str
    friendly_id: str
    # EEP entries keep their wire representation; unknown fields are preserved.
    eeps: list[JsonObject] = field(default_factory=list)
    manufacturer: str = ""
    physical_device: str = ""
    # Gateway device list: product number and software version of the device.
    product_id: str = ""
    software_revision: str = ""
    hardware_revision: str = ""
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
    # Gateway profile read at setup; None means the EEP tables decide.
    profile: DeviceProfile | None = field(default=None, repr=False, compare=False)
    # Link tables of the gateway's device list; None until one was read.
    link_tables: LinkTables | None = None

    # Fields a gateway device list owns. ``apply_listed_metadata`` replaces
    # them in place; every other field is runtime state of this integration
    # (channels, diagnostics) and survives a list (REST phase 02, E6).
    LISTED_METADATA_FIELDS: ClassVar[tuple[str, ...]] = (
        "friendly_id",
        "eeps",
        "manufacturer",
        "physical_device",
        "product_id",
        "software_revision",
        "hardware_revision",
        "first_seen",
        "last_seen",
        "dbm",
        "link_tables",
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
        if listed.eep_names != self.eep_names:
            # A profile describes the announced EEPs; it is stale otherwise.
            self.profile = None
        self.eeps = listed.eeps
        self.manufacturer = listed.manufacturer
        self.physical_device = listed.physical_device
        # A list that omits them keeps the known values.
        self.product_id = listed.product_id or self.product_id
        self.software_revision = listed.software_revision or self.software_revision
        self.hardware_revision = listed.hardware_revision or self.hardware_revision
        self.first_seen = listed.first_seen
        if listed.last_seen and not keep_signal:
            self.last_seen = listed.last_seen
        if listed.dbm is not None and not keep_signal:
            self.dbm = listed.dbm
        # A list without a table says nothing; it never clears a known one.
        if listed.link_tables is not None:
            self.link_tables = listed.link_tables

    def setting_reading(self, key: str) -> float | str | None:
        """Return the stored value of a device setting, None while unknown.

        Settings belong to the device and live on the default channel, where the
        number and select entities read them.
        """
        channel = self.channels.get(DEFAULT_CHANNEL)
        if channel is None:
            return None
        if key == KEY_VERTICAL_MOVEMENT_TIME:
            attribute = "vertical_movement_time"
        elif key == KEY_MAX_HOLDING_TIME:
            attribute = "max_holding_time"
        elif key == KEY_ROTATION_TIME:
            attribute = "rotation_time"
        elif key in DEVICE_OPTIONS:
            attribute = DEVICE_OPTIONS[key]
        elif key in DEVICE_SETTINGS:
            attribute = DEVICE_SETTINGS[key][0]
        else:
            return None
        value = getattr(channel, attribute, None)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        return value

    def setting_conflicts(
        self, key: str | None = None, *, value: float | None = None
    ) -> list[str]:
        """Return the settings combinations the OPUS config tool warns about.

        Only numbers that are both known are compared; ``key`` limits the result
        to the combinations that setting belongs to. With ``value`` the result is
        what writing ``value`` to ``key`` would do: a combination that is wrong
        afterwards is only reported when the write creates it or makes it worse,
        so a write that keeps or reduces an existing conflict is never refused.
        """
        conflicts: list[str] = []
        for name, larger, smaller in SETTING_CONSTRAINTS:
            if key is not None and key not in (larger, smaller):
                continue
            current = self._setting_gap(larger, smaller)
            if value is None:
                if current is not None and current < 0:
                    conflicts.append(name)
                continue
            high = value if larger == key else self.setting_reading(larger)
            low = value if smaller == key else self.setting_reading(smaller)
            if not isinstance(high, (int, float)) or not isinstance(low, (int, float)):
                continue
            gap = high - low
            if gap < 0 and (current is None or gap < current):
                conflicts.append(name)
        return conflicts

    def _setting_gap(self, larger: str, smaller: str) -> float | None:
        """Return the stored ``larger`` minus ``smaller``, None while one is unknown."""
        high, low = self.setting_reading(larger), self.setting_reading(smaller)
        if isinstance(high, (int, float)) and isinstance(low, (int, float)):
            return high - low
        return None

    @property
    def primary_eep(self) -> str | None:
        """Get the primary EEP for this device."""
        if self.eeps:
            return string_key(self.eeps[0], "eep")
        return None

    @property
    def eep_names(self) -> tuple[str, ...]:
        """Return the announced EEP names in gateway order."""
        return tuple(
            name
            for eep in self.eeps
            if isinstance(eep, dict) and (name := string_key(eep, "eep")) is not None
        )

    @property
    def _d2_01_profile_output(self) -> str | None:
        """Return the output type a D2-01 gateway profile declares."""
        eep = self.primary_eep
        if self.profile is None or eep is None or not eep.startswith("D2-01-"):
            return None
        return self.profile.output

    @property
    def entity_type(self) -> str | None:
        """Determine the entity type from the gateway profile or the primary EEP."""
        if (output := self._d2_01_profile_output) is not None:
            return output
        eep = self.primary_eep
        if eep and eep in EEP_MAPPINGS:
            return EEP_MAPPINGS[eep][0]
        return None

    @property
    def is_dimmable(self) -> bool:
        """Check if this device supports dimming (dimValue commands)."""
        if (output := self._d2_01_profile_output) is not None:
            return output == "light"
        eep = self.primary_eep
        return eep in D2_01_DIMMER_EEPS or eep in GATEWAY_DIMMER_EEPS

    def _reports(self, key: str, fallback_eeps: frozenset[str]) -> bool:
        """Return whether the device reports ``key``: profile first, then EEP."""
        if self.profile is not None:
            return key in self.profile.from_keys
        return self.primary_eep in fallback_eeps

    def _accepts(self, key: str, fallback_eeps: frozenset[str]) -> bool:
        """Return whether the device accepts ``key``: profile first, then EEP."""
        if self.profile is not None:
            return key in self.profile.to_keys
        return self.primary_eep in fallback_eeps

    @property
    def has_local_control(self) -> bool:
        """Return whether the actuator reports its local control setting."""
        return self._reports(KEY_LOCAL_CONTROL, LOCAL_CONTROL_EEPS)

    @property
    def supports_locking_mode(self) -> bool:
        """Return whether the blind accepts and reports a locking mode."""
        return self.is_cover and self._accepts(KEY_LOCKING_MODE, LOCKING_MODE_EEPS)

    @property
    def supports_repositioning(self) -> bool:
        """Return whether the blind accepts a reference run before positioning."""
        return self.is_cover and self._accepts(KEY_REPOSITIONING, REPOSITIONING_EEPS)

    @property
    def reports_illuminance(self) -> bool:
        """Return whether the presence detector reports illumination."""
        return self.is_presence_detector and self._reports(
            KEY_ILLUMINATION, ILLUMINATION_EEPS
        )

    @property
    def reports_error_code(self) -> bool:
        """Return whether the presence detector reports a supply error code."""
        return self.is_presence_detector and self._reports(
            KEY_ERROR_CODE, ERROR_CODE_EEPS
        )

    @property
    def illuminance_maximum(self) -> float:
        """Return the upper end of the illumination range in lux."""
        if self.profile is not None:
            maximum = self.profile.from_maxima.get(KEY_ILLUMINATION)
            if maximum is not None:
                return maximum
        return ILLUMINATION_MAX_LUX

    @property
    def rocker_eep(self) -> str | None:
        """Return the announced rocker switch EEP (F6-02-xx/F6-03-xx), if any."""
        return next(
            (name for name in self.eep_names if name.startswith(ROCKER_EEP_PREFIXES)),
            None,
        )

    @property
    def has_secondary_rocker(self) -> bool:
        """Return whether an actuator also reports its built-in rocker buttons."""
        return (
            self.rocker_eep is not None
            and self.entity_type != "event"
            and not self.is_opus_motion_detector
        )

    @property
    def button_keys(self) -> tuple[str, ...]:
        """Return the rocker button keys this device can report."""
        if self.profile is not None:
            reported = tuple(
                key for key in BUTTON_KEYS if key in self.profile.from_keys
            )
            if reported:
                return reported
        if (eep := self.rocker_eep) is not None and eep.startswith("F6-02-"):
            return ROCKER_2_BUTTON_KEYS
        return BUTTON_KEYS

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
        return set(self.eep_names)

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
        """Return the number of output channels.

        A D2-01 gateway profile with a ``channel`` key decides; without the key
        the actuator has one channel. Otherwise the EEP specification table
        applies (only D2-01-10, -11 and -12 have two channels).
        """
        eep = self.primary_eep
        if not eep:
            return 1
        if self._d2_01_profile_output is not None and self.profile is not None:
            return self.profile.channels or 1
        return D2_01_CHANNEL_COUNTS.get(eep, 1)

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

    def apply_locking_mode_estimate(
        self, snapshot: ChannelStateSnapshot, locking_mode: str
    ) -> bool:
        """Assume an acknowledged blind locking mode; return whether it applied."""
        if (channel := self._estimate_channel(snapshot)) is None:
            return False
        channel.locking_mode = locking_mode
        return True

    def apply_unlock_permission_estimate(
        self, snapshot: ChannelStateSnapshot, allowed: bool
    ) -> bool:
        """Assume an accepted unlock answer; return whether it applied."""
        if (channel := self._estimate_channel(snapshot)) is None:
            return False
        channel.unlock_allowed = allowed
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
    def routing_reading(cls, value: object) -> str | None:
        """Return a reported routing time as ``off`` or ``on``, None if unusable."""
        if value in (ROUTING_OFF, ROUTING_ON):
            return str(value)
        number = cls._parse_number(value, minimum=0)
        if number is None or number <= 0:
            return None
        return ROUTING_ON

    @staticmethod
    def _option_reading(value: object) -> str | None:
        """Return a reported setting value as text, or None if there is none."""
        if isinstance(value, str):
            return value or None
        if isinstance(value, int | float) and not isinstance(value, bool):
            return str(value) if isfinite(value) else None
        return None

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
            # A stored setting is no feedback: reading it must not cancel the
            # estimate of a command that awaits its acknowledgement.
            if key not in DEVICE_SETTING_KEYS and key not in BLIND_TIME_KEYS:
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
                # D2-05 reports "unknown" while a blind moves; the last known
                # position stays until the end report.
                channel.position = self._integer_reading(
                    channel.position, value, 0, 100
                )

            elif key == KEY_ANGLE:
                channel.angle = self._integer_reading(channel.angle, value, 0, 100)

            elif key == KEY_LOCKING_MODE:
                # "noChange" in a command keeps the reported mode.
                if isinstance(value, str) and value in LOCKING_MODES:
                    channel.locking_mode = value

            elif key == KEY_VERTICAL_MOVEMENT_TIME:
                channel.vertical_movement_time = self._float_reading(
                    channel.vertical_movement_time, value, minimum=0
                )

            elif key == KEY_MAX_HOLDING_TIME:
                # Range of the gateway's parameter catalog; anything else keeps
                # the known value.
                channel.max_holding_time = self._float_reading(
                    channel.max_holding_time, value, minimum=0, maximum=65535
                )

            elif key in DEVICE_OPTIONS:
                # The reported value is kept as it is, also when the catalog
                # does not list it; a missing or malformed one keeps the known.
                option = (
                    self.routing_reading(value)
                    if key == KEY_ROUTING_TIME
                    else self._option_reading(value)
                )
                if option is not None:
                    setattr(channel, DEVICE_OPTIONS[key], option)

            elif key in DEVICE_SETTINGS:
                attribute, minimum, maximum = DEVICE_SETTINGS[key]
                setattr(
                    channel,
                    attribute,
                    self._float_reading(
                        getattr(channel, attribute), value, minimum, maximum
                    ),
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
                key == KEY_ILLUMINATION and self.is_presence_detector
            ):
                if key == "supplyVoltage":
                    channel.supply_voltage = self._float_reading(
                        channel.supply_voltage, value, minimum=0, allow_unavailable=True
                    )
                elif value == VALUE_OVER_RANGE:
                    # Brighter than the sensor can measure: show its maximum.
                    channel.illuminance = self.illuminance_maximum
                    channel.illuminance_over_range = True
                else:
                    if value == "notAvailable":
                        channel.illuminance_over_range = None
                    elif self._parse_float(value, minimum=0) is not None:
                        channel.illuminance_over_range = False
                    channel.illuminance = self._float_reading(
                        channel.illuminance, value, minimum=0, allow_unavailable=True
                    )

            elif key == KEY_ERROR_CODE and self.is_presence_detector:
                if value == ERROR_CODE_NONE:
                    channel.error_code = ERROR_CODE_NONE
                elif (
                    code := self._parse_integer(value, minimum=251, maximum=255)
                ) is not None:
                    channel.error_code = str(code)

            elif key == "handleState" or (key == "handle" and self.is_window_handle):
                # Unknown values, e.g. the gateway's "unkown", are unknown.
                channel.handle_state = (
                    HANDLE_STATES.get(value) if isinstance(value, str) else None
                )

            elif key == "lock":
                channel.is_locked = self._parse_enum_boolean(
                    value, "locked", "unlocked"
                )

            elif key == "unlockRequest" or (key == KEY_UNLOCK and self.has_autolock):
                # "allowed"/"notAllowed" is the answer to the handle (a "to"
                # telegram or the gateway's target state), not a request.
                if value in (UNLOCK_ALLOWED, UNLOCK_NOT_ALLOWED):
                    channel.unlock_allowed = value == UNLOCK_ALLOWED
                else:
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
            product_id=text("productId"),
            software_revision=text("softwareRevision"),
            hardware_revision=text("hardwareRevision"),
            first_seen=text("firstSeen"),
            last_seen=text("lastSeen"),
            dbm=dbm,
        )
