"""Blueprints shipped in ``blueprints/automation/opus_greennet``.

The blueprints are not loaded by the integration; users import them. These
tests keep them valid: the files satisfy Home Assistant's blueprint schema, use
only declared inputs, point at this repository, and behave as described when
Home Assistant runs them as automations.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.blueprint.models import Blueprint
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.helpers import condition, template, translation, trigger
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import extract_inputs, load_yaml_dict

from custom_components.opus_greennet.const import BUTTON_KEYS

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT_DIR = ROOT / "blueprints/automation/opus_greennet"
BLUEPRINTS = sorted(BLUEPRINT_DIR.glob("*.yaml"))
SOURCE_PREFIX = (
    "https://github.com/kegelmeier/ha-opus-greennet/blob/main/"
    "blueprints/automation/opus_greennet/"
)
HANDLE_POSITIONS = {"open", "closed", "tilted"}


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def test_the_expected_blueprints_exist() -> None:
    assert {path.name for path in BLUEPRINTS} == {
        "button_to_light.yaml",
        "water_leak_warning.yaml",
        "window_handle_blind_up.yaml",
        "window_handle_notification.yaml",
    }


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
def test_blueprint_is_valid_and_uses_only_declared_inputs(path: Path) -> None:
    data = load_yaml_dict(str(path))
    blueprint = Blueprint(
        data, path=path.name, expected_domain="automation", schema=BLUEPRINT_SCHEMA
    )

    assert extract_inputs(data) <= set(blueprint.inputs)
    metadata = data["blueprint"]
    assert metadata["source_url"] == SOURCE_PREFIX + path.name
    assert metadata["name"].startswith("Opus GreenNet: ")
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert _version(metadata["homeassistant"]["min_version"]) <= _version(
        hacs["homeassistant"]
    )


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
def test_entity_pickers_are_limited_to_this_integration(path: Path) -> None:
    """Pickers of Opus GreenNet entities filter by integration."""
    inputs = load_yaml_dict(str(path))["blueprint"]["input"]
    for name, definition in inputs.items():
        entity = (definition.get("selector") or {}).get("entity")
        if entity is None or name == "blinds":  # blinds may come from any brand
            continue
        assert all(f.get("integration") == "opus_greennet" for f in entity["filter"])


def test_button_choices_are_buttons_the_integration_reports() -> None:
    data = load_yaml_dict(str(BLUEPRINT_DIR / "button_to_light.yaml"))
    options = data["blueprint"]["input"]["button"]["selector"]["select"]["options"]
    values = {option["value"] for option in options}
    assert values <= set(BUTTON_KEYS)
    assert data["blueprint"]["input"]["button"]["default"] in values


@pytest.mark.parametrize(
    "name", ["window_handle_notification.yaml", "window_handle_blind_up.yaml"]
)
def test_handle_choices_are_positions_the_sensor_reports(name: str) -> None:
    inputs = load_yaml_dict(str(BLUEPRINT_DIR / name))["blueprint"]["input"]
    options = inputs["handle_states"]["selector"]["select"]["options"]
    values = {option["value"] for option in options}
    assert values <= HANDLE_POSITIONS
    assert set(inputs["handle_states"]["default"]) <= values


# ── behaviour in a running automation ──────────────────────────────────────


async def _use(hass: HomeAssistant, name: str, inputs: dict[str, Any]) -> None:
    """Install a blueprint into the test config directory and use it."""
    # The shared hass fixture starts only the managers the integration needs.
    hass.set_state(CoreState.running)
    template.async_setup(hass)
    translation.async_setup(hass)
    await asyncio.gather(condition.async_setup(hass), trigger.async_setup(hass))
    target = Path(hass.config.path("blueprints/automation/opus_greennet"))
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT_DIR / name, target / name)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {"path": f"opus_greennet/{name}", "input": inputs}
            }
        },
    )
    await hass.async_block_till_done()


def _record(hass: HomeAssistant, domain: str, service: str) -> list[ServiceCall]:
    calls: list[ServiceCall] = []
    hass.services.async_register(domain, service, calls.append)
    return calls


async def test_rocker_button_toggles_the_light_on_every_press(
    hass: HomeAssistant,
) -> None:
    calls = _record(hass, "light", "toggle")
    await _use(
        hass,
        "button_to_light.yaml",
        {
            "button_event": "event.hall_rocker",
            "button": "buttonAI",
            "light_target": {"entity_id": "light.hall"},
            "light_action": "toggle",
        },
    )

    async def fire(event_type: str, stamp: str) -> None:
        hass.states.async_set("event.hall_rocker", stamp, {"event_type": event_type})
        await hass.async_block_till_done()

    await fire("buttonAI_pressed", "2026-11-05T10:00:00.000+00:00")
    assert [call.data["entity_id"] for call in calls] == [["light.hall"]]
    await fire("buttonAI_released", "2026-11-05T10:00:01.000+00:00")
    await fire("buttonA0_pressed", "2026-11-05T10:00:02.000+00:00")
    assert len(calls) == 1
    # The same button pressed again is its own event.
    await fire("buttonAI_pressed", "2026-11-05T10:00:03.000+00:00")
    assert len(calls) == 2


async def test_rocker_button_can_switch_on_instead_of_toggling(
    hass: HomeAssistant,
) -> None:
    toggled = _record(hass, "light", "toggle")
    switched_on = _record(hass, "light", "turn_on")
    await _use(
        hass,
        "button_to_light.yaml",
        {
            "button_event": "event.hall_rocker",
            "button": "buttonB0",
            "light_target": {"entity_id": "light.hall"},
            "light_action": "turn_on",
        },
    )
    hass.states.async_set(
        "event.hall_rocker", "2026-11-05T10:00:00.000+00:00", {"event_type": "x"}
    )
    hass.states.async_set(
        "event.hall_rocker",
        "2026-11-05T10:00:01.000+00:00",
        {"event_type": "buttonB0_pressed"},
    )
    await hass.async_block_till_done()
    assert len(switched_on) == 1
    assert not toggled


async def test_tilted_window_handle_sends_the_notification(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    hass.states.async_set("sensor.window_handle_position", "closed")
    await _use(
        hass,
        "window_handle_notification.yaml",
        {
            "handle_sensor": "sensor.window_handle_position",
            "handle_states": ["tilted"],
            "stays_for": {"hours": 0, "minutes": 0, "seconds": 0},
        },
    )

    for state in ("open", "unavailable", "unknown", "closed"):
        hass.states.async_set("sensor.window_handle_position", state)
        await hass.async_block_till_done()
    assert not messages

    hass.states.async_set("sensor.window_handle_position", "tilted")
    await hass.async_block_till_done()
    assert len(messages) == 1
    assert messages[0].data["title"] == "Window"


async def test_opened_window_handle_moves_only_blinds_that_are_not_fully_open(
    hass: HomeAssistant,
) -> None:
    opened = _record(hass, "cover", "open_cover")
    hass.states.async_set("sensor.window_handle_position", "closed")
    hass.states.async_set("cover.closed_blind", "closed", {"current_position": 0})
    hass.states.async_set("cover.half_blind", "open", {"current_position": 40})
    hass.states.async_set("cover.open_blind", "open", {"current_position": 100})
    await _use(
        hass,
        "window_handle_blind_up.yaml",
        {
            "handle_sensor": "sensor.window_handle_position",
            "handle_states": ["open", "tilted"],
            "blinds": ["cover.closed_blind", "cover.half_blind", "cover.open_blind"],
        },
    )

    hass.states.async_set("sensor.window_handle_position", "unavailable")
    await hass.async_block_till_done()
    assert not opened

    hass.states.async_set("sensor.window_handle_position", "tilted")
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in opened] == [
        ["cover.closed_blind"],
        ["cover.half_blind"],
    ]


async def test_water_leak_warns_once_and_not_on_an_outage(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    hass.states.async_set("binary_sensor.basement_water_leak", "off")
    await _use(
        hass,
        "water_leak_warning.yaml",
        {
            "leak_sensors": ["binary_sensor.basement_water_leak"],
            "repeat_minutes": 0,
        },
    )

    hass.states.async_set("binary_sensor.basement_water_leak", "unavailable")
    await hass.async_block_till_done()
    assert not messages

    hass.states.async_set("binary_sensor.basement_water_leak", "on")
    await hass.async_block_till_done()
    assert len(messages) == 1
    assert messages[0].data["title"] == "Water leak"
