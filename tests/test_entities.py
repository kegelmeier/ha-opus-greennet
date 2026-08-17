"""Tests for Opus GreenNet entity platforms."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfPower

from custom_components.opus_greennet import OpusGreenNetRuntimeData
from custom_components.opus_greennet.binary_sensor import (
    OpusGreenNetBatterySensor,
    OpusGreenNetProblemSensor,
    OpusGreenNetWindowSensor,
)
from custom_components.opus_greennet.binary_sensor import (
    async_setup_entry as async_setup_binary_sensors,
)
from custom_components.opus_greennet.climate import (
    OpusGreenNetClimate,
)
from custom_components.opus_greennet.climate import (
    async_setup_entry as async_setup_climate,
)
from custom_components.opus_greennet.cover import (
    OpusGreenNetCover,
)
from custom_components.opus_greennet.cover import (
    async_setup_entry as async_setup_covers,
)
from custom_components.opus_greennet.enocean_device import (
    EnOceanChannel,
    EnOceanDevice,
)
from custom_components.opus_greennet.event import (
    async_setup_entry as async_setup_events,
)
from custom_components.opus_greennet.light import (
    OpusGreenNetLight,
)
from custom_components.opus_greennet.light import (
    async_setup_entry as async_setup_lights,
)
from custom_components.opus_greennet.sensor import (
    OpusGreenNetFeedTemperatureSensor,
    OpusGreenNetHumiditySensor,
    OpusGreenNetPowerConsumptionSensor,
    OpusGreenNetSignalStrengthSensor,
)
from custom_components.opus_greennet.sensor import (
    async_setup_entry as async_setup_sensors,
)
from custom_components.opus_greennet.switch import (
    OpusGreenNetSwitch,
)
from custom_components.opus_greennet.switch import (
    async_setup_entry as async_setup_switches,
)

EAG_ID = "AABB0011"
GATEWAY_DEVICE_ID = "gateway-registry-id"


def _device(eep: str, *, device_id: str = "DEV1") -> EnOceanDevice:
    return EnOceanDevice(
        device_id=device_id,
        friendly_id="Test device",
        manufacturer="OPUS",
        eeps=[{"eep": eep}],
    )


def _coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.available = True
    coordinator.async_turn_on = AsyncMock()
    coordinator.async_turn_off = AsyncMock()
    coordinator.async_set_cover_position = AsyncMock()
    coordinator.async_set_cover_tilt = AsyncMock()
    coordinator.async_stop_cover = AsyncMock()
    coordinator.async_set_climate_setpoint = AsyncMock()
    coordinator.async_set_climate_mode = AsyncMock()
    return coordinator


def _entry(coordinator: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(
        data={"eag_id": EAG_ID},
        runtime_data=OpusGreenNetRuntimeData(
            coordinator=coordinator,
            gateway_device_id=GATEWAY_DEVICE_ID,
        ),
        async_on_unload=MagicMock(),
    )


@pytest.mark.asyncio
async def test_light_properties_and_commands() -> None:
    coordinator = _coordinator()
    device = _device("D2-01-06")
    device.channels[1] = EnOceanChannel(channel_id=1, is_on=True, brightness=50)
    entity = OpusGreenNetLight(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, device, channel_id=1
    )
    entity.async_write_ha_state = MagicMock()

    assert entity.is_on is True
    assert entity.brightness == 127
    assert entity.translation_placeholders == {"channel": "1"}
    assert entity.device_info["via_device_id"] == GATEWAY_DEVICE_ID
    assert entity.available is True

    await entity.async_turn_on(brightness=128)
    coordinator.async_turn_on.assert_awaited_once_with("DEV1", 1, 50, is_dimmable=True)
    await entity.async_turn_off()
    coordinator.async_turn_off.assert_awaited_once_with("DEV1", 1, is_dimmable=True)
    assert device.channels[1].is_on is False


@pytest.mark.asyncio
async def test_switch_properties_and_commands() -> None:
    coordinator = _coordinator()
    device = _device("D2-01-11")
    entity = OpusGreenNetSwitch(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, device, channel_id=0
    )
    entity.async_write_ha_state = MagicMock()

    assert entity.is_on is False
    await entity.async_turn_on()
    assert entity.is_on is True
    await entity.async_turn_off()
    assert entity.is_on is False
    coordinator.async_turn_on.assert_awaited_once_with("DEV1", 0)
    coordinator.async_turn_off.assert_awaited_once_with("DEV1", 0)


@pytest.mark.asyncio
async def test_cover_properties_and_commands() -> None:
    coordinator = _coordinator()
    device = _device("D2-05-00")
    device.channels[0] = EnOceanChannel(channel_id=0, position=20, angle=45)
    entity = OpusGreenNetCover(coordinator, EAG_ID, GATEWAY_DEVICE_ID, device)
    entity.async_write_ha_state = MagicMock()

    assert entity.current_cover_position == 80
    assert entity.current_cover_tilt_position == 45
    assert entity.is_closed is False
    assert entity.is_opening is None
    assert entity.is_closing is None

    await entity.async_open_cover()
    await entity.async_close_cover()
    await entity.async_set_cover_position(position=35)
    await entity.async_set_cover_tilt_position(tilt_position=70)
    await entity.async_stop_cover()

    assert coordinator.async_set_cover_position.await_args_list[0].args == (
        "DEV1",
        0,
        0,
    )
    assert coordinator.async_set_cover_position.await_args_list[1].args == (
        "DEV1",
        100,
        0,
    )
    assert coordinator.async_set_cover_position.await_args_list[2].args == (
        "DEV1",
        65,
        0,
    )
    coordinator.async_set_cover_tilt.assert_awaited_once_with("DEV1", 70, 0)
    coordinator.async_stop_cover.assert_awaited_once_with("DEV1", 0)


@pytest.mark.asyncio
async def test_climate_properties_and_commands() -> None:
    coordinator = _coordinator()
    device = _device("D1-4B-06")
    device.channels[0] = EnOceanChannel(
        channel_id=0,
        temperature=21.2,
        temperature_setpoint=22.0,
        humidity=53,
        heater_mode="on",
        thermal_mode="cooling",
    )
    entity = OpusGreenNetClimate(coordinator, EAG_ID, GATEWAY_DEVICE_ID, device)

    assert entity.current_temperature == 21.2
    assert entity.target_temperature == 22.0
    assert entity.current_humidity == 53
    assert entity.hvac_mode is HVACMode.HEAT_COOL
    assert entity.hvac_action is HVACAction.COOLING

    await entity.async_set_temperature(temperature=23.5)
    await entity.async_set_hvac_mode(HVACMode.OFF)
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    coordinator.async_set_climate_setpoint.assert_awaited_once_with("DEV1", 23.5)
    assert coordinator.async_set_climate_mode.await_args_list[0].args == (
        "DEV1",
        "off",
    )
    assert coordinator.async_set_climate_mode.await_args_list[1].args == (
        "DEV1",
        "on",
    )


def test_sensor_values_and_metadata() -> None:
    coordinator = _coordinator()
    device = _device("D1-4B-07")
    device.channels[0] = EnOceanChannel(
        channel_id=0,
        humidity=48.5,
        feed_temperature=34.2,
        energy_consumption=1.25,
    )
    device.dbm = 0

    humidity = OpusGreenNetHumiditySensor(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, device
    )
    feed = OpusGreenNetFeedTemperatureSensor(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, device
    )
    power = OpusGreenNetPowerConsumptionSensor(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, device
    )
    signal = OpusGreenNetSignalStrengthSensor(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, device
    )

    assert humidity.native_value == 48.5
    assert feed.native_value == 34.2
    assert power.native_value == 1.25
    assert power.device_class is SensorDeviceClass.POWER
    assert power.native_unit_of_measurement is UnitOfPower.KILO_WATT
    assert power.unique_id.endswith("_energy_consumption")
    assert signal.native_value == 0
    assert signal.entity_registry_enabled_default is False


def test_binary_sensor_values() -> None:
    coordinator = _coordinator()
    device = _device("D1-4B-05")
    device.channels[0] = EnOceanChannel(
        channel_id=0,
        window_open=True,
        actuator_not_responding="warning",
        actuator_low_battery="reset",
    )

    window = OpusGreenNetWindowSensor(coordinator, EAG_ID, GATEWAY_DEVICE_ID, device)
    problem = OpusGreenNetProblemSensor(
        coordinator,
        EAG_ID,
        GATEWAY_DEVICE_ID,
        device,
        suffix="actuator_not_responding",
        translation_key="actuator_not_responding",
        attr_name="actuator_not_responding",
    )
    battery = OpusGreenNetBatterySensor(coordinator, EAG_ID, GATEWAY_DEVICE_ID, device)

    assert window.is_on is True
    assert problem.is_on is True
    assert battery.is_on is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "eep", "expected_count"),
    [
        (async_setup_lights, "D2-01-06", 2),
        (async_setup_switches, "D2-01-11", 2),
        (async_setup_covers, "D2-05-00", 1),
        (async_setup_climate, "D1-4B-06", 1),
        (async_setup_sensors, "D1-4B-07", 3),
        (async_setup_binary_sensors, "D1-4B-05", 5),
        (async_setup_events, "F6-02-01", 1),
    ],
)
async def test_platform_setup_adds_expected_entities(
    setup, eep: str, expected_count: int
) -> None:
    coordinator = _coordinator()
    coordinator.devices = {"DEV1": _device(eep)}
    entry = _entry(coordinator)
    async_add_entities = MagicMock()

    module_name = setup.__module__
    with patch(f"{module_name}.async_dispatcher_connect", return_value=MagicMock()):
        await setup(MagicMock(), entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert len(entities) == expected_count
    entry.async_on_unload.assert_called_once()
