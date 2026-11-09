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
from homeassistant import config_entries
from homeassistant.components.blueprint.models import Blueprint
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.helpers import (
    condition,
    template,
    translation,
    trigger,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityInfo, entity_sources
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
    names = {
        "button_to_light",
        "fire_alarm_response",
        "water_leak_warning",
        "window_handle_blind_up",
        "window_handle_notification",
    }
    # Every blueprint also exists in German (".de.yaml").
    assert {path.name for path in BLUEPRINTS} == {
        f"{name}{suffix}.yaml" for name in names for suffix in ("", ".de")
    }


# Texts a user reads. Everything else must be identical in both languages.
_TEXT_KEYS = {"name", "description", "alias", "title", "message", "label", "source_url"}


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


@pytest.mark.parametrize(
    "path",
    [path for path in BLUEPRINTS if path.name.endswith(".de.yaml")],
    ids=lambda path: path.name,
)
def test_the_german_blueprint_only_differs_in_its_texts(path: Path) -> None:
    english = BLUEPRINT_DIR / path.name.replace(".de.yaml", ".yaml")
    assert _without_texts(load_yaml_dict(str(path))) == _without_texts(
        load_yaml_dict(str(english))
    )
    assert (
        load_yaml_dict(str(path))["blueprint"]["name"]
        != (load_yaml_dict(str(english))["blueprint"]["name"])
    )


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


@pytest.mark.parametrize(
    "name", ["water_leak_warning.yaml", "water_leak_warning.de.yaml"]
)
def test_water_leak_warning_accepts_sensors_of_any_maker(name: str) -> None:
    """The leak picker shows every moisture sensor, not only Opus ones."""
    inputs = load_yaml_dict(str(BLUEPRINT_DIR / name))["blueprint"]["input"]
    entity = inputs["leak_sensors"]["selector"]["entity"]
    assert entity["filter"] == [{"domain": "binary_sensor", "device_class": "moisture"}]


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
def test_entity_pickers_are_limited_to_this_integration(path: Path) -> None:
    """Pickers of Opus GreenNet entities filter by integration."""
    inputs = load_yaml_dict(str(path))["blueprint"]["input"]
    for name, definition in inputs.items():
        entity = (definition.get("selector") or {}).get("entity")
        # Lights, blinds, extra detectors and leak sensors may come from any brand.
        if entity is None or name in {
            "blinds",
            "lights",
            "extra_detectors",
            "leak_sensors",
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


async def _use(
    hass: HomeAssistant,
    name: str,
    inputs: dict[str, Any],
    *,
    restart: bool = False,
) -> None:
    """Install a blueprint into the test config directory and use it."""
    # The shared hass fixture starts only the managers the integration needs.
    hass.set_state(CoreState.not_running if restart else CoreState.running)
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
    if restart:
        # The automation starts with Home Assistant: "homeassistant start" fires.
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
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


# ── fire alarm ─────────────────────────────────────────────────────────────

FIRE = "fire_alarm_response.yaml"


def _detector(
    hass: HomeAssistant,
    entity_id: str,
    state: str = "off",
    device_class: str = "smoke",
    *,
    opus: bool = True,
) -> None:
    """Create a detector; Opus detectors are known as entities of the integration."""
    if opus:
        entity_sources(hass)[entity_id] = EntityInfo(
            domain="opus_greennet", custom_component=True, source="config_entry"
        )
    hass.states.async_set(entity_id, state, {"device_class": device_class})


def _device(hass: HomeAssistant, name: str, *, domain: str = "opus_greennet") -> str:
    """Create a device; the config entry is only registered, never set up."""
    entry = hass.config_entries._entries.get_entries_for_domain(domain)
    entry = next(iter(entry), None)
    if entry is None:
        entry = config_entries.ConfigEntry(
            version=1,
            minor_version=1,
            domain=domain,
            title=domain,
            data={},
            source=config_entries.SOURCE_USER,
            options={},
            unique_id=None,
            discovery_keys={},
            subentries_data=None,
        )
        hass.config_entries._entries[entry.entry_id] = entry
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(domain, name)}
    )
    return device.id


