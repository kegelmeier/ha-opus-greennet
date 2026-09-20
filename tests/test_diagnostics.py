"""Tests for downloadable integration diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.redact import REDACTED

from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import (
    EnOceanChannel,
    EnOceanDevice,
)


@pytest.mark.asyncio
async def test_diagnostics_are_complete_and_redacted() -> None:
    coordinator = MagicMock()
    coordinator.eag_id = "AABB0011"
    coordinator.gateway_info = {"serial_number": "secret-serial", "model": "EAG"}
    coordinator.gateway_uptime = "12 days"
    coordinator.available = True
    device = EnOceanDevice(
        device_id="DEV1",
        friendly_id="Bedroom",
        eeps=[{"eep": "D2-01-11"}],
        last_command_error="selector missing",
    )
    device.channels[1] = EnOceanChannel(channel_id=1, is_on=True)
    coordinator.devices = {"DEV1": device}
    entry = SimpleNamespace(
        entry_id="entry-id",
        title="My bridge",
        data={"eag_id": "AABB0011", "username": "admin"},
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    assert result["entry"]["data"] == {
        "eag_id": REDACTED,
        "username": REDACTED,
    }
    assert result["gateway"]["eag_id"] == REDACTED
    assert result["gateway"]["info"]["serial_number"] == REDACTED
    assert result["devices"][0]["device_id"] == REDACTED
    assert result["devices"][0]["friendly_id"] == REDACTED
    assert result["devices"][0]["last_command_error"] == "selector missing"
    assert result["devices"][0]["channels"][1]["is_on"] is True
