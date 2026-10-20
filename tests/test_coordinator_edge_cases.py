"""Defensive coordinator paths: malformed answers, failures and lifecycle.

Each test asserts the observable outcome (state, requests, logged message or
raised error), not only that a line runs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.api import (
    OpusApiError,
    OpusAuthError,
    OpusConnectionError,
    OpusResponseError,
    OpusTimeoutError,
)
from custom_components.opus_greennet.coordinator import (
    SIGNAL_AVAILABILITY_UPDATE,
    SIGNAL_DEVICE_REMOVED,
    SUPERSEDED,
    CoverMovement,
    GatewayMismatchError,
    OpusGreenNetCoordinator,
    request_error,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge

LOGGER = "custom_components.opus_greennet"
MODULE = "custom_components.opus_greennet.coordinator"
OK = {"header": {"httpStatus": 200}}


@pytest.fixture
def coord() -> Iterator[OpusGreenNetCoordinator]:
    """A coordinator with mocked hass and client; timers are recorded."""
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coordinator._gateway_available = True
    coordinator.client.async_put = AsyncMock(return_value=OK)
    coordinator.client.async_get = AsyncMock(return_value=OK)
    with patch(f"{MODULE}.async_call_later", side_effect=lambda *a: MagicMock()):
        yield coordinator


def _device(coord, eep: str = "D2-01-00", device_id: str = "DEV1") -> EnOceanDevice:
    device = EnOceanDevice(device_id, "Device", [{"eep": eep}])
    coord.devices[device_id] = device
    return device


# ── Command acknowledgements ────────────────────────────────────────────────


async def test_rejection_for_unknown_device_is_only_raised(coord) -> None:
    coord.client.async_put.side_effect = OpusResponseError("status 500", 500)
    with pytest.raises(HomeAssistantError) as err:
        await coord.async_turn_on("GHOST")
    assert err.value.translation_key == "request_rejected"
    assert err.value.translation_placeholders == {
        "device_id": "GHOST",
        "reason": "status 500",
    }
    assert coord.devices == {}
    assert coord._command_waiters == {}


@pytest.mark.parametrize(
    ("reason", "recorded"),
    [("status 400: channel selector missing", None), ("", "rejected")],
)
async def test_rejection_records_error_and_cancels_checks(
    coord, reason, recorded
) -> None:
    device = _device(coord)
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    coord.client.async_put.side_effect = OpusResponseError(reason, 400)
    with (
        patch(f"{MODULE}.async_dispatcher_send") as dispatch,
        pytest.raises(HomeAssistantError),
    ):
        await coord.async_turn_on("DEV1")
    assert device.last_command_error == (recorded or reason)
    cancel.assert_called_once()
    assert not coord._pending_reconciliation_queries
    dispatch.assert_called_once()


async def test_success_clears_error_without_stopping_checks(coord) -> None:
    device = _device(coord)
    device.last_command_error = "previous failure"
    cancel = MagicMock()
    coord._pending_reconciliation_queries[("DEV1", 0)] = [cancel]
    with (
        patch(f"{MODULE}.async_dispatcher_send") as dispatch,
        patch(f"{MODULE}._LOGGER.warning") as warning,
    ):
        await coord.async_query_device_status("DEV1")
    assert device.last_command_error is None
    cancel.assert_not_called()
    assert ("DEV1", 0) in coord._pending_reconciliation_queries
    dispatch.assert_called_once()
    warning.assert_not_called()
    coord.client.async_put.assert_awaited_once_with(
        "/devices/DEV1/state",
        {"state": {"functions": [{"key": "query", "value": "status"}]}},
    )


async def test_soft_smart_ack_status_is_success(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Status 201 (SoftSmartAck acceptance) acknowledges a command."""
    gateway.devices = [device_object("050BF714", "D2-01-01")]
    entry = await setup_bridge(hass, gateway)
    coordinator = entry.runtime_data.coordinator
    coordinator.devices["050BF714"].last_command_error = "previous failure"
    gateway.responses["/devices/050BF714/state"] = {
        "header": {"httpStatus": 201, "content": "state"}
    }
    await coordinator.async_turn_on("050BF714")
    assert coordinator.devices["050BF714"].last_command_error is None


