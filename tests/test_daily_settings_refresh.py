"""The daily read of the stored settings of the devices (05:00 local time).

Radio traffic with the lowest priority: every command and status query goes
first. Devices with settings only, no battery powered presence detectors, no
repeated read, and a stop after three failures in a row.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.opus_greennet.api import PRIORITY_BACKGROUND, OpusTimeoutError
from custom_components.opus_greennet.coordinator import (
    DAILY_SETTINGS_MAX_FAILURES,
    DAILY_SETTINGS_REFRESH_HOUR,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

DOCUMENT = {
    "header": {"httpStatus": 200},
    "configuration": {"parameters": [{"key": "maxHoldingTime", "value": 20.0}]},
}


def _add(coordinator, device_id: str, eep: str) -> EnOceanDevice:
    device = EnOceanDevice(device_id, device_id, [{"eep": eep}])
    coordinator.devices[device_id] = device
    return device


async def test_the_tick_hands_over_the_eligible_devices(
    connected_coordinator,
) -> None:
    with patch.object(
        type(connected_coordinator),
        "async_refresh_all_settings",
        new_callable=AsyncMock,
    ) as run:
        connected_coordinator._async_daily_settings_tick(None)
        for _ in range(5):
            await asyncio.sleep(0)

        run.assert_awaited_once()
        eligible = run.await_args.args[0]
    blind = EnOceanDevice("A", "A", [{"eep": "D2-05-02"}])
    switch = EnOceanDevice("B", "B", [{"eep": "D2-01-01"}])
    detector = EnOceanDevice("C", "C", [{"eep": "A5-07-03"}])
    sensor = EnOceanDevice("D", "D", [{"eep": "A5-04-01"}])
    assert eligible(blind) and eligible(switch)
    assert not eligible(detector)
    assert not eligible(sensor)


async def test_every_eligible_device_is_read_once_in_order(
    radio, connected_coordinator
) -> None:
    radio.response = DOCUMENT
    for device_id in ("B", "A", "C"):
        _add(connected_coordinator, device_id, "D2-05-02")
    priorities: list[int] = []
    queue = connected_coordinator._radio_queue
    original = queue.acquire

    async def spy(priority: int) -> None:
        priorities.append(priority)
        await original(priority)

    with patch.object(queue, "acquire", spy):
        read = await connected_coordinator.async_refresh_all_settings(lambda _d: True)

    assert read == 3
    configuration_paths = [
        path for method, path, _ in radio.sent if path.endswith("/configuration")
    ]
    assert configuration_paths == [
        "/devices/A/configuration",
        "/devices/B/configuration",
        "/devices/C/configuration",
    ]
    assert set(priorities) == {PRIORITY_BACKGROUND}
    assert all(method == "GET" for method, _path, _ in radio.sent)


async def test_ineligible_devices_are_not_read(radio, connected_coordinator) -> None:
    radio.response = DOCUMENT
    _add(connected_coordinator, "A", "D2-05-02")
    _add(connected_coordinator, "B", "A5-07-03")

    read = await connected_coordinator.async_refresh_all_settings(
        lambda device: device.device_id == "A"
    )

    assert read == 1
    assert not any("/B/" in path or path.endswith("/B") for _m, path, _b in radio.sent)


async def test_the_run_stops_after_three_failures_in_a_row(
    radio, connected_coordinator
) -> None:
    radio.response = OpusTimeoutError("no answer")
    for index in range(6):
        _add(connected_coordinator, f"D{index}", "D2-05-02")

    read = await connected_coordinator.async_refresh_all_settings(lambda _d: True)

    assert read == 0
    assert len(radio.sent) == DAILY_SETTINGS_MAX_FAILURES


async def test_a_success_resets_the_failure_count(radio, connected_coordinator) -> None:
    for index in range(6):
        _add(connected_coordinator, f"D{index}", "D2-05-02")
    answers = iter(
        [
            OpusTimeoutError("no answer"),
            OpusTimeoutError("no answer"),
            DOCUMENT,
            OpusTimeoutError("no answer"),
            OpusTimeoutError("no answer"),
            DOCUMENT,
        ]
    )
    request = radio._request

    async def next_answer(method, path, body, timeout):
        # The repeater mode read after a configuration read does not count.
        radio.response = next(answers) if path.endswith("/configuration") else DOCUMENT
        return await request(method, path, body, timeout)

    with patch.object(radio, "_request", next_answer):
        read = await connected_coordinator.async_refresh_all_settings(lambda _d: True)

    # Failures never reach three in a row, so all six devices are tried.
    assert read == 2
    assert len([p for _m, p, _b in radio.sent if p.endswith("/configuration")]) == 6


async def test_nothing_is_read_while_the_gateway_is_unavailable(
    radio, connected_coordinator
) -> None:
    _add(connected_coordinator, "A", "D2-05-02")
    connected_coordinator._gateway_available = False

    read = await connected_coordinator.async_refresh_all_settings(lambda _d: True)

    assert read == 0
    assert radio.sent == []


async def test_a_second_run_does_not_start_while_one_is_running(
    radio, connected_coordinator
) -> None:
    radio.auto_respond = False
    _add(connected_coordinator, "A", "D2-05-02")
    first = asyncio.create_task(
        connected_coordinator.async_refresh_all_settings(lambda _d: True)
    )
    await radio.wait_sent(1)

    assert await connected_coordinator.async_refresh_all_settings(lambda _d: True) == 0

    radio.respond(DOCUMENT)
    # The repeater mode follows the configuration read.
    await radio.wait_sent(2)
    radio.respond(DOCUMENT)
    assert await first == 1
    # The flag is released: the next run reads again.
    radio.auto_respond = True
    radio.response = DOCUMENT
    assert await connected_coordinator.async_refresh_all_settings(lambda _d: True) == 1


async def test_the_daily_timer_runs_at_five_local_time(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    with patch(
        "custom_components.opus_greennet.coordinator.async_track_time_change"
    ) as track:
        entry = await setup_bridge(hass, gateway)

    track.assert_called_once()
    assert track.call_args.kwargs == {
        "hour": DAILY_SETTINGS_REFRESH_HOUR,
        "minute": 0,
        "second": 0,
    }
    assert DAILY_SETTINGS_REFRESH_HOUR == 5
    assert entry.runtime_data.coordinator is not None


@pytest.mark.parametrize("hour", [5])
def test_hour_is_early_morning(hour: int) -> None:
    assert DAILY_SETTINGS_REFRESH_HOUR == hour
