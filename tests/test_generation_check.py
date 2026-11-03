"""A gateway of the earlier generation that was updated while Home Assistant runs.

The health probe notices a gateway that stays unreachable. After several failed
probes in a row it asks, without credentials and without a radio request,
whether a WebUI 2026 gateway answers at the address now, and then asks for a
user login once. A gateway that is only restarting is left alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.opus_greennet import async_setup_entry
from custom_components.opus_greennet.api import OpusConnectionError
from custom_components.opus_greennet.coordinator import (
    GENERATION_CHECK_AFTER_FAILURES,
    GENERATION_CHECK_INTERVAL,
    OpusGreenNetCoordinator,
)
from tests.test_gateway_generation_flow import (
    ENDPOINT,
    LEGACY_DATA,
    WEBUI_DATA,
    _setup_entry,
)

MODULE = "custom_components.opus_greennet.coordinator"
UPTIME = {"systemUptimeResponse": {"uptime": "20"}}


def _coordinator(
    check: AsyncMock | None, on_auth_failed: MagicMock | None = None
) -> OpusGreenNetCoordinator:
    coordinator = OpusGreenNetCoordinator(
        MagicMock(),
        MagicMock(),
        "AABB0011",
        on_auth_failed=on_auth_failed,
        generation_check=check,
    )
    coordinator.client.async_get = AsyncMock(
        side_effect=OpusConnectionError("unreachable")
    )
    coordinator._restart_stream = MagicMock()
    return coordinator


@pytest.fixture
def clock() -> Iterator[MagicMock]:
    with patch(f"{MODULE}.monotonic", return_value=1000.0) as monotonic:
        yield monotonic


async def _probe(coordinator: OpusGreenNetCoordinator, times: int) -> None:
    for _ in range(times):
        await coordinator._async_probe()


async def test_an_updated_gateway_asks_for_a_login_once(clock) -> None:
    check = AsyncMock(return_value=True)
    reauth = MagicMock()
    coordinator = _coordinator(check, reauth)

    await _probe(coordinator, GENERATION_CHECK_AFTER_FAILURES)

    check.assert_awaited_once()
    reauth.assert_called_once_with()
    # The stream is stopped and nothing asks again.
    clock.return_value += 10 * GENERATION_CHECK_INTERVAL
    await _probe(coordinator, 3)
    check.assert_awaited_once()
    reauth.assert_called_once_with()


async def test_a_gateway_that_is_only_restarting_is_not_asked(clock) -> None:
    check = AsyncMock(return_value=True)
    coordinator = _coordinator(check, MagicMock())

    await _probe(coordinator, GENERATION_CHECK_AFTER_FAILURES - 1)
    coordinator.client.async_get = AsyncMock(return_value=UPTIME)
    await _probe(coordinator, 1)
    # The failures in a row start again after an answer.
    coordinator.client.async_get = AsyncMock(
        side_effect=OpusConnectionError("unreachable")
    )
    await _probe(coordinator, GENERATION_CHECK_AFTER_FAILURES - 1)

    check.assert_not_awaited()


async def test_the_question_is_repeated_only_after_the_interval(clock) -> None:
    check = AsyncMock(return_value=False)
    reauth = MagicMock()
    coordinator = _coordinator(check, reauth)

    await _probe(coordinator, GENERATION_CHECK_AFTER_FAILURES + 5)
    assert check.await_count == 1

    clock.return_value += GENERATION_CHECK_INTERVAL
    await _probe(coordinator, 1)
    assert check.await_count == 2
    reauth.assert_not_called()


async def test_no_check_is_made_for_a_gateway_of_the_new_generation(clock) -> None:
    coordinator = _coordinator(None, MagicMock())

    await _probe(coordinator, GENERATION_CHECK_AFTER_FAILURES + 2)

    assert coordinator._probe_failures == GENERATION_CHECK_AFTER_FAILURES + 2


# ── what the setup hands to the coordinator ────────────────────────────────


@asynccontextmanager
async def _coordinator_check(hass, data, detect):
    """Run the setup of ``data``; yield what the coordinator was given.

    ``detect`` stays in place while the caller uses the check, which looks the
    function up when it is called.
    """
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock(side_effect=OpusConnectionError("down"))
    coordinator.async_unload = AsyncMock()
    coordinator.async_restore_setting_guards = AsyncMock()
    constructor = MagicMock(return_value=coordinator)

    with (
        patch("custom_components.opus_greennet.OpusGatewayClient", MagicMock()),
        patch("custom_components.opus_greennet.OpusGreenNetCoordinator", constructor),
        patch("custom_components.opus_greennet.async_detect_webui_2026", detect),
    ):
        with pytest.raises((ConfigEntryNotReady, ConfigEntryAuthFailed)):
            await async_setup_entry(hass, _setup_entry(dict(data)))
        yield constructor.call_args.kwargs["generation_check"]


async def test_an_earlier_generation_entry_can_ask_whether_it_was_updated(hass):
    detect = AsyncMock(return_value=None)

    async with _coordinator_check(hass, LEGACY_DATA, detect) as check:
        assert await check() is False
        detect.return_value = ENDPOINT
        assert await check() is True

    # The address and the port of the entry are asked, nothing else.
    assert detect.await_args.args[1:] == ("192.168.1.5", 8080)


async def test_a_new_generation_entry_asks_nothing_when_it_is_unreachable(hass):
    detect = AsyncMock(return_value=None)

    async with _coordinator_check(hass, WEBUI_DATA, detect) as check:
        assert check is None