async def test_unconfirmed_command_schedules_checks_confirmed_does_not(coord):
    _device(coord)
    with patch(f"{MODULE}.async_call_later", return_value=MagicMock()) as later:
        await coord.async_turn_on("DEV1")
    assert later.call_count == 2
    assert coord._pending_reconciliation_fields[("DEV1", 0)] == {"switch"}

    async def confirm(path, body):
        coord._handle_telegram(
            {
                "deviceId": "DEV1",
                "direction": "from",
                "functions": [{"key": "switch", "value": "off"}],
            }
        )
        return OK

    coord.client.async_put = AsyncMock(side_effect=confirm)
    with patch(f"{MODULE}.async_call_later", return_value=MagicMock()) as later:
        await coord.async_turn_off("DEV1")
    later.assert_not_called()
    assert ("DEV1", 0) not in coord._pending_reconciliation_queries


@pytest.mark.parametrize(
    ("error", "key", "reason"),
    [
        (OpusTimeoutError("slow"), "request_timeout", ""),
        (OpusConnectionError("refused"), "gateway_unavailable", ""),
        (
            OpusResponseError("status 404: unknown", 404),
            "request_rejected",
            "status 404: unknown",
        ),
        (OpusApiError("odd"), "request_rejected", "odd"),
        (OpusAuthError("401"), "request_rejected", "401"),
    ],
)
def test_transport_errors_are_translated(coord, error, key, reason) -> None:
    on_auth_failed = MagicMock()
    coord._on_auth_failed = on_auth_failed
    translated = coord._request_error(error, "DEV1")
    assert translated.translation_key == key
    assert translated.translation_placeholders["reason"] == reason
    assert on_auth_failed.call_count == (1 if isinstance(error, OpusAuthError) else 0)


def test_auth_failure_is_reported_once(coord) -> None:
    on_auth_failed = MagicMock()
    coord._on_auth_failed = on_auth_failed
    coord._handle_auth_failure()
    coord._handle_auth_failure()
    on_auth_failed.assert_called_once()
    assert coord.available is False
    # No stream is started with rejected credentials.
    coord._create_task = MagicMock()
    coord._start_stream()
    coord._create_task.assert_not_called()


def test_auth_failure_without_callback(coord) -> None:
    coord._handle_auth_failure()
    assert coord._auth_failed is True


def test_request_error_does_not_leak_payloads() -> None:
    error = request_error("request_rejected", "DEV1", "status 400")
    assert error.translation_domain == "opus_greennet"
    assert error.translation_placeholders == {
        "device_id": "DEV1",
        "reason": "status 400",
    }


# ── Requests while the gateway cannot be used ───────────────────────────────


async def test_device_requests_fail_fast_while_gateway_unavailable(coord) -> None:
    coord._gateway_available = False
    for request in (
        coord.async_get_device_configuration("DEV1"),
        coord.async_get_device_parameters("DEV1"),
        coord.async_set_device_configuration("DEV1", {"configuration": {}}),
        coord.async_turn_on("DEV1"),
    ):
        with pytest.raises(HomeAssistantError) as error:
            await request
        assert error.value.translation_key == "gateway_unavailable"
    coord.client.async_get.assert_not_awaited()
    coord.client.async_put.assert_not_awaited()


async def test_requests_after_unload_are_cancelled(coord) -> None:
    await coord.async_unload()
    with pytest.raises(HomeAssistantError) as error:
        await coord.async_get_device_configuration("DEV1")
    assert error.value.translation_key == "request_cancelled"


async def test_recom_requests_use_the_long_timeout(coord) -> None:
    await coord.async_get_device_configuration("DEV1")
    await coord.async_get_device_parameters("DEV1")
    await coord.async_set_device_configuration("DEV1", {"configuration": {}})
    assert [call.args[0] for call in coord.client.async_get.await_args_list] == [
        "/devices/DEV1/configuration",
        "/devices/DEV1/parameters",
    ]
    assert {
        call.kwargs["timeout"]
        for call in [
            *coord.client.async_get.await_args_list,
            *coord.client.async_put.await_args_list,
        ]
    } == {45.0}


