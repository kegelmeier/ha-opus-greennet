"""Pure helpers that read OPUS protocol documents.

Everything here is a side-effect free function of gateway JSON. The
coordinator owns state and timing; these helpers only extract function lists
and the fields a command or a report refers to.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from .const import (
    BUTTON_KEYS,
    DEVICE_OPTIONS,
    DEVICE_SETTING_KEYS,
    DEVICE_SETTINGS,
    KEY_ANGLE,
    KEY_CHANNEL,
    KEY_DIMMER,
    KEY_HEATER_MODE,
    KEY_LOCKING_MODE,
    KEY_MAX_HOLDING_TIME,
    KEY_POSITION,
    KEY_REPEATER_STATE,
    KEY_ROTATION_TIME,
    KEY_ROUTING_TIME,
    KEY_STOP,
    KEY_SWITCH,
    KEY_TEMPERATURE_SETPOINT,
    KEY_UNLOCK,
    KEY_VERTICAL_MOVEMENT_TIME,
    KNOWN_STATE_KEYS,
    LOCKING_MODES,
    REPEATER_EEPS,
    RWM_EEP,
)
from .enocean_device import DeviceProfile, EnOceanDevice, LinkedDevice, LinkTables
from .wire import WireFunction, WireMapping, as_object, object_items

if TYPE_CHECKING:
    from .wire import JsonValue

# Keys whose reported values can confirm an actuator command.
FEEDBACK_KEYS = frozenset(
    {KEY_DIMMER, KEY_POSITION, KEY_ANGLE, KEY_TEMPERATURE_SETPOINT}
)
# Keys of a device list entry that describe configuration, not live state.
CONFIGURATION_KEYS = frozenset(
    {
        KEY_CHANNEL,
        KEY_MAX_HOLDING_TIME,
        KEY_ROTATION_TIME,
        KEY_VERTICAL_MOVEMENT_TIME,
        "batteryLevel",
        *DEVICE_SETTING_KEYS,
    }
)
# Configuration parameters (cover times, D2-01 settings and options) that are
# read as state.
CONFIGURATION_STATE_KEYS = (
    KEY_MAX_HOLDING_TIME,
    KEY_ROTATION_TIME,
    KEY_VERTICAL_MOVEMENT_TIME,
    *DEVICE_SETTINGS,
    *DEVICE_OPTIONS,
)
# The RWM documents value-only slots in transmitModes: index 0 is the smoke
# alarm, index 1 the battery state.
RWM_TRANSMIT_MODE_KEYS = ("smokeAlarm", "batteryLow")


def metadata_text(value: object) -> str:
    """Return a device metadata field as text.

    Strings pass through unchanged and numbers are rendered back to text, so
    a numeric name such as ``friendlyId = 123`` survives. Other types (null,
    booleans, containers) are not names and fall back to an empty string.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def telegram_timestamp(value: object) -> float | None:
    """Return a gateway timestamp in epoch seconds.

    The gateway writes ``2026-10-19T18:06:03.990+0200``.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
    except ValueError:
        return None


def device_profile(document: WireMapping) -> DeviceProfile | None:
    """Read the capabilities of a ``/devices/<id>/profile`` document.

    The gateway lists the function groups of every announced EEP together.
    Each group has a ``direction`` (``to``: accepted commands, ``from``:
    reports) and ``functions`` with a ``key`` and allowed ``values`` (enums or
    a ``range``). Returns None when the document has no usable group.
    """
    profile = as_object(document.get("profile"))
    if profile is None:
        return None
    to_keys: set[str] = set()
    from_keys: set[str] = set()
    channels: int | None = None
    maxima: dict[str, float] = {}
    groups = object_items(profile.get("functionGroups"))
    for group in groups:
        direction = group.get("direction")
        if direction not in ("to", "from", "both"):
            continue
        for function in object_items(group.get("functions")):
            key = function.get("key")
            if not isinstance(key, str) or not key:
                continue
            if direction in ("to", "both"):
                to_keys.add(key)
            if direction in ("from", "both"):
                from_keys.add(key)
            for value in object_items(function.get("values")):
                limits = as_object(value.get("range"))
                if limits is None:
                    continue
                maximum = EnOceanDevice._parse_number(limits.get("max"), minimum=0)
                if maximum is None:
                    continue
                if key == KEY_CHANNEL and direction in ("to", "both"):
                    if float(maximum).is_integer():
                        channels = max(channels or 0, int(maximum) + 1)
                elif direction in ("from", "both"):
                    maxima[key] = float(maximum)
    if not to_keys and not from_keys:
        return None
    return DeviceProfile(
        to_keys=frozenset(to_keys),
        from_keys=frozenset(from_keys),
        channels=channels,
        from_maxima=MappingProxyType(maxima),
    )


def channel_from_functions(functions: Sequence[WireFunction]) -> int | None:
    """Return the telegram channel selector, defaulting to channel zero.

    A malformed selector yields None so callers can drop the functions instead
    of applying them to channel zero.
    """
    for function in functions:
        if function.get("key") != KEY_CHANNEL:
            continue
        value = EnOceanDevice._parse_number(
            function.get("value"), minimum=0, integer=True
        )
        return int(value) if value is not None else None
    return 0


def function_channel(function: WireFunction, default: int | None) -> int | None:
    """Return the channel a single function refers to."""
    channel = EnOceanDevice._parse_number(
        function.get("channel", default), minimum=0, integer=True
    )
    return int(channel) if channel is not None else None


def rwm_transmit_modes(modes: Sequence[object]) -> list[object]:
    """Resolve the RWM's documented value-only slots, scoped to F6-05-02."""
    resolved: list[object] = []
    for index, mode in enumerate(modes):
        if not isinstance(mode, dict):
            resolved.append(mode)
            continue
        entry = dict(mode)
        if index < len(RWM_TRANSMIT_MODE_KEYS) and entry.get("key") in (None, ""):
            entry["key"] = RWM_TRANSMIT_MODE_KEYS[index]
        resolved.append(entry)
    return resolved


