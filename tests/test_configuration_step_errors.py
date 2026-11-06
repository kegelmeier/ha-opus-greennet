"""A rejected configuration write and a rejected read-back are told apart.

Writing a setting takes two requests: the write, then the read-back that shows
whether the device took the value. A gateway rejection means different things
for the two (it is unknown whether the device got the value, or the write was
accepted and the value may be stored), so each has its own message. A write
that fails otherwise keeps its message, a read-back that fails otherwise is
reported as not confirmed, and neither request is repeated.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.api import OpusTimeoutError
from custom_components.opus_greennet.coordinator import _step_reason, request_error
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.test_coordinator_commands import (
    OK,
)
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

NO_RESPONSE = {
    "header": {"httpStatus": 504, "message": "no response from device"},
}
GATEWAY_TEXT = "status 504: no response from device"
KINDS = ["parameter", "option"]
KEYS = {"parameter": "verticalMovementTime", "option": "ledColor"}


def _device(kind: str) -> EnOceanDevice:
    eep = "D2-05-02" if kind == "parameter" else "D2-01-01"
    device = EnOceanDevice("DEV1", "Device", [{"eep": eep}])
    channel = device.get_or_create_channel()
    channel.vertical_movement_time = 25.51
    return device


async def _write(coordinator, kind: str) -> None:
    if kind == "parameter":
        await coordinator.async_set_device_parameter(
            "DEV1", "verticalMovementTime", 25.42, step=0.01, minimum=0
        )
    else:
        await coordinator.async_set_device_option("DEV1", "ledColor", "green")


def _methods(radio) -> list[str]:
    return [method for method, _, _ in radio.sent]


@pytest.mark.parametrize("kind", KINDS)
async def test_a_rejected_write_leaves_open_whether_the_device_got_it(
    radio, connected_coordinator, kind
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _device(kind)
    radio.response = NO_RESPONSE

    with pytest.raises(HomeAssistantError) as error:
        await _write(coord, kind)

    assert error.value.translation_key == "parameter_write_failed"
    assert error.value.translation_placeholders == {
        "device_id": "DEV1",
        "reason": f"{KEYS[kind]}: {GATEWAY_TEXT}",
    }
    assert isinstance(error.value.__cause__, HomeAssistantError)
    assert error.value.__cause__.translation_key == "request_rejected"
    # No read-back, no repeat, and the shown value is the previous one.
    assert _methods(radio) == ["PUT"]
    assert device.channels[0].vertical_movement_time == 25.51


@pytest.mark.parametrize("kind", KINDS)
async def test_a_rejected_read_back_says_the_value_may_be_stored(
    radio, connected_coordinator, kind
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = _device(kind)
    radio.auto_respond = False

    task = asyncio.create_task(_write(coord, kind))
    await radio.wait_sent(1)
    radio.respond(OK)
    await radio.wait_sent(2)
    radio.respond(NO_RESPONSE)
    with pytest.raises(HomeAssistantError) as error:
        await task

    assert error.value.translation_key == "parameter_read_back_failed"
    assert error.value.translation_placeholders == {
        "device_id": "DEV1",
        "reason": f"{KEYS[kind]}: {GATEWAY_TEXT}",
    }
    assert _methods(radio) == ["PUT", "GET"]
    assert device.channels[0].vertical_movement_time == 25.51


@pytest.mark.parametrize("kind", KINDS)
async def test_a_timeout_of_the_write_keeps_its_own_message(
    radio, connected_coordinator, kind
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _device(kind)
    radio.response = OpusTimeoutError("PUT timed out")

    with pytest.raises(HomeAssistantError) as error:
        await _write(coord, kind)

    assert error.value.translation_key == "request_timeout"
    assert _methods(radio) == ["PUT"]


@pytest.mark.parametrize("kind", KINDS)
async def test_a_timeout_of_the_read_back_is_not_confirmed(
    radio, connected_coordinator, kind
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _device(kind)
    radio.auto_respond = False

    task = asyncio.create_task(_write(coord, kind))
    await radio.wait_sent(1)
    radio.respond(OK)
    await radio.wait_sent(2)
    radio.respond(OpusTimeoutError("GET timed out"))
    with pytest.raises(HomeAssistantError) as error:
        await task

    assert error.value.translation_key == "parameter_not_confirmed"
    assert _methods(radio) == ["PUT", "GET"]


@pytest.mark.parametrize("kind", KINDS)
async def test_an_offline_gateway_keeps_its_own_message(
    radio, connected_coordinator, kind
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _device(kind)
    coord._set_gateway_available(False)

    with pytest.raises(HomeAssistantError) as error:
        await _write(coord, kind)

    assert error.value.translation_key == "gateway_unavailable"
    assert radio.sent == []


@pytest.mark.parametrize("kind", KINDS)
async def test_a_confirmed_write_raises_nothing(
    radio, connected_coordinator, kind
) -> None:
    coord = connected_coordinator
    coord.devices["DEV1"] = _device(kind)
    value = 25.42 if kind == "parameter" else "green"
    radio.response = {
        "header": {"httpStatus": 200},
        "configuration": {"parameters": [{"key": KEYS[kind], "value": value}]},
    }

    await _write(coord, kind)

    assert _methods(radio) == ["PUT", "GET"]


@pytest.mark.parametrize(
    ("reason", "expected"),
    [("status 504", "verticalMovementTime: status 504"), ("", "verticalMovementTime")],
)
def test_the_reason_names_the_setting(reason: str, expected: str) -> None:
    error = request_error("request_rejected", "DEV1", reason)
    assert _step_reason("verticalMovementTime", error) == expected


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_two_messages_do_not_claim_what_a_504_cannot_show(language: str) -> None:
    """A rejected write is not a proof of non-delivery; a read-back one of storage."""
    root = Path(__file__).parents[1] / "custom_components" / "opus_greennet"
    path = root / "translations" / f"{language}.json"
    messages = json.loads(path.read_text(encoding="utf-8"))["exceptions"]
    written = messages["parameter_write_failed"]["message"]
    read_back = messages["parameter_read_back_failed"]["message"]

    assert written != read_back
    for claim in ("probably", "vermutlich", "did not get", "nicht erhalten"):
        assert claim not in written
    assert "{reason}" in written
    assert "{reason}" in read_back
