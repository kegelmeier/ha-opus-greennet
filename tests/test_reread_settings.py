"""Reading the settings of a device on request ("Read settings" button).

One radio read per press, never repeated. What the device reports replaces what
Home Assistant shows and ends the protection of an earlier confirmed write; a
setting the device does not report keeps its known value; an answer that
overlapped a write is dropped because the write is newer.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.api import OpusTimeoutError
from custom_components.opus_greennet.button import (
    OpusGreenNetReadSettingsButton,
    async_setup_entry,
    has_settings,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import device_object
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

EAG_ID = "AABB0011"
CONFIG_PATH = "/devices/DEV1/configuration"
REPEATER_PATH = "/devices/DEV1/repeaterState"
BLIND = "D2-05-02"


def _configuration(**values: object) -> dict:
    return {
        "parameters": [{"key": key, "value": value} for key, value in values.items()]
    }


def _document(**values: object) -> dict:
    return {"header": {"httpStatus": 200}, "configuration": _configuration(**values)}


def _list(**values: object) -> dict:
    return {
        "devices": [
            device_object("DEV1", BLIND, configuration=_configuration(**values))
        ]
    }


@pytest.fixture
def blind(connected_coordinator):
    device = connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Blind", [{"eep": BLIND}]
    )
    channel = device.get_or_create_channel()
    channel.max_holding_time = 12.0
    channel.vertical_movement_time = 25.51
    return device


# ── coordinator ────────────────────────────────────────────────────────────


async def test_the_device_values_are_shown_after_one_read(
    radio, connected_coordinator, blind
) -> None:
    radio.response = _document(maxHoldingTime=33.0, verticalMovementTime=26.0)

    await connected_coordinator.async_reread_device_settings("DEV1")

    # The repeater mode of a blind is a separate resource, read after it.
    assert radio.sent == [("GET", CONFIG_PATH, None), ("GET", REPEATER_PATH, None)]
    assert blind.channels[0].max_holding_time == 33.0
    assert blind.channels[0].vertical_movement_time == 26.0


async def test_a_setting_the_device_does_not_report_keeps_its_value(
    radio, connected_coordinator, blind
) -> None:
    radio.response = _document(maxHoldingTime=33.0)

    await connected_coordinator.async_reread_device_settings("DEV1")

    assert blind.channels[0].max_holding_time == 33.0
    assert blind.channels[0].vertical_movement_time == 25.51


@pytest.mark.parametrize("answer", [{"header": {"httpStatus": 200}}, _document()])
async def test_an_answer_without_settings_changes_nothing(
    radio, connected_coordinator, blind, answer
) -> None:
    radio.response = answer

    await connected_coordinator.async_reread_device_settings("DEV1")

    assert blind.channels[0].max_holding_time == 12.0
    assert [method for method, _, _ in radio.sent] == ["GET", "GET"]


async def test_a_read_ends_the_protection_of_the_settings_it_reports(
    radio, connected_coordinator, blind
) -> None:
    """What the device reports is the truth: the protection of that setting ends."""
    coord = connected_coordinator
    radio.response = _document(maxHoldingTime=29.0)
    await coord.async_set_device_parameter(
        "DEV1", "maxHoldingTime", 29, step=1, minimum=0
    )
    coord._guard_confirmed_setting("DEV1", "verticalMovementTime", 25.51, 30.0)
    assert set(coord._setting_guards["DEV1"]) == {
        "maxHoldingTime",
        "verticalMovementTime",
    }

    radio.response = _document(maxHoldingTime=12.0)
    await coord.async_reread_device_settings("DEV1")

    assert blind.channels[0].max_holding_time == 12.0
    # Only the setting the device reported is released.
    assert set(coord._setting_guards["DEV1"]) == {"verticalMovementTime"}
    # Without the protection a list shows what it carries.
    coord._apply_device_list(_list(maxHoldingTime=40.0), authoritative=False)
    assert blind.channels[0].max_holding_time == 40.0


async def test_a_rejected_read_is_reported_and_not_repeated(
    radio, connected_coordinator, blind
) -> None:
    radio.response = {"header": {"httpStatus": 504, "message": "no response"}}

    with pytest.raises(HomeAssistantError) as error:
        await connected_coordinator.async_reread_device_settings("DEV1")

    assert error.value.translation_key == "request_rejected"
    assert [method for method, _, _ in radio.sent] == ["GET"]
    assert blind.channels[0].max_holding_time == 12.0


async def test_a_timeout_is_reported_and_not_repeated(
    radio, connected_coordinator, blind
) -> None:
    radio.response = OpusTimeoutError("GET timed out")

    with pytest.raises(HomeAssistantError) as error:
        await connected_coordinator.async_reread_device_settings("DEV1")

    assert error.value.translation_key == "request_timeout"
    assert [method for method, _, _ in radio.sent] == ["GET"]
    assert blind.channels[0].max_holding_time == 12.0
    assert "DEV1" not in connected_coordinator._settings_reading


async def test_an_unknown_device_sends_nothing(radio, connected_coordinator) -> None:
    with pytest.raises(HomeAssistantError) as error:
        await connected_coordinator.async_reread_device_settings("DEV1")

    assert error.value.translation_key == "device_not_found"
    assert radio.sent == []


async def test_a_second_press_while_reading_is_ignored(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    radio.auto_respond = False

    first = asyncio.create_task(coord.async_reread_device_settings("DEV1"))
    await radio.wait_sent(1)
    await coord.async_reread_device_settings("DEV1")
    assert len(radio.sent) == 1

    radio.respond(_document(maxHoldingTime=33.0))
    await radio.wait_sent(2)  # the repeater mode follows the configuration
    radio.respond({"header": {"httpStatus": 200}, "repeaterState": "off"})
    await first
    assert blind.channels[0].max_holding_time == 33.0
    assert len(radio.sent) == 2
    # Afterwards a new read is possible again.
    radio.auto_respond = True
    radio.response = _document(maxHoldingTime=34.0)
    await coord.async_reread_device_settings("DEV1")
    assert blind.channels[0].max_holding_time == 34.0


async def test_an_answer_that_overlapped_a_write_is_dropped(
    radio, connected_coordinator, blind
) -> None:
    """The write is newer than a read that was on its way before it ended."""
    coord = connected_coordinator
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_reread_device_settings("DEV1"))
    await radio.wait_sent(1)

    coord._note_setting_write("DEV1")  # a write step happened meanwhile
    radio.respond(_document(maxHoldingTime=33.0))
    await radio.wait_sent(2)
    radio.respond({"header": {"httpStatus": 200}, "repeaterState": "off"})
    await task

    assert blind.channels[0].max_holding_time == 12.0


async def test_every_write_marks_the_device(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    radio.response = _document(maxHoldingTime=29.0)
    before = coord._setting_writes.get("DEV1", 0)

    await coord.async_set_device_parameter(
        "DEV1", "maxHoldingTime", 29, step=1, minimum=0
    )

    assert coord._setting_writes["DEV1"] >= before + 2


async def test_a_failed_write_marks_the_device_too(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    radio.response = OpusTimeoutError("PUT timed out")

    with pytest.raises(HomeAssistantError):
        await coord.async_set_device_parameter(
            "DEV1", "maxHoldingTime", 29, step=1, minimum=0
        )

    assert coord._setting_writes["DEV1"] >= 2


async def test_a_device_forgotten_while_reading_gets_nothing(
    radio, connected_coordinator, blind
) -> None:
    coord = connected_coordinator
    radio.auto_respond = False
    task = asyncio.create_task(coord.async_reread_device_settings("DEV1"))
    await radio.wait_sent(1)

    coord.async_forget_device("DEV1")
    radio.respond(_document(maxHoldingTime=33.0))
    # The forgotten device's request is cancelled or answered; neither may
    # bring the device back or fail loudly. A follow-up read is answered too.
    for _ in range(10):
        await asyncio.sleep(0)
        if radio.waiting:
            radio.respond({"header": {"httpStatus": 200}})
    try:
        await task
    except HomeAssistantError:
        pass

    assert "DEV1" not in coord.devices


# ── button ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("eep", "expected"),
    [
        ("D2-05-02", True),
        ("D2-01-03", True),
        ("D2-01-01", True),
        ("D2-01-11", True),
        ("A5-07-03", True),
        ("A5-07-01", False),
        ("F6-02-01", False),
        ("A5-04-01", False),
    ],
)
def test_only_devices_with_settings_get_the_button(eep: str, expected: bool) -> None:
    assert has_settings(EnOceanDevice("DEV1", "Device", [{"eep": eep}])) is expected


def test_the_button_metadata() -> None:
    device = EnOceanDevice("DEV1", "Blind", [{"eep": BLIND}])
    button = OpusGreenNetReadSettingsButton(MagicMock(), EAG_ID, "gateway", device)

    assert button.unique_id == f"{EAG_ID}_DEV1_read_settings"
    assert button.translation_key == "read_settings"
    assert button.entity_category is EntityCategory.DIAGNOSTIC
    assert button.entity_registry_enabled_default is True


async def test_pressing_the_button_reads_the_settings() -> None:
    coordinator = MagicMock()
    coordinator.async_reread_device_settings = AsyncMock()
    device = EnOceanDevice("DEV1", "Blind", [{"eep": BLIND}])

    await OpusGreenNetReadSettingsButton(
        coordinator, EAG_ID, "gateway", device
    ).async_press()

    coordinator.async_reread_device_settings.assert_awaited_once_with("DEV1")


@pytest.mark.parametrize(
    ("eep", "unique_ids"),
    [
        (BLIND, {"read_settings", "reference_run_up", "reference_run_down"}),
        ("D2-01-01", {"read_settings"}),
        ("A5-07-03", {"read_settings"}),
        ("F6-02-01", set()),
    ],
)
async def test_the_platform_adds_the_button_per_device(eep, unique_ids) -> None:
    device = EnOceanDevice("DEV1", "Device", [{"eep": eep}])
    coordinator = MagicMock(eag_id=EAG_ID, devices={"DEV1": device})
    coordinator.available = True
    added: list = []
    entry = SimpleNamespace(
        entry_id="entry",
        data={"eag_id": EAG_ID},
        runtime_data=SimpleNamespace(
            coordinator=coordinator, gateway_device_id="gateway"
        ),
        async_on_unload=MagicMock(),
    )
    with patch(
        "custom_components.opus_greennet.button.async_dispatcher_connect",
        return_value=MagicMock(),
    ):
        await async_setup_entry(MagicMock(), entry, added.extend)

    prefix = f"{EAG_ID}_DEV1_"
    assert {entity.unique_id.removeprefix(prefix) for entity in added} == unique_ids
