"""Settings combinations the OPUS config tool warns about are refused.

The tool warns when a blind's maximum holding time is shorter than its drive
time, when the drive time is shorter than the rotation time, when the cleaning
mode time of a switch is shorter than its run-on time and when the switch-off
warning is longer than the run-on time. The integration refuses a write that
creates or worsens such a combination, before anything is sent to the gateway.
A write that keeps or reduces an existing conflict is allowed, and a value that
is not known is never compared.
"""

from __future__ import annotations

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

BLIND = "D2-05-02"
SWITCH = "D2-01-01"


def _blind(
    *, holding: float | None, travel: float | None, rotation: float | None
) -> EnOceanDevice:
    device = EnOceanDevice("DEV1", "Blind", [{"eep": BLIND}])
    channel = device.get_or_create_channel()
    channel.max_holding_time = holding
    channel.vertical_movement_time = travel
    channel.rotation_time = rotation
    return device


def _switch(**values: float | None) -> EnOceanDevice:
    device = EnOceanDevice("DEV1", "Switch", [{"eep": SWITCH}])
    channel = device.get_or_create_channel()
    for name, value in values.items():
        setattr(channel, name, value)
    return device


# ── the stored values ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("holding", "travel", "rotation", "expected"),
    [
        (29, 25.5, 0, []),
        (29, 25.5, 1.2, []),
        (25, 25.5, 0, ["holding_time_shorter_than_travel_time"]),
        (25.5, 25.5, 0, []),  # equal is fine
        (40, 25.5, 30, ["travel_time_shorter_than_rotation_time"]),
        (
            20,
            25.5,
            30,
            [
                "holding_time_shorter_than_travel_time",
                "travel_time_shorter_than_rotation_time",
            ],
        ),
        (None, 25.5, 30, ["travel_time_shorter_than_rotation_time"]),
        (29, None, 1, []),  # an unknown value is never compared
        (None, None, None, []),
    ],
)
def test_blind_conflicts_in_the_stored_values(holding, travel, rotation, expected):
    device = _blind(holding=holding, travel=travel, rotation=rotation)
    assert device.setting_conflicts() == expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"timer": 300, "timer_long": 3600, "switch_off_warning": 30}, []),
        (
            {"timer": 300, "timer_long": 120, "switch_off_warning": 30},
            ["cleaning_time_shorter_than_run_on_time"],
        ),
        (
            {"timer": 300, "timer_long": 3600, "switch_off_warning": 400},
            ["switch_off_warning_longer_than_run_on_time"],
        ),
        ({"timer": 300, "timer_long": 3600, "switch_off_warning": 0}, []),
        ({"timer": None, "timer_long": 3600, "switch_off_warning": 30}, []),
    ],
)
def test_switch_conflicts_in_the_stored_values(values, expected) -> None:
    assert _switch(**values).setting_conflicts() == expected


def test_a_key_limits_the_result_to_its_own_combinations() -> None:
    device = _blind(holding=20, travel=25.5, rotation=30)
    assert device.setting_conflicts("maxHoldingTime") == [
        "holding_time_shorter_than_travel_time"
    ]
    assert device.setting_conflicts("rotationTime") == [
        "travel_time_shorter_than_rotation_time"
    ]
    assert device.setting_conflicts("verticalMovementTime") == [
        "holding_time_shorter_than_travel_time",
        "travel_time_shorter_than_rotation_time",
    ]
    assert device.setting_conflicts("ledColor") == []


# ── a planned write ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("maxHoldingTime", 33, []),
        ("maxHoldingTime", 25, ["holding_time_shorter_than_travel_time"]),
        ("maxHoldingTime", 25.51, []),  # equal is fine
        ("verticalMovementTime", 30, ["holding_time_shorter_than_travel_time"]),
        ("verticalMovementTime", 20, []),
        ("rotationTime", 1.5, []),
        ("rotationTime", 26, ["travel_time_shorter_than_rotation_time"]),
        ("ledColor", 1, []),
    ],
)
def test_a_planned_write_is_checked_against_the_stored_values(
    key, value, expected
) -> None:
    device = _blind(holding=29, travel=25.51, rotation=0)
    assert device.setting_conflicts(key, value=value) == expected


