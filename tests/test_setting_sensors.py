"""Read-only diagnostic sensors for the settings stored on a device.

They show what the number and select entities would write, so the values are
visible without enabling the writing entities. They read the same channel
fields, are enabled by default and never write anything.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import DEVICE_TIMES
from custom_components.opus_greennet.select import DEVICE_SELECTS
from custom_components.opus_greennet.sensor import (
    OpusGreenNetSettingChoiceSensor,
    OpusGreenNetSettingSensor,
    async_setup_entry,
)

EAG_ID = "AABB0011"


def _device(eep: str) -> EnOceanDevice:
    return EnOceanDevice("DEV1", "Device", [{"eep": eep}])


async def _created(eep: str) -> list:
    device = _device(eep)
    coordinator = MagicMock(eag_id=EAG_ID, devices={"DEV1": device})
    coordinator.available = True
    coordinator.gateway_info = {}
    added: list = []
    entry = SimpleNamespace(
        entry_id="entry",
        data={"eag_id": EAG_ID},
        runtime_data=SimpleNamespace(
            coordinator=coordinator, gateway_device_id="gateway"
        ),
        async_on_unload=MagicMock(),
    )
    with (
        patch(
            "custom_components.opus_greennet.sensor.async_dispatcher_connect",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.opus_greennet.sensor.migrate_legacy_entity_suffix",
            MagicMock(),
        ),
    ):
        await async_setup_entry(MagicMock(), entry, added.extend)
    return [
        entity
        for entity in added
        if isinstance(
            entity, OpusGreenNetSettingSensor | OpusGreenNetSettingChoiceSensor
        )
    ]


@pytest.mark.parametrize(
    ("eep", "numbers", "choices"),
    [
        ("D2-05-02", 3, 4),
        ("D2-01-03", 3, 1),
        ("D2-01-01", 5, 7),
        ("D2-01-11", 5, 8),
        ("A5-07-03", 3, 0),
        ("A5-07-01", 0, 0),
        ("F6-02-01", 0, 0),
        ("A5-04-01", 0, 0),
    ],
)
async def test_every_device_with_settings_gets_one_sensor_per_setting(
    eep: str, numbers: int, choices: int
) -> None:
    created = await _created(eep)

    assert (
        sum(isinstance(e, OpusGreenNetSettingSensor) for e in created),
        sum(isinstance(e, OpusGreenNetSettingChoiceSensor) for e in created),
    ) == (numbers, choices)
    assert len({entity.unique_id for entity in created}) == len(created)


async def test_the_sensors_follow_the_number_and_select_entities() -> None:
    """One sensor per number or select the device has, none for others."""
    for eep in ("D2-05-02", "D2-01-03", "D2-01-01", "D2-01-11"):
        device = _device(eep)
        expected = {
            f"{EAG_ID}_DEV1_{time.translation_key}_setting"
            for time in DEVICE_TIMES
            if time.applies_to(device)
        } | {
            f"{EAG_ID}_DEV1_{select.translation_key}_setting"
            for select in DEVICE_SELECTS
            if select.values_for(device)
        }
        assert {e.unique_id for e in await _created(eep)} == expected


ROCKER_SENSORS = ("function_rocker_a_setting", "function_rocker_b_setting")


async def test_the_sensors_are_read_only_diagnostics_enabled_by_default() -> None:
    for entity in await _created("D2-01-11"):
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        # The stored rocker functions are the exception: disabled by default.
        assert entity.entity_registry_enabled_default is (
            entity.translation_key not in ROCKER_SENSORS
        )
        assert entity.translation_key.endswith("_setting")
        assert not hasattr(entity, "async_set_native_value")
        assert not hasattr(entity, "async_select_option")
        assert entity.state_class is None


@pytest.mark.parametrize("eep", ["D2-01-01", "D2-01-11", "D2-05-02"])
async def test_the_stored_rocker_functions_are_disabled_by_default(eep: str) -> None:
    created = {e.translation_key: e for e in await _created(eep)}
    for key in ROCKER_SENSORS:
        assert created[key].entity_registry_enabled_default is False


async def test_the_unique_ids_differ_from_the_writing_entities() -> None:
    writing = {f"{EAG_ID}_DEV1_{time.translation_key}" for time in DEVICE_TIMES} | {
        f"{EAG_ID}_DEV1_{select.translation_key}" for select in DEVICE_SELECTS
    }
    assert not {e.unique_id for e in await _created("D2-01-11")} & writing


def test_a_number_sensor_shows_the_channel_field_and_the_unit() -> None:
    device = _device("D2-05-02")
    vertical = next(
        time for time in DEVICE_TIMES if time.parameter == "verticalMovementTime"
    )
    sensor = OpusGreenNetSettingSensor(MagicMock(), EAG_ID, "gateway", device, vertical)

    assert sensor.native_value is None
    channel = device.get_or_create_channel()
    assert sensor.native_value is None
    channel.vertical_movement_time = 25.51
    assert sensor.native_value == 25.51
    assert sensor.native_unit_of_measurement == "s"
    assert sensor.device_class is SensorDeviceClass.DURATION


def test_the_illumination_threshold_is_an_illuminance_in_lux() -> None:
    device = _device("D2-01-01")
    threshold = next(
        time for time in DEVICE_TIMES if time.parameter == "illuminationThreshold"
    )
    sensor = OpusGreenNetSettingSensor(
        MagicMock(), EAG_ID, "gateway", device, threshold
    )

    assert sensor.device_class is SensorDeviceClass.ILLUMINANCE
    assert sensor.native_unit_of_measurement == "lx"


def test_a_choice_sensor_shows_the_option_and_hides_an_unlisted_value() -> None:
    device = _device("D2-01-01")
    select = next(item for item in DEVICE_SELECTS if item.parameter == "operationMode")
    values = select.values_for(device)
    assert values is not None
    sensor = OpusGreenNetSettingChoiceSensor(
        MagicMock(), EAG_ID, "gateway", device, select, values
    )
    channel = device.get_or_create_channel()

    assert sensor.device_class is SensorDeviceClass.ENUM
    assert sensor.native_value is None
    channel.operation_mode = "bottomOn"
    assert sensor.native_value == "bottom_on"
    assert sensor.native_value in sensor.options
    channel.operation_mode = "somethingNew"
    assert sensor.native_value is None
