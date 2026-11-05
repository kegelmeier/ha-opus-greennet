"""Commands of D2-35-00 (mode, setpoint) and D1-4B-50 (LEDs) as select and number."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import number as number_platform
from custom_components.opus_greennet import select as select_platform
from custom_components.opus_greennet.number import OpusGreenNetCommandNumber
from custom_components.opus_greennet.readings import COMMANDS, READINGS
from custom_components.opus_greennet.select import OpusGreenNetCommandSelect

EAG_ID = "AABB0011"
MODE, SETPOINT = COMMANDS["D2-35-00"]
(LED,) = COMMANDS["D1-4B-50"]


def _coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.async_send_command = AsyncMock()
    return coordinator


def _select(device, command, coordinator=None) -> OpusGreenNetCommandSelect:
    return OpusGreenNetCommandSelect(
        coordinator or _coordinator(), EAG_ID, "gateway", device, command
    )


def _number(device, command, coordinator=None) -> OpusGreenNetCommandNumber:
    return OpusGreenNetCommandNumber(
        coordinator or _coordinator(), EAG_ID, "gateway", device, command
    )


def test_commands_match_the_readings_of_their_profile() -> None:
    """The state a command shows must be a reading of the same profile."""
    for eep, commands in COMMANDS.items():
        readings = {spec.key: spec for spec in READINGS[eep]}
        for command in commands:
            reading = readings[command.key]
            if command.kind == "select":
                assert {shown for shown, _ in command.options} <= set(reading.options)
            else:
                assert reading.minimum is not None
                assert reading.maximum is not None
                assert command.minimum >= reading.minimum
                assert command.maximum <= reading.maximum


def test_no_firmware_command_is_offered() -> None:
    wire_values = {
        wire
        for commands in COMMANDS.values()
        for c in commands
        for _, wire in c.options
    }
    keys = {c.key for commands in COMMANDS.values() for c in commands}
    assert not wire_values & {"resetFirmware", "startUpdate"}
    assert not keys & {"resetFirmware", "startUpdate"}
    assert set(COMMANDS) == {"D2-35-00", "D1-4B-50"}


def test_command_entities_are_experimental_and_disabled(make_device) -> None:
    device = make_device("D2-35-00")
    for entity in (_select(device, MODE), _number(device, SETPOINT)):
        assert entity.entity_registry_enabled_default is False
        assert entity.entity_category is None


def test_mode_select_shows_the_reported_mode(make_device, make_telegram) -> None:
    device = make_device("D2-35-00")
    entity = _select(device, MODE)
    assert entity.options == ["managed", "off", "on"]
    assert entity.current_option is None

    device.update_from_telegram(make_telegram([{"key": "mode", "value": "managed"}]))
    assert entity.current_option == "managed"
    # "error" is only ever reported, never offered.
    device.update_from_telegram(make_telegram([{"key": "mode", "value": "error"}]))
    assert entity.current_option is None


async def test_mode_command_does_not_change_the_shown_mode(
    make_device, make_telegram
) -> None:
    device = make_device("D2-35-00")
    device.update_from_telegram(make_telegram([{"key": "mode", "value": "on"}]))
    coordinator = _coordinator()
    entity = _select(device, MODE, coordinator)

    await entity.async_select_option("off")

    coordinator.async_send_command.assert_awaited_once_with(
        device.device_id, [{"key": "mode", "value": "off"}]
    )
    # The device report decides, not the gateway acceptance.
    assert entity.current_option == "on"
    device.update_from_telegram(make_telegram([{"key": "mode", "value": "off"}]))
    assert entity.current_option == "off"


async def test_rejected_command_raises_and_keeps_the_state(
    make_device, make_telegram
) -> None:
    device = make_device("D2-35-00")
    device.update_from_telegram(make_telegram([{"key": "mode", "value": "on"}]))
    coordinator = _coordinator()
    coordinator.async_send_command.side_effect = HomeAssistantError("rejected")
    entity = _select(device, MODE, coordinator)

    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("off")

    assert entity.current_option == "on"
    assert coordinator.async_send_command.await_count == 1


def test_setpoint_number(make_device, make_telegram) -> None:
    device = make_device("D2-35-00")
    entity = _number(device, SETPOINT)
    assert (entity.native_min_value, entity.native_max_value) == (5, 40)
    assert entity.native_step == 0.5
    assert entity.native_value is None

    device.update_from_telegram(
        make_telegram([{"key": "temperatureSetpoint", "value": "21.5"}])
    )
    assert entity.native_value == 21.5


async def test_setpoint_command(make_device, make_telegram) -> None:
    device = make_device("D2-35-00")
    device.update_from_telegram(
        make_telegram([{"key": "temperatureSetpoint", "value": "20"}])
    )
    coordinator = _coordinator()
    entity = _number(device, SETPOINT, coordinator)

    await entity.async_set_native_value(22.5)

    coordinator.async_send_command.assert_awaited_once_with(
        device.device_id, [{"key": "temperatureSetpoint", "value": "22.5"}]
    )
    assert entity.native_value == 20


async def test_led_select_is_a_disabled_configuration_entity(
    make_device, make_telegram
) -> None:
    device = make_device("D1-4B-50")
    coordinator = _coordinator()
    entity = _select(device, LED, coordinator)
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.entity_registry_enabled_default is False

    device.update_from_telegram(
        make_telegram([{"key": "ledStatus", "value": "enabled"}])
    )
    assert entity.current_option == "enabled"

    await entity.async_select_option("disabled")
    coordinator.async_send_command.assert_awaited_once_with(
        device.device_id, [{"key": "ledStatus", "value": "disable"}]
    )
    assert entity.current_option == "enabled"


def test_unique_ids_are_stable(make_device) -> None:
    device = make_device("D2-35-00")
    assert (
        _select(device, MODE).unique_id
        == f"{EAG_ID}_{device.device_id}_actuator_mode_command"
    )
    assert (
        _number(device, SETPOINT).unique_id
        == f"{EAG_ID}_{device.device_id}_temperature_setpoint_command"
    )


@pytest.mark.parametrize(
    ("eep", "selects", "numbers"),
    [
        ("D2-35-00", 1, 1),
        ("D1-4B-50", 1, 0),
        ("D2-34-10", 0, 0),
        ("D1-4B-03", 0, 0),
    ],
)
async def test_entities_only_for_the_profiles_with_commands(
    hass: HomeAssistant, make_device, eep, selects, numbers
) -> None:
    device = make_device(eep)
    coordinator = MagicMock(
        eag_id=EAG_ID, devices={device.device_id: device}, available=True
    )
    entry = SimpleNamespace(
        data={"eag_id": EAG_ID},
        runtime_data=SimpleNamespace(
            coordinator=coordinator, gateway_device_id="gateway"
        ),
        async_on_unload=MagicMock(),
    )
    for platform, kind, expected in (
        (select_platform, OpusGreenNetCommandSelect, selects),
        (number_platform, OpusGreenNetCommandNumber, numbers),
    ):
        add = MagicMock()
        await platform.async_setup_entry(hass, entry, add)
        added = [e for e in add.call_args.args[0] if isinstance(e, kind)]
        assert len(added) == expected
