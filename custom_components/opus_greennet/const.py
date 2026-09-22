"""Constants for the Opus GreenNet Bridge integration.

Base: kegelmeier/ha-opus-greennet v0.3.3b0 (unchanged by the PR #30 hardening,
confirmed via commit diff). Extended with four EnOcean devices ported from the
fubu2k fork (HOPPE window handles with/without eLock, Jaeger Direkt RWM smoke
detector, OPUS SMS presence sensor).
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "opus_greennet"

# Configuration keys
CONF_EAG_ID: Final = "eag_id"

# MQTT Topic patterns (EnOcean over IP specification)
TOPIC_BASE: Final = "EnOcean"
TOPIC_STREAM_TELEGRAM: Final = "{base}/{eag_id}/stream/telegram/{device_id}/from"
TOPIC_STREAM_TELEGRAM_TO: Final = "{base}/{eag_id}/stream/telegram/{device_id}/to"
TOPIC_STREAM_DEVICE: Final = "{base}/{eag_id}/stream/device/{device_id}"
TOPIC_PUT_STATE: Final = "{base}/{eag_id}/put/devices/{device_id}/state"
# HOPPE window handle access-control command (fire-and-forget, no putAnswer).
TOPIC_WINDOW_HANDLE_ACCESS: Final = (
    "{base}/{eag_id}/stream/telegram/{device_id}/functions/0/value"
)
TOPIC_SUB_PUT_ANSWER_STATE: Final = "{base}/{eag_id}/putAnswer/devices/+/state"
TOPIC_GET_DEVICES: Final = "{base}/{eag_id}/get/devices"
TOPIC_GET_ANSWER_DEVICES: Final = "{base}/{eag_id}/getAnswer/devices/#"
TOPIC_GET_DEVICE_PROFILE: Final = "{base}/{eag_id}/get/devices/{device_id}/profile"
TOPIC_GET_ANSWER_DEVICE_PROFILE: Final = (
    "{base}/{eag_id}/getAnswer/devices/{device_id}/profile"
)

# ReCom API topics
TOPIC_GET_DEVICE_CONFIGURATION: Final = (
    "{base}/{eag_id}/get/devices/{device_id}/configuration"
)
TOPIC_GET_ANSWER_DEVICE_CONFIGURATION: Final = (
    "{base}/{eag_id}/getAnswer/devices/{device_id}/configuration"
)
TOPIC_PUT_DEVICE_CONFIGURATION: Final = (
    "{base}/{eag_id}/put/devices/{device_id}/configuration"
)
TOPIC_PUT_ANSWER_DEVICE_CONFIGURATION: Final = (
    "{base}/{eag_id}/putAnswer/devices/{device_id}/configuration"
)
TOPIC_GET_DEVICE_PARAMETERS: Final = (
    "{base}/{eag_id}/get/devices/{device_id}/parameters"
)
TOPIC_GET_ANSWER_DEVICE_PARAMETERS: Final = (
    "{base}/{eag_id}/getAnswer/devices/{device_id}/parameters"
)

# Gateway system info topics
TOPIC_GET_SYSTEM_INFO: Final = "{base}/{eag_id}/get/config/system/info"
TOPIC_GET_ANSWER_SYSTEM_INFO: Final = "{base}/{eag_id}/getAnswer/config/system/info"
TOPIC_GET_SYSTEM_UPTIME: Final = "{base}/{eag_id}/get/config/system/uptime"
TOPIC_GET_ANSWER_SYSTEM_UPTIME: Final = "{base}/{eag_id}/getAnswer/config/system/uptime"

# Subscription patterns (with wildcards)
TOPIC_SUB_TELEGRAM_FROM_ALL: Final = "{base}/{eag_id}/stream/telegram/#"
TOPIC_SUB_DEVICE_STREAM_ALL: Final = "{base}/{eag_id}/stream/device/#"
TOPIC_SUB_DEVICES_ALL: Final = "{base}/{eag_id}/stream/devices/#"

# EEP (EnOcean Equipment Profile) to primary entity type mapping.
# Format: EEP -> (primary_platform, description)
# NOTE: this only decides which platform module "owns" the device (i.e. which
# async_setup_entry reacts to SIGNAL_DEVICE_DISCOVERED). Supplementary
# entities (diagnostics, additional sensors, locks, ...) are declared once,
# centrally, in entity_descriptions.py - platform files never hardcode EEPs.
EEP_MAPPINGS: Final = {
    # Electronic Switch Actuators (D2-01-xx)
    "D2-01-00": ("switch", "Electronic Switch Actuator, 1 Channel"),
    "D2-01-01": ("switch", "Electronic Switch Actuator, 1 Channel with Energy"),
    "D2-01-02": ("light", "Dimmer, 1 Channel"),
    "D2-01-03": ("light", "Dimmer, 1 Channel with Energy"),
    "D2-01-04": ("switch", "Electronic Switch Actuator, 2 Channels"),
    "D2-01-05": ("switch", "Electronic Switch Actuator, 2 Channels with Energy"),
    "D2-01-06": ("light", "Dimmer, 2 Channels"),
    "D2-01-07": ("light", "Dimmer, 2 Channels with Energy"),
    "D2-01-08": ("switch", "Electronic Switch Actuator, 4 Channels"),
    "D2-01-09": ("switch", "Electronic Switch Actuator, 4 Channels with Energy"),
    "D2-01-0A": ("light", "Dimmer, 4 Channels"),
    "D2-01-0B": ("light", "Dimmer, 4 Channels with Energy"),
    "D2-01-0C": ("switch", "Pilot Wire Controller"),
    "D2-01-0D": ("switch", "Electronic Switch Actuator, 8 Channels"),
    "D2-01-0E": ("switch", "Electronic Switch Actuator, 8 Channels with Energy"),
    "D2-01-0F": ("light", "Dimmer, 8 Channels"),
    "D2-01-10": ("light", "Dimmer, 8 Channels with Energy"),
    "D2-01-11": ("switch", "Electronic Switch Actuator, 2 Channels, 2 Channels with Local Control"),
    "D2-01-12": ("light", "Dimmer, 2 Channels with Local Control"),
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
    # Rocker Switch (F6-02-xx / F6-03-xx) - typically used as triggers
    "F6-02-01": ("event", "Rocker Switch, 2 Rocker"),
    "F6-02-02": ("event", "Rocker Switch, 2 Rocker"),
    "F6-02-03": ("event", "Rocker Switch, 2 Rocker"),
    "F6-03-01": ("event", "Rocker Switch, 4 Rocker"),
    "F6-03-02": ("event", "Rocker Switch, 4 Rocker"),
    # Liquid Leakage Sensor (F6-05-01)
    "F6-05-01": ("binary_sensor", "Liquid Leakage Sensor"),
    # --- Ported from fubu2k fork -------------------------------------------
    # HOPPE Smart Window Handle with eLock: primary platform is "lock" so the
    # device gets a Lock entity; the passive handle/unlock sensors are added
    # via entity_descriptions.py.
    "D2-06-40": ("lock", "HOPPE Smart Window Handle (eLock)"),
    # HOPPE Window Handle without eLock: passive sensor only.
    "F6-10-00": ("sensor", "HOPPE Window Handle"),
    "D2-03-10": ("sensor", "HOPPE Window Handle"),
    # Jaeger Direkt Rauchwarnmelder (RWM), Produkt-ID 00401000002E.
    "F6-05-02": ("binary_sensor", "Jaeger Direkt Smoke Detector (RWM)"),
    # OPUS SMS Anwesenheit / motion & presence detector.
    "A5-07-03": ("binary_sensor", "OPUS SMS Presence Detector"),
}

# Entity type to platform mapping
ENTITY_PLATFORMS: Final = {
    "light": "light",
    "switch": "switch",
    "cover": "cover",
    "climate": "climate",
    "binary_sensor": "binary_sensor",
    "event": "event",
    "lock": "lock",
    "sensor": "sensor",
}

# Function keys used in EnOcean telegrams
KEY_SWITCH: Final = "switch"
KEY_DIMMER: Final = "dimValue"
KEY_POSITION: Final = "position"
KEY_ANGLE: Final = "angle"
KEY_ROTATION_TIME: Final = "rotationTime"
KEY_CHANNEL: Final = "channel"
KEY_LOCAL_CONTROL: Final = "localControl"
KEY_ENERGY: Final = "energy"
KEY_POWER: Final = "power"
KEY_LIQUID_DETECTED: Final = "liquidDetected"
KEY_QUERY: Final = "query"

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

# Climate error/warning keys
KEY_ACTUATOR_DEACTIVATED: Final = "actuatorDeactivated"
KEY_ACTUATOR_LOW_BATTERY: Final = "actuatorLowBattery"
KEY_ACTUATOR_NOT_RESPONDING: Final = "actuatorNotResponding"
KEY_MISSING_TEMPERATURE: Final = "missingTemperature"
KEY_CIRCUIT_IN_USE: Final = "circuitInUse"

# --- Ported from fubu2k fork: new telegram function keys -------------------
# OPUS SMS Anwesenheit (A5-07-03). Delivered via indexed states/{n} pairs on
# stream/device/{DeviceID}/states/{n}/key + .../value, see coordinator.py.
KEY_MOTION_DETECTED: Final = "motionDetected"
KEY_ILLUMINATION: Final = "illumination"
KEY_SUPPLY_VOLTAGE: Final = "supplyVoltage"
KEY_BATTERY_LEVEL: Final = "batteryLevel"  # global device property, e.g. "72%"

# HOPPE window handle (D2-06-40 / F6-10-00 / D2-03-10)
KEY_HANDLE: Final = "handle"
KEY_LOCK: Final = "lock"
KEY_UNLOCK: Final = "unlock"

# Jaeger Direkt RWM (F6-05-02). Delivered via indexed transmitModes/{n} pairs,
# see coordinator.py.
KEY_ALARM: Final = "alarm"
KEY_BATTERY_LOW: Final = "batteryLow"

# Rocker switch button keys (F6-02-xx / F6-03-xx profiles)
BUTTON_KEYS: Final = (
    "buttonA0",
    "buttonAI",
    "buttonB0",
    "buttonBI",
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

# HOPPE window handle states and commands
HANDLE_CLOSED: Final = "closed"
HANDLE_OPEN: Final = "open"
HANDLE_TILT: Final = "tilt"
LOCK_LOCKED: Final = "locked"
LOCK_UNLOCKED: Final = "unlocked"
UNLOCK_NOT_REQUESTED: Final = "notRequested"
UNLOCK_REQUESTED: Final = "requested"
# Command payloads for TOPIC_WINDOW_HANDLE_ACCESS. NOTE: per fubu2k's own
# testing notes this command path does not reliably change the physical
# lock state - the lock entity is ported "as is" (best effort / experimental)
# per explicit product decision, see PORTING_NOTES.md.
LOCK_COMMAND_ALLOWED: Final = "allowed"
LOCK_COMMAND_NOT_ALLOWED: Final = "notAllowed"

# Default values
DEFAULT_CHANNEL: Final = 0

# All known state keys for initial state application / fast-path parsing.
KNOWN_STATE_KEYS: Final = frozenset(
    {
        "switch",
        "dimValue",
        "position",
        "angle",
        KEY_ROTATION_TIME,
        "localControl",
        "energy",
        "power",
        "liquidDetected",
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
        KEY_MOTION_DETECTED,
        KEY_ILLUMINATION,
        KEY_SUPPLY_VOLTAGE,
        KEY_BATTERY_LEVEL,
        KEY_HANDLE,
        KEY_LOCK,
        KEY_UNLOCK,
        KEY_ALARM,
        KEY_BATTERY_LOW,
        *BUTTON_KEYS,
    }
)

# Top-level indexed containers the OPUS gateway uses for flat key/value
# fragment pairs, e.g. ".../states/0/key" + ".../states/0/value" or
# ".../transmitModes/0/key" + ".../transmitModes/0/value". Every device type
# uses exactly one of these containers; both are treated identically once the
# {key, value} pair is complete. New containers only need to be added here -
# no coordinator.py logic changes required (see coordinator._INDEXED_STATE_CONTAINERS).
INDEXED_STATE_CONTAINERS: Final = ("states", "transmitModes")
