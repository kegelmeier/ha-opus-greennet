"""EEP alignment with the gateway profiles (``/devices/<id>/profile``).

Covers the profile parser and the capabilities derived from it, the corrected
D2-01 fallback table, cover ``unknown`` reports, the blind blockage
(``lockingMode``) and reference runs (``repositioning``), actuator buttons as
event entities, and the new diagnostic sensors.
"""

from __future__ import annotations

from time import monotonic as monotonic_now
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet import OpusGreenNetRuntimeData, protocol
from custom_components.opus_greennet.api import OpusAuthError, OpusResponseError
from custom_components.opus_greennet.binary_sensor import (
    async_setup_entry as async_setup_binary_sensors,
)
from custom_components.opus_greennet.binary_sensor_descriptions import (
    BINARY_SENSOR_DESCRIPTIONS,
    error_code_problem,
)
from custom_components.opus_greennet.button import (
    REFERENCE_RUNS,
    OpusGreenNetReferenceRunButton,
)
from custom_components.opus_greennet.button import (
    async_setup_entry as async_setup_buttons,
)
from custom_components.opus_greennet.const import (
    BUTTON_KEYS,
    DOMAIN,
    ILLUMINATION_MAX_LUX,
    ROCKER_2_BUTTON_KEYS,
)
from custom_components.opus_greennet.coordinator import (
    CommandDelivery,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import (
    DeviceProfile,
    EnOceanChannel,
    EnOceanDevice,
)
from custom_components.opus_greennet.entity import migrate_output_entities
from custom_components.opus_greennet.event import OpusGreenNetEvent, event_types
from custom_components.opus_greennet.event import (
    async_setup_entry as async_setup_events,
)
from custom_components.opus_greennet.sensor import (
    async_setup_entry as async_setup_sensors,
)
from custom_components.opus_greennet.switch import OpusGreenNetBlockageSwitch
from custom_components.opus_greennet.switch import (
    async_setup_entry as async_setup_switches,
)
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

EAG_ID = "AABB0011"
MODULE = "custom_components.opus_greennet.coordinator"


def _values(*values: str) -> list[dict[str, str]]:
    return [{"value": value, "meaning": value} for value in values]


def _range(minimum: float, maximum: float, step: float = 1, **extra) -> dict:
    return {"range": {"min": minimum, "max": maximum, "step": step, **extra}}


ROCKER_GROUPS = [
    {
        "direction": "from",
        "functions": [
            {"key": "multipleButtons", "values": _values("pressed", "released")}
        ],
    },
    {
        "direction": "from",
        "functions": [
            {"key": key, "values": _values("pressed", "released")}
            for key in BUTTON_KEYS
            if key != "multipleButtons"
        ],
    },
]

# Gateway profile of D2-01-11 + F6-03-01 (OPUS-IQ-DOT 1.21.30).
TWO_CHANNEL_SWITCH_PROFILE = {
    "functionGroups": [
        {
            "title": "Actuator Set Output",
            "direction": "to",
            "functions": [
                {
                    "key": "channel",
                    "values": [_range(0, 1), *_values("all", "input")],
                },
                {"key": "switch", "values": _values("off", "on")},
            ],
        },
        {
            "title": "Actuator Status Response",
            "direction": "from",
            "functions": [
                {"key": "channel", "values": [_range(0, 1), *_values("input")]},
                {"key": "switch", "values": _values("off", "on")},
            ],
        },
        *ROCKER_GROUPS,
    ]
}

# A two-channel dimmer as a profile would declare it.
TWO_CHANNEL_DIMMER_PROFILE = {
    "functionGroups": [
        {
            "direction": "to",
            "functions": [
                {"key": "channel", "values": [_range(0, 1)]},
                {"key": "dimValue", "values": [_range(0, 100, unit="%")]},
                {"key": "rampingMode", "values": _values("switch", "rampingTime1")},
            ],
        },
        {
            "direction": "from",
            "functions": [
                {"key": "channel", "values": [_range(0, 1)]},
                {"key": "dimValue", "values": [_range(0, 100, unit="%")]},
                {"key": "localControl", "values": _values("off", "on")},
            ],
        },
    ]
}

# Gateway profile of D2-05-02 (without the rocker groups).
BLIND_PROFILE = {
    "functionGroups": [
        {
            "title": "Go to Position and Angle",
            "direction": "to",
            "functions": [
                {"key": "angle", "values": [_range(0, 100, unit="%")]},
                {
                    "key": "lockingMode",
                    "values": _values("block", "noChange", "unblock"),
                },
                {"key": "position", "values": [_range(0, 100, unit="%")]},
                {
                    "key": "repositioning",
                    "values": _values("direct", "firstDown", "firstUp"),
                },
            ],
        },
        {
            "title": "Reply Position and Angle",
            "direction": "from",
            "functions": [
                {"key": "angle", "values": [_range(0, 100, unit="%")]},
                {"key": "lockingMode", "values": _values("block", "unblock")},
                {"key": "position", "values": [_range(0, 100, unit="%")]},
            ],
        },
    ]
}

# Gateway profile of A5-07-03.
PRESENCE_PROFILE = {
    "functionGroups": [
        {
            "direction": "from",
            "functions": [
                {
                    "key": "errorCode",
                    "values": [_range(251, 255), *_values("noError")],
                },
                {
                    "key": "illumination",
                    "values": [_range(0, 1000, unit="lx"), *_values("overRange")],
                },
                {"key": "motionDetected", "values": _values("false", "true")},
                {"key": "supplyVoltage", "values": [_range(0, 5, 0.02, unit="V")]},
            ],
        }
    ]
}


def _profile(document: dict) -> DeviceProfile:
    profile = protocol.device_profile({"profile": document})
    assert profile is not None
    return profile


def _device(*eeps: str, profile: dict | None = None) -> EnOceanDevice:
    device = EnOceanDevice("DEV1", "Device", [{"eep": eep} for eep in eeps])
    if profile is not None:
        device.profile = _profile(profile)
    return device


def _entry(coordinator: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="entry",
        data={"eag_id": EAG_ID},
        runtime_data=OpusGreenNetRuntimeData(
            coordinator=coordinator, gateway_device_id="gateway"
        ),
        async_on_unload=MagicMock(),
    )


async def _platform_entities(setup, device: EnOceanDevice) -> list:
    coordinator = MagicMock(eag_id=EAG_ID, devices={device.device_id: device})
    coordinator.available = True
    added: list = []
    with (
        patch(f"{setup.__module__}.async_dispatcher_connect", return_value=MagicMock()),
        patch(
            f"{setup.__module__}.migrate_legacy_entity_suffix", MagicMock(), create=True
        ),
        patch(f"{setup.__module__}.migrate_output_entities", MagicMock(), create=True),
    ):
        await setup(MagicMock(), _entry(coordinator), added.extend)
    # Gateway-level entities do not belong to the device under test.
    return [
        entity
        for entity in added
        if not entity.unique_id.endswith(("_learn_mode", "_learn_mode_start"))
    ]


# ── Profile parser ───────────────────────────────────────────────────────────


def test_gateway_profile_of_a_two_channel_switch() -> None:
    profile = _profile(TWO_CHANNEL_SWITCH_PROFILE)
    assert profile.to_keys == {"channel", "switch"}
    assert {"channel", "switch", *BUTTON_KEYS} == profile.from_keys
    assert profile.channels == 2
    assert profile.output == "switch"


def test_gateway_profile_ranges_and_dimmer_output() -> None:
    assert _profile(TWO_CHANNEL_DIMMER_PROFILE).output == "light"
    presence = _profile(PRESENCE_PROFILE)
    assert presence.output is None
    assert presence.channels is None
    assert presence.from_maxima["illumination"] == 1000
    assert presence.from_maxima["supplyVoltage"] == 5


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"profile": None},
        {"profile": {"functionGroups": "x"}},
        {"profile": {"functionGroups": [{"direction": "sideways", "functions": []}]}},
        {"profile": {"functionGroups": [{"direction": "to", "functions": [{}]}]}},
    ],
)
def test_unusable_profile_documents(document: dict) -> None:
    assert protocol.device_profile(document) is None


