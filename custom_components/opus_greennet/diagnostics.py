"""Diagnostics for the Opus GreenNet Bridge integration."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import asdict
from time import monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import REDACTED

from .discovery import async_gateway_address
from .enocean_device import EnOceanDevice, LinkedDevice, LinkTables
from .wire import JsonList, JsonObject, parse_json

if TYPE_CHECKING:
    from . import OpusGreenNetConfigEntry

TO_REDACT = {
    "accesstoken",
    "address",
    "apikey",
    "authorization",
    "baseid",
    "certificatefingerprint",
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
    "ipv4",
    "ipv6",
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


def _linked_devices(linked: tuple[LinkedDevice, ...]) -> list[dict[str, object]]:
    return [
        {
            # Redacted with the other identifiers by ``_redact_diagnostics``.
            "device_id": entry.device_id,
            "eep": entry.eep,
            "parameters": dict(entry.parameters),
        }
        for entry in linked
    ]


def _link_tables(tables: LinkTables | None) -> dict[str, object] | None:
    """Return the link tables of a device; None while none was read."""
    if tables is None:
        return None
    return {
        "inbound": _linked_devices(tables.inbound),
        "outbound": _linked_devices(tables.outbound),
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


def _info_int(info: object, key: str) -> int | None:
    value = info.get(key) if isinstance(info, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _gateway_versions(info: object) -> dict[str, str | None]:
    """Return firmware, application and API version from the gateway reply."""
    source = info if isinstance(info, dict) else {}

    def text(key: str) -> str | None:
        value = source.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return str(value)
        return value if isinstance(value, str) and value else None

    return {
        "firmware": text("version"),
        "app": text("appVersion"),
        "api": text("apiVersion"),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OpusGreenNetConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for one gateway."""
    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator

    devices: list[dict[str, Any]] = []
    # REST phase 02: last vertical run duration and position origin per
    # channel, for diagnostics only.
    movement_times = getattr(coordinator, "last_vertical_movement_times", None)
    stream_facts = getattr(coordinator, "stream_diagnostics", None)
    stream = stream_facts() if callable(stream_facts) else None
    configured_host = entry.data.get(CONF_HOST)
    # Resolved when the download is made (bounded, never raises): no polling.
    hostname, ipv4, ipv6 = (
        await async_gateway_address(hass, configured_host)
        if isinstance(configured_host, str) and configured_host.strip()
        else (None, None, None)
    )
    origin = getattr(coordinator, "position_origin", None)
    # What was sent over the radio and how the gateway answered, per device.
    journal_facts = getattr(coordinator, "radio_journal", None)
    journal = journal_facts() if callable(journal_facts) else None
    for device in coordinator.devices.values():
        devices.append(
            {
                "device_id": device.device_id,
                "friendly_id": device.friendly_id,
                "eeps": device.eeps,
                "manufacturer": device.manufacturer,
                "product_id": device.product_id,
                "software_revision": device.software_revision,
                "hardware_revision": device.hardware_revision,
                "last_seen": device.last_seen,
                "last_update_source": device.last_update_source,
                "dbm": device.dbm,
                "last_command_error": device.last_command_error,
                "link_tables": _link_tables(device.link_tables),
                "channels": {
                    channel_id: asdict(channel)
                    for channel_id, channel in device.channels.items()
                },
                # Where type and channels come from: the gateway profile read
                # at setup, or the EEP tables when it could not be read.
                "capabilities": {
                    "source": "gateway profile" if device.profile else "EEP table",
                    "entity_type": device.entity_type,
                    "channel_count": device.channel_count,
                    "dimmable": device.is_dimmable,
                },
            }
        )
        if isinstance(journal, list):
            # Oldest first; "seq" orders the entries across devices. The device
            # is the block they sit in, its identifier is redacted anyway.
            devices[-1]["radio_journal"] = [
                {key: value for key, value in entry.items() if key != "device_id"}
                for entry in journal
                if entry.get("device_id") == device.device_id
            ]
        runs = getattr(coordinator, "last_cover_runs", None)
        if isinstance(runs, dict):
            devices[-1]["last_cover_run"] = {
                channel_id: asdict(run)
                for (device_id, channel_id), run in runs.items()
                if device_id == device.device_id
            }
        if isinstance(movement_times, dict):
            devices[-1]["last_vertical_movement_time"] = dict(
                movement_times.get(device.device_id, {})
            )
            if callable(origin):
                devices[-1]["position_origin"] = {
                    channel_id: origin(device.device_id, channel_id)
                    for channel_id, channel in device.channels.items()
                    if channel.position is not None
                }

    resolved = {"hostname": hostname, "ipv4": ipv4, "ipv6": ipv6}
    result = _redact_diagnostics(
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
                # Read from /system/info every 30 minutes (and at setup).
                "versions": _gateway_versions(coordinator.gateway_info),
                "receive_counter": _info_int(
                    coordinator.gateway_info, "receiveCounter"
                ),
                "average_dbm": _info_int(coordinator.gateway_info, "averageDbm"),
                "info_read_at": getattr(coordinator, "gateway_info_read_at", None),
                "uptime": coordinator.gateway_uptime,
                # Seconds from opening the event stream to its first device
                # list at setup.
                "first_device_list_seconds": getattr(
                    coordinator, "first_device_list_seconds", None
                ),
                # Cached stream facts: connection state, requested framing,
                # attempt and reconnect counters, waiting radio requests.
                "stream": stream if isinstance(stream, dict) else None,
            },
            "devices": devices,
        },
        {
            coordinator.eag_id,
            *(device.device_id for device in coordinator.devices.values()),
            # Wherever else the name or address appears, it is masked as well.
            *(value for value in resolved.values() if value),
        },
    )
    # Added after the redaction: a value is masked, an unresolved one stays
    # empty (None), so the download shows that it could not be resolved.
    gateway = result["gateway"]
    if isinstance(gateway, dict):
        gateway["connection"] = {
            key: REDACTED if value else None for key, value in resolved.items()
        }
    return result
