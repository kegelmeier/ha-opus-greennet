"""Config flow error recovery (quality-scale rule config-flow-test-coverage).

Every error the user step can show is followed by a corrected submission in
the same flow, which must create the entry with the normalised unique ID.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.opus_greennet.const import DOMAIN


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    return result["flow_id"]


def _assert_created(hass: HomeAssistant, result) -> None:
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Opus GreenNet (AABB0011)"
    assert result["data"] == {"eag_id": "AABB0011"}
    assert result["result"].unique_id == "AABB0011"
    assert [entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN)] == [
        "AABB0011"
    ]


async def test_happy_path_sets_normalised_unique_id(
    hass: HomeAssistant, mqtt_transport
):
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"eag_id": " aabb0011 "}
    )
    _assert_created(hass, result)


async def test_recovers_from_invalid_identifier(hass: HomeAssistant, mqtt_transport):
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {"eag_id": "XYZ"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"eag_id": "invalid_eag_id"}
    # An invalid identifier is rejected before any MQTT request is published.
    assert mqtt_transport.published == []
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"eag_id": "AABB0011"}
    )
    _assert_created(hass, result)


async def test_recovers_from_disconnected_broker(hass: HomeAssistant, mqtt_transport):
    flow_id = await _start(hass)
    mqtt_transport.connected = False
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"eag_id": "AABB0011"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    mqtt_transport.connected = True
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"eag_id": "AABB0011"}
    )
    _assert_created(hass, result)


async def test_recovers_from_unresponsive_gateway(hass: HomeAssistant, mqtt_transport):
    flow_id = await _start(hass)
    mqtt_transport.reply = False
    with patch(
        "custom_components.opus_greennet.mqtt_transport.SETUP_REQUEST_TIMEOUT", 0.01
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"eag_id": "AABB0011"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "gateway_unavailable"}
    assert mqtt_transport.subscriptions == []
    mqtt_transport.reply = True
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"eag_id": "AABB0011"}
    )
    _assert_created(hass, result)


async def test_recovers_from_unexpected_exception(
    hass: HomeAssistant, mqtt_transport, caplog: pytest.LogCaptureFixture
):
    flow_id = await _start(hass)
    with patch(
        "custom_components.opus_greennet.config_flow.async_probe_gateway",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"eag_id": "AABB0011"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert "Unexpected exception" in caplog.text
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"eag_id": "AABB0011"}
    )
    _assert_created(hass, result)


async def test_second_entry_for_same_gateway_is_aborted(
    hass: HomeAssistant, mqtt_transport
):
    flow_id = await _start(hass)
    _assert_created(
        hass,
        await hass.config_entries.flow.async_configure(flow_id, {"eag_id": "AABB0011"}),
    )
    await hass.async_block_till_done()
    mqtt_transport.published.clear()
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"eag_id": "aabb0011"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # The duplicate is detected before the gateway is probed again.
    assert mqtt_transport.published == []
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_aborts_when_mqtt_is_not_set_up(hass: HomeAssistant, mqtt_transport):
    with patch(
        "homeassistant.components.mqtt.async_wait_for_mqtt_client",
        new=AsyncMock(return_value=False),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"
    assert hass.config_entries.async_entries(DOMAIN) == []
