"""Branches of the sensor platform that the usual flows do not reach."""

from __future__ import annotations

import re
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import SIGNAL_DEVICE_DISCOVERED
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.readings import READINGS
from custom_components.opus_greennet.sensor import (
    OpusGreenNetCoverRunSensor,
    OpusGreenNetHandleSensor,
    OpusGreenNetReadingSensor,
)
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import (
    seed_restored_states,
    setup_bridge,
    wait_for_entity,
    wait_until,
)

EAG_ID = "AABB0011"


def test_reading_sensor_takes_a_suggested_precision_from_its_spec() -> None:
    device = EnOceanDevice(
        device_id="DEV1", friendly_id="Device", eeps=[{"eep": "D2-14-54"}]
    )
    spec = next(s for s in READINGS["D2-14-54"] if s.key == "temperature")

    sensor = OpusGreenNetReadingSensor(
        MagicMock(), EAG_ID, "gw", device, replace(spec, precision=2)
    )

    assert sensor.suggested_display_precision == 2


def test_unlock_request_stays_unknown_until_the_handle_reports_it() -> None:
    device = EnOceanDevice(
        device_id="HANDLE", friendly_id="Handle", eeps=[{"eep": "D2-06-40"}]
    )
    device.get_or_create_channel(0)
    sensor = OpusGreenNetHandleSensor(
        MagicMock(), EAG_ID, "gw", device, "unlock_request"
    )

    assert sensor.native_value is None


async def test_rediscovery_and_foreign_readings_add_no_channel_entities(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("COSI1", "D2-34-10")]
    entry = await setup_bridge(hass, gateway)
    await wait_for_entity(hass, "sensor", f"{EAG_ID}_COSI1_heater_mode")
    coordinator = entry.runtime_data.coordinator
    registry = er.async_get(hass)

    def channel_ids() -> set[str]:
        return {
            e.unique_id
            for e in registry.entities.values()
            if e.platform == DOMAIN and re.search(r"_channel_\d+_", e.unique_id)
        }

    # A channel whose only reading is none of the channel readings.
    await gateway.push(telegram("COSI1", [{"key": "channelNumber", "value": "2"}]))
    await gateway.push(
        telegram(
            "COSI1",
            [
                {"key": "channel", "value": "2"},
                {"key": "heaterMode", "value": "on"},
            ],
        )
    )
    await wait_until(lambda: 2 in coordinator.devices["COSI1"].channels)
    async_dispatcher_send(
        hass,
        f"{SIGNAL_DEVICE_DISCOVERED}_{EAG_ID}",
        coordinator.devices["COSI1"],
    )
    await hass.async_block_till_done()
    assert channel_ids() == set()


async def test_run_sensor_restores_a_value_without_a_saved_state(
    hass: HomeAssistant,
) -> None:
    device = EnOceanDevice(
        device_id="BLIND", friendly_id="Blind", eeps=[{"eep": "D2-05-02"}]
    )
    sensor = OpusGreenNetCoverRunSensor(MagicMock(), EAG_ID, "gw", device, 0)
    sensor.hass = hass
    sensor.entity_id = "sensor.blind_last_run_time"
    stored = restore_state.StoredState(
        State("sensor.blind_last_run_time", "12.5"),
        restore_state.RestoredExtraData(
            {"native_value": 12.5, "native_unit_of_measurement": "s"}
        ),
        dt_util.utcnow(),
    )
    seed_restored_states(hass, {sensor.entity_id: stored})

    with patch.object(sensor, "async_get_last_state", AsyncMock(return_value=None)):
        await sensor.async_added_to_hass()

    assert sensor._restored_value == 12.5
    assert sensor._restored_attributes is None