def test_profile_tolerates_malformed_ranges_and_both_direction() -> None:
    profile = protocol.device_profile(
        {
            "profile": {
                "functionGroups": [
                    {
                        "direction": "both",
                        "functions": [
                            {"key": "channel", "values": [_range(0, 1.5)]},
                            {"key": "level", "values": [{"range": "x"}, _range(0, -1)]},
                            {"key": "", "values": []},
                        ],
                    }
                ]
            }
        }
    )
    assert profile is not None
    assert profile.to_keys == profile.from_keys == {"channel", "level"}
    assert profile.channels is None
    assert dict(profile.from_maxima) == {}


# ── Capabilities: profile first, EEP table as fallback ──────────────────────


@pytest.mark.parametrize(
    ("eep", "profile", "entity_type", "dimmable", "channels"),
    [
        ("D2-01-12", None, "switch", False, 2),
        ("D2-01-12", TWO_CHANNEL_DIMMER_PROFILE, "light", True, 2),
        ("D2-01-06", None, "switch", False, 1),
        ("D2-01-04", None, "light", True, 1),
        ("D2-01-11", TWO_CHANNEL_SWITCH_PROFILE, "switch", False, 2),
        ("D2-01-01", {"functionGroups": ROCKER_GROUPS}, "switch", False, 1),
        ("A5-38-08", None, "light", True, 1),
    ],
)
def test_output_type_and_channels(eep, profile, entity_type, dimmable, channels):
    device = _device(eep, profile=profile)
    assert device.entity_type == entity_type
    assert device.is_dimmable is dimmable
    assert device.channel_count == channels


def test_single_channel_d2_01_profile_without_channel_key() -> None:
    profile = {
        "functionGroups": [
            {"direction": "to", "functions": [{"key": "dimValue", "values": []}]}
        ]
    }
    device = _device("D2-01-0F", profile=profile)
    assert (device.entity_type, device.is_dimmable, device.channel_count) == (
        "light",
        True,
        1,
    )


def test_cover_profile_does_not_change_the_cover_type() -> None:
    device = _device("D2-05-02", profile=BLIND_PROFILE)
    assert device.entity_type == "cover"
    assert device.supports_locking_mode
    assert device.supports_repositioning


@pytest.mark.parametrize(
    ("eep", "profile", "local_control"),
    [
        ("D2-01-01", None, True),
        ("D2-01-0D", None, True),
        ("D2-01-11", None, False),
        ("D2-01-07", None, False),
        ("D2-01-12", TWO_CHANNEL_DIMMER_PROFILE, True),
        ("D2-01-11", TWO_CHANNEL_SWITCH_PROFILE, False),
    ],
)
def test_local_control_capability(eep, profile, local_control) -> None:
    assert _device(eep, profile=profile).has_local_control is local_control


@pytest.mark.parametrize(
    ("eep", "locking", "repositioning"),
    [
        ("D2-05-00", True, True),
        ("D2-05-02", True, True),
        ("D2-05-01", False, False),
        ("D2-01-01", False, False),
    ],
)
def test_blind_capabilities_without_profile(eep, locking, repositioning) -> None:
    device = _device(eep)
    assert device.supports_locking_mode is locking
    assert device.supports_repositioning is repositioning


@pytest.mark.parametrize(
    ("eep", "profile", "illuminance", "error_code"),
    [
        ("A5-07-03", None, True, True),
        ("A5-07-01", None, False, True),
        ("A5-07-03", PRESENCE_PROFILE, True, True),
        ("A5-07-01", {"functionGroups": []}, False, False),
        ("D2-01-01", None, False, False),
    ],
)
def test_presence_capabilities(eep, profile, illuminance, error_code) -> None:
    device = _device(eep)
    if profile is not None:
        device.profile = protocol.device_profile({"profile": profile}) or (
            DeviceProfile()
        )
    assert device.reports_illuminance is illuminance
    assert device.reports_error_code is error_code