def _blind(
    hass: HomeAssistant,
    cover: str,
    state: str,
    position: int,
    *,
    lock: str | None = None,
    lock_enabled: bool = True,
    device: str | None = None,
) -> None:
    """Create a blind; its Blockage switch belongs to the same device."""
    registry = er.async_get(hass)
    device_id = _device(hass, device or cover)
    registry.async_get_or_create(
        "cover",
        "opus_greennet",
        cover,
        device_id=device_id,
        suggested_object_id=cover.split(".")[1],
    )
    hass.states.async_set(cover, state, {"current_position": position})
    if lock is None:
        return
    entry = registry.async_get_or_create(
        "switch",
        "opus_greennet",
        lock,
        device_id=device_id,
        suggested_object_id=lock.split(".")[1],
        disabled_by=None if lock_enabled else er.RegistryEntryDisabler.USER,
    )
    entity_sources(hass)[entry.entity_id] = EntityInfo(
        domain="opus_greennet", custom_component=True, source="config_entry"
    )
    if lock_enabled:
        hass.states.async_set(entry.entity_id, "off")


def _fire_inputs(**override: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "include_opus": True,
        "excluded_detectors": [],
        "extra_detectors": [],
        "repeat_minutes": 0,
        "lights": ["light.hall"],
        "blinds": ["cover.closed_blind", "cover.open_blind"],
        "keep_blinds_up": True,
        "open_timeout": 10,
        "locks": ["lock.balcony_autolock"],
    }
    inputs.update(override)
    return inputs


def _fire_states(hass: HomeAssistant) -> None:
    _detector(hass, "binary_sensor.hall_smoke_alarm")
    _blind(hass, "cover.closed_blind", "closed", 0, lock="switch.closed_blind_blockage")
    # This blind has no lock; it is reported but does not stop the rest.
    _blind(hass, "cover.open_blind", "open", 100)


