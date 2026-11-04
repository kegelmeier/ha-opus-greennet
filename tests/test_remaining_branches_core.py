"""Remaining branches of the pure helpers, diagnostics and the device model."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.diagnostics import (
    _gateway_versions,
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import (
    DeviceProfile,
    EnOceanDevice,
)

# ── protocol ────────────────────────────────────────────────────────────────


def _configuration(parameters: object) -> dict[str, object]:
    return {"configuration": {"channel": 0, "parameters": parameters}}


def test_unusable_routing_value_is_not_reported_as_a_function() -> None:
    document = _configuration([{"key": "routingTime", "value": "garbage"}])
    assert protocol.configuration_state_functions(document) == []


def test_last_vertical_movement_needs_a_list_of_parameters() -> None:
    assert protocol.last_vertical_movement_times(_configuration("nope")) == {}


def test_last_vertical_movement_skips_parameters_that_are_no_objects() -> None:
    document = _configuration(
        [
            "stray",
            {"key": "lastVerticalMovementTime", "value": 12.5},
        ]
    )
    assert protocol.last_vertical_movement_times(document) == {0: 12.5}


def test_configuration_routing_needs_a_list_of_parameters() -> None:
    assert protocol.configuration_routing(_configuration("nope")) is None
    assert protocol.configuration_routing({}) is None


def test_configuration_routing_skips_other_parameters() -> None:
    document = _configuration(
        [{"key": "repeaterState", "value": "off"}, {"key": "routingTime", "value": 5}]
    )
    assert protocol.configuration_routing(document) == "on"


def test_configuration_routing_without_a_routing_parameter_is_unknown() -> None:
    document = _configuration([{"key": "repeaterState", "value": "off"}])
    assert protocol.configuration_routing(document) is None


# ── diagnostics ─────────────────────────────────────────────────────────────


def test_gateway_versions_ignore_booleans_and_empty_text() -> None:
    assert _gateway_versions({"version": True, "appVersion": 3, "apiVersion": ""}) == {
        "firmware": None,
        "app": "3",
        "api": None,
    }


@pytest.mark.asyncio
async def test_diagnostics_without_a_position_origin_provider() -> None:
    coordinator = MagicMock()
    coordinator.eag_id = "AABB0011"
    coordinator.gateway_info = {}
    coordinator.available = True
    coordinator.last_vertical_movement_times = {"DEV1": {0: 4.0}}
    coordinator.position_origin = None
    device = EnOceanDevice(device_id="DEV1", friendly_id="Blind", eeps=[])
    coordinator.devices = {"DEV1": device}
    entry = SimpleNamespace(
        entry_id="entry-id",
        title="Bridge",
        data={"eag_id": "AABB0011", "host": "192.168.2.10"},
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    (listed,) = result["devices"]
    assert listed["last_vertical_movement_time"] == {0: 4.0}
    assert "position_origin" not in listed


# ── device model ────────────────────────────────────────────────────────────


def _telegram(*functions: dict[str, object]) -> dict[str, object]:
    return {"functions": list(functions)}


@pytest.mark.parametrize(
    ("key", "value", "attribute"),
    [
        ("thermalMode", "cooling", "thermal_mode"),
        ("temperatureOrigin", "external", "temperature_origin"),
        ("actuatorDeactivated", "info", "actuator_deactivated"),
        ("circuitInUse", "error", "circuit_in_use"),
    ],
)
def test_heat_area_state_keys_store_a_listed_value_only(
    key: str, value: str, attribute: str
) -> None:
    device = EnOceanDevice(
        device_id="DEV1", friendly_id="Heat", eeps=[{"eep": "D1-4B-03"}]
    )
    device.update_from_telegram(_telegram({"key": key, "value": "unlisted"}))
    assert getattr(device.channels[0], attribute) is None

    device.update_from_telegram(_telegram({"key": key, "value": value}))
    assert getattr(device.channels[0], attribute) == value


def test_motion_detector_ignores_other_pressed_keys_and_actions() -> None:
    device = EnOceanDevice(
        device_id="DEV1",
        friendly_id="Motion",
        eeps=[{"eep": "F6-02-01"}, {"eep": "A5-04-01"}],
    )
    assert device.is_opus_motion_detector
    device.update_from_telegram(_telegram({"key": "buttonB0", "value": "pressed"}))
    assert device.channels[0].motion is None
    assert device.channels[0].last_button == "buttonB0"

    device.update_from_telegram(_telegram({"key": "buttonAI", "value": "bounced"}))
    assert device.channels[0].last_button is None
    assert device.channels[0].motion is None


def test_profile_without_button_keys_falls_back_to_the_eep_table() -> None:
    device = EnOceanDevice(
        device_id="DEV1", friendly_id="Rocker", eeps=[{"eep": "F6-02-01"}]
    )
    device.profile = DeviceProfile(from_keys=frozenset({"switch"}))
    assert (
        device.button_keys
        == EnOceanDevice(
            device_id="DEV2", friendly_id="Rocker", eeps=[{"eep": "F6-02-01"}]
        ).button_keys
    )
