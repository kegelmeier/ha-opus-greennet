"""Shared fixtures for Opus GreenNet tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice


@pytest.fixture
def make_device():
    """Factory fixture for creating EnOceanDevice instances."""

    def _make(
        eep: str,
        device_id: str = "AABB1122",
        friendly_id: str = "Test Device",
    ) -> EnOceanDevice:
        return EnOceanDevice(
            device_id=device_id,
            friendly_id=friendly_id,
            eeps=[{"eep": eep}],
        )

    return _make


@pytest.fixture
def make_telegram():
    """Factory fixture for creating telegram dicts."""

    def _make(
        functions: list[dict],
        timestamp: str | None = None,
        dbm: int | None = None,
    ) -> dict:
        telegram: dict = {"functions": functions}
        if timestamp:
            telegram["timestamp"] = timestamp
        if dbm is not None:
            telegram["telegramInfo"] = {"dbm": dbm}
        return telegram

    return _make


@pytest.fixture
def coordinator():
    """Create a coordinator with mocked hass and patched async_send_command."""
    hass = MagicMock()
    c = OpusGreenNetCoordinator(hass, "AABB0011")
    c.async_send_command = AsyncMock()
    return c
