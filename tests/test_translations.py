"""Translation and service-description consistency.

Hassfest validates the schema of ``strings.json``. These tests add the
project-specific consistency checks that hassfest does not perform for a
custom integration: the shipped translations match the source strings, and
every translation key raised or rendered by the code exists.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from homeassistant.config_entries import ConfigEntryState

from custom_components.opus_greennet import (
    binary_sensor,
    climate,
    cover,
    event,
    light,
    lock,
    sensor,
    switch,
)
from custom_components.opus_greennet.const import EEP_MAPPINGS
from custom_components.opus_greennet.enocean_device import EnOceanDevice

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components/opus_greennet"
STRINGS = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
TRANSLATIONS = {
    path.stem: json.loads(path.read_text(encoding="utf-8"))
    for path in (INTEGRATION / "translations").glob("*.json")
}
PLATFORMS = {
    "binary_sensor": binary_sensor,
    "climate": climate,
    "cover": cover,
    "event": event,
    "light": light,
    "lock": lock,
    "sensor": sensor,
    "switch": switch,
}


def _key_paths(value: object, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if not isinstance(value, dict):
        return {prefix}
    paths: set[tuple[str, ...]] = set()
    for key, item in value.items():
        paths |= _key_paths(item, (*prefix, key))
    return paths


def _string_arguments(function_names: set[str], keyword: str) -> set[str]:
    """Collect literal keys passed positionally or as ``keyword=`` in the code."""
    keys: set[str] = set()
    for module in INTEGRATION.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", None))
            for kw in node.keywords:
                if kw.arg == keyword and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        keys.add(kw.value.value)
            if name in function_names and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
    return keys


def test_english_translation_equals_source_strings() -> None:
    assert TRANSLATIONS["en"] == STRINGS


@pytest.mark.parametrize("language", sorted(TRANSLATIONS))
def test_every_translation_has_the_source_key_structure(language: str) -> None:
    assert _key_paths(TRANSLATIONS[language]) == _key_paths(STRINGS)


@pytest.mark.parametrize("language", sorted(TRANSLATIONS))
def test_placeholders_are_kept_in_every_translation(language: str) -> None:
    def leaves(value: object, prefix: tuple[str, ...] = ()):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from leaves(item, (*prefix, key))
        else:
            yield prefix, value

    source = dict(leaves(STRINGS))
    for path, text in leaves(TRANSLATIONS[language]):
        placeholders = {part.split("}")[0] for part in str(source[path]).split("{")[1:]}
        for placeholder in placeholders:
            assert f"{{{placeholder}}}" in text, (language, path, placeholder)


def test_raised_exception_keys_are_translated() -> None:
    used = _string_arguments(
        {"request_error", "async_cancel_pending"}, "translation_key"
    )
    # Entity translation keys are also passed as translation_key=; restrict to
    # keys that are not entity names.
    entity_keys = {key for platform in STRINGS["entity"].values() for key in platform}
    exception_keys = used - entity_keys
    assert exception_keys
    assert exception_keys <= set(STRINGS["exceptions"]), exception_keys - set(
        STRINGS["exceptions"]
    )


def test_config_flow_errors_and_aborts_are_translated() -> None:
    tree = ast.parse((INTEGRATION / "config_flow.py").read_text(encoding="utf-8"))
    errors: set[str] = set()
    aborts: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.value, ast.Constant)
        ):
            errors.add(node.value.value)
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                    aborts.add(kw.value.value)
    # _abort_if_unique_id_configured() uses the standard reason.
    aborts.add("already_configured")
    assert errors == set(STRINGS["config"]["error"])
    assert aborts == set(STRINGS["config"]["abort"])
    assert set(STRINGS["config"]["step"]["user"]["data"]) == set(
        STRINGS["config"]["step"]["user"]["data_description"]
    )


def test_services_yaml_matches_service_strings() -> None:
    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text())
    assert set(services) == set(STRINGS["services"])
    for service, definition in services.items():
        assert set(definition["fields"]) == set(
            STRINGS["services"][service]["fields"]
        ), service


async def test_every_created_entity_has_a_translated_name(hass) -> None:
    """Instantiate entities for every supported EEP and check their keys."""
    devices = {
        f"D{index:07d}": EnOceanDevice(
            device_id=f"D{index:07d}", friendly_id="Device", eeps=[{"eep": eep}]
        )
        for index, eep in enumerate(EEP_MAPPINGS)
    }
    coordinator = MagicMock()
    coordinator.devices = devices
    coordinator.eag_id = "AABB0011"
    coordinator.available = True
    entry = SimpleNamespace(
        entry_id="entry-id",
        data={"eag_id": "AABB0011"},
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(
            coordinator=coordinator, gateway_device_id="gateway"
        ),
        async_on_unload=MagicMock(),
    )
    seen: dict[str, set[str | None]] = {}
    for platform, module in PLATFORMS.items():
        added: list = []
        with (
            patch.object(
                module, "migrate_legacy_entity_suffix", MagicMock(), create=True
            ),
            patch.object(
                module, "migrate_legacy_multichannel_entity", MagicMock(), create=True
            ),
        ):
            await module.async_setup_entry(
                hass, entry, lambda entities, *_a, **_k: added.extend(entities)
            )
        await hass.async_block_till_done()
        assert added, platform
        seen[platform] = {entity.translation_key for entity in added}
        for entity in added:
            assert entity.has_entity_name is True, entity
            assert entity.should_poll is False, entity
            key = entity.translation_key
            if key is None:
                # Single-channel actuators use the device name as entity name.
                assert platform in {"light", "switch", "cover", "climate", "event"}
                continue
            assert key in STRINGS["entity"][platform], (platform, key)
    # The test must actually reach the translated names it guards.
    assert {"signal_strength", "battery_level"} <= seen["sensor"]
    assert "water_leak" in seen["binary_sensor"]