def configuration_state_functions(data: WireMapping) -> list[WireFunction]:
    """Read reported cover times and D2-01 settings, never parameter defaults."""
    configuration = data.get("configuration")
    if not isinstance(configuration, dict):
        return []
    parameters = configuration.get("parameters")
    if not isinstance(parameters, list):
        return []
    channel: JsonValue = configuration.get("channel", 0)
    functions: list[WireFunction] = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        if parameter.get("key") == KEY_CHANNEL:
            channel = parameter.get("value")
        elif parameter.get("key") == KEY_ROUTING_TIME and "value" in parameter:
            if (routing := routing_state(parameter["value"])) is not None:
                functions.append(
                    {"channel": channel, "key": KEY_ROUTING_TIME, "value": routing}
                )
        elif parameter.get("key") in CONFIGURATION_STATE_KEYS and "value" in parameter:
            functions.append({"channel": channel, **parameter})
    return functions


# Duration of the last vertical run as the gateway's device list reports it
# (configuration parameter, seconds). Diagnostic only.
KEY_LAST_VERTICAL_MOVEMENT_TIME = "lastVerticalMovementTime"


def last_vertical_movement_times(data: WireMapping) -> dict[int, float]:
    """Return the last vertical run duration per channel from a device object.

    Only a finite, non-negative number counts; the key is optional and absent
    from the event stream's device list (no ``configuration`` block there).
    """
    configuration = data.get("configuration")
    if not isinstance(configuration, dict):
        return {}
    parameters = configuration.get("parameters")
    if not isinstance(parameters, list):
        return {}
    channel: object = configuration.get("channel", 0)
    durations: dict[int, float] = {}
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        key = parameter.get("key")
        if key == KEY_CHANNEL:
            channel = parameter.get("value")
        elif key == KEY_LAST_VERTICAL_MOVEMENT_TIME:
            value = EnOceanDevice._parse_number(parameter.get("value"), minimum=0)
            channel_id = EnOceanDevice._parse_integer(channel)
            if value is not None and channel_id is not None:
                durations[channel_id] = float(value)
    return durations


def configuration_parameter(document: WireMapping, key: str) -> float | None:
    """Return one numeric parameter of a ``configuration`` document.

    ``document`` is the answer to ``GET /devices/{id}/configuration``. A missing,
    malformed or negative value yields None, so a read-back never confirms
    something the device did not report.
    """
    configuration = as_object(document.get("configuration"))
    parameters = None if configuration is None else configuration.get("parameters")
    if not isinstance(parameters, list):
        return None
    for parameter in parameters:
        if isinstance(parameter, dict) and parameter.get("key") == key:
            value = EnOceanDevice._parse_number(parameter.get("value"), minimum=0)
            return None if value is None else float(value)
    return None


def supports_repeater_state(device: EnOceanDevice) -> bool:
    """Return whether the device is known to have the repeater mode resource."""
    return any(eep in REPEATER_EEPS for eep in device.eep_names)


def routing_state(value: object) -> str | None:
    """Return a reported routing time as ``off`` or ``on``, None if unusable.

    The gateway reports "off" or a time in seconds; any positive time is "on".
    """
    return EnOceanDevice.routing_reading(value)


def configuration_routing(document: WireMapping) -> str | None:
    """Return the routing of a ``configuration`` document as off/on, or None."""
    configuration = as_object(document.get("configuration"))
    parameters = None if configuration is None else configuration.get("parameters")
    if not isinstance(parameters, list):
        return None
    for parameter in parameters:
        if isinstance(parameter, dict) and parameter.get("key") == KEY_ROUTING_TIME:
            return routing_state(parameter.get("value"))
    return None