# ── Gateway identity and health ─────────────────────────────────────────────


def test_system_info_is_stored_and_identity_checked(coord) -> None:
    coord._apply_system_info({"systemInfo": {"eurid": "aabb0011", "model": "EAG"}})
    assert coord.gateway_info == {"eurid": "aabb0011", "model": "EAG"}
    coord._apply_system_info({"header": OK["header"]})
    assert coord.gateway_info == {}
    coord._apply_system_info({"systemInfo": {"eurid": 7}})
    assert coord.gateway_info == {"eurid": 7}
    coord.client.host = "192.0.2.1"
    with pytest.raises(GatewayMismatchError, match="11223344"):
        coord._apply_system_info({"systemInfo": {"eurid": "11223344"}})
    assert coord.gateway_info == {"eurid": 7}


@pytest.mark.parametrize(
    "answer",
    [
        {"header": {"httpStatus": 200}},
        {"systemUptimeResponse": {"uptime": "soon"}},
        {"systemUptimeResponse": {"uptime": -1}},
        {"systemUptimeResponse": {"uptime": True}},
    ],
)
async def test_invalid_uptime_answer_keeps_previous_value(
    coord, caplog, answer
) -> None:
    coord.gateway_uptime = "10"
    coord.client.async_get.return_value = answer
    coord._restart_stream = MagicMock()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await coord._async_probe()
    assert coord.gateway_uptime == "10"
    assert coord.available is False
    coord._restart_stream.assert_called_once()
    assert "health probe failed" in caplog.text

    # A second failure while already unavailable does not restart again.
    await coord._async_probe()
    coord._restart_stream.assert_called_once()


@pytest.mark.parametrize("stream_connected", [False, True])
async def test_probe_restores_availability_only_with_an_open_stream(
    coord, stream_connected
) -> None:
    coord._gateway_available = False
    coord._stream_connected = stream_connected
    coord.client.async_get.return_value = {"systemUptimeResponse": {"uptime": 20}}
    await coord._async_probe()
    assert coord.gateway_uptime == "20"
    assert coord.available is stream_connected


async def test_first_probe_after_unknown_uptime_does_not_restart(coord) -> None:
    coord._restart_stream = MagicMock()
    coord.client.async_get.return_value = {"systemUptimeResponse": {"uptime": "5"}}
    await coord._async_probe()
    coord._restart_stream.assert_not_called()
    assert coord.gateway_uptime == "5"


def test_ticks_do_not_overlap_running_work(coord) -> None:
    created = []

    def create_task(coroutine, name):
        coroutine.close()
        created.append(name)
        task = MagicMock()
        task.done.return_value = False
        return task

    coord._create_task = create_task
    for _ in range(2):
        coord._async_health_tick(None)
        coord._async_refresh_tick(None)
    assert created == ["health probe", "device list refresh"]
    coord._probe_task.done.return_value = True
    coord._refresh_task.done.return_value = True
    coord._async_health_tick(None)
    coord._async_refresh_tick(None)
    assert created == ["health probe", "device list refresh"] * 2


def test_restart_stream_starts_one_without_a_running_task(coord) -> None:
    created = []

    def create_task(coroutine, name):
        coroutine.close()
        created.append(name)
        return MagicMock()

    coord._create_task = create_task
    coord._stream_connected = True
    coord._restart_stream()
    assert created == ["stream"]
    assert coord._stream_connected is False
    # A finished task is replaced without cancelling it.
    coord._stream_task.done.return_value = True
    coord._restart_stream()
    coord._stream_task.cancel.assert_not_called()
    assert created == ["stream", "stream"]


async def test_stream_loop_ends_after_unload(coord) -> None:
    coord._unloaded = True
    await coord._async_run_stream(coord._stream_generation)
    coord.client.async_stream.assert_not_called()


