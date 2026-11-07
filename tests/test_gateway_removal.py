"""Deleting a device from the gateway (device management, stage 3).

The gateway side is the ``FakeGateway`` handling of ``DELETE /devices/<id>``.
The method and path follow the handler in the gateway software (findings for
1.21.30), the answer shape is an assumption. Nothing here was run against a
real gateway.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.api import OpusConnectionError
from custom_components.opus_greennet.const import (
    CONF_ALLOW_DEVICE_MANAGEMENT,
    CONF_ALLOW_SETTING_WRITES,
    DOMAIN,
)
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge, wait_until

DEVICE_ID = "05055BBE"
OTHER_ID = "AABB1122"
PATH = f"/devices/{DEVICE_ID}"
DAYTIME = datetime(2026, 11, 7, 14, 0, tzinfo=dt_util.get_default_time_zone())
NIGHT = datetime(2026, 11, 7, 3, 30, tzinfo=dt_util.get_default_time_zone())


@pytest.fixture(autouse=True)
def _daytime():
    """A fixed local time outside the nightly restart."""
    with patch.object(dt_util, "now", return_value=DAYTIME):
        yield


async def _bridge(
    hass: HomeAssistant, gateway: FakeGateway, *, management: bool = True
):
    gateway.devices = [
        device_object(DEVICE_ID, "F6-02-01", friendly_id="Taught in"),
        device_object(OTHER_ID, "D2-01-00", friendly_id="Other"),
    ]
    entry = await setup_bridge(hass, gateway)
    options = {CONF_ALLOW_SETTING_WRITES: True}
    if management:
        options[CONF_ALLOW_DEVICE_MANAGEMENT] = True
    hass.config_entries.async_update_entry(entry, options=options)
    coordinator = entry.runtime_data.coordinator
    await wait_until(
        lambda: (
            _registered(hass, entry, DEVICE_ID) and _registered(hass, entry, OTHER_ID)
        )
    )
    return entry, coordinator


def _registered(hass: HomeAssistant, entry, device_id: str) -> bool:
    registry = dr.async_get(hass)
    return (
        registry.async_get_device_by_identifier(
            (DOMAIN, f"{entry.data['eag_id']}_{device_id}"), entry.entry_id
        )
        is not None
    )


def _deletes(gateway: FakeGateway) -> list[tuple[str, str, object]]:
    return [r for r in gateway.requests if r[0] == "DELETE"]


async def test_delete_is_sent_once_without_force_and_the_device_is_removed(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)
    assert _registered(hass, entry, DEVICE_ID)

    await coordinator.async_delete_from_gateway(DEVICE_ID)

    assert _deletes(gateway) == [("DELETE", PATH, None)]
    assert all(d["deviceId"] != DEVICE_ID for d in gateway.devices)
    assert coordinator.get_device(DEVICE_ID) is None
    assert not _registered(hass, entry, DEVICE_ID)
    assert _registered(hass, entry, OTHER_ID)
    assert coordinator.get_device(OTHER_ID) is not None


async def test_nothing_is_sent_without_the_option(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway, management=False)

    with pytest.raises(ServiceValidationError) as raised:
        await coordinator.async_delete_from_gateway(DEVICE_ID)

    assert raised.value.translation_key == "device_management_disabled"
    assert _deletes(gateway) == []
    assert _registered(hass, entry, DEVICE_ID)


async def test_not_in_the_nightly_restart_window(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)

    with (
        patch.object(dt_util, "now", return_value=NIGHT),
        pytest.raises(ServiceValidationError) as raised,
    ):
        await coordinator.async_delete_from_gateway(DEVICE_ID)

    assert raised.value.translation_key == "device_delete_night"
    assert _deletes(gateway) == []
    assert _registered(hass, entry, DEVICE_ID)


async def test_an_unknown_device_is_refused_before_anything_is_sent(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)

    with pytest.raises(ServiceValidationError) as raised:
        await coordinator.async_delete_from_gateway("DEADBEEF")

    assert raised.value.translation_key == "device_not_found"
    assert _deletes(gateway) == []


async def test_a_rejection_is_reported_and_neither_repeated_nor_forced(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)
    gateway.delete_rejection = {
        "httpStatus": 409,
        "message": "Remove the corresponding entry, then try again.",
    }

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_delete_from_gateway(DEVICE_ID)

    assert raised.value.translation_key == "device_delete_not_confirmed"
    assert _deletes(gateway) == [("DELETE", PATH, None)]
    assert any(d["deviceId"] == DEVICE_ID for d in gateway.devices)
    assert coordinator.get_device(DEVICE_ID) is not None
    assert _registered(hass, entry, DEVICE_ID)


async def test_an_accepted_delete_that_leaves_the_device_listed_is_not_confirmed(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)
    gateway.delete_ignored = True

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_delete_from_gateway(DEVICE_ID)

    assert raised.value.translation_key == "device_delete_not_confirmed"
    assert len(_deletes(gateway)) == 1
    assert coordinator.get_device(DEVICE_ID) is not None
    assert _registered(hass, entry, DEVICE_ID)


async def test_a_delete_without_answer_is_judged_by_the_device_list(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """The answer was lost but the device is gone: the list decides, no repeat."""
    entry, coordinator = await _bridge(hass, gateway)
    real_delete = coordinator.client.async_delete

    async def lost_answer(path: str, **kwargs):
        await real_delete(path, **kwargs)
        raise OpusConnectionError("connection lost")

    with patch.object(coordinator.client, "async_delete", lost_answer):
        await coordinator.async_delete_from_gateway(DEVICE_ID)

    assert len(_deletes(gateway)) == 1
    assert not _registered(hass, entry, DEVICE_ID)
    assert coordinator.get_device(DEVICE_ID) is None


async def test_an_unreadable_list_after_the_delete_keeps_the_device(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)

    async def unreadable(path: str, **kwargs):
        raise OpusConnectionError("no answer")

    with (
        patch.object(coordinator.client, "async_get", unreadable),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await coordinator.async_delete_from_gateway(DEVICE_ID)

    assert raised.value.translation_key == "device_delete_unclear"
    assert len(_deletes(gateway)) == 1
    assert coordinator.get_device(DEVICE_ID) is not None
    assert _registered(hass, entry, DEVICE_ID)


async def test_service_needs_the_id_typed_again(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)

    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN,
            "delete_device_from_gateway",
            {"device_id": DEVICE_ID, "confirm_device_id": OTHER_ID},
            blocking=True,
        )
    assert raised.value.translation_key == "device_delete_confirmation"
    assert _deletes(gateway) == []

    await hass.services.async_call(
        DOMAIN,
        "delete_device_from_gateway",
        {
            "device_id": DEVICE_ID,
            "confirm_device_id": DEVICE_ID.lower(),
            "config_entry_id": entry.entry_id,
        },
        blocking=True,
    )
    assert len(_deletes(gateway)) == 1
    assert not _registered(hass, entry, DEVICE_ID)