def repeater_state_function(document: WireMapping) -> WireFunction | None:
    """Read the answer of ``GET /devices/{id}/repeaterState`` as a state function."""
    value = document.get("repeaterState")
    if not isinstance(value, str) or not value:
        return None
    return {"channel": 0, "key": KEY_REPEATER_STATE, "value": value}


def configuration_option(document: WireMapping, key: str) -> str | None:
    """Return one text parameter of a ``configuration`` document.

    ``document`` is the answer to ``GET /devices/{id}/configuration``. A
    missing or malformed value yields None, so a read-back never confirms
    something the device did not report.
    """
    configuration = as_object(document.get("configuration"))
    parameters = None if configuration is None else configuration.get("parameters")
    if not isinstance(parameters, list):
        return None
    for parameter in parameters:
        if isinstance(parameter, dict) and parameter.get("key") == key:
            value = parameter.get("value")
            return value if isinstance(value, str) and value else None
    return None


def _text_value(value: object) -> str | None:
    """Return a scalar parameter value as text, None for anything else."""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _linked_devices(value: object) -> tuple[LinkedDevice, ...]:
    """Read the entries of one link table list, skipping unusable ones."""
    linked: list[LinkedDevice] = []
    for entry in object_items(value):
        device_id = entry.get("deviceId")
        if not isinstance(device_id, str) or not device_id:
            continue
        eep = entry.get("eep")
        parameters: list[tuple[str, str]] = []
        for parameter in object_items(entry.get("parameters")):
            key = parameter.get("key")
            text = _text_value(parameter.get("value"))
            if isinstance(key, str) and key and text is not None:
                parameters.append((key, text))
        linked.append(
            LinkedDevice(
                device_id=device_id,
                eep=eep if isinstance(eep, str) else "",
                parameters=tuple(parameters),
            )
        )
    return tuple(linked)


def link_tables(data: WireMapping) -> LinkTables | None:
    """Read the ``linkTables`` block of a device object.

    The gateway lists a table for every actuator, always with an ``outbound``
    side and without an ``inbound`` list when no wall switch is linked. A
    device without a table (a sensor) lists an empty object. None therefore
    means that no table was read (unknown), while a table with no inbound
    entries is a read table with no wall switch. The gateway's names for the
    entries are not kept.
    """
    table = as_object(data.get("linkTables"))
    if table is None:
        return None
    inbound = table.get("inbound")
    outbound = table.get("outbound")
    if not isinstance(inbound, list) and not isinstance(outbound, list):
        return None
    return LinkTables(
        inbound=_linked_devices(inbound), outbound=_linked_devices(outbound)
    )


# Stands in for the gateway's own EURID in link tables. The outbound list names
# the gateway, and its EURID is also the factory password, so it is never passed
# on.
MASKED_GATEWAY_ID = "gateway"


def mask_gateway_id(value: JsonValue, gateway_id: str) -> JsonValue:
    """Return a copy of a gateway document without the gateway's own EURID.

    Only a string that equals the identifier (ignoring case) is replaced; keys,
    other identifiers and every other value are passed on unchanged.
    """
    if isinstance(value, dict):
        return {key: mask_gateway_id(item, gateway_id) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_gateway_id(item, gateway_id) for item in value]
    if (
        gateway_id
        and isinstance(value, str)
        and value.casefold() == gateway_id.casefold()
    ):
        return MASKED_GATEWAY_ID
    return value


def device_state_functions(data: WireMapping) -> list[WireFunction]:
    """Read the state functions of one device object.

    Device objects carry their state as ``states`` (device list) or as
    ``state.functions`` (state endpoint). Button keys are never state: they
    describe a past occurrence, which only a live telegram may report.
    """
    state = data.get("state", {})
    sources: list[object] = (
        [state.get("functions", [])] if isinstance(state, dict) else []
    )
    sources.append(data.get("states", []))
    state_functions: list[WireFunction] = []
    for functions in sources:
        if not isinstance(functions, list):
            continue
        complete: list[WireFunction] = [
            function
            for function in functions
            if isinstance(function, dict)
            and isinstance(function.get("key"), str)
            and function["key"] not in BUTTON_KEYS
            and "value" in function
        ]
        if complete:
            state_functions = complete
            break
    modes = data.get("transmitModes", [])
    eeps = data.get("eeps", [])
    if (
        isinstance(eeps, list)
        and any(isinstance(eep, dict) and eep.get("eep") == RWM_EEP for eep in eeps)
        and isinstance(modes, list)
    ):
        modes = rwm_transmit_modes(modes)
    if isinstance(modes, list):
        state_functions.extend(
            function
            for function in modes
            if isinstance(function, dict)
            and function.get("key") in ("alarm", "smokeAlarm", "batteryLow")
            and "value" in function
        )
    # The repeater mode is a device field of the device list (cache, no radio).
    if (repeater := repeater_state_function(data)) is not None:
        state_functions.append(repeater)
    if "batteryLevel" in data:
        state_functions.append({"key": "batteryLevel", "value": data["batteryLevel"]})
    # The answer the gateway stores for a sleeping device's next request
    # (HOPPE unlock permission) is listed as ``targetState``.
    targets = data.get("targetState")
    if isinstance(targets, list):
        state_functions.extend(
            target
            for target in targets
            if isinstance(target, dict)
            and target.get("key") == KEY_UNLOCK
            and "value" in target
        )
    return state_functions + configuration_state_functions(data)