def test_illuminance_maximum_follows_the_profile() -> None:
    assert _device("A5-07-03").illuminance_maximum == ILLUMINATION_MAX_LUX
    device = _device("A5-07-03")
    device.profile = DeviceProfile(from_maxima={"illumination": 2000.0})
    assert device.illuminance_maximum == 2000.0
    device.profile = DeviceProfile()
    assert device.illuminance_maximum == ILLUMINATION_MAX_LUX


@pytest.mark.parametrize(
    ("eeps", "profile", "secondary", "buttons"),
    [
        (("D2-01-11", "F6-03-01"), None, True, BUTTON_KEYS),
        (("D2-01-11", "F6-03-01"), TWO_CHANNEL_SWITCH_PROFILE, True, BUTTON_KEYS),
        (("D2-05-02", "F6-02-01"), None, True, ROCKER_2_BUTTON_KEYS),
        (("F6-03-01",), None, False, BUTTON_KEYS),
        (("F6-02-02",), None, False, ROCKER_2_BUTTON_KEYS),
        (("A5-04-01", "F6-02-01"), None, False, ROCKER_2_BUTTON_KEYS),
        (("D2-01-11",), None, False, BUTTON_KEYS),
    ],
)
def test_rocker_buttons(eeps, profile, secondary, buttons) -> None:
    device = _device(*eeps, profile=profile)
    assert device.has_secondary_rocker is secondary
    assert device.button_keys == buttons


def test_profile_buttons_limit_the_event_types() -> None:
    device = _device("D2-01-01", "F6-03-01")
    device.profile = DeviceProfile(from_keys=frozenset({"buttonA0", "buttonAI"}))
    assert device.button_keys == ("buttonA0", "buttonAI")


def test_changed_eeps_drop_a_stale_profile() -> None:
    device = _device("D2-01-11", profile=TWO_CHANNEL_SWITCH_PROFILE)
    device.apply_listed_metadata(_device("D2-01-11"))
    assert device.profile is not None
    device.apply_listed_metadata(_device("D2-01-01"))
    assert device.profile is None


# ── Reported values ──────────────────────────────────────────────────────────


def _update(device: EnOceanDevice, *functions: dict) -> EnOceanChannel:
    device.update_from_telegram({"functions": list(functions)})
    return device.channels[0]


def test_unknown_cover_values_keep_the_last_reading() -> None:
    device = _device("D2-05-00")
    channel = _update(
        device, {"key": "position", "value": 40}, {"key": "angle", "value": 20}
    )
    assert (channel.position, channel.angle) == (40, 20)
    # D2-05 reports "unknown" while the blind moves.
    _update(device, {"key": "position", "value": "unknown"})
    _update(device, {"key": "angle", "value": "unknown"})
    assert (channel.position, channel.angle) == (40, 20)
    # Other invalid values keep the last reading as well.
    _update(device, {"key": "position", "value": 30}, {"key": "angle", "value": 5})
    _update(device, {"key": "position", "value": "noChange"})
    _update(device, {"key": "angle", "value": "x"})
    assert (channel.position, channel.angle) == (30, 5)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("block", "block"), ("unblock", "unblock"), ("alarm", "alarm")],
)
def test_locking_mode_reports(value, expected) -> None:
    device = _device("D2-05-02")
    assert _update(device, {"key": "lockingMode", "value": value}).locking_mode == (
        expected
    )
    # A command's "noChange" keeps the reported mode.
    assert _update(device, {"key": "lockingMode", "value": "noChange"}).locking_mode
    assert device.channels[0].locking_mode == expected


def test_illumination_over_range() -> None:
    device = _device("A5-07-03")
    channel = _update(device, {"key": "illumination", "value": "overRange"})
    assert channel.illuminance == ILLUMINATION_MAX_LUX
    assert channel.illuminance_over_range is True
    _update(device, {"key": "illumination", "value": "bad"})
    assert (channel.illuminance, channel.illuminance_over_range) == (1000.0, True)
    _update(device, {"key": "illumination", "value": 8})
    assert (channel.illuminance, channel.illuminance_over_range) == (8.0, False)
    _update(device, {"key": "illumination", "value": "notAvailable"})
    assert (channel.illuminance, channel.illuminance_over_range) == (None, None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("noError", "noError"), (252, "252"), ("255", "255"), (250, None), ("x", None)],
)
def test_presence_error_codes(value, expected) -> None:
    device = _device("A5-07-03")
    assert _update(device, {"key": "errorCode", "value": value}).error_code == (
        expected
    )


def test_error_code_of_other_devices_is_ignored() -> None:
    device = _device("D2-01-01")
    assert _update(device, {"key": "errorCode", "value": 252}).error_code is None


@pytest.mark.parametrize(
    ("eep", "value", "expected"),
    [
        ("F6-10-00", "up", "tilted"),
        ("D2-03-10", "tilt", "tilted"),
        ("D2-06-40", "tilted", "tilted"),
        ("D2-06-40", "unkown", None),
        ("F6-10-00", "open", "open"),
        ("F6-10-00", 3, None),
    ],
)
def test_handle_values_of_every_profile(eep, value, expected) -> None:
    device = _device(eep)
    assert _update(device, {"key": "handle", "value": value}).handle_state == (expected)


def test_four_rocker_buttons_are_parsed() -> None:
    device = _device("D2-01-11", "F6-03-01")
    channel = _update(device, {"key": "buttonD0", "value": "pressed"})
    assert (channel.last_button, channel.last_button_action) == (
        "buttonD0",
        "pressed",
    )


def test_locking_mode_confirms_a_command() -> None:
    assert protocol.confirmed_fields_by_channel(
        [{"key": "lockingMode", "value": "block"}]
    ) == {0: {"lockingMode"}}
    assert (
        protocol.confirmed_fields_by_channel(
            [{"key": "lockingMode", "value": "noChange"}]
        )
        == {}
    )


def test_locking_mode_estimate_respects_newer_reports() -> None:
    device = _device("D2-05-02")
    snapshot = device.snapshot_channel_state(0)
    assert device.apply_locking_mode_estimate(snapshot, "block")
    assert device.channels[0].locking_mode == "block"
    snapshot = device.snapshot_channel_state(0)
    _update(device, {"key": "lockingMode", "value": "unblock"})
    assert not device.apply_locking_mode_estimate(snapshot, "block")
    assert device.channels[0].locking_mode == "unblock"


