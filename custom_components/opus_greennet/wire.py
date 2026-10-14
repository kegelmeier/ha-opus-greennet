"""Typed OPUS wire data and validation helpers for MQTT/JSON boundaries.

Wire data is what the bridge publishes: JSON values, flattened MQTT property
paths and their string payloads. It is kept separate from the normalised
domain model in ``enocean_device`` (``EnOceanDevice``/``EnOceanChannel``).

The helpers only check container and field types. They never reinterpret
values: valid string representations ("on", "50", "notAvailable", proprietary
HeatArea profiles, channel "0") pass through unchanged, and unknown additive
fields are preserved for the existing parsers to ignore.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonList = list[JsonValue]


def payload_text(payload: str | bytes | bytearray) -> str:
    """Return an MQTT payload as text, exactly as the parsers did before."""
    if isinstance(payload, bytes | bytearray):
        return payload.decode()
    return str(payload)


def parse_json(text: str | bytes | bytearray) -> JsonValue:
    """Decode JSON text.

    ``json.loads`` only produces ``dict``/``list``/``str``/``int``/``float``/
    ``bool``/``None``, so its untyped result is a ``JsonValue`` by
    construction. NaN and Infinity literals are accepted here as before; the
    domain parsers reject non-finite numbers per field.
    """
    value: JsonValue = json.loads(text)
    return value


def as_object(value: object) -> JsonObject | None:
    """Return ``value`` as a JSON object, or None for any other container."""
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return value
    return None


def as_list(value: object) -> JsonList | None:
    """Return ``value`` as a JSON list, or None for any other container."""
    return value if isinstance(value, list) else None


def object_items(value: object) -> list[JsonObject]:
    """Return the JSON objects of a list, dropping entries of other types."""
    items = as_list(value)
    if items is None:
        return []
    return [item for item in (as_object(entry) for entry in items) if item is not None]


def indexed_objects(value: object) -> list[JsonObject]:
    """Return objects from a JSON list or a flattened ``{"0": {...}}`` mapping.

    Flattened MQTT topics produce mappings keyed by index strings; numeric keys
    are ordered numerically, as the coordinator did before.
    """
    if isinstance(value, list):
        return object_items(value)
    mapping = as_object(value)
    if mapping is None:
        return []
    ordered = sorted(
        mapping, key=lambda key: (0, int(key), "") if key.isdigit() else (1, 0, key)
    )
    return [
        item
        for item in (as_object(mapping[key]) for key in ordered)
        if item is not None
    ]


def string_key(mapping: Mapping[str, object], key: str) -> str | None:
    """Return a string field, or None when missing or of another type."""
    value = mapping.get(key)
    return value if isinstance(value, str) else None


# Read-only views. Mapping/Sequence are covariant, so freshly built
# ``dict[str, object]`` functions can be passed without copying.
type WireMapping = Mapping[str, object]
type WireFunction = Mapping[str, object]
