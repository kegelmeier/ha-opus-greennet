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
from homeassistant.util import dt as dt_util
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
        "simple_sun_shading.yaml",
        "simple_sun_shading.de.yaml",
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
        # Blinds, sensors and the helper of the sun shading may come from any brand.
        if entity is None or name in {
            "blinds",
            "cover",
            "pause_helper",
            "temperature_sensor",
            "lux_sensor",
            "door_contacts",
        }:
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


# ── simple sun shading ─────────────────────────────────────────────────────

# Texts a user reads. Everything else must be identical in both languages.
_TEXT_KEYS = {"name", "description", "alias", "label", "source_url", "stop"}


def _without_texts(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _without_texts(value)
            for key, value in node.items()
            if key not in _TEXT_KEYS
        }
    if isinstance(node, list):
        return [_without_texts(item) for item in node]
    return node


def test_the_german_sun_shading_only_differs_in_its_texts() -> None:
    english = load_yaml_dict(str(BLUEPRINT_DIR / "simple_sun_shading.yaml"))
    german = load_yaml_dict(str(BLUEPRINT_DIR / "simple_sun_shading.de.yaml"))
    assert _without_texts(german) == _without_texts(english)
    assert german["blueprint"]["name"] != english["blueprint"]["name"]


def test_sun_shading_never_closes_the_blind_completely() -> None:
    inputs = load_yaml_dict(str(BLUEPRINT_DIR / "simple_sun_shading.yaml"))[
        "blueprint"
    ]["input"]
    shade = inputs["basic_section"]["input"]["shade_position"]
    assert shade["selector"]["number"]["min"] >= 10
    assert 25 <= shade["default"] <= 30


_SHADING = {
    "cover": "cover.blind",
    "pause_helper": "input_datetime.pause",
    "manual_events": ["event.rocker"],
    "use_time": False,
    "lux_sensor": "sensor.lux",
    "lux_threshold": 20000,
    "start_delay": {"hours": 0, "minutes": 0, "seconds": 0},
    "end_delay": {"hours": 0, "minutes": 0, "seconds": 0},
}


async def _settle() -> None:
    """Let runs progress. A shading run waits for ever, so no block_till_done."""
    await asyncio.sleep(0.4)


async def _shading_setup(
    hass: HomeAssistant, name: str, position: int, **inputs: Any
) -> list[int]:
    """Use the blueprint; return the list of positions the blind was sent to."""
    sent: list[int] = []

    def set_position(call: ServiceCall) -> None:
        sent.append(call.data["position"])
        hass.states.async_set(
            "cover.blind",
            "open" if call.data["position"] else "closed",
            {"current_position": call.data["position"]},
        )

    hass.services.async_register("cover", "set_cover_position", set_position)
    hass.services.async_register("cover", "stop_cover", lambda call: None)
    hass.states.async_set("cover.blind", "open", {"current_position": position})
    hass.states.async_set("sensor.lux", "100", {"unit_of_measurement": "lx"})
    hass.states.async_set("sun.sun", "above_horizon", {"azimuth": 180, "elevation": 40})
    hass.states.async_set("event.rocker", "2026-11-14T10:00:00.000+00:00", {})
    assert await async_setup_component(
        hass,
        "input_datetime",
        {"input_datetime": {"pause": {"has_date": True, "initial": "2020-01-01"}}},
    )
    await _use(hass, name, {**_SHADING, **inputs})
    return sent


def _bright(hass: HomeAssistant, lux: str) -> None:
    hass.states.async_set("sensor.lux", lux, {"unit_of_measurement": "lx"})


@pytest.mark.parametrize(
    "name", ["simple_sun_shading.yaml", "simple_sun_shading.de.yaml"]
)
async def test_sun_shading_closes_to_the_shade_position_and_goes_back(
    hass: HomeAssistant, name: str
) -> None:
    sent = await _shading_setup(hass, name, 80)

    _bright(hass, "30000")
    await _settle()
    assert sent == [30]

    _bright(hass, "100")
    await _settle()
    assert sent == [30, 80]


async def test_sun_shading_keeps_shading_in_the_hysteresis_band(
    hass: HomeAssistant,
) -> None:
    sent = await _shading_setup(hass, "simple_sun_shading.yaml", 80)
    _bright(hass, "30000")
    await _settle()
    # 17000 is below the start limit but above the lowered keep limit (15000).
    _bright(hass, "17000")
    await _settle()
    assert sent == [30]


async def test_a_press_on_the_chosen_rocker_pauses_the_day(
    hass: HomeAssistant,
) -> None:
    sent = await _shading_setup(hass, "simple_sun_shading.yaml", 80)

    for kind, extra in (
        ("buttonBI_pressed", {}),
        ("buttonAI_released", {}),
        ("buttonAI_pressed", {"channel": 1}),
    ):
        hass.states.async_set(
            "event.rocker",
            f"2026-11-14T10:00:0{len(extra)}.{len(kind)}+00:00",
            {"event_type": kind, **extra},
        )
        await _settle()
    assert hass.states.get("input_datetime.pause").state == "2020-01-01"

    hass.states.async_set(
        "event.rocker",
        "2026-11-14T10:01:00.000+00:00",
        {"event_type": "buttonAI_pressed"},
    )
    await _settle()
    assert hass.states.get("input_datetime.pause").state == dt_util.now().strftime(
        "%Y-%m-%d"
    )

    _bright(hass, "30000")
    await _settle()
    assert not sent


async def test_a_press_during_shading_cancels_the_return_drive(
    hass: HomeAssistant,
) -> None:
    sent = await _shading_setup(hass, "simple_sun_shading.yaml", 80)
    _bright(hass, "30000")
    await _settle()
    hass.states.async_set(
        "event.rocker",
        "2026-11-14T10:01:00.000+00:00",
        {"event_type": "buttonAI_pressed"},
    )
    await _settle()
    _bright(hass, "100")
    await _settle()
    assert sent == [30]


async def test_an_open_door_blocks_closing_until_it_is_shut(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set("binary_sensor.door", "on")
    sent = await _shading_setup(
        hass, "simple_sun_shading.yaml", 80, door_contacts=["binary_sensor.door"]
    )
    _bright(hass, "30000")
    await _settle()
    assert not sent

    hass.states.async_set("binary_sensor.door", "off")
    await _settle()
    assert sent == [30]


async def test_sun_shading_neither_closes_fully_nor_raises_a_closed_blind(
    hass: HomeAssistant,
) -> None:
    # A value below the minimum, written into the file by hand, is lifted to 10.
    sent = await _shading_setup(hass, "simple_sun_shading.yaml", 100, shade_position=0)
    _bright(hass, "30000")
    await _settle()
    assert sent == [10]

    # Another automation closes the blind completely: it is not raised again.
    hass.states.async_set("cover.blind", "closed", {"current_position": 0})
    _bright(hass, "100")
    await _settle()
    assert sent == [10]
