"""Result of the daily read of the stored settings (sensor and diagnostics)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.api import OpusTimeoutError
from custom_components.opus_greennet.coordinator import DailySettingsRun
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.sensor import OpusGreenNetDailySettingsRunSensor
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge, wait_for_entity
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

DOCUMENT = {
    "header": {"httpStatus": 200},
    "configuration": {"parameters": [{"key": "maxHoldingTime", "value": 20.0}]},
}


def _add(coordinator, device_id: str) -> None:
    coordinator.devices[device_id] = EnOceanDevice(
        device_id, device_id, [{"eep": "D2-05-02"}]
    )


async def test_a_complete_run_is_recorded(radio, connected_coordinator) -> None:
    radio.response = DOCUMENT
    for device_id in ("A", "B"):
        _add(connected_coordinator, device_id)
    assert connected_coordinator.daily_settings_last_run is None

    with patch(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send"
    ) as send:
        await connected_coordinator.async_refresh_all_settings(lambda _d: True)

    run = connected_coordinator.daily_settings_last_run
    assert (run.read, run.failed, run.stopped) == (2, 0, None)
    assert run.finished_at.tzinfo is not None
    assert any("daily_settings_run" in str(call) for call in send.call_args_list)


async def test_failures_and_the_stop_are_recorded(radio, connected_coordinator) -> None:
    radio.response = OpusTimeoutError("no answer")
    for index in range(5):
        _add(connected_coordinator, f"D{index}")

    await connected_coordinator.async_refresh_all_settings(lambda _d: True)

    run = connected_coordinator.daily_settings_last_run
    assert (run.read, run.failed, run.stopped) == (0, 3, "failures")


async def test_an_unreachable_gateway_is_recorded(radio, connected_coordinator) -> None:
    _add(connected_coordinator, "A")
    connected_coordinator._gateway_available = False

    await connected_coordinator.async_refresh_all_settings(lambda _d: True)

    run = connected_coordinator.daily_settings_last_run
    assert (run.read, run.failed, run.stopped) == (0, 0, "unavailable")
    assert radio.sent == []


async def test_a_run_that_does_not_start_leaves_the_result(
    radio, connected_coordinator
) -> None:
    earlier = DailySettingsRun(datetime(2026, 11, 3, 4, 0, tzinfo=UTC), 3, 0, None)
    connected_coordinator.daily_settings_last_run = earlier
    connected_coordinator._daily_settings_running = True

    await connected_coordinator.async_refresh_all_settings(lambda _d: True)

    assert connected_coordinator.daily_settings_last_run is earlier


async def test_the_sensor_shows_the_run_and_restores_the_last_one() -> None:
    finished = datetime(2026, 11, 3, 4, 1, tzinfo=UTC)
    coordinator = MagicMock(daily_settings_last_run=None)
    sensor = OpusGreenNetDailySettingsRunSensor(coordinator, "AABB0011")
    assert sensor.native_value is None

    restored = MagicMock(native_value=finished)
    state = State(
        "sensor.x",
        "2026-11-03T04:01:00+00:00",
        {"read": 4, "failed": 1, "stopped": None},
    )
    with (
        patch.object(
            sensor, "async_get_last_sensor_data", AsyncMock(return_value=restored)
        ),
        patch.object(sensor, "async_get_last_state", AsyncMock(return_value=state)),
        patch("custom_components.opus_greennet.sensor.async_dispatcher_connect"),
        patch.object(sensor, "async_on_remove"),
    ):
        await sensor.async_added_to_hass()

    assert sensor.native_value == finished
    assert sensor.extra_state_attributes == {"read": 4, "failed": 1, "stopped": None}

    # A new run replaces the restored values.
    coordinator.daily_settings_last_run = DailySettingsRun(
        datetime(2026, 11, 4, 4, 0, tzinfo=UTC), 7, 0, None
    )
    assert sensor.native_value == coordinator.daily_settings_last_run.finished_at
    assert sensor.extra_state_attributes == {"read": 7, "failed": 0, "stopped": None}
    assert sensor.unique_id == "AABB0011_daily_settings_run"


async def test_sensor_and_diagnostics_in_home_assistant(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    eag = entry.data["eag_id"]
    entity_id = await wait_for_entity(hass, "sensor", f"{eag}_daily_settings_run")
    assert hass.states.get(entity_id).state == "unknown"
    entity = er.async_get(hass).async_get(entity_id)
    assert entity.entity_category.value == "diagnostic"

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["gateway"]["daily_settings_read"] == {
        "enabled": True,
        "time": "05:00:00",
        "last_run": None,
    }

    await coordinator.async_refresh_all_settings(lambda _d: False)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state != "unknown"
    assert state.attributes["read"] == 0
    assert state.attributes["stopped"] is None
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    last = diagnostics["gateway"]["daily_settings_read"]["last_run"]
    assert (last["read"], last["failed"], last["stopped"]) == (0, 0, None)