# ── Availability ─────────────────────────────────────────────────────────────


def test_unavailability_is_logged_once_and_recovery_once(coord, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(3):
            coord._set_gateway_available(False)
        for _ in range(3):
            coord._set_gateway_available(True)
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("OPUS gateway AABB0011 is")
    ]
    assert messages == [
        "OPUS gateway AABB0011 is unavailable",
        "OPUS gateway AABB0011 is available",
    ]
    assert all(record.levelno == logging.INFO for record in caplog.records)


def test_availability_change_after_unload_is_not_logged(coord, caplog) -> None:
    coord._unloaded = True
    with (
        caplog.at_level(logging.INFO, logger=LOGGER),
        patch(f"{MODULE}.async_dispatcher_send") as dispatch,
    ):
        coord._set_gateway_available(False)
    assert "is unavailable" not in caplog.text
    dispatch.assert_called_once_with(
        coord.hass, f"{SIGNAL_AVAILABILITY_UPDATE}_AABB0011"
    )
    assert coord.available is False


def test_unavailable_gateway_drops_estimates_and_checks(coord) -> None:
    device = _device(coord, "D2-05-00")
    channel = device.get_or_create_channel()
    channel.movement = "closing"
    movement_cancel, check_cancel = MagicMock(), MagicMock()
    coord._cover_movements[("DEV1", 0)] = CoverMovement(
        0, 100, 0.0, 30.0, cancel=movement_cancel
    )
    coord._pending_reconciliation_queries[("DEV1", 0)] = [check_cancel]
    coord._reconcile_needed.add(("DEV1", 0))
    with patch(f"{MODULE}.async_dispatcher_send") as dispatch:
        coord._set_gateway_available(False)
    movement_cancel.assert_called_once()
    check_cancel.assert_called_once()
    assert channel.movement is None
    assert coord._cover_movements == {}
    assert coord._pending_reconciliation_queries == {}
    assert coord._reconcile_needed == set()
    # Only the availability signal; no per-device state dispatch.
    dispatch.assert_called_once()


# ── Devices ──────────────────────────────────────────────────────────────────


def test_devices_by_type(coord) -> None:
    _device(coord, "D2-01-00", "A")
    _device(coord, "D2-05-00", "B")
    assert [device.device_id for device in coord.get_devices_by_type("cover")] == ["B"]
    assert coord.get_device("A") is coord.devices["A"]
    assert coord.get_device("C") is None


def test_forgetting_a_device_drops_its_feedback_movement_and_checks(coord) -> None:
    _device(coord, "D2-05-00")
    movement_cancel, check_cancel, other_check = MagicMock(), MagicMock(), MagicMock()
    other_movement = MagicMock()
    coord._cover_movements[("OTHER", 0)] = CoverMovement(
        0, 100, 0.0, 30.0, cancel=other_movement
    )
    coord._cover_movements[("DEV1", 0)] = CoverMovement(
        0, 100, 0.0, 30.0, cancel=movement_cancel
    )
    coord._pending_reconciliation_queries[("DEV1", 1)] = [check_cancel]
    coord._pending_reconciliation_queries[("OTHER", 0)] = [other_check]
    coord._feedback_revisions[("DEV1", 0, "position")] = 3
    coord._feedback_revisions[("OTHER", 0, "switch")] = 1
    with patch(f"{MODULE}.async_dispatcher_send") as dispatch:
        coord.async_forget_device("DEV1")
    assert "DEV1" not in coord.devices
    assert coord._feedback_revisions == {("OTHER", 0, "switch"): 1}
    movement_cancel.assert_called_once()
    check_cancel.assert_called_once()
    other_check.assert_not_called()
    other_movement.assert_not_called()
    assert list(coord._cover_movements) == [("OTHER", 0)]
    assert coord._device_generations["DEV1"] == 1
    dispatch.assert_called_once_with(
        coord.hass, f"{SIGNAL_DEVICE_REMOVED}_AABB0011", "DEV1"
    )


# ── Reconciliation queries ───────────────────────────────────────────────────