# ── Coordinator: profile reads, outbound telegrams, commands ────────────────


def _listed(device_id: str, *eeps: str) -> dict:
    return device_object(device_id, list(eeps))


async def test_profiles_are_read_once_per_eep_combination() -> None:
    coord = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)

    async def answer(path: str, **_: object) -> dict:
        if path == "/devices/BROKEN/profile":
            raise OpusResponseError("not found")
        return {"profile": TWO_CHANNEL_SWITCH_PROFILE}

    coord.client.async_get = AsyncMock(side_effect=answer)
    with patch(f"{MODULE}.async_dispatcher_send"):
        for device_id, eeps in (
            ("A", ("D2-01-11", "F6-03-01")),
            ("B", ("D2-01-11", "F6-03-01")),
            ("BROKEN", ("D2-01-12",)),
            ("EMPTY", ()),
        ):
            coord._apply_device_object(  # noqa: SLF001
                _listed(device_id, *eeps), authoritative=True
            )
        await coord._async_load_profiles()  # noqa: SLF001
        # Already known combinations are not read again.
        await coord._async_load_profiles()  # noqa: SLF001
        # A device paired later takes the profile of the same EEPs.
        coord._apply_device_object(  # noqa: SLF001
            _listed("C", "D2-01-11", "F6-03-01"), authoritative=True
        )
    assert coord.client.async_get.await_count == 2
    assert coord.devices["A"].profile is coord.devices["B"].profile
    assert coord.devices["C"].profile is coord.devices["A"].profile
    assert coord.devices["A"].channel_count == 2
    assert coord.devices["BROKEN"].profile is None
    assert coord.profiles[("D2-01-12",)] is None


async def test_profile_assigned_when_a_device_gains_known_eeps() -> None:
    coord = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    profile = _profile(TWO_CHANNEL_SWITCH_PROFILE)
    coord.profiles[("D2-01-11",)] = profile
    with patch(f"{MODULE}.async_dispatcher_send"):
        coord._apply_device_object(  # noqa: SLF001
            {"deviceId": "LATE", "eeps": []}, authoritative=True
        )
        coord._apply_device_object(  # noqa: SLF001
            _listed("LATE", "D2-01-11"), authoritative=True
        )
    assert coord.devices["LATE"].profile is profile


async def test_rejected_credentials_while_reading_profiles_fail_setup() -> None:
    coord = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    coord.client.async_get = AsyncMock(side_effect=OpusAuthError("status 401"))
    with patch(f"{MODULE}.async_dispatcher_send"):
        coord._apply_device_object(  # noqa: SLF001
            _listed("A", "D2-01-11"), authoritative=True
        )
    with pytest.raises(OpusAuthError):
        await coord._async_load_profiles()  # noqa: SLF001


def _outbound(coord, *functions: dict) -> None:
    coord._handle_telegram(  # noqa: SLF001
        telegram("DEV1", list(functions), direction="to")["telegram"]
    )


def test_foreign_set_parameters_command_keeps_configured_times() -> None:
    coord = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    with (
        patch(f"{MODULE}.async_dispatcher_send"),
        patch(f"{MODULE}.async_call_later"),
    ):
        coord._apply_device_object(  # noqa: SLF001
            _listed("DEV1", "D2-05-00"), authoritative=True
        )
        channel = coord.devices["DEV1"].get_or_create_channel(0)
        channel.rotation_time, channel.vertical_movement_time = 1.2, 30.0
        # "Set parameters" uses milliseconds; the device list uses seconds.
        _outbound(
            coord,
            {"key": "rotationTime", "value": 1200},
            {"key": "verticalMovementTime", "value": 30000},
            {"key": "lockingMode", "value": "block"},
        )
        assert (channel.rotation_time, channel.vertical_movement_time) == (1.2, 30.0)
        assert channel.locking_mode == "block"
        # Zero has no unit: no rotation, as before.
        _outbound(coord, {"key": "rotationTime", "value": 0})
    assert channel.rotation_time == 0


@pytest.mark.parametrize(
    ("current", "repositioning", "travel", "movement"),
    [
        (50, "firstUp", 40.0, "opening"),  # 50 % up, then 50 % down
        (50, "firstDown", 40.0, "closing"),
        (None, "firstUp", 60.0, "opening"),  # full run, then 50 % down
        (50, "direct", 0.0, None),
    ],
)
def test_reference_run_travel_estimate(current, repositioning, travel, movement):
    coord = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    with (
        patch(f"{MODULE}.async_dispatcher_send"),
        patch(f"{MODULE}.async_call_later") as call_later,
    ):
        coord._apply_device_object(  # noqa: SLF001
            _listed("DEV1", "D2-05-02"), authoritative=True
        )
        device = coord.devices["DEV1"]
        channel = device.get_or_create_channel(0)
        channel.position, channel.vertical_movement_time = current, 40.0
        started = coord._start_cover_commands(  # noqa: SLF001
            device,
            [
                {"key": "position", "value": "50"},
                {"key": "repositioning", "value": repositioning},
            ],
        )
    if movement is None:
        assert started == {}
        call_later.assert_not_called()
    else:
        assert started[0].travel == pytest.approx(travel)
        assert channel.movement == movement


async def test_cover_commands_with_repositioning_and_locking(coordinator) -> None:
    coordinator.devices["DEV1"] = _device("D2-05-02")
    await coordinator.async_set_cover_position("DEV1", 30, 0, repositioning="firstUp")
    await coordinator.async_set_cover_position("DEV1", 30)
    await coordinator.async_set_cover_locking_mode("DEV1", "block")
    assert [
        call.args[1] for call in coordinator.async_send_command.await_args_list
    ] == [
        [
            {"key": "position", "value": "30"},
            {"key": "repositioning", "value": "firstUp"},
        ],
        [{"key": "position", "value": "30"}],
        [{"key": "lockingMode", "value": "block"}],
    ]


# ── Entities ─────────────────────────────────────────────────────────────────


