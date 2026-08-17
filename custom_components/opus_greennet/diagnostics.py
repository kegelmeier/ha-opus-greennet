"""Diagnostics for the Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .enocean_device import EnOceanDevice

TO_REDACT = {
    "device_id",
    "eag_id",
    "friendly_id",
    "password",
    "serial_number",
    "token",
    "username",
}


def _duration_ms(start: float | None, end: float) -> str:
    """Return a displayable millisecond duration."""
    if start is None:
        return "unknown"
    return f"{(end - start) * 1000:.1f}"


def log_entity_state_write(
    logger: logging.Logger,
    entity_name: str,
    device: EnOceanDevice,
    channel_id: int | None = None,
) -> None:
    """Log latency from MQTT receipt to the entity state write."""
    if not logger.isEnabledFor(logging.DEBUG):
        return

    now = monotonic()
    logger.debug(
        "OPUS update latency entity_write: entity=%s device_id=%s "
        "friendly_id=%s channel=%s source=%s dispatch_to_write_ms=%s "
        "receive_to_write_ms=%s",
        entity_name,
        device.device_id,
        device.friendly_id,
        channel_id,
        device.last_update_source or "unknown",
        _duration_ms(device.last_update_dispatched_monotonic, now),
        _duration_ms(device.last_update_received_monotonic, now),
    )


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for one gateway."""
    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator

    devices: list[dict[str, Any]] = []
    for device in coordinator.devices.values():
        devices.append(
            {
                "device_id": device.device_id,
                "friendly_id": device.friendly_id,
                "eeps": device.eeps,
                "manufacturer": device.manufacturer,
                "last_seen": device.last_seen,
                "last_update_source": device.last_update_source,
                "dbm": device.dbm,
                "last_command_error": device.last_command_error,
                "channels": {
                    channel_id: vars(channel)
                    for channel_id, channel in device.channels.items()
                },
            }
        )

    return async_redact_data(
        {
            "entry": {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "data": dict(entry.data),
            },
            "gateway": {
                "eag_id": coordinator.eag_id,
                "available": coordinator.available,
                "info": coordinator.gateway_info,
                "uptime": coordinator.gateway_uptime,
            },
            "devices": devices,
        },
        TO_REDACT,
    )
