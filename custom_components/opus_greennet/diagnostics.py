"""Diagnostic helpers for Opus GreenNet entities."""
from __future__ import annotations

import logging
from time import monotonic
from typing import Any

from .enocean_device import EnOceanDevice


def _duration_ms(start: float | None, end: float) -> str:
    """Return a displayable millisecond duration."""
    if start is None:
        return "unknown"
    return f"{(end - start) * 1000:.1f}"


def device_diagnostic_attributes(device: EnOceanDevice) -> dict[str, Any]:
    """Return common device diagnostic attributes for entity state."""
    attrs: dict[str, Any] = {}
    if device.last_seen:
        attrs["last_seen"] = device.last_seen
    if device.last_update_source:
        attrs["last_update_source"] = device.last_update_source
    return attrs


def log_entity_state_write(
    logger: logging.Logger,
    entity_name: str,
    device: EnOceanDevice,
    channel_id: int | None = None,
) -> None:
    """Log latency from MQTT receive/dispatch to the entity state write."""
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