def _blockage(device: EnOceanDevice, delivery=CommandDelivery.SENT):
    coordinator = MagicMock(available=True)
    coordinator.async_set_cover_locking_mode = AsyncMock(return_value=delivery)
    entity = OpusGreenNetBlockageSwitch(coordinator, EAG_ID, "gateway", device)
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


async def test_blockage_switch_states_and_commands() -> None:
    device = _device("D2-05-02")
    entity, coordinator = _blockage(device)
    assert entity.unique_id == f"{EAG_ID}_DEV1_blockage"
    assert entity.translation_key == "blockage"
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.is_on is None
    assert entity.extra_state_attributes == {}
    for mode, expected in (("block", True), ("alarm", True), ("unblock", False)):
        device.get_or_create_channel(0).locking_mode = mode
        assert entity.is_on is expected
        assert entity.extra_state_attributes == {"locking_mode": mode}
    await entity.async_turn_on()
    assert device.channels[0].locking_mode == "block"
    await entity.async_turn_off()
    assert device.channels[0].locking_mode == "unblock"
    assert [
        call.args for call in coordinator.async_set_cover_locking_mode.await_args_list
    ] == [
        ("DEV1", "block", 0),
        ("DEV1", "unblock", 0),
    ]
    assert entity.async_write_ha_state.call_count == 2


async def test_deferred_blockage_command_keeps_the_reported_mode() -> None:
    device = _device("D2-05-02")
    entity, _ = _blockage(device, CommandDelivery.DEFERRED)
    await entity.async_turn_on()
    assert device.channels.get(0) is None
    entity.async_write_ha_state.assert_not_called()


async def _device_switches(device: EnOceanDevice) -> list:
    """The switches of a device."""
    return list(await _platform_entities(async_setup_switches, device))


async def test_switch_platform_adds_blockage_only_for_locking_blinds() -> None:
    added = await _device_switches(_device("D2-05-02"))
    assert [entity.unique_id for entity in added] == [f"{EAG_ID}_DEV1_blockage"]
    assert await _device_switches(_device("D2-05-01")) == []
    two_channel = await _device_switches(
        _device("D2-05-02", profile=TWO_CHANNEL_SWITCH_PROFILE)
    )
    # A blind profile without a locking mode: no blockage switch.
    assert two_channel == []


async def test_reference_run_buttons() -> None:
    device = _device("D2-05-02")
    added = [
        entity
        for entity in await _platform_entities(async_setup_buttons, device)
        if "reference_run" in entity.unique_id
    ]
    assert [entity.unique_id for entity in added] == [
        f"{EAG_ID}_DEV1_reference_run_up",
        f"{EAG_ID}_DEV1_reference_run_down",
    ]
    assert all(e.entity_category is EntityCategory.DIAGNOSTIC for e in added)
    assert not [
        entity
        for entity in await _platform_entities(async_setup_buttons, _device("D2-05-01"))
        if "reference_run" in entity.unique_id
    ]

    coordinator = MagicMock(available=True)
    coordinator.async_set_cover_position = AsyncMock()
    up, down = (
        OpusGreenNetReferenceRunButton(coordinator, EAG_ID, "gw", device, run)
        for run in REFERENCE_RUNS
    )
    await up.async_press()  # unknown position: stay at the end position
    device.get_or_create_channel(0).position = 30
    await down.async_press()  # known position: return to it
    assert [
        (call.args, call.kwargs)
        for call in coordinator.async_set_cover_position.await_args_list
    ] == [
        (("DEV1", 0, 0), {"repositioning": "firstUp"}),
        (("DEV1", 30, 0), {"repositioning": "firstDown"}),
    ]


def test_multi_channel_unique_ids_of_blind_extras() -> None:
    device = _device("D2-05-02")
    # A channel index gets a suffix when a device has more than one channel.
    with patch.object(EnOceanDevice, "channel_count", 2):
        blockage = OpusGreenNetBlockageSwitch(MagicMock(), EAG_ID, "gw", device, 1)
        button = OpusGreenNetReferenceRunButton(
            MagicMock(), EAG_ID, "gw", device, REFERENCE_RUNS[0], 1
        )
    assert blockage.unique_id == f"{EAG_ID}_DEV1_blockage_ch1"
    assert button.unique_id == f"{EAG_ID}_DEV1_reference_run_up_ch1"


async def test_actuator_buttons_get_their_own_event_entity() -> None:
    device = _device("D2-01-11", "F6-03-01")
    added = await _platform_entities(async_setup_events, device)
    assert len(added) == 1
    entity = added[0]
    assert entity.unique_id == f"{EAG_ID}_DEV1_buttons"
    assert entity.translation_key == "buttons"
    assert entity.event_types == event_types(BUTTON_KEYS)
    assert "buttonD0_pressed" in entity.event_types
    # A plain rocker keeps its unique ID and the device name.
    rocker = OpusGreenNetEvent(MagicMock(), EAG_ID, "gw", _device("F6-03-01"))
    assert rocker.unique_id == f"{EAG_ID}_DEV1"
    assert rocker.translation_key is None
    # Actuators without a rocker profile get no event entity.
    assert await _platform_entities(async_setup_events, _device("D2-01-11")) == []


def test_actuator_button_press_fires_its_event() -> None:
    device = _device("D2-05-02", "F6-03-01")
    entity = OpusGreenNetEvent(MagicMock(), EAG_ID, "gw", device)
    entity._trigger_event = MagicMock()  # noqa: SLF001
    entity.async_write_ha_state = MagicMock()
    device.update_from_telegram(
        {"functions": [{"key": "buttonC0", "value": "pressed"}]}
    )
    entity._handle_state_update(device)  # noqa: SLF001
    entity._trigger_event.assert_called_once_with(  # noqa: SLF001
        "buttonC0_pressed", {"button": "buttonC0", "action": "pressed"}
    )


