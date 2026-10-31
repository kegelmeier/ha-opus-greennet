"""Constants for the Opus GreenNet Bridge integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "opus_greennet"

# Configuration keys (host, port, username and password use Home Assistant's
# shared CONF_* names). The EAG identifier is the gateway's EURID; it anchors
# every device and entity unique ID.
CONF_EAG_ID: Final = "eag_id"

DEFAULT_PORT: Final = 8080
DEFAULT_USERNAME: Final = "admin"

# Gateway generations. Gateways with firmware before WebUI 2026 serve the REST
# API over plain HTTP (port 8080) with basic authentication; the entry data
# carries no generation for them, and a missing key always means this one.
# Gateways with WebUI 2026 serve the same paths only over HTTPS (port 443) with
# a user login and a token, and a self-signed certificate that is remembered.
CONF_GENERATION: Final = "generation"
CONF_CERTIFICATE_FINGERPRINT: Final = "certificate_fingerprint"
WEBUI_2026_PORT: Final = 443

# EEP (EnOcean Equipment Profile) to entity type mappings
# Format: EEP -> (entity_type, description)
#
# D2-01 types follow the EEP specification (EnOcean Alliance EEP viewer, the
# same profile format the gateway serves under /devices/<id>/profile). Their
# number is not a channel count: most types are single-channel actuators that
# differ in measurement and local-control features. When the gateway profile
# of a device can be read, it decides switch vs. dimmer and the channel count
# (``EnOceanDevice.profile``); this table is the fallback.
EEP_MAPPINGS: Final = {
    # Electronic switches and dimmers (D2-01-xx)
    "D2-01-00": ("switch", "Switch, 1 channel, energy measurement, local control"),
    "D2-01-01": ("switch", "Switch, 1 channel, local control"),
    "D2-01-02": ("light", "Dimmer, 1 channel, energy measurement, local control"),
    "D2-01-03": ("light", "Dimmer, 1 channel, local control"),
    "D2-01-04": (
        "light",
        "Dimmer, 1 channel, energy and power measurement, local control",
    ),
    "D2-01-05": (
        "light",
        "Dimmer, 1 channel, energy and power measurement, local control",
    ),
    "D2-01-06": ("switch", "Switch, 1 channel, energy measurement"),
    "D2-01-07": ("switch", "Switch, 1 channel"),
    "D2-01-08": (
        "switch",
        "Switch, 1 channel, energy and power measurement, local control",
    ),
    "D2-01-09": (
        "light",
        "Dimmer, 1 channel, energy and power measurement, local control",
    ),
    "D2-01-0A": ("switch", "Switch, 1 channel, power failure detection, local control"),
    "D2-01-0B": (
        "switch",
        "Switch, 1 channel, energy and power measurement, power failure detection, "
        "local control",
    ),
    "D2-01-0C": (
        "switch",
        "Pilot wire controller, energy and power measurement, local control",
    ),
    "D2-01-0D": ("switch", "Switch, 1 channel, local control"),
    # D2-01-0E could not be checked against the EEP viewer; single channel as
    # in the EEP specification, without assumed local control.
    "D2-01-0E": ("switch", "Switch, 1 channel"),
    "D2-01-0F": ("switch", "Switch, 1 channel, external button, local control"),
    "D2-01-10": ("switch", "Switch, 2 channels, energy measurement"),
    "D2-01-11": ("switch", "Switch, 2 channels"),
    "D2-01-12": ("switch", "Switch, 2 channels, external button"),
    # Blinds Control (D2-05-xx)
    "D2-05-00": ("cover", "Blinds Control for Position and Angle"),
    "D2-05-01": ("cover", "Blinds Control for Position"),
    "D2-05-02": ("cover", "Blinds Control for Position and Angle, Lock"),
    # HeatArea (D1-4B-xx) - Proprietary OPUS profiles
    "D1-4B-05": ("climate", "OPUS Valve Area"),
    "D1-4B-06": ("climate", "OPUS CosiTherm Area"),
    "D1-4B-07": ("climate", "OPUS Electro Heating Area"),
    # Lighting Control (A5-38-xx)
    "A5-38-08": ("light", "Gateway Dimming"),
    "A5-38-09": ("light", "Gateway Switching"),
    # Rocker Switch (F6-02-xx) - typically used as triggers
    "F6-02-01": ("event", "Rocker Switch, 2 Rocker"),
    "F6-02-02": ("event", "Rocker Switch, 2 Rocker"),
    "F6-02-03": ("event", "Rocker Switch, 2 Rocker"),
    # 4-Button Switch (F6-03-xx)
    "F6-03-01": ("event", "Rocker Switch, 4 Rocker"),
    "F6-03-02": ("event", "Rocker Switch, 4 Rocker"),
    "F6-05-02": ("binary_sensor", "OPUS RWM Smoke Detector"),
    "A5-07-01": ("binary_sensor", "OPUS SMS Presence Detector"),
    "A5-07-03": ("binary_sensor", "OPUS SMS Presence Detector"),
    "A5-04-01": ("sensor", "Temperature and Humidity Sensor"),
    "A5-04-02": ("sensor", "Temperature and Humidity Sensor"),
    "A5-04-03": ("sensor", "Temperature and Humidity Sensor"),
    "D5-00-01": ("binary_sensor", "Single Input Contact"),
    "D2-06-40": ("lock", "HOPPE SecureConnect Window Handle"),
    "F6-10-00": ("sensor", "HOPPE Window Handle"),
    "D2-03-10": ("sensor", "HOPPE Window Handle"),
    # Liquid Leakage Sensor (F6-05-01)
    "F6-05-01": ("binary_sensor", "Liquid Leakage Sensor"),
}

# Fallback capabilities of the D2-01 types (EEP specification), used only
# when the gateway profile of a device could not be read.
D2_01_DIMMER_EEPS: Final = frozenset(
    {"D2-01-02", "D2-01-03", "D2-01-04", "D2-01-05", "D2-01-09"}
)
D2_01_CHANNEL_COUNTS: Final = {"D2-01-10": 2, "D2-01-11": 2, "D2-01-12": 2}
LOCAL_CONTROL_EEPS: Final = frozenset(
    {
        "D2-01-00",
        "D2-01-01",
        "D2-01-02",
        "D2-01-03",
        "D2-01-04",
        "D2-01-05",
        "D2-01-08",
        "D2-01-09",
        "D2-01-0A",
        "D2-01-0B",
        "D2-01-0C",
        "D2-01-0D",
        "D2-01-0F",
    }
)
# Gateway dimming (A5-38-08) has no D2-01 profile; it stays a dimmer.
GATEWAY_DIMMER_EEPS: Final = frozenset({"A5-38-08"})

# Blinds with the "Go to Position and Angle" locking mode and repositioning.
LOCKING_MODE_EEPS: Final = frozenset({"D2-05-00", "D2-05-02"})
REPOSITIONING_EEPS: Final = frozenset({"D2-05-00", "D2-05-02"})

# Presence detectors: A5-07-03 reports illumination, A5-07-01 does not.
ILLUMINATION_EEPS: Final = frozenset({"A5-07-03"})
ERROR_CODE_EEPS: Final = frozenset({"A5-07-01", "A5-07-03"})
# Upper end of the A5-07-03 illumination range; "overRange" lies above it.
ILLUMINATION_MAX_LUX: Final = 1000.0

# Rocker switch profiles that may also be announced by actuators.
ROCKER_EEP_PREFIXES: Final = ("F6-02-", "F6-03-")

# Temperature range (degC) per A5-04 profile; heating profiles use 0..40.
A5_04_TEMPERATURE_RANGES: Final = {
    "A5-04-01": (0, 40),
    "A5-04-02": (-20, 60),
    "A5-04-03": (-20, 60),
}

# The OPUS motion detector announces a rocker profile for motion
# (buttonAI = motion, buttonA0 = no motion) plus A5-04-01 for its climate data.
OPUS_MOTION_DETECTOR_EEPS: Final = frozenset({"F6-02-01", "A5-04-01"})
KEY_BUTTON_MOTION: Final = "buttonAI"
KEY_BUTTON_NO_MOTION: Final = "buttonA0"

RWM_EEP: Final = "F6-05-02"
KEY_SMOKE_ALARM: Final = "smokeAlarm"
KEY_BATTERY_LOW_RWM: Final = "batteryLow"

# Function keys used in EnOcean telegrams
KEY_SWITCH: Final = "switch"
KEY_DIMMER: Final = "dimValue"
# D2-01 dimmers: without rampingMode the gateway uses the device's first dim
# timer, a slow fade. "switch" sets the new value at once (like HomeKit);
# rampingTime1 keeps the device's configured fade for HA transitions.
KEY_RAMPING_MODE: Final = "rampingMode"
RAMPING_MODE_SWITCH: Final = "switch"
RAMPING_MODE_FADE: Final = "rampingTime1"
KEY_POSITION: Final = "position"
KEY_STOP: Final = "stop"
# D2-05 "Go to Position and Angle": blockage and reference run.
KEY_LOCKING_MODE: Final = "lockingMode"
LOCKING_MODE_BLOCK: Final = "block"
LOCKING_MODE_UNBLOCK: Final = "unblock"
LOCKING_MODE_ALARM: Final = "alarm"  # D2-05-00 only
LOCKING_MODES: Final = (LOCKING_MODE_BLOCK, LOCKING_MODE_UNBLOCK, LOCKING_MODE_ALARM)
KEY_REPOSITIONING: Final = "repositioning"
REPOSITIONING_DIRECT: Final = "direct"
REPOSITIONING_FIRST_UP: Final = "firstUp"
REPOSITIONING_FIRST_DOWN: Final = "firstDown"
REPOSITIONING_MODES: Final = (
    REPOSITIONING_DIRECT,
    REPOSITIONING_FIRST_UP,
    REPOSITIONING_FIRST_DOWN,
)
# Value of a cover position or angle the device cannot report right now.
VALUE_UNKNOWN: Final = "unknown"
KEY_ANGLE: Final = "angle"
KEY_ROTATION_TIME: Final = "rotationTime"
KEY_VERTICAL_MOVEMENT_TIME: Final = "verticalMovementTime"
# Longest run of a D2-05-02 blind in seconds, a configuration parameter. Not a
# state key: a command of another controller carrying it is not applied.
KEY_MAX_HOLDING_TIME: Final = "maxHoldingTime"
# Configuration parameters of D2-01 dimmers (seconds) and switches (seconds, lux).
KEY_RAMPING_TIME: Final = "rampingTime"
KEY_SHORT_PRESS: Final = "shortPress"
KEY_LONG_PRESS: Final = "longPress"
KEY_TIMER: Final = "timer"
KEY_TIMER_LONG: Final = "timerLong"
KEY_SWITCH_OFF_WARNING: Final = "switchOffWarning"
KEY_SWITCH_OFF_WARNING_SIGNAL: Final = "switchOffWarningSignal"
KEY_ILLUMINATION_THRESHOLD: Final = "illuminationThreshold"
# A5-07-03 presence detector settings: alive telegram cycle (minutes), time
# until the PIR reports "off" and cycle of the ReCom readiness (seconds).
KEY_ALIVE_CYCLE: Final = "aliveCycle"
KEY_PIR_OFF_TIME: Final = "pirOffTime"
KEY_RECOM_READY_TIME: Final = "recomReadyTime"
# Device settings and the channel field they fill, with the range of the
# gateway's parameter catalog (a reported value outside it keeps the known one).
# Like the holding time they are no state keys: a command of another controller
# carrying them is not applied, and reading them is no state update.
DEVICE_SETTINGS: Final = {
    KEY_RAMPING_TIME: ("ramping_time", 0, 25.5),
    KEY_SHORT_PRESS: ("short_press_time", 0, 2.55),
    KEY_LONG_PRESS: ("long_press_time", 0, 2.55),
    KEY_TIMER: ("timer", 60, 7200),
    KEY_TIMER_LONG: ("timer_long", 60, 18000),
    KEY_SWITCH_OFF_WARNING: ("switch_off_warning", 0, 45),
    KEY_SWITCH_OFF_WARNING_SIGNAL: ("switch_off_warning_signal", 0.2, 2),
    KEY_ILLUMINATION_THRESHOLD: ("illumination_threshold", 0, 1500),
    KEY_ALIVE_CYCLE: ("alive_cycle", 0, 1440),
    KEY_PIR_OFF_TIME: ("pir_off_time", 0, 3600),
    KEY_RECOM_READY_TIME: ("recom_ready_time", 0, 655350),
}
# Device settings with a fixed set of values (select entities): key -> channel
# field. The field holds the value the gateway reported, listed or not; the
# select shows an unlisted value as unknown and never writes it back.
KEY_OPERATION_MODE: Final = "operationMode"
KEY_LED_COLOR: Final = "ledColor"
KEY_LED_CHANNEL: Final = "ledChannel"
KEY_FUNCTION_EXTERNAL_INPUT: Final = "functionExternalInput"
# Routing (configuration parameter) is "off" or a time in seconds; only "off"
# was seen on devices. It is shown as off/on: any time is "on". "on" is written
# as the smallest time of the catalog (0.3 s), an assumption until a device
# that routes was read. The repeater mode is no ReCom parameter but the device
# resource /repeaterState (off, level_1, level_2).
KEY_ROUTING_TIME: Final = "routingTime"
KEY_REPEATER_STATE: Final = "repeaterState"
ROUTING_ON_SECONDS: Final = 0.3
# D2-01 switches (catalog read from a D2-01-01 and a D2-01-11).
# Radio link quality by signal strength (dBm): above GOOD is good, down to
# POOR (inclusive) sufficient, below it poor (a repeater is recommended).
SIGNAL_GOOD_DBM: Final = -75
SIGNAL_POOR_DBM: Final = -85
SWITCH_EEPS: Final = ("D2-01-01", "D2-01-11")
# Devices that report the repeater mode (D2-01-03 read from a dimmer, not written).
REPEATER_EEPS: Final = (*SWITCH_EEPS, "D2-01-03")
ROUTING_OFF: Final = "off"
ROUTING_ON: Final = "on"
DEVICE_OPTIONS: Final = {
    KEY_OPERATION_MODE: "operation_mode",
    KEY_LED_COLOR: "led_color",
    KEY_LED_CHANNEL: "led_channel",
    KEY_FUNCTION_EXTERNAL_INPUT: "function_external_input",
    KEY_ROUTING_TIME: "routing",
    KEY_REPEATER_STATE: "repeater",
}
DEVICE_SETTING_KEYS: Final = frozenset(DEVICE_SETTINGS) | frozenset(DEVICE_OPTIONS)
# How long a confirmed setting is protected against the gateway's cached copy of
# the configuration. The device list carries that cache; the gateway reads the
# configuration itself only now and then (nightly in the E0 catalog), so a list
# can repeat the value from before a confirmed write. A list that shows the
# written value or a third value ends the protection earlier.
SETTING_CACHE_GUARD_SECONDS: Final = 26 * 3600.0
# Combinations of settings the OPUS config tool warns about: (name, key that
# should be at least as large, key that should not be larger). A write that
# would create or worsen such a combination is refused before anything is sent.
SETTING_CONSTRAINTS: Final = (
    (
        "holding_time_shorter_than_travel_time",
        KEY_MAX_HOLDING_TIME,
        KEY_VERTICAL_MOVEMENT_TIME,
    ),
    (
        "travel_time_shorter_than_rotation_time",
        KEY_VERTICAL_MOVEMENT_TIME,
        KEY_ROTATION_TIME,
    ),
    ("cleaning_time_shorter_than_run_on_time", KEY_TIMER_LONG, KEY_TIMER),
    ("switch_off_warning_longer_than_run_on_time", KEY_TIMER, KEY_SWITCH_OFF_WARNING),
)
COVER_MOVEMENT_GRACE_SECONDS: Final = 5.0
# A run into an end stop is reported about 1.4 s later than a partial run
# (measured on one installation of D2-05-02 blinds), so its estimate waits longer.
COVER_END_STOP_GRACE_SECONDS: Final = 7.0
COVER_RECONCILIATION_DELAYS: Final = (10.0, 20.0)
KEY_CHANNEL: Final = "channel"
KEY_LOCAL_CONTROL: Final = "localControl"
KEY_ENERGY: Final = "energy"
KEY_POWER: Final = "power"
KEY_LIQUID_DETECTED: Final = "liquidDetected"
KEY_CONTACT: Final = "contact"
KEY_ILLUMINATION: Final = "illumination"
VALUE_OVER_RANGE: Final = "overRange"
KEY_ERROR_CODE: Final = "errorCode"
# HOPPE SecureConnect (D2-06-40): the handle reports an unlock request
# ("from": requested/notRequested); the controller answers ("to": allowed or
# notAllowed), which unlocks or locks the handle.
KEY_UNLOCK: Final = "unlock"
UNLOCK_ALLOWED: Final = "allowed"
UNLOCK_NOT_ALLOWED: Final = "notAllowed"
ERROR_CODE_NONE: Final = "noError"

# Climate function keys
KEY_TEMPERATURE: Final = "temperature"
KEY_TEMPERATURE_SETPOINT: Final = "temperatureSetpoint"
KEY_HEATER_MODE: Final = "heaterMode"
KEY_HUMIDITY: Final = "humidity"
KEY_WINDOW_OPEN: Final = "windowOpen"
KEY_SUMMER_MODE: Final = "summerMode"
KEY_FEED_TEMPERATURE: Final = "feedTemperature"
KEY_THERMAL_MODE: Final = "thermalMode"
KEY_ENERGY_CONSUMPTION: Final = "energyConsumption"
KEY_POWER_STATE: Final = "powerState"
KEY_TEMPERATURE_ORIGIN: Final = "temperatureOrigin"
KEY_QUERY: Final = "query"

# Climate error/warning keys
KEY_ACTUATOR_DEACTIVATED: Final = "actuatorDeactivated"
KEY_ACTUATOR_LOW_BATTERY: Final = "actuatorLowBattery"
KEY_ACTUATOR_NOT_RESPONDING: Final = "actuatorNotResponding"
KEY_MISSING_TEMPERATURE: Final = "missingTemperature"
KEY_CIRCUIT_IN_USE: Final = "circuitInUse"

# Rocker switch button keys. F6-02-xx has the rockers A and B, F6-03-xx
# additionally C and D.
ROCKER_2_BUTTON_KEYS: Final = (
    "buttonA0",
    "buttonAI",
    "buttonB0",
    "buttonBI",
    "multipleButtons",
)
BUTTON_KEYS: Final = (
    "buttonA0",
    "buttonAI",
    "buttonB0",
    "buttonBI",
    "buttonC0",
    "buttonCI",
    "buttonD0",
    "buttonDI",
    "multipleButtons",
)
BUTTON_VALUE_PRESSED: Final = "pressed"
BUTTON_VALUE_RELEASED: Final = "released"

# Switch/Light states
STATE_ON: Final = "on"
STATE_OFF: Final = "off"


# Climate heater mode values
HEATER_MODE_HEATING: Final = "heating"
HEATER_MODE_ON: Final = "on"
HEATER_MODE_OFF: Final = "off"
HEATER_MODE_AUTO_OFF: Final = "autoOff"
HEATER_MODE_CONFIG_INCOMPLETE: Final = "configIncomplete"
HEATER_MODE_ERROR: Final = "error"

# Default values
DEFAULT_CHANNEL: Final = 0

# All known state keys for initial state application
KNOWN_STATE_KEYS: Final = frozenset(
    {
        "switch",
        "dimValue",
        "position",
        "angle",
        KEY_ROTATION_TIME,
        KEY_VERTICAL_MOVEMENT_TIME,
        "localControl",
        "energy",
        "power",
        "liquidDetected",
        KEY_CONTACT,
        "handleState",
        "handle",
        "unlock",
        "mechanics",
        "lock",
        "unlockRequest",
        "mechanicsFault",
        KEY_SMOKE_ALARM,
        "alarm",
        KEY_BATTERY_LOW_RWM,
        "batteryLevel",
        "motionDetector",
        "motionDetected",
        "illuminance",
        "illumination",
        "supplyVoltage",
        "temperature",
        "temperatureSetpoint",
        "heaterMode",
        "humidity",
        "windowOpen",
        "summerMode",
        "feedTemperature",
        "thermalMode",
        "energyConsumption",
        "powerState",
        "temperatureOrigin",
        "actuatorDeactivated",
        "actuatorLowBattery",
        "actuatorNotResponding",
        "missingTemperature",
        "circuitInUse",
        KEY_LOCKING_MODE,
        KEY_ERROR_CODE,
        *BUTTON_KEYS,
    }
)
