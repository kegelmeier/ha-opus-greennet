"""The last run time of a blind survives a Home Assistant restart."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import (
    seed_restored_states,
    setup_bridge,
    wait_for_entity,
    wait_until,
)

EAG_ID = "AABB0011"
UNIQUE_ID = f"{EAG_ID}_BLIND_last_run_time"
ENTITY_ID = "sensor.blind_last_run_time"
RUN_ATTRIBUTES = {
    "direction": "down",
    "start_position": 100,
    "end_position": 0,
    "full_travel": True,
    "finished_at": "2026-10-22T09:30:00+00:00",
}


def _blind() -> dict:
    return device_object(
        "BLIND", "D2-05-02", states=[{"key": "position", "value": 100.0}]
    )


def _register(hass: HomeAssistant) -> None:
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, UNIQUE_ID, suggested_object_id="blind_last_run_time"
    )


def _restore(hass: HomeAssistant, value: object, attributes: dict) -> None:
    """Seed what Home Assistant saved for the sensor before the restart."""
    seed_restored_states(
        hass,
        {
            ENTITY_ID: restore_state.StoredState(
                State(
                    ENTITY_ID, str(value), {**attributes, "unit_of_measurement": "s"}
                ),
                restore_state.RestoredExtraData(
                    {"native_value": value, "native_unit_of_measurement": "s"}
                ),
                dt_util.utcnow(),
            )
        },
    )


async def test_last_run_is_restored_until_the_next_run(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _register(hass)
    _restore(hass, 46.5, RUN_ATTRIBUTES)
    gateway.devices = [_blind()]
    entry = await setup_bridge(hass, gateway)
    assert await wait_for_entity(hass, "sensor", UNIQUE_ID) == ENTITY_ID
    state = hass.states.get(ENTITY_ID)
    assert float(state.state) == 46.5
    assert {key: state.attributes[key] for key in RUN_ATTRIBUTES} == RUN_ATTRIBUTES

    # A new run replaces the restored value and its attributes.
    cover = await wait_for_entity(hass, "cover", f"{EAG_ID}_BLIND")
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": cover}, blocking=True
    )
    coordinator = entry.runtime_data.coordinator
    coordinator._cover_runs[("BLIND", 0)].started_at -= 30  # noqa: SLF001
    await gateway.push(telegram("BLIND", [{"key": "position", "value": 0}]))
    await wait_until(lambda: hass.states.get(ENTITY_ID).state != "46.5")
    state = hass.states.get(ENTITY_ID)
    assert float(state.state) == pytest.approx(30, abs=1)
    assert state.attributes["direction"] == "up"
    assert state.attributes["start_position"] == 0
    assert state.attributes["end_position"] == 100


@pytest.mark.parametrize(
    ("value", "attributes", "expected_state", "expected_attributes"),
    [
        # Nothing measured before the restart.
        (None, {}, "unknown", {}),
        # A value without the run attributes keeps no attributes.
        (12.0, {}, "12.0", {}),
    ],
)
async def test_restore_without_a_full_run(
    hass: HomeAssistant,
    gateway: FakeGateway,
    value: object,
    attributes: dict,
    expected_state: str,
    expected_attributes: dict,
) -> None:
    _register(hass)
    _restore(hass, value, attributes)
    gateway.devices = [_blind()]
    await setup_bridge(hass, gateway)
    await wait_for_entity(hass, "sensor", UNIQUE_ID)
    state = hass.states.get(ENTITY_ID)
    assert state.state == expected_state
    assert {
        key: state.attributes[key] for key in RUN_ATTRIBUTES if key in state.attributes
    } == expected_attributes