def test_two_rocker_profile_ignores_unknown_buttons() -> None:
    device = _device("F6-02-01")
    entity = OpusGreenNetEvent(MagicMock(), EAG_ID, "gw", device)
    entity._trigger_event = MagicMock()  # noqa: SLF001
    device.update_from_telegram(
        {"functions": [{"key": "buttonC0", "value": "pressed"}]}
    )
    entity._handle_state_update(device)  # noqa: SLF001
    entity._trigger_event.assert_not_called()  # noqa: SLF001


async def test_local_control_and_sensor_error_binary_sensors() -> None:
    switch = await _platform_entities(async_setup_binary_sensors, _device("D2-01-01"))
    assert [entity.unique_id for entity in switch] == [f"{EAG_ID}_DEV1_local_control"]
    local = switch[0]
    assert local.entity_category is EntityCategory.DIAGNOSTIC
    assert local.entity_registry_enabled_default is False
    assert local.is_on is None
    local._device.get_or_create_channel(0).local_control = True  # noqa: SLF001
    assert local.is_on is True
    assert (
        await _platform_entities(async_setup_binary_sensors, _device("D2-01-11")) == []
    )

    presence = await _platform_entities(async_setup_binary_sensors, _device("A5-07-03"))
    error = next(e for e in presence if e.unique_id.endswith("_error_code"))
    assert error.translation_key == "sensor_error"
    assert error.device_class is BinarySensorDeviceClass.PROBLEM
    assert error.entity_category is EntityCategory.DIAGNOSTIC


@pytest.mark.parametrize(
    ("code", "expected"), [(None, False), ("noError", False), ("252", True)]
)
def test_sensor_error_state(code, expected) -> None:
    device = _device("A5-07-03")
    if code is not None:
        device.get_or_create_channel(0).error_code = code
    assert error_code_problem(device) is expected
    assert {d.key for d in BINARY_SENSOR_DESCRIPTIONS} >= {
        "local_control",
        "error_code",
    }


async def test_presence_illuminance_only_where_reported() -> None:
    sms_03 = await _platform_entities(async_setup_sensors, _device("A5-07-03"))
    sms_01 = await _platform_entities(async_setup_sensors, _device("A5-07-01"))
    suffixes = {e.unique_id.removeprefix(f"{EAG_ID}_DEV1_") for e in sms_03}
    assert "illuminance" in suffixes
    assert "illuminance" not in {
        e.unique_id.removeprefix(f"{EAG_ID}_DEV1_") for e in sms_01
    }
    illuminance = next(e for e in sms_03 if e.unique_id.endswith("_illuminance"))
    assert illuminance.extra_state_attributes is None
    illuminance._device.update_from_telegram(  # noqa: SLF001
        {"functions": [{"key": "illumination", "value": "overRange"}]}
    )
    assert illuminance.native_value == ILLUMINATION_MAX_LUX
    assert illuminance.extra_state_attributes == {"over_range": True}
    voltage = next(e for e in sms_03 if e.unique_id.endswith("_supply_voltage"))
    assert voltage.extra_state_attributes is None


@pytest.mark.parametrize("eep", ["D2-06-40", "F6-10-00", "D2-03-10"])
async def test_window_handles_get_a_diagnostic_battery_sensor(eep: str) -> None:
    entities = await _platform_entities(async_setup_sensors, _device(eep))
    battery = next(e for e in entities if e.unique_id.endswith("_battery_level"))
    assert battery.entity_category is EntityCategory.DIAGNOSTIC
    battery._device.update_from_telegram(  # noqa: SLF001
        {"functions": [{"key": "batteryLevel", "value": "100%"}]}
    )
    assert battery.native_value == 100


@pytest.mark.parametrize("eep", ["D2-01-03", "D2-01-11", "D2-05-02"])
async def test_mains_actuators_get_no_battery_sensor(eep: str) -> None:
    entities = await _platform_entities(async_setup_sensors, _device(eep))
    assert not [e for e in entities if e.unique_id.endswith("_battery_level")]


# ── Registry migration of switch and light entities ─────────────────────────


def _register(registry, domain: str, unique_id: str) -> str:
    return registry.async_get_or_create(domain, DOMAIN, unique_id).entity_id


async def test_single_channel_actuator_gets_its_plain_unique_id(
    hass: HomeAssistant,
) -> None:
    registry = er.async_get(hass)
    base = f"{EAG_ID}_DEV1"
    channel_zero = _register(registry, "switch", f"{base}_ch0")
    channel_one = _register(registry, "switch", f"{base}_ch1")
    light = _register(registry, "light", f"{base}_ch0")
    # EEP fallback: rename only, never delete settings.
    migrate_output_entities(registry, "switch", EAG_ID, _device("D2-01-06"))
    assert registry.async_get(channel_zero).unique_id == base
    assert registry.async_get(channel_one) is not None
    assert registry.async_get(light) is not None


async def test_profile_confirmed_outputs_remove_stale_entities(
    hass: HomeAssistant,
) -> None:
    registry = er.async_get(hass)
    base = f"{EAG_ID}_DEV1"
    kept = _register(registry, "switch", base)
    duplicate_zero = _register(registry, "switch", f"{base}_ch0")
    stale_channel = _register(registry, "switch", f"{base}_ch3")
    stale_light = _register(registry, "light", f"{base}_ch1")
    device = _device("D2-01-06")
    device.profile = DeviceProfile(to_keys=frozenset({"switch"}))
    migrate_output_entities(registry, "switch", EAG_ID, device)
    assert registry.async_get(kept) is not None
    assert registry.async_get(duplicate_zero) is None
    assert registry.async_get(stale_channel) is None
    assert registry.async_get(stale_light) is None