def test_a_write_that_keeps_or_reduces_an_existing_conflict_is_allowed() -> None:
    device = _blind(holding=20, travel=25.51, rotation=0)  # already wrong
    # Raising the holding time reduces the gap: allowed, even though still short.
    assert device.setting_conflicts("maxHoldingTime", value=22) == []
    # Lowering the travel time reduces it too.
    assert device.setting_conflicts("verticalMovementTime", value=24) == []
    # Making it worse is refused.
    assert device.setting_conflicts("maxHoldingTime", value=18) == [
        "holding_time_shorter_than_travel_time"
    ]
    assert device.setting_conflicts("verticalMovementTime", value=28) == [
        "holding_time_shorter_than_travel_time"
    ]
    # The same value keeps the gap: allowed.
    assert device.setting_conflicts("maxHoldingTime", value=20) == []


def test_a_planned_write_never_compares_an_unknown_value() -> None:
    # The other setting of the combination is unknown: nothing to compare with.
    device = _blind(holding=29, travel=None, rotation=None)
    assert device.setting_conflicts("maxHoldingTime", value=5) == []
    assert device.setting_conflicts("rotationTime", value=40) == []
    # With the travel time known, the same writes are checked.
    device = _blind(holding=None, travel=25.51, rotation=None)
    assert device.setting_conflicts("maxHoldingTime", value=5) == [
        "holding_time_shorter_than_travel_time"
    ]
    assert device.setting_conflicts("rotationTime", value=40) == [
        "travel_time_shorter_than_rotation_time"
    ]


def test_a_planned_switch_write_is_checked_too() -> None:
    device = _switch(timer=300, timer_long=3600, switch_off_warning=30)
    assert device.setting_conflicts("timerLong", value=120) == [
        "cleaning_time_shorter_than_run_on_time"
    ]
    assert device.setting_conflicts("timer", value=7200) == [
        "cleaning_time_shorter_than_run_on_time"
    ]
    assert device.setting_conflicts("switchOffWarning", value=400) == [
        "switch_off_warning_longer_than_run_on_time"
    ]
    assert device.setting_conflicts("timer", value=20) == [
        "switch_off_warning_longer_than_run_on_time"
    ]
    assert device.setting_conflicts("timer", value=600) == []


# ── coordinator ────────────────────────────────────────────────────────────


def _document(**values: object) -> dict:
    return {
        "header": {"httpStatus": 200},
        "configuration": {
            "parameters": [
                {"key": key, "value": value} for key, value in values.items()
            ]
        },
    }


@pytest.fixture
def blind(connected_coordinator):
    device = connected_coordinator.devices["DEV1"] = _blind(
        holding=29, travel=25.51, rotation=0
    )
    return device


async def test_a_conflicting_write_is_refused_before_anything_is_sent(
    radio, connected_coordinator, blind
) -> None:
    with pytest.raises(ServiceValidationError) as raised:
        await connected_coordinator.async_set_device_parameter(
            "DEV1", "maxHoldingTime", 20, step=1, minimum=0
        )

    assert (
        raised.value.translation_key
        == "setting_conflict_holding_time_shorter_than_travel_time"
    )
    assert radio.sent == []
    assert blind.channels[0].max_holding_time == 29


async def test_a_write_without_a_conflict_is_sent(
    radio, connected_coordinator, blind
) -> None:
    radio.response = _document(maxHoldingTime=33.0)

    await connected_coordinator.async_set_device_parameter(
        "DEV1", "maxHoldingTime", 33, step=1, minimum=0
    )

    assert blind.channels[0].max_holding_time == 33.0
    assert radio.sent


async def test_a_write_that_improves_an_existing_conflict_is_sent(
    radio, connected_coordinator, blind
) -> None:
    blind.channels[0].max_holding_time = 20.0  # already shorter than the travel time
    radio.response = _document(maxHoldingTime=22.0)

    await connected_coordinator.async_set_device_parameter(
        "DEV1", "maxHoldingTime", 22, step=1, minimum=0
    )

    assert blind.channels[0].max_holding_time == 22.0


async def test_a_text_setting_is_never_checked(
    radio, connected_coordinator, blind
) -> None:
    radio.response = _document(ledColor="green")

    await connected_coordinator.async_set_device_option("DEV1", "ledColor", "green")

    assert radio.sent
