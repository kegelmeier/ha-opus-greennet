"""Gateway processes as a diagnostic sensor (``processes`` of ``/system/info``).

Plain gateway read, no radio traffic. ``state`` is passed through as text, so a
value nobody has seen yet neither raises nor is dropped.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.coordinator import (
    SIGNAL_AVAILABILITY_UPDATE,
    SIGNAL_GATEWAY_INFO_UPDATE,
)
from custom_components.opus_greennet.sensor import (
    OpusGreenNetGatewayProcessesSensor,
    gateway_processes,
)
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge

EAG_ID = "AABB0011"


def _process(feature_id: str, state: str, activated: bool) -> dict:
    return {
        "featureId": feature_id,
        "heartBeatInterval": 60,
        "startTimeoutSec": 10,
        "version": "1.0",
        "buildDate": "2025-01-01T00:00:00Z",
        "state": state,
        "isActivated": activated,
    }


def _sensor(info: dict, *, available: bool = True):
    coordinator = MagicMock(gateway_info=info, available=available)
    return OpusGreenNetGatewayProcessesSensor(coordinator, EAG_ID)


def test_counts_only_activated_processes_that_do_not_run() -> None:
    info = {
        "processes": [
            _process("dcgwCore", "running", True),
            _process("dcgwMqtt", "notRunning", False),  # switched off: not counted
            _process("scheduler", "notRunning", True),
            _process("zonemanager", "starting", True),  # unknown value: counted
        ]
    }
    sensor = _sensor(info)

    assert sensor.native_value == 2
    assert [p["feature_id"] for p in sensor.extra_state_attributes["processes"]] == [
        "dcgwCore",
        "dcgwMqtt",
        "scheduler",
        "zonemanager",
    ]
    assert sensor.extra_state_attributes["processes"][3] == {
        "feature_id": "zonemanager",
        "state": "starting",
        "activated": True,
    }


def test_all_running_is_zero() -> None:
    assert (
        _sensor({"processes": [_process("dcgwCore", "running", True)]}).native_value
        == 0
    )
    assert _sensor({"processes": []}).native_value == 0


@pytest.mark.parametrize("value", [None, "x", 5, {}, True])
def test_no_list_is_unknown(value) -> None:
    sensor = _sensor({"processes": value})
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None
    assert _sensor({}).native_value is None


def test_malformed_entries_are_skipped_not_fatal() -> None:
    raw = [
        "text",
        None,
        {"state": "running"},  # no featureId
        {"featureId": "  ", "state": "running"},
        {"featureId": "a", "state": 7, "isActivated": "yes"},
        _process("b", "running", True),
    ]
    assert gateway_processes({"processes": raw}) == [
        {"feature_id": "a", "state": None, "activated": None},
        {"feature_id": "b", "state": "running", "activated": True},
    ]
    # unknown state and unknown activation count as "not running"
    assert _sensor({"processes": raw}).native_value == 1


def test_metadata_and_unavailable() -> None:
    sensor = _sensor({}, available=False)
    assert sensor.available is False
    assert sensor.entity_category.value == "diagnostic"
    assert sensor.entity_registry_enabled_default is False
    assert sensor.should_poll is False
    assert sensor.unique_id == f"{EAG_ID}_gateway_processes_not_running"


async def test_a_new_reading_is_shown() -> None:
    sensor = _sensor({})
    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.test"
    sensor.platform = MagicMock()
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_update()
    write.assert_called_once()


async def test_listens_for_new_readings_and_availability() -> None:
    sensor = _sensor({})
    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.test"
    sensor.platform = MagicMock()
    with (
        patch(
            "custom_components.opus_greennet.sensor.async_dispatcher_connect"
        ) as connect,
        patch.object(sensor, "async_on_remove") as on_remove,
    ):
        await sensor.async_added_to_hass()

    signals = [call.args[1] for call in connect.call_args_list]
    assert signals == [
        f"{SIGNAL_GATEWAY_INFO_UPDATE}_{EAG_ID}",
        f"{SIGNAL_AVAILABILITY_UPDATE}_{EAG_ID}",
    ]
    assert on_remove.call_count == 2


async def test_the_sensor_in_home_assistant(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    gateway.system_info = {
        "processes": [
            _process("dcgwCore", "running", True),
            _process("dcgwMqtt", "notRunning", False),
        ]
    }
    entry = await setup_bridge(hass, gateway)
    unique_id = f"{entry.data['eag_id']}_gateway_processes_not_running"

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "opus_greennet", unique_id)
    assert entity_id is not None
    # Disabled by default: registered, but no state until the user enables it.
    assert registry.async_get(entity_id).disabled_by is not None