async def test_two_channel_profile_keeps_its_channels(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    base = f"{EAG_ID}_DEV1"
    channels = [_register(registry, "light", f"{base}_ch{n}") for n in range(4)]
    device = _device("D2-01-12", profile=TWO_CHANNEL_DIMMER_PROFILE)
    migrate_output_entities(registry, "light", EAG_ID, device)
    assert [registry.async_get(entity_id) is not None for entity_id in channels] == [
        True,
        True,
        False,
        False,
    ]


# ── End to end against the fake gateway ─────────────────────────────────────


async def test_gateway_profile_decides_dimmer_and_channels(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object("DIMMER", "D2-01-12"),
        device_object("SWITCH", "D2-01-12"),
    ]
    # Both devices share their EEPs, so the gateway is asked once.
    gateway.profiles = {"DIMMER": TWO_CHANNEL_DIMMER_PROFILE}
    entry = await setup_bridge(hass, gateway)
    for device_id in ("DIMMER", "SWITCH"):
        for channel in (0, 1):
            await wait_for_entity(hass, "light", f"{EAG_ID}_{device_id}_ch{channel}")
    profile_reads = [
        path
        for method, path, _ in gateway.requests
        if method == "GET" and path.endswith("/profile")
    ]
    assert profile_reads == ["/devices/DIMMER/profile"]
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["devices"][0]["capabilities"] == {
        "source": "gateway profile",
        "entity_type": "light",
        "channel_count": 2,
        "dimmable": True,
    }


async def test_without_gateway_profile_the_eep_table_decides(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("SWITCH", "D2-01-12")]
    entry = await setup_bridge(hass, gateway)
    await wait_for_entity(hass, "switch", f"{EAG_ID}_SWITCH_ch1")
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["devices"][0]["capabilities"]["source"] == "EEP table"


async def test_blind_blockage_and_set_cover_position_action(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object(
            "BLIND",
            ["D2-05-02", "F6-03-01"],
            states=[
                {"key": "position", "value": 20.0},
                {"key": "angle", "value": "unknown"},
                {"key": "lockingMode", "value": "unblock"},
            ],
        ),
        device_object("PLAIN", "D2-05-01"),
        device_object("SWITCH", "D2-01-01"),
    ]
    # The blockage switch is disabled by default; enable it beforehand.
    _register(er.async_get(hass), "switch", f"{EAG_ID}_BLIND_blockage")
    await setup_bridge(hass, gateway)
    blockage = await wait_for_entity(hass, "switch", f"{EAG_ID}_BLIND_blockage")
    assert hass.states.get(blockage).state == "off"
    assert (
        er.async_get(hass).async_get(blockage).entity_category
        is EntityCategory.DIAGNOSTIC
    )
    buttons = await wait_for_entity(hass, "event", f"{EAG_ID}_BLIND_buttons")
    buttons_entry = er.async_get(hass).async_get(buttons)
    assert buttons_entry.disabled_by is None
    assert buttons_entry.hidden_by is er.RegistryEntryHider.INTEGRATION
    await wait_for_entity(hass, "button", f"{EAG_ID}_BLIND_reference_run_up")

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": blockage}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN,
        "set_cover_position",
        {"device_id": "BLIND", "position": 70, "repositioning": "first_up"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        "set_cover_position",
        {"device_id": "PLAIN", "position": 100},
        blocking=True,
    )
    puts = [
        (path, body["state"]["functions"])
        for method, path, body in gateway.requests
        if method == "PUT"
    ]
    assert puts == [
        ("/devices/BLIND/state", [{"key": "lockingMode", "value": "block"}]),
        (
            "/devices/BLIND/state",
            [
                {"key": "position", "value": "30"},
                {"key": "repositioning", "value": "firstUp"},
            ],
        ),
        ("/devices/PLAIN/state", [{"key": "position", "value": "0"}]),
    ]
    await wait_until(lambda: hass.states.get(blockage).state == "on")

    for data in (
        {"device_id": "SWITCH", "position": 10},
        {"device_id": "PLAIN", "position": 10, "repositioning": "first_down"},
        {"device_id": "BLIND", "position": 10, "channel": 1},
    ):
        with pytest.raises(ServiceValidationError) as raised:
            await hass.services.async_call(
                DOMAIN, "set_cover_position", data, blocking=True
            )
        assert raised.value.translation_key == "repositioning_not_supported"
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN,
            "set_cover_position",
            {"device_id": "NOPE", "position": 10},
            blocking=True,
        )
    assert raised.value.translation_key == "device_not_found"


async def test_f6_10_00_tilted_handle_reaches_the_entities(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("HANDLE", ["F6-10-00", "D2-03-10"])]
    await setup_bridge(hass, gateway)
    state = await wait_for_entity(hass, "sensor", f"{EAG_ID}_HANDLE_handle_state")
    tilt = await wait_for_entity(hass, "binary_sensor", f"{EAG_ID}_HANDLE_handle_tilt")
    await gateway.push(telegram("HANDLE", [{"key": "handle", "value": "up"}]))
    await wait_until(lambda: hass.states.get(state).state == "tilted")
    assert hass.states.get(tilt).state == "on"
    await gateway.push(telegram("HANDLE", [{"key": "handle", "value": "unkown"}]))
    await wait_until(lambda: hass.states.get(state).state == "unknown")
    assert hass.states.get(tilt).state == "unknown"


# ── Cover run times ──────────────────────────────────────────────────────────


def _cover_coordinator(position: int | None = 0) -> OpusGreenNetCoordinator:
    coord = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    with patch(f"{MODULE}.async_dispatcher_send"):
        coord._apply_device_object(  # noqa: SLF001
            _listed("DEV1", "D2-05-02"), authoritative=True
        )
    coord.devices["DEV1"].get_or_create_channel(0).position = position
    return coord


def _report_position(coord, value, channel: int | None = None) -> None:
    function: dict = {"key": "position", "value": value}
    if channel is not None:
        function["channel"] = channel
    coord._record_confirmed_feedback("DEV1", [function])  # noqa: SLF001


def test_run_time_from_command_to_end_report() -> None:
    coord = _cover_coordinator(position=0)
    device = coord.devices["DEV1"]
    coord._start_cover_runs(  # noqa: SLF001
        device, [{"key": "position", "value": "100"}], monotonic_now() - 25.4
    )
    _report_position(coord, 50)  # intermediate report: keeps timing
    assert coord.last_cover_runs == {}
    _report_position(coord, "unknown")
    _report_position(coord, 99)  # within the tolerance of the target
    run = coord.last_cover_runs[("DEV1", 0)]
    assert run.seconds == pytest.approx(25.4, abs=0.5)
    assert (run.direction, run.full_travel) == ("down", True)
    assert run.finished_at is not None
    assert coord._cover_runs == {}  # noqa: SLF001


