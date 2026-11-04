"""Edge cases of the coordinator: replaced devices, guards and failing steps."""

from __future__ import annotations

from time import monotonic
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.api import OpusConnectionError
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

CONFIG_PATH = "/devices/DEV1/configuration"
REPEATER_PATH = "/devices/DEV1/repeaterState"


def _switch() -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Switch", [{"eep": "D2-01-01"}])


def _blind() -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Blind", [{"eep": "D2-05-02"}])


def _document(**values: object) -> dict:
    return {
        "header": {"httpStatus": 200},
        "configuration": {
            "parameters": [{"key": k, "value": v} for k, v in values.items()]
        },
    }


# ── radio journal ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [True, "nope", 200.5])
async def test_journal_records_an_answer_with_an_unusable_status(
    connected_coordinator, status
) -> None:
    coord = connected_coordinator

    coord._journal_radio_request(  # noqa: SLF001
        "DEV1",
        "configuration_read",
        0,
        None,
        waited=0.0,
        started=monotonic(),
        result={"header": {"httpStatus": status}},
    )

    (entry,) = coord.radio_journal()
    assert entry["outcome"] == "ok"
    assert entry["gateway_status"] is None


# ── settings read ───────────────────────────────────────────────────────────


async def test_a_read_for_a_replaced_device_changes_nothing(
    radio, connected_coordinator
) -> None:
    """The device object was replaced while the read ran: the answer is stale."""
    coord = connected_coordinator
    old = coord.devices["DEV1"] = _blind()
    old.get_or_create_channel().max_holding_time = 12.0
    replacement = _blind()
    replacement.get_or_create_channel().max_holding_time = 7.0
    radio.response = _document(maxHoldingTime=33.0)
    original = radio._request  # noqa: SLF001

    async def replacing(method, path, body, timeout):
        coord.devices["DEV1"] = replacement
        return await original(method, path, body, timeout)

    radio._request = replacing  # noqa: SLF001

    await coord.async_reread_device_settings("DEV1")

    assert old.channels[0].max_holding_time == 12.0
    assert replacement.channels[0].max_holding_time == 7.0


async def test_a_read_keeps_the_guards_of_settings_it_does_not_report(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _blind()
    device.get_or_create_channel().max_holding_time = 12.0
    coord._guard_confirmed_setting(  # noqa: SLF001
        "DEV1", "verticalMovementTime", 25.51, 30.0
    )
    radio.response = _document(maxHoldingTime=33.0)

    with patch.object(coord, "_save_setting_guards") as save:
        await coord.async_reread_device_settings("DEV1")

    assert set(coord._setting_guards["DEV1"]) == {"verticalMovementTime"}  # noqa: SLF001
    save.assert_not_called()


# ── setting guards ──────────────────────────────────────────────────────────


async def test_a_write_that_leaves_nothing_stale_keeps_the_other_guards(
    connected_coordinator,
) -> None:
    coord = connected_coordinator
    coord._guard_confirmed_setting(  # noqa: SLF001
        "DEV1", "verticalMovementTime", 25.51, 30.0
    )

    with patch.object(coord, "_save_setting_guards") as save:
        # The value before equals the written one: nothing to protect.
        coord._guard_confirmed_setting("DEV1", "maxHoldingTime", 29.0, 29.0)  # noqa: SLF001

    assert set(coord._setting_guards["DEV1"]) == {"verticalMovementTime"}  # noqa: SLF001
    save.assert_not_called()


async def test_a_device_list_entry_without_a_key_is_kept(
    connected_coordinator,
) -> None:
    coord = connected_coordinator
    coord._guard_confirmed_setting(  # noqa: SLF001
        "DEV1", "verticalMovementTime", 25.51, 30.0
    )
    nameless = {"value": 25.51}
    stale = {"key": "verticalMovementTime", "value": 25.51}

    kept = coord._without_stale_settings("DEV1", [nameless, stale])  # noqa: SLF001

    assert kept == [nameless]


# ── repeater mode ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["repeaterState", "routingTime"])
async def test_repeater_and_routing_of_an_unknown_device_are_refused(
    connected_coordinator, key
) -> None:
    with pytest.raises(HomeAssistantError) as error:
        await connected_coordinator.async_set_device_option("GONE", key, "off")

    assert error.value.translation_key == "device_not_found"


async def test_a_failing_repeater_write_is_reported_as_it_is_and_not_repeated(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _switch()
    radio.response = OpusConnectionError("gateway unreachable")

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "repeaterState", "level_1")

    assert error.value.translation_key != "parameter_write_failed"
    assert [method for method, _, _ in radio.sent] == ["PUT"]


async def test_a_failing_repeater_read_back_is_not_taken_for_a_rejection(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().repeater = "off"
    original = radio._request  # noqa: SLF001

    async def routed(method, path, body, timeout):
        radio.response = (
            {"header": {"httpStatus": 200}}
            if method == "PUT"
            else OpusConnectionError("gateway unreachable")
        )
        return await original(method, path, body, timeout)

    radio._request = routed  # noqa: SLF001

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "repeaterState", "level_1")

    assert error.value.translation_key != "parameter_read_back_failed"
    assert device.channels[0].repeater == "off"
    assert [method for method, _, _ in radio.sent] == ["PUT", "GET"]


async def test_a_rejected_repeater_read_back_is_reported_as_such(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _switch()
    device.get_or_create_channel().repeater = "off"
    original = radio._request  # noqa: SLF001

    async def routed(method, path, body, timeout):
        radio.response = {"header": {"httpStatus": 200 if method == "PUT" else 400}}
        return await original(method, path, body, timeout)

    radio._request = routed  # noqa: SLF001

    with pytest.raises(HomeAssistantError) as error:
        await coord.async_set_device_option("DEV1", "repeaterState", "level_1")

    assert error.value.translation_key == "parameter_read_back_failed"
    assert device.channels[0].repeater == "off"


# ── status query with an unreadable channel ─────────────────────────────────


async def test_a_query_with_an_unreadable_channel_marks_no_run(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _blind()

    with patch.object(coord, "_mark_cover_runs_queried") as mark:
        await coord.async_send_command(
            "DEV1",
            [{"key": "query", "value": "status"}, {"key": "channel", "value": "x"}],
        )

    mark.assert_not_called()
    assert len(radio.sent) == 1