async def test_fire_alarm_with_nothing_chosen_only_warns(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    lights = _record(hass, "light", "turn_on")
    opened = _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    unlocked = _record(hass, "lock", "unlock")
    _fire_states(hass)
    await _use(hass, FIRE, {})

    hass.states.async_set(
        "binary_sensor.hall_smoke_alarm", "unavailable", {"device_class": "smoke"}
    )
    await hass.async_block_till_done()
    assert not messages

    hass.states.async_set(
        "binary_sensor.hall_smoke_alarm", "on", {"device_class": "smoke"}
    )
    await hass.async_block_till_done()
    assert [message.data["title"] for message in messages] == ["Fire alarm"]
    assert "hall_smoke_alarm" in messages[0].data["message"] or (
        "hall smoke" in messages[0].data["message"].lower()
    )
    assert not (lights or opened or blocked or released or unlocked)


async def test_fire_alarm_opens_blinds_then_blocks_and_releases_when_clear(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    lights = _record(hass, "light", "turn_on")
    opened = _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    unlocked = _record(hass, "lock", "unlock")
    _fire_states(hass)
    await _use(hass, FIRE, _fire_inputs())

    _detector(hass, "binary_sensor.hall_smoke_alarm", "on", opus=False)
    # Not async_block_till_done: the script now waits for the blind to get up.
    await asyncio.sleep(0.1)

    assert len(messages) == 2  # the warning and "cover.open_blind cannot be locked"
    assert "cover.open_blind" in messages[1].data["message"]
    assert [call.data["entity_id"] for call in lights] == [["light.hall"]]
    assert lights[0].data["brightness_pct"] == 100
    assert [call.data["entity_id"] for call in unlocked] == [["lock.balcony_autolock"]]
    # The blockage is released first, the blind already open gets no command.
    assert [call.data["entity_id"] for call in released] == [
        ["switch.closed_blind_blockage"]
    ]
    assert [call.data["entity_id"] for call in opened] == [["cover.closed_blind"]]
    # A moving blind must not be blocked: wait until it is up.
    assert not blocked

    hass.states.async_set("cover.closed_blind", "open", {"current_position": 100})
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in blocked] == [
        ["switch.closed_blind_blockage"]
    ]

    # The alarm is over: the blockage is released again, nothing moves back.
    _detector(hass, "binary_sensor.hall_smoke_alarm", "off", opus=False)
    await hass.async_block_till_done()
    assert len(released) == 2
    assert [call.data["entity_id"] for call in opened] == [["cover.closed_blind"]]


async def test_fire_alarm_does_not_block_blinds_that_did_not_get_up(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    _record(hass, "switch", "turn_off")
    _fire_states(hass)
    await _use(
        hass,
        FIRE,
        _fire_inputs(
            open_timeout=0.05, locks=[], lights=[], blinds=["cover.closed_blind"]
        ),
    )

    _detector(hass, "binary_sensor.hall_smoke_alarm", "on", opus=False)
    await asyncio.sleep(0.3)  # the blind never reaches the top
    await hass.async_block_till_done()

    assert not blocked
    assert len(messages) == 2
    assert "not locked" in messages[1].data["message"]


async def test_fire_alarm_waits_for_every_detector_and_warns_for_later_ones(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    _detector(hass, "binary_sensor.hall_smoke_alarm")
    _detector(hass, "binary_sensor.kitchen_smoke_alarm")
    _blind(hass, "cover.up", "open", 100, lock="switch.up_blockage")
    await _use(hass, FIRE, {"blinds": ["cover.up"], "keep_blinds_up": True})

    _detector(hass, "binary_sensor.kitchen_smoke_alarm", "on", opus=False)
    await hass.async_block_till_done()
    assert len(messages) == 1
    assert len(blocked) == 1  # the blind is already up: locked at once
    assert len(released) == 1  # the release before the blinds move

    # A second detector raises the alarm: another warning, nothing else.
    _detector(hass, "binary_sensor.hall_smoke_alarm", "on", opus=False)
    await hass.async_block_till_done()
    assert len(messages) == 2
    assert len(blocked) == 1

    _detector(hass, "binary_sensor.hall_smoke_alarm", "off", opus=False)
    await hass.async_block_till_done()
    assert len(released) == 1  # the kitchen detector still reports an alarm

    _detector(hass, "binary_sensor.kitchen_smoke_alarm", "off", opus=False)
    await hass.async_block_till_done()
    assert len(released) == 2


async def test_fire_alarm_ignores_excluded_opus_detectors_and_non_opus_ones(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    _detector(hass, "binary_sensor.hall_smoke_alarm")
    _detector(hass, "binary_sensor.kitchen_smoke_alarm")
    _detector(hass, "binary_sensor.other_brand_smoke", opus=False)
    await _use(
        hass,
        FIRE,
        {"excluded_detectors": ["binary_sensor.kitchen_smoke_alarm"]},
    )

    # Left out, and not an Opus Greennet device: neither starts the automation.
    _detector(hass, "binary_sensor.kitchen_smoke_alarm", "on", opus=False)
    _detector(hass, "binary_sensor.other_brand_smoke", "on", opus=False)
    await hass.async_block_till_done()
    assert not messages

    # An Opus detector is used without being picked.
    _detector(hass, "binary_sensor.hall_smoke_alarm", "on", opus=False)
    await hass.async_block_till_done()
    assert len(messages) == 1


async def test_fire_alarm_takes_additional_smoke_and_heat_detectors_of_any_brand(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    _detector(hass, "binary_sensor.hall_smoke_alarm")
    _detector(hass, "binary_sensor.attic_heat", device_class="heat", opus=False)
    await _use(
        hass,
        FIRE,
        {
            "include_opus": False,
            "extra_detectors": ["binary_sensor.attic_heat"],
        },
    )

    # Opus detectors switched off.
    _detector(hass, "binary_sensor.hall_smoke_alarm", "on", opus=False)
    await hass.async_block_till_done()
    assert not messages

    _detector(hass, "binary_sensor.attic_heat", "on", "heat", opus=False)
    await hass.async_block_till_done()
    assert len(messages) == 1
    assert "attic" in messages[0].data["message"].lower()


# ── window handle keeps the blinds up ──────────────────────────────────────


def _keep_up_inputs() -> dict[str, Any]:
    return {
        "handle_sensor": "sensor.window_handle_position",
        "handle_states": ["open", "tilted"],
        "blinds": ["cover.closed_blind"],
        "keep_blinds_up": True,
        "open_timeout": 10,
    }


async def test_open_handle_blocks_the_blind_once_it_is_up_and_releases_on_close(
    hass: HomeAssistant,
) -> None:
    opened = _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    _record(hass, "persistent_notification", "create")
    hass.states.async_set("sensor.window_handle_position", "closed")
    _blind(hass, "cover.closed_blind", "closed", 0, lock="switch.closed_blind_blockage")
    await _use(hass, "window_handle_blind_up.yaml", _keep_up_inputs())

    hass.states.async_set("sensor.window_handle_position", "tilted")
    # Not async_block_till_done: the script waits for the blind to get up.
    await asyncio.sleep(0.1)
    assert len(released) == 1  # the blockage goes first, or the blind stays
    assert [call.data["entity_id"] for call in opened] == [["cover.closed_blind"]]
    assert not blocked  # blocking a moving blind would stop it

    hass.states.async_set("cover.closed_blind", "open", {"current_position": 100})
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in blocked] == [
        ["switch.closed_blind_blockage"]
    ]

    hass.states.async_set("sensor.window_handle_position", "closed")
    await hass.async_block_till_done()
    assert len(released) == 2
    # The blind is not moved back; it can be controlled again.
    assert len(opened) == 1


async def test_a_handle_closed_while_the_blind_moves_leaves_it_unblocked(
    hass: HomeAssistant,
) -> None:
    _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    _record(hass, "switch", "turn_off")
    _record(hass, "persistent_notification", "create")
    hass.states.async_set("sensor.window_handle_position", "closed")
    _blind(hass, "cover.closed_blind", "closed", 0, lock="switch.closed_blind_blockage")
    await _use(hass, "window_handle_blind_up.yaml", _keep_up_inputs())

    hass.states.async_set("sensor.window_handle_position", "open")
    await asyncio.sleep(0.1)
    hass.states.async_set("sensor.window_handle_position", "closed")
    await asyncio.sleep(0.1)
    hass.states.async_set("cover.closed_blind", "open", {"current_position": 100})
    await hass.async_block_till_done()
    assert not blocked


async def test_blind_that_does_not_get_up_is_not_blocked(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    _record(hass, "switch", "turn_off")
    hass.states.async_set("sensor.window_handle_position", "closed")
    _blind(hass, "cover.closed_blind", "closed", 0, lock="switch.closed_blind_blockage")
    await _use(
        hass,
        "window_handle_blind_up.yaml",
        _keep_up_inputs() | {"open_timeout": 0.05},
    )

    hass.states.async_set("sensor.window_handle_position", "open")
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()

    assert not blocked
    assert len(messages) == 1
    assert "not locked" in messages[0].data["message"]


async def test_only_the_blockage_switch_of_an_opus_blind_is_used(
    hass: HomeAssistant,
) -> None:
    """Lights, foreign switches and disabled switches are never locked."""
    messages = _record(hass, "persistent_notification", "create")
    _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    _blind(hass, "cover.a", "open", 100, lock="switch.a_blockage")
    _blind(hass, "cover.b", "open", 100, lock="switch.b_blockage", lock_enabled=False)
    registry = er.async_get(hass)
    # A switch of another integration on the same device, and a switch of this
    # integration on a different device (a relay, a light).
    registry.async_get_or_create(
        "switch",
        "other",
        "foreign",
        device_id=dr.async_get(hass)
        .async_get_device(identifiers={("opus_greennet", "cover.a")})
        .id,
        suggested_object_id="foreign",
    )
    hass.states.async_set("switch.foreign", "off")
    registry.async_get_or_create(
        "switch",
        "opus_greennet",
        "relay",
        device_id=_device(hass, "relay"),
        suggested_object_id="relay",
    )
    entity_sources(hass)["switch.relay"] = EntityInfo(
        domain="opus_greennet", custom_component=True, source="config_entry"
    )
    hass.states.async_set("switch.relay", "off")
    hass.states.async_set("sensor.window_handle_position", "closed")
    await _use(
        hass,
        "window_handle_blind_up.yaml",
        _keep_up_inputs() | {"blinds": ["cover.a", "cover.b"]},
    )

    hass.states.async_set("sensor.window_handle_position", "open")
    await hass.async_block_till_done()

    assert [call.data["entity_id"] for call in blocked] == [["switch.a_blockage"]]
    assert [call.data["entity_id"] for call in released] == [["switch.a_blockage"]]
    assert len(messages) == 1  # cover.b has no active Blockage entity
    assert "cover.b" in messages[0].data["message"]


async def test_blinds_are_not_locked_when_the_option_is_off(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    opened = _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    hass.states.async_set("sensor.window_handle_position", "closed")
    _blind(hass, "cover.closed_blind", "closed", 0, lock="switch.closed_blind_blockage")
    await _use(
        hass,
        "window_handle_blind_up.yaml",
        _keep_up_inputs() | {"keep_blinds_up": False},
    )

    hass.states.async_set("sensor.window_handle_position", "tilted")
    await hass.async_block_till_done()

    assert len(opened) == 1
    assert not (blocked or released or messages)


async def test_a_device_with_another_blind_is_locked_only_if_all_are_chosen(
    hass: HomeAssistant,
) -> None:
    """Locking one channel would also lock a blind that is not up."""
    messages = _record(hass, "persistent_notification", "create")
    _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    _record(hass, "switch", "turn_off")
    _blind(hass, "cover.ch1", "open", 100, lock="switch.ch1_blockage", device="dev")
    _blind(hass, "cover.ch2", "closed", 0, device="dev")
    hass.states.async_set("sensor.window_handle_position", "closed")
    await _use(
        hass,
        "window_handle_blind_up.yaml",
        _keep_up_inputs() | {"blinds": ["cover.ch1"]},
    )

    hass.states.async_set("sensor.window_handle_position", "open")
    await hass.async_block_till_done()

    assert not blocked
    assert len(messages) == 1
    assert "cover.ch1" in messages[0].data["message"]


async def test_alarm_that_ends_while_the_blinds_move_does_not_lock_them(
    hass: HomeAssistant,
) -> None:
    _record(hass, "persistent_notification", "create")
    _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    _record(hass, "switch", "turn_off")
    _blind(hass, "cover.closed_blind", "closed", 0, lock="switch.closed_blind_blockage")
    _detector(hass, "binary_sensor.hall_smoke_alarm", opus=False)
    await _use(
        hass,
        FIRE,
        _fire_inputs(blinds=["cover.closed_blind"], locks=[], lights=[]),
    )

    _detector(hass, "binary_sensor.hall_smoke_alarm", "on", opus=False)
    await asyncio.sleep(0.1)
    _detector(hass, "binary_sensor.hall_smoke_alarm", "off", opus=False)
    await asyncio.sleep(0.1)
    hass.states.async_set("cover.closed_blind", "open", {"current_position": 100})
    await hass.async_block_till_done()

    assert not blocked


async def test_restart_releases_blinds_when_the_alarm_is_over(
    hass: HomeAssistant,
) -> None:
    opened = _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    messages = _record(hass, "persistent_notification", "create")
    _blind(hass, "cover.closed_blind", "open", 100, lock="switch.closed_blind_blockage")
    inputs = _fire_inputs(
        blinds=["cover.closed_blind"],
        locks=[],
        lights=[],
        include_opus=False,
        extra_detectors=["binary_sensor.hall_smoke_alarm"],
    )

    # Home Assistant starts while an alarm is still reported: nothing happens.
    _detector(hass, "binary_sensor.hall_smoke_alarm", "on", opus=False)
    await _use(hass, FIRE, inputs, restart=True)
    assert not (released or blocked or messages or opened)


async def test_restart_after_the_alarm_released_the_lock(
    hass: HomeAssistant,
) -> None:
    """The alarm ended while Home Assistant was down: nothing stays locked."""
    opened = _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    messages = _record(hass, "persistent_notification", "create")
    _blind(hass, "cover.closed_blind", "open", 100, lock="switch.closed_blind_blockage")
    _detector(hass, "binary_sensor.hall_smoke_alarm", "off", opus=False)
    inputs = _fire_inputs(
        blinds=["cover.closed_blind"],
        locks=[],
        lights=[],
        include_opus=False,
        extra_detectors=["binary_sensor.hall_smoke_alarm"],
    )
    await _use(hass, FIRE, inputs, restart=True)

    assert [call.data["entity_id"] for call in released] == [
        ["switch.closed_blind_blockage"]
    ]
    assert not (blocked or opened or messages)


async def test_restart_with_an_open_handle_does_nothing(
    hass: HomeAssistant,
) -> None:
    opened = _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    _record(hass, "persistent_notification", "create")
    _blind(hass, "cover.closed_blind", "open", 100, lock="switch.closed_blind_blockage")
    hass.states.async_set("sensor.window_handle_position", "open")
    await _use(hass, "window_handle_blind_up.yaml", _keep_up_inputs(), restart=True)
    assert not (opened or blocked or released)


async def test_restart_with_a_closed_handle_releases_the_blinds(
    hass: HomeAssistant,
) -> None:
    opened = _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    _record(hass, "persistent_notification", "create")
    _blind(hass, "cover.closed_blind", "open", 100, lock="switch.closed_blind_blockage")
    hass.states.async_set("sensor.window_handle_position", "closed")
    await _use(hass, "window_handle_blind_up.yaml", _keep_up_inputs(), restart=True)
    assert len(released) == 1
    assert not (opened or blocked)


async def test_german_fire_alarm_works_like_the_english_one(
    hass: HomeAssistant,
) -> None:
    messages = _record(hass, "persistent_notification", "create")
    _record(hass, "cover", "open_cover")
    blocked = _record(hass, "switch", "turn_on")
    released = _record(hass, "switch", "turn_off")
    _blind(hass, "cover.closed_blind", "closed", 0, lock="switch.closed_blind_blockage")
    _detector(hass, "binary_sensor.hall_smoke_alarm", opus=False)
    await _use(
        hass,
        "fire_alarm_response.de.yaml",
        _fire_inputs(
            blinds=["cover.closed_blind"],
            locks=[],
            lights=[],
            include_opus=False,
            extra_detectors=["binary_sensor.hall_smoke_alarm"],
        ),
    )

    _detector(hass, "binary_sensor.hall_smoke_alarm", "on", opus=False)
    await asyncio.sleep(0.1)
    assert messages[0].data["title"] == "Feueralarm"
    assert not blocked  # the blind is still moving

    hass.states.async_set("cover.closed_blind", "open", {"current_position": 100})
    await hass.async_block_till_done()
    assert len(blocked) == 1

    _detector(hass, "binary_sensor.hall_smoke_alarm", "off", opus=False)
    await hass.async_block_till_done()
    assert len(released) == 2
