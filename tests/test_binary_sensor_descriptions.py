"""Pinned moisture/HeatArea inventory and state semantics from stable v0.4.0."""

from unittest.mock import MagicMock

import pytest

from custom_components.opus_greennet.binary_sensor import (
    OpusGreenNetDescriptiveBinarySensor,
    async_setup_entry,
)
from custom_components.opus_greennet.binary_sensor_descriptions import (
    BINARY_SENSOR_DESCRIPTIONS,
)
from tests.test_binary_sensor_discovery import entry_for

# Independent inventory taken from the accepted pre-refactor platform.
METADATA = {
    "liquid_detected": ("water_leak", "moisture", None),
    "window_open": ("window", "window", None),
    "actuator_not_responding": ("actuator_not_responding", "problem", "diagnostic"),
    "missing_temperature": ("missing_temperature", "problem", "diagnostic"),
    "actuator_low_battery": ("actuator_battery", "battery", "diagnostic"),
    "actuator_deactivated": ("actuator_deactivated", "problem", "diagnostic"),
    "circuit_in_use": ("circuit_in_use", "problem", "diagnostic"),
}
COMMON = {"window_open", "actuator_not_responding", "missing_temperature"}


@pytest.mark.parametrize(
    "eep,keys",
    [
        ("F6-05-01", {"liquid_detected"}),
        ("D1-4B-05", COMMON | {"actuator_low_battery", "actuator_deactivated"}),
        ("D1-4B-06", COMMON | {"circuit_in_use"}),
        ("D1-4B-07", COMMON),
    ],
)
async def test_description_inventory_matches_stable(hass, make_device, eep, keys):
    device = make_device(eep)
    coordinator = MagicMock(
        eag_id="AABB0011", devices={device.device_id: device}, available=True
    )
    entry = entry_for(coordinator)
    add = MagicMock()
    await async_setup_entry(hass, entry, add)
    entities = add.call_args.args[0]
    assert {
        e.unique_id.removeprefix(f"AABB0011_{device.device_id}_") for e in entities
    } == keys
    for entity in entities:
        assert isinstance(entity, OpusGreenNetDescriptiveBinarySensor)
        key = entity.unique_id.removeprefix(f"AABB0011_{device.device_id}_")
        assert (
            entity.translation_key,
            entity.device_class,
            entity.entity_category,
        ) == METADATA[key]
        assert entity.entity_registry_enabled_default is True
        assert entity.should_poll is False
        assert entity.is_on is None
        assert entity.available is True
    coordinator.available = False
    assert all(entity.available is False for entity in entities)
    for call in entry.async_on_unload.call_args_list:
        call.args[0]()


@pytest.mark.parametrize(
    "key,value",
    [
        (key, value)
        for key in METADATA
        for value in (
            [None, False, True]
            if key in ("liquid_detected", "window_open")
            else [None, False, True, "reset", "warning"]
        )
    ],
)
def test_description_states_match_stable(make_device, key, value):
    device = make_device("D1-4B-05")
    description = next(d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == key)
    entity = OpusGreenNetDescriptiveBinarySensor(
        MagicMock(), "AABB0011", "gateway", device, description
    )
    assert entity.is_on is None
    channel = device.get_or_create_channel()
    setattr(channel, key, value)
    expected = (
        value
        if key in ("liquid_detected", "window_open")
        else None
        if value is None
        else value != "reset"
    )
    assert entity.is_on is expected
