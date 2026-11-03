"""Counters of the gateway as diagnostic sensors (received telegrams, mean signal).

Both come from ``/system/info``, which is read at setup and every 30 minutes:
plain gateway reads, no radio traffic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.coordinator import SIGNAL_GATEWAY_INFO_UPDATE
from custom_components.opus_greennet.sensor import (
    GATEWAY_INFO_NUMBERS,
    OpusGreenNetGatewayNumberSensor,
)
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge, wait_for_entity

EAG_ID = "AABB0011"


def _sensor(info: dict, key: str, *, available: bool = True):
    coordinator = MagicMock(gateway_info=info, available=available)
    return OpusGreenNetGatewayNumberSensor(coordinator, EAG_ID, key)


def test_the_counters_are_listed() -> None:
    assert set(GATEWAY_INFO_NUMBERS) == {
        "gateway_receive_counter",
        "gateway_average_dbm",
    }


def test_values_and_metadata() -> None:
    info = {"receiveCounter": 1234, "averageDbm": -61}
    counter = _sensor(info, "gateway_receive_counter")
    signal = _sensor(info, "gateway_average_dbm")

    assert counter.native_value == 1234
    assert counter.state_class is SensorStateClass.TOTAL_INCREASING
    assert counter.device_class is None
    assert signal.native_value == -61
    assert signal.device_class is SensorDeviceClass.SIGNAL_STRENGTH
    assert signal.native_unit_of_measurement == SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    assert signal.state_class is SensorStateClass.MEASUREMENT
    for sensor in (counter, signal):
        assert sensor.entity_category is EntityCategory.DIAGNOSTIC
        assert sensor.entity_registry_enabled_default is True
        assert sensor.should_poll is False
        assert sensor.has_entity_name is True
    assert counter.unique_id == f"{EAG_ID}_gateway_receive_counter"
    assert signal.unique_id == f"{EAG_ID}_gateway_average_dbm"


@pytest.mark.parametrize("value", [None, True, False, "12", "n/a", [], {}])
def test_anything_but_a_number_is_unknown(value) -> None:
    for key, field in (
        ("gateway_receive_counter", "receiveCounter"),
        ("gateway_average_dbm", "averageDbm"),
    ):
        assert _sensor({field: value}, key).native_value is None
    assert _sensor({}, "gateway_average_dbm").native_value is None


def test_unavailable_with_the_gateway() -> None:
    assert _sensor({}, "gateway_receive_counter", available=False).available is False


async def test_a_new_reading_is_shown() -> None:
    info = {"receiveCounter": 5}
    sensor = _sensor(info, "gateway_receive_counter")
    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.test"
    sensor.platform = MagicMock()
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_update()

    write.assert_called_once()
    assert SIGNAL_GATEWAY_INFO_UPDATE.endswith("gateway_info_update")


async def test_the_sensors_in_home_assistant(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    gateway.system_info = {"receiveCounter": 4321, "averageDbm": -67}
    entry = await setup_bridge(hass, gateway)
    eag = entry.data["eag_id"]
    counter_id = await wait_for_entity(hass, "sensor", f"{eag}_gateway_receive_counter")
    signal_id = await wait_for_entity(hass, "sensor", f"{eag}_gateway_average_dbm")

    registry = er.async_get(hass)
    for entity_id in (counter_id, signal_id):
        assert registry.async_get(entity_id).entity_category.value == "diagnostic"
    assert hass.states.get(counter_id).state == "4321"
    assert hass.states.get(signal_id).state == "-67"
    assert hass.states.get(signal_id).attributes["unit_of_measurement"] == "dBm"
