"""Tests for Opus GreenNet entity platforms."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import climate
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.climate import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfPower

from custom_components.opus_greennet import OpusGreenNetRuntimeData
from custom_components.opus_greennet.binary_sensor import (
    OpusGreenNetBatterySensor,
    OpusGreenNetMoistureSensor,
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
from custom_components.opus_greennet.entity import (
    migrate_legacy_multichannel_entity,
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
from tests.ha_helpers import configure_bridge, wait_for_entity

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
    assert entity.brightness == 128
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

    assert entity.is_on is None
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


@pytest.mark.parametrize("eep", ["D2-05-00", "D2-05-01", "D2-05-02"])
def test_cover_tilt_features_follow_late_parameter_updates(eep, make_telegram):
    device = _device(eep)
    device.channels[0] = EnOceanChannel(channel_id=0, position=20, angle=45)
    entity = OpusGreenNetCover(_coordinator(), EAG_ID, GATEWAY_DEVICE_ID, device)
    position_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    expected_features = position_features
    if eep != "D2-05-01":
        expected_features |= CoverEntityFeature.SET_TILT_POSITION

    assert entity.supported_features == expected_features
    assert entity.current_cover_tilt_position == (45 if eep != "D2-05-01" else None)

    device.update_from_telegram(
        make_telegram([{"key": "rotationTime", "value": "noRotation"}])
    )
    assert entity.supported_features == position_features
    assert entity.current_cover_tilt_position is None
    assert entity.current_cover_position == 80

    device.update_from_telegram(
        make_telegram([{"key": "rotationTime", "value": "1.5"}])
    )
    assert entity.supported_features == expected_features
    assert entity.current_cover_tilt_position == (45 if eep != "D2-05-01" else None)


def test_cover_tilt_capability_is_specific_to_entity_channel(make_telegram):
    device = _device("D2-05-00")
    device.channels[0] = EnOceanChannel(channel_id=0, angle=30)
    device.channels[1] = EnOceanChannel(channel_id=1, angle=60)
    default_entity = OpusGreenNetCover(
        _coordinator(), EAG_ID, GATEWAY_DEVICE_ID, device
    )
    second_entity = OpusGreenNetCover(
        _coordinator(), EAG_ID, GATEWAY_DEVICE_ID, device, channel_id=1
    )
    device.update_from_telegram(
        make_telegram([{"key": "rotationTime", "value": 0, "channel": 1}])
    )

    assert default_entity.supported_features & CoverEntityFeature.SET_TILT_POSITION
    assert default_entity.current_cover_tilt_position == 30
    assert not second_entity.supported_features & CoverEntityFeature.SET_TILT_POSITION
    assert second_entity.current_cover_tilt_position is None


@pytest.mark.asyncio
@pytest.mark.parametrize("eep", ["D2-05-00", "D2-05-01", "D2-05-02"])
async def test_cover_without_tilt_retains_position_commands(eep, make_telegram):
    coordinator = _coordinator()
    device = _device(eep)
    device.channels[1] = EnOceanChannel(channel_id=1, angle=45)
    entity = OpusGreenNetCover(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, device, channel_id=1
    )
    entity.async_write_ha_state = MagicMock()
    device.update_from_telegram(
        make_telegram([{"key": "rotationTime", "value": "0", "channel": 1}])
    )

    await entity.async_set_cover_tilt_position(tilt_position=70)
    coordinator.async_set_cover_tilt.assert_not_awaited()
    assert device.channels[1].angle == 45
    entity.async_write_ha_state.assert_not_called()

    await entity.async_open_cover()
    assert entity.current_cover_position == 100
    await entity.async_close_cover()
    assert entity.current_cover_position == 0
    await entity.async_set_cover_position(position=35)
    assert entity.current_cover_position == 35
    await entity.async_stop_cover()

    assert [
        call.args for call in coordinator.async_set_cover_position.await_args_list
    ] == [
        ("DEV1", 0, 1),
        ("DEV1", 100, 1),
        ("DEV1", 65, 1),
    ]
    coordinator.async_stop_cover.assert_awaited_once_with("DEV1", 1)


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
    assert entity.hvac_action is None

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


def test_moisture_sensor_values_and_metadata() -> None:
    coordinator = _coordinator()
    device = _device("F6-05-01")
    moisture = OpusGreenNetMoistureSensor(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, device
    )

    assert moisture.is_on is None
    assert moisture.device_class is BinarySensorDeviceClass.MOISTURE
    assert moisture.unique_id == f"{EAG_ID}_DEV1_liquid_detected"

    device.channels[0] = EnOceanChannel(channel_id=0, liquid_detected=True)
    assert moisture.is_on is True

    device.channels[0].liquid_detected = False
    assert moisture.is_on is False


def test_removes_obsolete_aggregate_when_channel_zero_exists() -> None:
    registry = MagicMock()
    registry.async_get_entity_id.side_effect = [
        "switch.test_device",
        "switch.test_device_channel_0",
    ]

    migrate_legacy_multichannel_entity(
        registry,
        "switch",
        EAG_ID,
        _device("D2-01-11"),
    )

    registry.async_remove.assert_called_once_with("switch.test_device")
    registry.async_update_entity.assert_not_called()


def test_migrates_legacy_entity_when_channel_zero_is_missing() -> None:
    registry = MagicMock()
    registry.async_get_entity_id.side_effect = ["switch.test_device", None]

    migrate_legacy_multichannel_entity(
        registry,
        "switch",
        EAG_ID,
        _device("D2-01-11"),
    )

    registry.async_update_entity.assert_called_once_with(
        "switch.test_device",
        new_unique_id=f"{EAG_ID}_DEV1_ch0",
    )
    registry.async_remove.assert_not_called()


def test_keeps_single_channel_registry_entity() -> None:
    registry = MagicMock()

    migrate_legacy_multichannel_entity(
        registry,
        "switch",
        EAG_ID,
        _device("D2-01-01"),
    )

    registry.async_get_entity_id.assert_not_called()
    registry.async_remove.assert_not_called()
    registry.async_update_entity.assert_not_called()


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
        (async_setup_binary_sensors, "F6-05-01", 1),
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


@pytest.mark.asyncio
async def test_sensor_setup_deduplicates_discovery_entities() -> None:
    coordinator = _coordinator()
    device = _device("D2-01-01")
    coordinator.devices = {device.device_id: device}
    entry = _entry(coordinator)
    async_add_entities = MagicMock()

    with patch(
        "custom_components.opus_greennet.sensor.async_dispatcher_connect"
    ) as connect:
        await async_setup_sensors(MagicMock(), entry, async_add_entities)

    discovery_callback = connect.call_args.args[2]
    discovery_callback(device)

    async_add_entities.assert_called_once()
    initial_entities = async_add_entities.call_args.args[0]
    assert [entity.unique_id for entity in initial_entities] == [
        f"{EAG_ID}_DEV1_signal_strength"
    ]


@pytest.mark.asyncio
async def test_sensor_setup_adds_new_types_after_profile_discovery() -> None:
    coordinator = _coordinator()
    incomplete_device = _device("D2-01-01")
    incomplete_device.eeps = []
    coordinator.devices = {incomplete_device.device_id: incomplete_device}
    entry = _entry(coordinator)
    async_add_entities = MagicMock()

    with patch(
        "custom_components.opus_greennet.sensor.async_dispatcher_connect"
    ) as connect:
        await async_setup_sensors(MagicMock(), entry, async_add_entities)

    discovery_callback = connect.call_args.args[2]
    discovery_callback(_device("D1-4B-05"))

    assert async_add_entities.call_count == 2
    discovered_entities = async_add_entities.call_args_list[1].args[0]
    assert {entity.unique_id for entity in discovered_entities} == {
        f"{EAG_ID}_DEV1_humidity",
        f"{EAG_ID}_DEV1_feed_temperature",
    }


@pytest.mark.parametrize(
    "entity_class,eep,domain",
    [
        (OpusGreenNetLight, "D2-01-02", "light"),
        (OpusGreenNetSwitch, "D2-01-01", "switch"),
        (OpusGreenNetClimate, "D1-4B-07", "climate"),
    ],
)
async def test_unreported_state_is_unknown_in_home_assistant(
    hass, mqtt_transport, entity_class, eep, domain
):
    mqtt_transport.devices = [
        {"deviceId": "DEV1", "friendlyId": "Test", "eeps": [{"eep": eep}]}
    ]
    await configure_bridge(hass)
    entity_id = await wait_for_entity(hass, domain, f"{EAG_ID}_DEV1")

    assert hass.states.get(entity_id).state == "unknown"
    entity = entity_class(_coordinator(), EAG_ID, GATEWAY_DEVICE_ID, _device(eep))
    assert entity.should_poll is False


@pytest.mark.parametrize("brightness,expected", [(1, 1), (2, 1), (128, 50), (255, 100)])
async def test_light_nonzero_brightness_never_turns_off(brightness, expected):
    coordinator = _coordinator()
    entity = OpusGreenNetLight(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, _device("D2-01-02")
    )
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_on(brightness=brightness)

    coordinator.async_turn_on.assert_awaited_once_with(
        "DEV1", 0, expected, is_dimmable=True
    )
    assert entity.is_on is True
    assert entity.brightness >= 1


async def test_light_zero_brightness_uses_off_command():
    coordinator = _coordinator()
    entity = OpusGreenNetLight(
        coordinator, EAG_ID, GATEWAY_DEVICE_ID, _device("D2-01-02")
    )
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_on(brightness=0)

    coordinator.async_turn_on.assert_not_awaited()
    coordinator.async_turn_off.assert_awaited_once_with("DEV1", 0, is_dimmable=True)
    assert entity.is_on is False


@pytest.mark.parametrize(
    "eep,enabled_mode,command_mode",
    [
        ("D1-4B-05", HVACMode.HEAT, "heating"),
        ("D1-4B-06", HVACMode.HEAT_COOL, "on"),
        ("D1-4B-07", HVACMode.HEAT, "heating"),
    ],
)
async def test_climate_standard_actions_in_home_assistant(
    hass, mqtt_transport, eep, enabled_mode, command_mode
):
    mqtt_transport.devices = [
        {"deviceId": "DEV1", "friendlyId": "Test", "eeps": [{"eep": eep}]}
    ]
    result = await configure_bridge(hass)
    entity_id = await wait_for_entity(hass, "climate", f"{EAG_ID}_DEV1")
    coordinator = result["result"].runtime_data.coordinator
    device = coordinator.get_device("DEV1")
    channel = device.get_or_create_channel()
    entity = hass.data[climate.DATA_COMPONENT].get_entity(entity_id)

    assert entity.supported_features & ClimateEntityFeature.TURN_ON
    assert entity.supported_features & ClimateEntityFeature.TURN_OFF
    for action, reported_mode, expected_command in (
        ("turn_on", "off", command_mode),
        ("turn_off", command_mode, "off"),
        ("toggle", "off", command_mode),
        ("toggle", command_mode, "off"),
    ):
        channel.heater_mode = reported_mode
        entity.async_write_ha_state()
        await hass.services.async_call(
            "climate", action, {"entity_id": entity.entity_id}, blocking=True
        )
        command_payloads = [
            json.loads(payload)
            for topic, payload in mqtt_transport.published
            if topic.endswith("/put/devices/DEV1/state")
        ]
        assert command_payloads[-1]["state"]["functions"] == [
            {"key": "heaterMode", "value": expected_command}
        ]

    channel.heater_mode = "autoOff"
    entity.async_write_ha_state()
    state = hass.states.get(entity.entity_id)
    assert state.state == enabled_mode
    assert state.attributes["hvac_action"] == HVACAction.IDLE


@pytest.mark.parametrize(
    "heater_mode,power_state,expected",
    [
        ("heating", "active", HVACAction.HEATING),
        ("on", "inactive", HVACAction.IDLE),
        ("heating", None, None),
        ("autoOff", "active", HVACAction.IDLE),
        ("off", "active", HVACAction.OFF),
        ("configIncomplete", "active", None),
        ("error", "active", None),
    ],
)
def test_climate_action_uses_reported_actuator_activity(
    heater_mode, power_state, expected
):
    device = _device("D1-4B-07")
    device.channels[0] = EnOceanChannel(
        channel_id=0, heater_mode=heater_mode, power_state=power_state
    )
    entity = OpusGreenNetClimate(_coordinator(), EAG_ID, GATEWAY_DEVICE_ID, device)
    assert entity.hvac_action == expected


@pytest.mark.parametrize("mode", [None, "error", "configIncomplete"])
def test_climate_unknown_and_failed_modes_do_not_claim_off(mode):
    device = _device("D1-4B-05")
    device.channels[0] = EnOceanChannel(channel_id=0, heater_mode=mode)
    entity = OpusGreenNetClimate(_coordinator(), EAG_ID, GATEWAY_DEVICE_ID, device)
    assert entity.hvac_mode is None
    assert entity.hvac_action is None


@pytest.mark.parametrize("initial_channel", [False, True])
@pytest.mark.parametrize(
    "entity_class,eep,action,kwargs,coordinator_method,key,value,attribute,expected",
    [
        (
            OpusGreenNetSwitch,
            "D2-01-00",
            "async_turn_on",
            {},
            "async_turn_on",
            "switch",
            "off",
            "is_on",
            False,
        ),
        (
            OpusGreenNetSwitch,
            "D2-01-00",
            "async_turn_off",
            {},
            "async_turn_off",
            "switch",
            "on",
            "is_on",
            True,
        ),
        (
            OpusGreenNetLight,
            "D2-01-02",
            "async_turn_on",
            {"brightness": 200},
            "async_turn_on",
            "dimValue",
            30,
            "brightness",
            30,
        ),
        (
            OpusGreenNetLight,
            "D2-01-02",
            "async_turn_off",
            {},
            "async_turn_off",
            "dimValue",
            70,
            "brightness",
            70,
        ),
        (
            OpusGreenNetCover,
            "D2-05-00",
            "async_open_cover",
            {},
            "async_set_cover_position",
            "position",
            50,
            "position",
            50,
        ),
        (
            OpusGreenNetCover,
            "D2-05-00",
            "async_close_cover",
            {},
            "async_set_cover_position",
            "position",
            50,
            "position",
            50,
        ),
        (
            OpusGreenNetCover,
            "D2-05-00",
            "async_set_cover_position",
            {"position": 65},
            "async_set_cover_position",
            "position",
            30,
            "position",
            30,
        ),
        (
            OpusGreenNetCover,
            "D2-05-00",
            "async_set_cover_tilt_position",
            {"tilt_position": 75},
            "async_set_cover_tilt",
            "angle",
            20,
            "angle",
            20,
        ),
    ],
)
async def test_feedback_during_command_ack_wins_over_optimistic_state(
    initial_channel,
    entity_class,
    eep,
    action,
    kwargs,
    coordinator_method,
    key,
    value,
    attribute,
    expected,
):
    coordinator = _coordinator()
    device = _device(eep)
    if initial_channel:
        device.get_or_create_channel()
    entity = entity_class(coordinator, EAG_ID, GATEWAY_DEVICE_ID, device)
    entity.async_write_ha_state = MagicMock()

    async def receive_feedback_before_ack(*args, **kwargs):
        await asyncio.sleep(0)
        device.update_from_telegram({"functions": [{"key": key, "value": value}]})
        entity._handle_state_update(device)

    getattr(coordinator, coordinator_method).side_effect = receive_feedback_before_ack
    await getattr(entity, action)(**kwargs)

    assert getattr(device.channels[0], attribute) == expected
    entity.async_write_ha_state.assert_called_once()


async def test_other_channel_feedback_does_not_block_optimistic_state():
    coordinator = _coordinator()
    device = _device("D2-01-04")
    device.channels[0] = EnOceanChannel(channel_id=0, is_on=False)
    entity = OpusGreenNetSwitch(coordinator, EAG_ID, GATEWAY_DEVICE_ID, device)
    entity.async_write_ha_state = MagicMock()

    async def receive_other_channel_feedback(*args):
        device.update_from_telegram(
            {"functions": [{"key": "switch", "value": "off", "channel": 1}]}
        )

    coordinator.async_turn_on.side_effect = receive_other_channel_feedback
    await entity.async_turn_on()

    assert device.channels[0].is_on is True
    assert device.channels[1].is_on is False