def configuration_functions(data: WireMapping) -> list[WireFunction]:
    """Return only the configuration and battery functions of a device object."""
    return [
        function
        for function in device_state_functions(data)
        if function.get("key") in CONFIGURATION_KEYS
    ]


def configuration_read_time(data: WireMapping) -> datetime | None:
    """Return when the gateway last read the configuration of a device object.

    ``configuration.lastUpdateTime`` is an ISO 8601 time with a UTC offset. A
    missing, malformed or offset-less value is unknown: the time zone is never
    guessed.
    """
    configuration = data.get("configuration")
    if not isinstance(configuration, dict):
        return None
    value = configuration.get("lastUpdateTime")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def confirmed_fields_by_channel(
    functions: Sequence[WireFunction],
) -> dict[int, set[str]]:
    """Validate the fields that can confirm an actuator command."""
    default = channel_from_functions(functions)
    confirmed: dict[int, set[str]] = {}
    for function in functions:
        key, value = function.get("key"), function.get("value")
        if key in FEEDBACK_KEYS:
            maximum = 40 if key == KEY_TEMPERATURE_SETPOINT else 100
            valid = (
                EnOceanDevice._parse_number(
                    value,
                    minimum=0,
                    maximum=maximum,
                    integer=key != KEY_TEMPERATURE_SETPOINT,
                )
                is not None
            )
        elif key == KEY_SWITCH:
            valid = value in ("on", "off")
        elif key == KEY_HEATER_MODE:
            valid = value in ("heating", "on", "off", "autoOff")
        elif key == KEY_LOCKING_MODE:
            # "noChange" in a position command expects no locking feedback.
            valid = value in LOCKING_MODES
        else:
            continue
        channel = function_channel(function, default)
        if valid and channel is not None and isinstance(key, str):
            confirmed.setdefault(channel, set()).add(key)
    return confirmed


def has_operational_state(functions: Sequence[WireFunction]) -> bool:
    """Require usable actuator feedback, not interim values or metadata."""
    return bool(confirmed_fields_by_channel(functions))


def command_fields_by_channel(
    functions: Sequence[WireFunction],
    device: EnOceanDevice | None = None,
) -> dict[int, set[str]]:
    """Return the fields a command expects feedback for, per channel.

    Stop needs final position/tilt feedback, never an estimated position.
    """
    expected = confirmed_fields_by_channel(functions)
    default = channel_from_functions(functions)
    for function in functions:
        if function.get("key") != KEY_STOP or function.get("value") != "true":
            continue
        channel = function_channel(function, default)
        if channel is None:
            continue
        fields = expected.setdefault(channel, set())
        fields.add(KEY_POSITION)
        if device is not None and device.supports_tilt_for_channel(channel):
            fields.add(KEY_ANGLE)
    return expected


def outbound_state_functions(
    functions: Sequence[WireFunction],
) -> list[WireFunction]:
    """Keep the parts of an outbound command that describe target state."""
    return [
        function
        for function in functions
        if function.get("key") in KNOWN_STATE_KEYS
        or function.get("key") in (KEY_CHANNEL, KEY_STOP)
    ]


def has_target_state(functions: Sequence[WireFunction]) -> bool:
    """Return whether an outbound command sets state or stops a cover."""
    return any(
        function.get("key") in KNOWN_STATE_KEYS or function.get("key") == KEY_STOP
        for function in functions
    )


def command_signature(functions: Sequence[WireFunction]) -> frozenset[object]:
    """Identify the target state of a command independent of value format.

    The gateway echoes ``"0"`` as ``0.0``; numbers are compared by value.
    """
    default = channel_from_functions(functions)
    signature: set[object] = set()
    for function in outbound_state_functions(functions):
        key = function.get("key")
        if key == KEY_CHANNEL:
            continue
        value = function.get("value")
        number = EnOceanDevice._parse_number(value)
        signature.add(
            (
                function_channel(function, default),
                key,
                number if number is not None else str(value),
            )
        )
    return frozenset(signature)
