"""Diagnostics for the Opus GreenNet Bridge integration."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import asdict
from time import monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import REDACTED

from .enocean_device import EnOceanDevice
from .wire import JsonList, JsonObject, parse_json

if TYPE_CHECKING:
    from . import OpusGreenNetConfigEntry

TO_REDACT = {
    "accesstoken",
    "address",
    "apikey",
    "authorization",
    "baseid",
    "deviceid",
    "eagid",
    "eurid",
    "friendlyid",
    "homekitpin",
    # The HomeKit QR setup payload (X-HM://...) encodes the same setup code.
    "homekitqr",
    "host",
    "hostname",
    "ip",
    "ipaddress",
    "mac",
    "macaddress",
    "name",
    "password",
    "refreshtoken",
    "serial",
    "serialnumber",
    "title",
    "token",
    "username",
}


def _redact_diagnostics(
    data: Mapping[str, object], identifiers: set[str]
) -> dict[str, object]:
    """Redact both structured fields and identifiers embedded in gateway errors."""

    def sensitive_key(key: object) -> bool:
        return re.sub(r"[^a-z0-9]", "", str(key).casefold()) in TO_REDACT

    def json_container(value: object) -> JsonObject | JsonList | None:
        if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                decoded = parse_json(value)
            except ValueError:
                return None
            if isinstance(decoded, dict | list):
                return decoded
        return None

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if sensitive_key(key) and isinstance(item, str) and item:
                    identifiers.add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif (decoded := json_container(value)) is not None:
            collect(decoded)

    def identifier_pattern(values: set[str]) -> re.Pattern[str]:
        return re.compile(
            "|".join(
                re.escape(value)
                for value in sorted(values, key=len, reverse=True)
                if value
            ),
            re.IGNORECASE,
        )

    # Dictionary keys can contain device identifiers, but friendly names must not
    # rename schema fields such as "power" or "channels".
    key_pattern = identifier_pattern(identifiers)
    collect(data)
    pattern = identifier_pattern(identifiers)

    def redact_text_key(key: str) -> str:
        if key_pattern.pattern:
            return key_pattern.sub(lambda _: REDACTED, key)
        return key

    def redact_key(key: object) -> object:
        return redact_text_key(key) if isinstance(key, str) else key

    def redact(value: object) -> object:
        if isinstance(value, dict):
            return {
                redact_key(key): REDACTED if sensitive_key(key) else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if (decoded := json_container(value)) is not None:
            return json.dumps(redact(decoded))
        if isinstance(value, str) and pattern.pattern:
            return pattern.sub(lambda _: REDACTED, value)
        return value

    return {
        redact_text_key(key): REDACTED if sensitive_key(key) else redact(item)
        for key, item in data.items()
    }


def _duration_ms(start: float | None, end: float) -> str:
    """Return a displayable millisecond duration."""
    if start is None:
        return "unknown"
    return f"{(end - start) * 1000:.1f}"


def log_entity_state_write(
    logger: logging.Logger,
    entity_name: str | None,
    device: EnOceanDevice,
    channel_id: int | None = None,
) -> None:
    """Log latency from telegram receipt to the entity state write."""
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
    hass: HomeAssistant, entry: OpusGreenNetConfigEntry
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
                    channel_id: asdict(channel)
                    for channel_id, channel in device.channels.items()
                },
            }
        )

    return _redact_diagnostics(
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
                # Seconds from opening the event stream to its first device
                # list at setup.
                "first_device_list_seconds": coordinator.first_device_list_seconds,
            },
            "devices": devices,
        },
        {
            coordinator.eag_id,
            *(device.device_id for device in coordinator.devices.values()),
        },
    )
