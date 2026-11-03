"""Heat Area 3.0 Physical (D1-4B-03, Moellenhoff room control unit).

The profile comes from the gateway catalog (``GET /profiles/D1-4B-03``): the
controller sends ``temperatureSetpoint`` and ``query``; it reports
``heaterMode`` (heating/cooling), ``temperature``, ``temperatureSetpoint``,
``valvePosition`` and the errors ``communicationError``, ``heatCircuitsError``,
``lowBattery`` and ``rcuMalfunction``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.exceptions import ServiceValidationError

from custom_components.opus_greennet.climate import OpusGreenNetClimate
from custom_components.opus_greennet.const import EEP_MAPPINGS
from custom_components.opus_greennet.enocean_device import EnOceanChannel
from custom_components.opus_greennet.sensor import (
    OpusGreenNetValvePositionSensor,
)
from custom_components.opus_greennet.sensor import (
    async_setup_entry as async_setup_sensors,
)


def _climate(make_device, **fields) -> tuple[OpusGreenNetClimate, MagicMock]:
    coordinator = MagicMock()
    coordinator.available = True
    coordinator.async_set_climate_setpoint = AsyncMock()
    coordinator.async_set_climate_mode = AsyncMock()
    device = make_device("D1-4B-03", device_id="DEV1")
    device.channels[0] = EnOceanChannel(channel_id=0, **fields)
    return OpusGreenNetClimate(coordinator, "AABB0011", "gw", device), coordinator


def test_profile_is_a_climate_device(make_device) -> None:
    device = make_device("D1-4B-03")

    assert EEP_MAPPINGS["D1-4B-03"][0] == "climate"
    assert device.entity_type == "climate"
    assert device.is_climate
    assert device.heat_area_type == "physical"
    assert device.setpoint_step == 0.2
    assert device.setpoint_limits == (5.0, 30.0)
    # Existing profiles keep their ranges.
    for eep in ("D1-4B-05", "D1-4B-06", "D1-4B-07"):
        assert make_device(eep).setpoint_limits == (0.0, 40.0)


def test_telegram_fields(make_device, make_telegram) -> None:
    device = make_device("D1-4B-03")
    device.update_from_telegram(
        make_telegram(
            [
                {"key": "heaterMode", "value": "cooling"},
                {"key": "temperature", "value": "21.4"},
                {"key": "temperatureSetpoint", "value": "22.2"},
                {"key": "valvePosition", "value": "35"},
                {"key": "communicationError", "value": "error"},
                {"key": "heatCircuitsError", "value": "error"},
                {"key": "lowBattery", "value": "warning"},
                {"key": "rcuMalfunction", "value": "error"},
            ]
        )
    )
    channel = device.channels[0]

    assert channel.heater_mode == "cooling"
    assert channel.temperature == 21.4
    assert channel.temperature_setpoint == 22.2
    assert channel.valve_position == 35
    assert channel.communication_error == "error"
    assert channel.heat_circuits_error == "error"
    assert channel.rcu_low_battery == "warning"
    assert channel.rcu_malfunction == "error"

    device.update_from_telegram(
        make_telegram(
            [
                {"key": key, "value": "reset"}
                for key in (
                    "communicationError",
                    "heatCircuitsError",
                    "lowBattery",
                    "rcuMalfunction",
                )
            ]
        )
    )
    assert channel.communication_error == "reset"
    assert channel.rcu_low_battery == "reset"


def test_valve_position_unavailable_and_malformed(make_device, make_telegram) -> None:
    device = make_device("D1-4B-03")
    channel = device.get_or_create_channel()
    device.update_from_telegram(
        make_telegram([{"key": "valvePosition", "value": "50"}])
    )
    assert channel.valve_position == 50

    # Out of range or malformed keeps the known value (missing is no delete).
    for bad in ("101", "-1", "abc", None, True, {}):
        device.update_from_telegram(
            make_telegram([{"key": "valvePosition", "value": bad}])
        )
        assert channel.valve_position == 50

    device.update_from_telegram(
        make_telegram([{"key": "valvePosition", "value": "notAvailable"}])
    )
    assert channel.valve_position is None


@pytest.mark.parametrize(
    "key", ["communicationError", "heatCircuitsError", "rcuMalfunction"]
)
@pytest.mark.parametrize("value", ["warning", "noChange", None, 1, {}])
def test_invalid_error_values_keep_state(
    make_device, make_telegram, key, value
) -> None:
    device = make_device("D1-4B-03")
    channel = device.get_or_create_channel()
    attribute = {
        "communicationError": "communication_error",
        "heatCircuitsError": "heat_circuits_error",
        "rcuMalfunction": "rcu_malfunction",
    }[key]
    setattr(channel, attribute, "error")

    device.update_from_telegram(make_telegram([{"key": key, "value": value}]))

    assert getattr(channel, attribute) == "error"


@pytest.mark.parametrize("value", ["error", "noChange", None, 1, {}])
def test_invalid_low_battery_keeps_state(make_device, make_telegram, value) -> None:
    device = make_device("D1-4B-03")
    channel = device.get_or_create_channel()
    channel.rcu_low_battery = "warning"

    device.update_from_telegram(make_telegram([{"key": "lowBattery", "value": value}]))

    assert channel.rcu_low_battery == "warning"


@pytest.mark.asyncio
async def test_climate_entity(make_device) -> None:
    entity, coordinator = _climate(
        make_device,
        temperature=20.8,
        temperature_setpoint=21.0,
        heater_mode="heating",
        valve_position=60,
    )

    assert entity.min_temp == 5
    assert entity.max_temp == 30
    assert entity.target_temperature_step == 0.2
    assert entity.supported_features == ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.hvac_modes == [HVACMode.HEAT, HVACMode.COOL]
    assert entity.current_temperature == 20.8
    assert entity.target_temperature == 21.0
    assert entity.hvac_mode is HVACMode.HEAT
    # The planned valve share is not a report that the actuator is heating.
    assert entity.hvac_action is None

    entity._device.channels[0].heater_mode = "cooling"
    assert entity.hvac_mode is HVACMode.COOL
    assert entity.hvac_action is None

    await entity.async_set_temperature(temperature=22.4)
    coordinator.async_set_climate_setpoint.assert_awaited_once_with("DEV1", 22.4)


@pytest.mark.asyncio
async def test_hvac_mode_cannot_be_written(make_device) -> None:
    entity, coordinator = _climate(make_device, heater_mode="heating")

    for mode in (HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF):
        with pytest.raises(ServiceValidationError):
            await entity.async_set_hvac_mode(mode)
    coordinator.async_set_climate_mode.assert_not_awaited()


def test_hvac_mode_unknown_without_report(make_device) -> None:
    entity, _ = _climate(make_device)

    assert entity.hvac_mode is None
    assert entity.hvac_action is not HVACAction.HEATING


def test_cooling_stays_unmapped_for_other_profiles(make_device) -> None:
    device = make_device("D1-4B-05")
    device.channels[0] = EnOceanChannel(channel_id=0, heater_mode="cooling")
    entity = OpusGreenNetClimate(MagicMock(), "AABB0011", "gw", device)

    assert entity.hvac_mode is None


def test_valve_position_sensor(make_device) -> None:
    device = make_device("D1-4B-03")
    device.channels[0] = EnOceanChannel(channel_id=0, valve_position=40)
    sensor = OpusGreenNetValvePositionSensor(MagicMock(), "AABB0011", "gw", device)

    assert sensor.native_value == 40
    assert sensor.unique_id == "AABB0011_AABB1122_valve_position"
    assert sensor.translation_key == "valve_position"
    assert sensor.native_unit_of_measurement == "%"


@pytest.mark.asyncio
async def test_sensor_platform_has_valve_position_but_no_humidity(
    hass, make_device
) -> None:
    device = make_device("D1-4B-03")
    coordinator = MagicMock(
        eag_id="AABB0011", devices={device.device_id: device}, available=True
    )
    entry = SimpleNamespace(
        data={"eag_id": "AABB0011"},
        runtime_data=SimpleNamespace(coordinator=coordinator, gateway_device_id="gw"),
        entry_id="entry",
        async_on_unload=MagicMock(),
    )
    add = MagicMock()

    await async_setup_sensors(hass, entry, add)

    keys = {
        entity.unique_id.removeprefix("AABB0011_AABB1122_")
        for call in add.call_args_list
        for entity in call.args[0]
    }
    assert "valve_position" in keys
    assert "humidity" not in keys
    assert "feed_temperature" not in keys