@pytest.mark.parametrize(
    ("start", "target", "repositioning", "direction", "full"),
    [
        (100, 0, None, "up", True),
        (40, 60, "direct", "down", False),
        (None, 0, None, "unknown", False),
        (30, 30, "firstUp", "reference_up", False),
        (30, 30, "firstDown", "reference_down", False),
    ],
)
def test_run_direction_and_full_travel(start, target, repositioning, direction, full):
    coord = _cover_coordinator(position=start)
    functions = [{"key": "position", "value": str(target)}]
    if repositioning:
        functions.append({"key": "repositioning", "value": repositioning})
    coord._start_cover_runs(  # noqa: SLF001
        coord.devices["DEV1"], functions, monotonic_now()
    )
    run = coord._cover_runs[("DEV1", 0)]  # noqa: SLF001
    assert (run.direction, run.full_travel) == (direction, full)


def test_runs_without_a_usable_end_record_nothing() -> None:
    coord = _cover_coordinator()
    device = coord.devices["DEV1"]
    key = ("DEV1", 0)
    start = coord._start_cover_runs  # noqa: SLF001
    # Stop ends the timing.
    start(device, [{"key": "position", "value": "100"}], monotonic_now())
    start(device, [{"key": "stop", "value": "true"}], monotonic_now())
    assert key not in coord._cover_runs  # noqa: SLF001
    # A status query in between makes the time meaningless.
    start(device, [{"key": "position", "value": "100"}], monotonic_now())
    coord._mark_cover_runs_queried("DEV1", 0)  # noqa: SLF001
    coord._mark_cover_runs_queried("DEV1", 3)  # noqa: SLF001
    _report_position(coord, 100)
    # Too old runs are dropped.
    start(device, [{"key": "position", "value": "100"}], monotonic_now() - 400)
    _report_position(coord, 100)
    # Reports for other channels, malformed values and selectors are ignored.
    start(device, [{"key": "position", "value": "100"}], monotonic_now())
    _report_position(coord, 100, channel=1)
    _report_position(coord, "x")
    _report_position(coord, 100, channel=-1)
    coord._record_confirmed_feedback(  # noqa: SLF001
        "DEV1", [{"key": "angle", "value": 10}]
    )
    assert coord.last_cover_runs == {}
    assert key in coord._cover_runs  # noqa: SLF001
    # Malformed targets, selectors and other devices start nothing.
    coord._cover_runs.clear()  # noqa: SLF001
    start(device, [{"key": "position", "value": "x"}], monotonic_now())
    start(device, [{"key": "position", "value": "1", "channel": -1}], 0.0)
    start(_device("D2-01-01"), [{"key": "position", "value": "1"}], 0.0)
    start(None, [{"key": "position", "value": "1"}], 0.0)
    assert coord._cover_runs == {}  # noqa: SLF001
    # Forgetting the device drops its runs.
    start(device, [{"key": "position", "value": "100"}], monotonic_now())
    coord.last_cover_runs[key] = coord._cover_runs[key]  # noqa: SLF001
    with patch(f"{MODULE}.async_dispatcher_send"):
        coord.async_forget_device("DEV1")
    assert coord._cover_runs == {} and coord.last_cover_runs == {}  # noqa: SLF001


def test_foreign_command_is_timed_too() -> None:
    coord = _cover_coordinator(position=100)
    with (
        patch(f"{MODULE}.async_dispatcher_send"),
        patch(f"{MODULE}.async_call_later"),
    ):
        _outbound(coord, {"key": "position", "value": 0})
        coord._cover_runs[("DEV1", 0)].started_at -= 26  # noqa: SLF001
        coord._handle_telegram(  # noqa: SLF001
            telegram("DEV1", [{"key": "position", "value": 0}])["telegram"]
        )
    run = coord.last_cover_runs[("DEV1", 0)]
    assert run.direction == "up"
    assert run.seconds == pytest.approx(26, abs=0.5)


async def test_run_time_sensor_end_to_end(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [
        device_object(
            "BLIND",
            "D2-05-02",
            states=[{"key": "position", "value": 0.0}],
        )
    ]
    entry = await setup_bridge(hass, gateway)
    cover = await wait_for_entity(hass, "cover", f"{EAG_ID}_BLIND")
    sensor = await wait_for_entity(hass, "sensor", f"{EAG_ID}_BLIND_last_run_time")
    registry_entry = er.async_get(hass).async_get(sensor)
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get(sensor).state == "unknown"

    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": cover}, blocking=True
    )
    coordinator = entry.runtime_data.coordinator
    coordinator._cover_runs[("BLIND", 0)].started_at -= 25  # noqa: SLF001
    await gateway.push(telegram("BLIND", [{"key": "position", "value": 100}]))
    await wait_until(lambda: hass.states.get(sensor).state != "unknown")
    state = hass.states.get(sensor)
    assert float(state.state) == pytest.approx(25, abs=1)
    assert state.attributes["unit_of_measurement"] == "s"
    assert state.attributes["direction"] == "down"
    assert state.attributes["start_position"] == 100
    assert state.attributes["end_position"] == 0
    assert state.attributes["full_travel"] is True
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["devices"][0]["last_cover_run"][0]["target"] == 100


def test_run_time_sensor_without_a_run_and_on_channels() -> None:
    from custom_components.opus_greennet.sensor import OpusGreenNetCoverRunSensor

    coordinator = MagicMock(last_cover_runs={})
    device = _device("D2-05-02")
    with patch.object(EnOceanDevice, "channel_count", 2):
        sensor = OpusGreenNetCoverRunSensor(coordinator, EAG_ID, "gw", device, 1)
    assert sensor.unique_id == f"{EAG_ID}_DEV1_last_run_time_ch1"
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


async def test_blind_blockage_switch_is_disabled_by_default(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("BLIND", "D2-05-02")]
    await setup_bridge(hass, gateway)
    blockage = await wait_for_entity(hass, "switch", f"{EAG_ID}_BLIND_blockage")
    entry = er.async_get(hass).async_get(blockage)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(blockage) is None