async def test_reconciliation_for_forgotten_device_is_skipped(coord) -> None:
    coord.async_query_device_status = AsyncMock()
    await coord._async_reconcile_status("GHOST", 0)
    coord.async_query_device_status.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (HomeAssistantError("timeout"), "Could not reconcile OPUS state for DEV1"),
        (
            request_error("request_cancelled", "DEV1", SUPERSEDED),
            "Dropped superseded OPUS status check for DEV1 channel 0",
        ),
    ],
)
async def test_failed_reconciliation_is_logged_at_debug(
    coord, caplog, error, message
) -> None:
    _device(coord)
    coord.async_query_device_status = AsyncMock(side_effect=error)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await coord._async_reconcile_status("DEV1", 0)
    assert message in caplog.text
    assert coord._reconciling == set()


async def test_reconciliation_is_sent_once_and_only_while_needed(coord) -> None:
    _device(coord)
    coord._reconcile_needed.add(("DEV1", 0))
    await coord._async_reconcile_status("DEV1", 0)
    assert coord.client.async_put.await_count == 1
    assert coord.client.async_put.await_args.args[0] == "/devices/DEV1/state"
    # Consumed: a second check without a new timer is dropped unsent.
    await coord._async_reconcile_status("DEV1", 0)
    assert coord.client.async_put.await_count == 1


def test_reconciliation_timer_does_nothing_while_unavailable(coord) -> None:
    timers = []
    coord._create_task = MagicMock()
    with patch(
        f"{MODULE}.async_call_later",
        side_effect=lambda hass, delay, action: timers.append(action) or MagicMock(),
    ):
        coord._schedule_reconciliation_queries("DEV1", 0)
    assert len(timers) == 2
    coord._gateway_available = False
    timers[0](None)
    coord._create_task.assert_not_called()
    assert coord._reconcile_needed == set()


def test_reconciliation_timers_coalesce_while_a_check_is_queued(coord) -> None:
    timers = []
    created = []

    def create_task(coroutine, name):
        coroutine.close()
        created.append(name)
        coord._reconciling.add(("DEV1", 0))
        return MagicMock()

    coord._create_task = create_task
    with patch(
        f"{MODULE}.async_call_later",
        side_effect=lambda hass, delay, action: timers.append(action) or MagicMock(),
    ):
        coord._schedule_reconciliation_queries("DEV1", 0, fields={"switch"})
    for timer in timers:
        timer(None)
    assert created == ["status check"]
    assert coord._reconcile_needed == {("DEV1", 0)}


def test_overlapping_commands_keep_earlier_unconfirmed_fields(coord) -> None:
    coord._schedule_reconciliation_queries("DEV1", 0, fields={"position"})
    coord._schedule_reconciliation_queries("DEV1", 0, fields={"angle"})
    assert coord._pending_reconciliation_fields[("DEV1", 0)] == {
        "position",
        "angle",
    }
    assert len(coord._pending_reconciliation_queries[("DEV1", 0)]) == 2


# ── Unload ───────────────────────────────────────────────────────────────────


async def test_unload_cancels_timers_tasks_and_pending_work(coord) -> None:
    timer, check = MagicMock(), MagicMock()
    coord._timers.append(timer)
    coord._pending_reconciliation_queries[("DEV1", 0)] = [check]
    _device(coord, "D2-05-00")
    movement_cancel = MagicMock()
    coord._cover_movements[("DEV1", 0)] = CoverMovement(
        None, 100, 0.0, 30.0, cancel=movement_cancel
    )
    started = asyncio.Event()

    async def forever() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())
    coord._tasks.add(task)
    await started.wait()
    with patch(f"{MODULE}.async_dispatcher_send"):
        await coord.async_unload()
    timer.assert_called_once()
    check.assert_called_once()
    movement_cancel.assert_called_once()
    assert task.cancelled()
    assert coord._timers == []
    assert coord._tasks == set()
    assert coord._cover_movements == {}
    assert coord.available is False
    # Nothing restarts after unload.
    coord._create_task = MagicMock()
    coord._start_stream()
    coord._create_task.assert_not_called()
