"""Tests for the Opus GreenNet config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet.config_flow import (
    CannotConnect,
    GatewayUnavailable,
    InvalidEagId,
    validate_input,
)


@pytest.fixture
def mock_hass():
    """Create a mock HomeAssistant instance."""
    return MagicMock()


class TestValidateInput:
    """Tests for the validate_input function."""

    @pytest.mark.asyncio
    async def test_valid_hex_eag_id_accepted(self, mock_hass):
        """Valid 8-char hex EAG ID is accepted and uppercased."""
        with (
            patch(
                "custom_components.opus_greennet.config_flow.mqtt.is_connected",
                return_value=True,
            ),
            patch(
                "custom_components.opus_greennet.config_flow.async_probe_gateway",
                new_callable=AsyncMock,
            ) as probe,
        ):
            result = await validate_input(mock_hass, {"eag_id": " aabb0011 "})

        probe.assert_awaited_once_with(mock_hass, "AABB0011")
        assert result["eag_id"] == "AABB0011"
        assert "Opus GreenNet" in result["title"]
        assert "AABB0011" in result["title"]

    @pytest.mark.asyncio
    async def test_invalid_eag_id_raises(self, mock_hass):
        """Non-hex or wrong-length EAG ID raises InvalidEagId."""
        with patch(
            "custom_components.opus_greennet.config_flow.mqtt.is_connected",
            return_value=True,
        ):
            with pytest.raises(InvalidEagId):
                await validate_input(mock_hass, {"eag_id": "not-hex!"})

    @pytest.mark.asyncio
    async def test_short_eag_id_raises(self, mock_hass):
        """EAG ID shorter than 8 chars raises InvalidEagId."""
        with patch(
            "custom_components.opus_greennet.config_flow.mqtt.is_connected",
            return_value=True,
        ):
            with pytest.raises(InvalidEagId):
                await validate_input(mock_hass, {"eag_id": "AABB"})

    @pytest.mark.asyncio
    async def test_mqtt_not_connected_raises(self, mock_hass):
        """If MQTT is not connected, CannotConnect is raised."""
        with patch(
            "custom_components.opus_greennet.config_flow.mqtt.is_connected",
            return_value=False,
        ):
            with pytest.raises(CannotConnect):
                await validate_input(mock_hass, {"eag_id": "AABB0011"})

    @pytest.mark.asyncio
    async def test_connected_broker_without_gateway_raises(self, mock_hass):
        """An arbitrary valid identifier must not create an empty integration."""
        with (
            patch(
                "custom_components.opus_greennet.config_flow.mqtt.is_connected",
                return_value=True,
            ),
            patch(
                "custom_components.opus_greennet.config_flow.async_probe_gateway",
                side_effect=HomeAssistantError("No response"),
            ),
            pytest.raises(GatewayUnavailable),
        ):
            await validate_input(mock_hass, {"eag_id": "AABB0011"})
