"""Tests for downloadable integration diagnostics."""

from __future__ import annotations

import json
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


@pytest.mark.asyncio
async def test_diagnostics_redact_identifiers_inside_titles_and_raw_errors() -> None:
    """A public diagnostic download must not reveal the default gateway password."""
    gateway_id = "AABB0011"
    device_id = "AABB1122"
    device = EnOceanDevice(
        device_id=device_id,
        friendly_id="Private bedroom",
        last_command_error=json.dumps(
            {
                "message": f"Device {device_id.lower()} failed on {gateway_id}",
                "request": {"accessToken": "private-token", "password": "private-pass"},
            }
        ),
    )
    coordinator = SimpleNamespace(
        eag_id=gateway_id,
        available=True,
        devices={device_id: device},
        gateway_info={
            "serialNumber": "private-serial",
            "network": {"ipAddress": "192.0.2.12", "macAddress": "AA:BB:CC:DD:EE:FF"},
            "message": "private-serial at 192.0.2.12",
            "model": "GreenNet",
        },
        gateway_uptime="12 days",
    )
    entry = SimpleNamespace(
        entry_id="entry-id",
        title=f"Opus GreenNet ({gateway_id})",
        data={"eag_id": gateway_id},
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)
    serialized = json.dumps(result).casefold()
    for value in (
        gateway_id,
        device_id,
        "Private bedroom",
        "private-serial",
        "192.0.2.12",
        "AA:BB:CC:DD:EE:FF",
        "private-token",
        "private-pass",
    ):
        assert value.casefold() not in serialized
    assert result["entry"]["title"] == REDACTED
    assert result["gateway"]["info"]["model"] == "GreenNet"
    assert result["gateway"]["uptime"] == "12 days"


@pytest.mark.parametrize("friendly_name", ["Power", "Channels"])
async def test_friendly_names_do_not_rename_diagnostic_schema_fields(friendly_name):
    device = EnOceanDevice(
        device_id="AABB1122",
        friendly_id=friendly_name,
        last_command_error=f"{friendly_name} (aabb1122) on gateway AABB0011",
    )
    device.channels[0] = EnOceanChannel(channel_id=0, power=12.5, power_state="active")
    coordinator = SimpleNamespace(
        eag_id="AABB0011",
        available=True,
        devices={device.device_id: device},
        gateway_info={"AABB1122": {"power": 12.5}},
        gateway_uptime="12 days",
    )
    entry = SimpleNamespace(
        entry_id="entry-id",
        title="Bridge",
        data={"eag_id": "AABB0011"},
        runtime_data=SimpleNamespace(coordinator=coordinator),
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    diagnostic_device = result["devices"][0]
    assert diagnostic_device["friendly_id"] == REDACTED
    assert diagnostic_device["device_id"] == REDACTED
    assert diagnostic_device["channels"][0]["power"] == 12.5
    assert diagnostic_device["channels"][0]["power_state"] == "active"
    assert diagnostic_device["last_command_error"] == (
        f"{REDACTED} ({REDACTED}) on gateway {REDACTED}"
    )
    assert result["gateway"]["info"] == {REDACTED: {"power": 12.5}}
