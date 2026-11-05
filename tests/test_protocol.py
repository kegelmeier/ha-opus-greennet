"""Pure protocol helpers that read OPUS gateway documents."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.const import (
    BUTTON_KEYS,
    KEY_LIQUID_DETECTED,
    KNOWN_STATE_KEYS,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice

# ── metadata_text / telegram_timestamp ──────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Kitchen", "Kitchen"),
        ("", ""),
        (123, "123"),
        (1.5, "1.5"),
        (True, ""),
        (None, ""),
        (["x"], ""),
        ({"name": "x"}, ""),
    ],
)
def test_metadata_text(value: object, expected: str) -> None:
    assert protocol.metadata_text(value) == expected


def test_telegram_timestamp_reads_gateway_format() -> None:
    expected = datetime(2026, 10, 19, 16, 6, 3, 990000, tzinfo=UTC).timestamp()
    assert protocol.telegram_timestamp("2026-10-19T18:06:03.990+0200") == expected


@pytest.mark.parametrize(
    "value", [None, 1760889963, "2026-10-19T18:06:03+0200", "yesterday", ""]
)
def test_telegram_timestamp_rejects_other_values(value: object) -> None:
    assert protocol.telegram_timestamp(value) is None


# ── channel selectors ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("functions", "expected"),
    [
        ([], 0),
        ([{"key": "switch", "value": "on"}], 0),
        ([{"key": "channel", "value": "1"}, {"key": "switch", "value": "on"}], 1),
        ([{"key": "channel", "value": 2.0}], 2),
        # The first selector wins.
        ([{"key": "channel", "value": 3}, {"key": "channel", "value": 1}], 3),
    ],
)
def test_channel_from_functions(functions: list[dict], expected: int) -> None:
    assert protocol.channel_from_functions(functions) == expected


@pytest.mark.parametrize("channel", [-1, True, "NaN", "inf", "1.5", None, "one"])
def test_malformed_channel_selector_is_not_channel_zero(channel: object) -> None:
    assert protocol.channel_from_functions([{"key": "channel", "value": channel}]) is (
        None
    )


@pytest.mark.parametrize(
    ("function", "default", "expected"),
    [
        ({"key": "switch", "value": "on"}, 0, 0),
        ({"key": "switch", "value": "on"}, 2, 2),
        ({"key": "switch", "value": "on", "channel": 1}, 0, 1),
        ({"key": "switch", "value": "on", "channel": "1"}, None, 1),
        ({"key": "switch", "value": "on"}, None, None),
        ({"key": "switch", "value": "on", "channel": -1}, 0, None),
        ({"key": "switch", "value": "on", "channel": None}, 0, None),
    ],
)
def test_function_channel(function: dict, default: int | None, expected) -> None:
    assert protocol.function_channel(function, default) == expected


# ── RWM transmit modes ──────────────────────────────────────────────────────


def test_rwm_fallback_preserves_slots_and_explicit_keys() -> None:
    modes = [
        {"key": "", "value": "on"},
        {"key": None, "value": True},
        None,
        {"key": "other", "value": "on"},
    ]
    resolved = protocol.rwm_transmit_modes(modes)
    assert [
        entry.get("key") if isinstance(entry, dict) else entry for entry in resolved
    ] == ["smokeAlarm", "batteryLow", None, "other"]
    assert modes[0]["key"] == ""  # The source document is not mutated.
    assert protocol.rwm_transmit_modes([{"key": "other"}]) == [{"key": "other"}]
    # Only the first two slots have a documented meaning.
    assert protocol.rwm_transmit_modes([{"value": 1}, {"value": 2}, {"value": 3}]) == [
        {"key": "smokeAlarm", "value": 1},
        {"key": "batteryLow", "value": 2},
        {"value": 3},
    ]


# ── configuration_state_functions ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("configuration", "expected"),
    [
        (None, []),
        ("not-an-object", []),
        ({"parameters": "not-a-list"}, []),
        (
            {"parameters": ["junk", {"key": "rotationTime", "value": 2}]},
            [{"channel": 0, "key": "rotationTime", "value": 2}],
        ),
    ],
)
def test_configuration_functions_tolerate_malformed_parameters(
    configuration: object, expected: list[dict]
) -> None:
    assert (
        protocol.configuration_state_functions({"configuration": configuration})
        == expected
    )


@pytest.mark.parametrize(
    "parameters",
    [
        [{"key": "rotationTime", "defaultValue": 0.0}],
        [{"key": "verticalMovementTime", "defaultValue": 30.0}],
        [{"key": "maxHoldingTime", "defaultValue": 12.0}],
        [{"key": "rampingTime", "defaultValue": 5}],
        [{"key": "shortPress", "defaultValue": 0.1}],
        [{"key": "longPress", "defaultValue": 0.1}],
        [{"key": "timer", "defaultValue": 60}],
        [{"key": "timerLong", "defaultValue": 60}],
        [{"key": "switchOffWarning", "defaultValue": 30}],
        [{"key": "switchOffWarningSignal", "defaultValue": 0.5}],
        [{"key": "illuminationThreshold", "defaultValue": 80}],
        [{"key": "operationMode", "defaultValue": "toggle"}],
        [{"key": "ledColor", "defaultValue": "off"}],
        [{"key": "ledChannel", "defaultValue": "channel1"}],
        [{"key": "functionExternalInput", "defaultValue": "channel1"}],
        [{"key": "lastVerticalMovementTime", "value": 30.45}],
    ],
)
def test_configuration_defaults_and_unrelated_parameters_are_not_state(
    parameters: list[dict],
) -> None:
    assert (
        protocol.configuration_state_functions(
            {"configuration": {"parameters": parameters}}
        )
        == []
    )


def test_configuration_reads_real_cover_parameters() -> None:
    """The shape of a D2-05-02 configuration in a real device list."""
    configuration = {
        "lastUpdateTime": "2026-10-19T03:55:00.850+0200",
        "deviceId": "01A02F6C",
        "friendlyId": "EG_Kueche",
        "parameters": [
            {
                "key": "verticalMovementTime",
                "description": "Measured duration of a vertical run",
                "value": 56.26,
                "unit": "s",
            },
            {
                "key": "rotationTime",
                "description": "Measured duration of rotation",
                "value": 1.73,
                "unit": "s",
            },
        ],
    }
    assert protocol.configuration_state_functions({"configuration": configuration}) == [
        {"channel": 0, **configuration["parameters"][0]},
        {"channel": 0, **configuration["parameters"][1]},
    ]


def test_configuration_channel_selectors() -> None:
    functions = protocol.configuration_state_functions(
        {
            "configuration": {
                "channel": 1,
                "parameters": [
                    {"key": "rotationTime", "value": 1.5},
                    {"key": "channel", "value": 2},
                    {"key": "verticalMovementTime", "value": 30.0},
                ],
            }
        }
    )
    assert functions == [
        {"channel": 1, "key": "rotationTime", "value": 1.5},
        {"channel": 2, "key": "verticalMovementTime", "value": 30.0},
    ]


def test_configuration_parameter_channel_overrides_selector() -> None:
    functions = protocol.configuration_state_functions(
        {
            "configuration": {
                "parameters": [{"key": "rotationTime", "value": 0.0, "channel": 1}]
            }
        }
    )
    assert functions == [{"channel": 1, "key": "rotationTime", "value": 0.0}]


# ── device_state_functions ──────────────────────────────────────────────────


def test_states_list_ignores_incomplete_or_malformed_descriptors() -> None:
    functions = protocol.device_state_functions(
        {
            "states": [
                None,
                "not a descriptor",
                {"key": "dimValue"},
                {"value": 10},
                {"key": ["switch"], "value": "on"},
                {"key": "dimValue", "value": 0.0, "channel": 1},
            ]
        }
    )
    assert functions == [{"key": "dimValue", "value": 0.0, "channel": 1}]


def test_state_functions_take_precedence_over_states() -> None:
    data = {
        "state": {"functions": [{"key": "switch", "value": "on"}]},
        "states": [{"key": "switch", "value": "off"}],
    }
    assert protocol.device_state_functions(data) == [{"key": "switch", "value": "on"}]


@pytest.mark.parametrize(
    "state",
    [
        {"functions": []},
        {"functions": [{"key": "buttonA0", "value": "pressed"}]},
        {"functions": "invalid"},
        {},
        "not an object",
    ],
)
def test_states_are_used_when_state_functions_are_empty(state: object) -> None:
    data = {"state": state, "states": [{"key": "switch", "value": "off"}]}
    assert protocol.device_state_functions(data) == [{"key": "switch", "value": "off"}]


@pytest.mark.parametrize("states", [{"switch": "on"}, "on", None])
def test_states_must_be_a_list(states: object) -> None:
    assert protocol.device_state_functions({"states": states}) == []


@pytest.mark.parametrize("key", BUTTON_KEYS)
def test_button_keys_are_never_device_state(key: str) -> None:
    data = {
        "states": [
            {"key": key, "value": "pressed"},
            {"key": "switch", "value": "on"},
        ]
    }
    assert protocol.device_state_functions(data) == [{"key": "switch", "value": "on"}]


def test_real_device_object_state_functions() -> None:
    """A two-channel actuator as the real gateway lists it."""
    data = {
        "deviceId": "0507BF73",
        "eeps": [
            {"eep": "D2-01-11", "version": 0.9, "direction": "both"},
            {"eep": "F6-03-01", "version": 0.9, "direction": "from"},
        ],
        "batteryLevel": "96%",
        "dbm": -71,
        # Real transmit modes describe reporting, they carry no value.
        "transmitModes": [
            {
                "key": "switch",
                "transmitOnConnect": True,
                "transmitOnEvent": True,
                "transmitOnDuplicate": False,
            },
            {"key": "buttonA0", "transmitOnEvent": True},
        ],
        "configuration": {
            "deviceId": "0507BF73",
            "friendlyId": "OG_Flur",
            "lastUpdateTime": "2026-10-19T03:55:00.850+0200",
            "parameters": [{"key": "timer", "value": 300.0, "unit": "s"}],
        },
        "states": [
            {
                "key": "switch",
                "channel": 1,
                "value": "off",
                "meaning": "Output value OFF",
                "timestamp": "2026-10-19T18:17:17.979+0200",
                "age": 26481,
            },
            {"key": "switch", "channel": 0, "value": "on", "age": 26576},
        ],
    }
    # The reported timer of the D2-01-11 is a device setting and read as state.
    timer = {"channel": 0, "key": "timer", "value": 300.0, "unit": "s"}
    assert protocol.device_state_functions(data) == [
        data["states"][0],
        data["states"][1],
        {"key": "batteryLevel", "value": "96%"},
        timer,
    ]
    assert protocol.configuration_functions(data) == [
        {"key": "batteryLevel", "value": "96%"},
        timer,
    ]


def test_transmit_modes_contribute_only_detector_values() -> None:
    data = {
        "transmitModes": [
            {"key": "alarm", "value": "off"},
            {"key": "smokeAlarm", "value": True},
            {"key": "batteryLow", "value": False},
            {"key": "batteryLow"},
            {"key": "switch", "value": "on"},
            "junk",
        ],
    }
    assert protocol.device_state_functions(data) == [
        {"key": "alarm", "value": "off"},
        {"key": "smokeAlarm", "value": True},
        {"key": "batteryLow", "value": False},
    ]


def test_value_only_transmit_modes_are_resolved_only_for_the_rwm() -> None:
    modes = [{"value": "on"}, {"value": "true"}]
    rwm = protocol.device_state_functions(
        {"eeps": [{"eep": "F6-05-02"}], "transmitModes": modes}
    )
    assert rwm == [
        {"key": "smokeAlarm", "value": "on"},
        {"key": "batteryLow", "value": "true"},
    ]
    other = protocol.device_state_functions(
        {"eeps": [{"eep": "A5-07-03"}], "transmitModes": modes}
    )
    assert other == []


@pytest.mark.parametrize("modes", [None, "x", {"key": "alarm", "value": "on"}])
def test_malformed_transmit_modes_are_ignored(modes: object) -> None:
    data = {"eeps": [{"eep": "F6-05-02"}], "transmitModes": modes}
    assert protocol.device_state_functions(data) == []


def test_battery_and_configuration_are_appended() -> None:
    data = {
        "states": [{"key": "position", "value": 78.0}],
        "batteryLevel": None,
        "configuration": {"parameters": [{"key": "rotationTime", "value": 0.0}]},
    }
    assert protocol.device_state_functions(data) == [
        {"key": "position", "value": 78.0},
        {"key": "batteryLevel", "value": None},
        {"channel": 0, "key": "rotationTime", "value": 0.0},
    ]
    assert protocol.configuration_functions(data) == [
        {"key": "batteryLevel", "value": None},
        {"channel": 0, "key": "rotationTime", "value": 0.0},
    ]


def test_configuration_functions_keep_channel_and_movement_times() -> None:
    data = {
        "states": [
            {"key": "channel", "value": 1},
            {"key": "position", "value": 20},
            {"key": "rotationTime", "value": "noChange"},
            {"key": "verticalMovementTime", "value": 30},
        ]
    }
    assert protocol.configuration_functions(data) == [
        {"key": "channel", "value": 1},
        {"key": "rotationTime", "value": "noChange"},
        {"key": "verticalMovementTime", "value": 30},
    ]


# ── feedback and command fields ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("function", "confirms"),
    [
        ({"key": "dimValue", "value": "50"}, True),
        ({"key": "dimValue", "value": 0.0}, True),
        ({"key": "dimValue", "value": "invalid"}, False),
        ({"key": "dimValue", "value": 101}, False),
        ({"key": "dimValue", "value": 50.5}, False),
        ({"key": "position", "value": 100}, True),
        ({"key": "position", "value": "unknown"}, False),
        ({"key": "angle", "value": "unknown"}, False),
        ({"key": "angle", "value": -1}, False),
        ({"key": "temperatureSetpoint", "value": 21.5}, True),
        ({"key": "temperatureSetpoint", "value": 41}, False),
        ({"key": "temperatureSetpoint", "value": "notAvailable"}, False),
        ({"key": "switch", "value": "on"}, True),
        ({"key": "switch", "value": "off"}, True),
        ({"key": "switch", "value": True}, False),
        ({"key": "heaterMode", "value": "autoOff"}, True),
        ({"key": "heaterMode", "value": "error"}, False),
        ({"key": "localControl", "value": "on"}, False),
        ({"key": "rotationTime", "value": 0}, False),
        ({"key": "batteryLevel", "value": "90%"}, False),
    ],
)
def test_confirmed_fields_require_usable_feedback(
    function: dict, confirms: bool
) -> None:
    expected = {0: {function["key"]}} if confirms else {}
    assert protocol.confirmed_fields_by_channel([function]) == expected
    assert protocol.has_operational_state([function]) is confirms


def test_confirmed_fields_are_grouped_by_channel() -> None:
    functions = [
        {"key": "channel", "value": "1"},
        {"key": "position", "value": 40},
        {"key": "angle", "value": 20},
        {"key": "switch", "value": "on", "channel": 0},
        {"key": "dimValue", "value": 10, "channel": -1},
    ]
    assert protocol.confirmed_fields_by_channel(functions) == {
        1: {"position", "angle"},
        0: {"switch"},
    }


@pytest.mark.parametrize("channel", [-1, True, "NaN", "inf", "1.5", None])
def test_malformed_channels_do_not_reconcile_channel_zero(channel: object) -> None:
    assert (
        protocol.command_fields_by_channel(
            [
                {"key": "channel", "value": channel},
                {"key": "switch", "value": "on"},
            ]
        )
        == {}
    )


def test_stop_expects_final_position_and_tilt_when_supported() -> None:
    stop = [{"key": "stop", "value": "true"}]
    assert protocol.command_fields_by_channel(stop) == {0: {"position"}}
    venetian = EnOceanDevice("C1", "Blind", [{"eep": "D2-05-00"}])
    assert protocol.command_fields_by_channel(stop, venetian) == {
        0: {"position", "angle"}
    }
    roller = EnOceanDevice("C2", "Roller", [{"eep": "D2-05-00"}])
    roller.get_or_create_channel(1).rotation_time = 0
    assert protocol.command_fields_by_channel(
        [{"key": "channel", "value": "1"}, *stop], roller
    ) == {1: {"position"}}


@pytest.mark.parametrize(
    "functions",
    [
        [{"key": "stop", "value": "false"}],
        [{"key": "stop", "value": "true", "channel": "x"}],
        [{"key": "query", "value": "status"}],
    ],
)
def test_commands_without_expected_feedback(functions: list[dict]) -> None:
    assert protocol.command_fields_by_channel(functions) == {}


def test_outbound_state_functions_keep_state_selector_and_stop() -> None:
    functions = [
        {"key": "channel", "value": "1"},
        {"key": "switch", "value": "on"},
        {"key": "stop", "value": "true"},
        {"key": "query", "value": "status"},
        {"key": "lockingMode", "value": "unblock"},
    ]
    # The blind locking mode is a target state; a query is not.
    assert protocol.outbound_state_functions(functions) == [
        *functions[:3],
        functions[4],
    ]


@pytest.mark.parametrize(
    ("functions", "expected"),
    [
        ([{"key": "switch", "value": "on"}], True),
        ([{"key": "stop", "value": "true"}], True),
        ([{"key": "rotationTime", "value": 0}], True),
        ([{"key": "channel", "value": "1"}], False),
        ([{"key": "query", "value": "status"}], False),
        ([], False),
    ],
)
def test_has_target_state(functions: list[dict], expected: bool) -> None:
    assert protocol.has_target_state(functions) is expected


# ── KNOWN_STATE_KEYS ────────────────────────────────────────────────────────


def test_known_state_keys_include_rocker_button_keys() -> None:
    for key in BUTTON_KEYS:
        assert key in KNOWN_STATE_KEYS, f"{key} missing from KNOWN_STATE_KEYS"


def test_known_state_keys_include_liquid_detected() -> None:
    assert KEY_LIQUID_DETECTED in KNOWN_STATE_KEYS
