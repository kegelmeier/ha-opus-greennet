"""``opus_greennet.set_cover_position`` can send the slat angle in the same command.

The gateway's D2-05-02 command "Go to Position and Angle" carries position and
angle together. The standard cover actions send them as two commands; the
integration's own action sends one.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge, wait_for_entity

STATE_PATH = "/devices/BLIND/state"


def _blind(rotation_time: float = 1.2) -> dict:
    return device_object(
        "BLIND",
        "D2-05-02",
        friendly_id="Blind",
        states=[
            {"key": "position", "value": 0.0},
            {"key": "angle", "value": 0.0},
        ],
        configuration={
            "parameters": [
                {"key": "verticalMovementTime", "value": 30},
                {"key": "rotationTime", "value": rotation_time},
            ]
        },
    )


async def _setup(hass: HomeAssistant, gateway: FakeGateway, **fields) -> None:
    gateway.devices = [{**_blind(), **fields}]
    await setup_bridge(hass, gateway)
    await wait_for_entity(hass, "cover", f"{gateway.eurid}_BLIND")


async def _call(hass: HomeAssistant, **data) -> None:
    await hass.services.async_call(
        DOMAIN,
        "set_cover_position",
        {"device_id": "BLIND", **data},
        blocking=True,
    )


async def test_position_and_angle_go_out_in_one_command(
    hass: HomeAssistant, gateway: FakeGateway
):
    await _setup(hass, gateway)
    # Home Assistant scale 0 = closed becomes OPUS position 100.
    await _call(hass, position=0, angle=100)
    assert gateway.requests_to("PUT", STATE_PATH) == [
        {
            "state": {
                "functions": [
                    {"key": "position", "value": "100"},
                    {"key": "angle", "value": "100"},
                ]
            }
        }
    ]


async def test_without_angle_the_command_is_unchanged(
    hass: HomeAssistant, gateway: FakeGateway
):
    await _setup(hass, gateway)
    await _call(hass, position=30)
    assert gateway.requests_to("PUT", STATE_PATH) == [
        {"state": {"functions": [{"key": "position", "value": "70"}]}}
    ]


async def test_angle_is_sent_with_a_reference_run(
    hass: HomeAssistant, gateway: FakeGateway
):
    await _setup(hass, gateway)
    await _call(hass, position=30, angle=50, repositioning="first_up")
    (body,) = gateway.requests_to("PUT", STATE_PATH)
    assert body["state"]["functions"] == [
        {"key": "position", "value": "70"},
        {"key": "angle", "value": "50"},
        {"key": "repositioning", "value": "firstUp"},
    ]


async def test_angle_zero_is_sent(hass: HomeAssistant, gateway: FakeGateway):
    """An angle of 0 (slats open) is a value, not "not given"."""
    await _setup(hass, gateway)
    await _call(hass, position=100, angle=0)
    (body,) = gateway.requests_to("PUT", STATE_PATH)
    assert {"key": "angle", "value": "0"} in body["state"]["functions"]


@pytest.mark.parametrize("angle", [-1, 101])
async def test_angle_outside_the_range_is_refused(
    hass: HomeAssistant, gateway: FakeGateway, angle: int
):
    await _setup(hass, gateway)
    with pytest.raises(Exception, match="angle|value must be"):
        await _call(hass, position=50, angle=angle)
    assert gateway.requests_to("PUT", STATE_PATH) == []


async def test_angle_for_a_blind_without_slat_rotation_is_refused(
    hass: HomeAssistant, gateway: FakeGateway
):
    """A roller shutter reports no rotation time: no slats, so no angle."""
    await _setup(
        hass,
        gateway,
        configuration={
            "parameters": [
                {"key": "verticalMovementTime", "value": 30},
                {"key": "rotationTime", "value": 0},
            ]
        },
    )
    with pytest.raises(ServiceValidationError) as error:
        await _call(hass, position=50, angle=100)
    assert error.value.translation_key == "repositioning_not_supported"
    assert gateway.requests_to("PUT", STATE_PATH) == []
    # Moving without an angle still works.
    await _call(hass, position=50)
    assert len(gateway.requests_to("PUT", STATE_PATH)) == 1
