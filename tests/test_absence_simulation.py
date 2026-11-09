"""Behaviour of the blueprint ``absence_simulation``.

The decisions of the blueprint sit in templates that are repeated at several
places (start condition, waits, switch-on condition). Three kinds of test:

* the repeated gate is identical everywhere (a drifting copy would be a bug),
* the gate is rendered with real states (people, absence switch, brightness),
* an automation made from the blueprint ends at once when the gate closes.

Waits of the simulation last minutes, so the tests never wait for a switch-on;
they only check that nothing is switched and that a run ends when it must.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.helpers import condition, template, translation, trigger
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT_DIR = ROOT / "blueprints/automation/opus_greennet"
FILES = ["absence_simulation.yaml", "absence_simulation.de.yaml"]

ANNA = "person.anna"
BEN = "person.ben"
SWITCH = "input_boolean.nobody_home"
LUX_1 = "sensor.hall_lux"
LUX_2 = "sensor.kitchen_lux"
LIGHT = "light.hall"


def _walk(node: Any) -> Any:
    """Yield every (key, value) pair of a nested blueprint structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _templates(name: str, needle: str) -> list[tuple[str, str]]:
    data = load_yaml_dict(str(BLUEPRINT_DIR / name))
    return [
        (key, value)
        for key, value in _walk(data)
        if key in {"wait_template", "value_template"}
        and isinstance(value, str)
        and needle in value
    ]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _gate_clause(text: str) -> str:
    """The shared part: everything from the first ``set`` up to ``set stop``."""
    match = re.search(r"\{%- set bad = .*?\{%- set stop = .*? -%\}", text, re.S)
    assert match
    return _norm(match.group(0))


def _start_clause(text: str) -> str:
    match = re.search(r"\{%- set sx = .*?\{%- set start_ok = .*? -%\}", text, re.S)
    assert match
    return _norm(match.group(0))


@pytest.mark.parametrize("name", FILES)
def test_the_stop_gate_is_the_same_everywhere(name: str) -> None:
    found = _templates(name, "set stop")
    # start condition, start wait, continue condition, loop, waits, slot places
    assert len({text for _, text in found}) >= 6
    assert len({_gate_clause(text) for _, text in found}) == 1


@pytest.mark.parametrize("name", FILES)
def test_the_start_brightness_is_checked_once_before_the_loop(name: str) -> None:
    found = _templates(name, "set start_ok")
    assert [key for key, _ in found] == ["wait_template"]
    ((_, text),) = found
    assert _norm(text).endswith("{{ stop or start_ok }}")


@pytest.mark.parametrize("name", FILES)
def test_the_switch_on_step_still_checks_the_stop_gate(name: str) -> None:
    (condition_text,) = {
        text
        for key, text in _templates(name, "repeat.item.t_on - 3")
        if key == "value_template"
    }
    assert "not stop and is_state(repeat.item.entity, 'off')" in _norm(condition_text)
    assert "dark" not in condition_text


@pytest.mark.parametrize("name", FILES)
def test_old_inputs_are_gone_and_new_ones_exist(name: str) -> None:
    data = load_yaml_dict(str(BLUEPRINT_DIR / name))
    inputs: dict[str, Any] = {}
    for section in data["blueprint"]["input"].values():
        inputs.update(section["input"])
    for gone in ("absence_method", "light_switches", "lux_sensors", "lux_threshold"):
        assert gone not in inputs
    assert {
        "persons",
        "absence_switch",
        "lights",
        "lux_start_sensors",
        "lux_start_direction",
        "lux_start_value",
        "lux_end_sensors",
        "lux_end_direction",
        "lux_end_value",
        "lux_match",
    } <= set(inputs)
    assert inputs["lights"]["selector"]["entity"]["filter"] == [
        {"domain": ["light", "switch"]}
    ]
    for key in ("lux_start_sensors", "lux_end_sensors"):
        assert inputs[key]["default"] == []
        assert inputs[key]["selector"]["entity"]["filter"] == [
            {"domain": "sensor", "device_class": "illuminance"}
        ]
    # The lux conditions are optional: without sensors nothing changes.
    assert inputs["persons"]["default"] == []
    assert inputs["absence_switch"]["default"] == []


@pytest.mark.parametrize("name", FILES)
def test_the_brightness_texts_say_that_only_dimmable_lights_are_affected(
    name: str,
) -> None:
    data = load_yaml_dict(str(BLUEPRINT_DIR / name))
    section = data["blueprint"]["input"]["brightness_section"]
    for key in ("set_brightness", "brightness_pct"):
        text = section["input"][key]["name"].lower()
        assert "dimm" in text


def _states(hass: HomeAssistant, **values: str) -> None:
    for entity_id, state in {
        ANNA: "not_home",
        BEN: "not_home",
        SWITCH: "off",
        **{key.replace("__", "."): value for key, value in values.items()},
    }.items():
        hass.states.async_set(entity_id, state)


async def _render(
    hass: HomeAssistant, name: str, text: str, variables: dict[str, Any]
) -> Any:
    template.async_setup(hass)
    full = {
        "persons": [],
        "absence_switch": [],
        "release_entities": [],
        "targets": [LIGHT],
        "light_sensor": {},
        "lux_start_sensors": [],
        "lux_start_direction": "below",
        "lux_start_value": 50,
        "lux_end_sensors": [],
        "lux_end_direction": "above",
        "lux_end_value": 100,
        "lux_match": "all",
        "end_ts": 10**12,
        **variables,
    }
    return template.Template(text, hass).async_render(full)


def _continue_condition(name: str) -> str:
    # The first template that contains the stop gate is the start condition.
    return _templates(name, "set stop")[0][1]


def _start_wait(name: str) -> str:
    return _templates(name, "set start_ok")[0][1]


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize(
    ("persons", "switches", "states", "expected"),
    [
        # people only: every chosen person must be away
        ([ANNA, BEN], [], {}, True),
        ([ANNA, BEN], [], {"person__ben": "home"}, False),
        ([ANNA, BEN], [], {"person__anna": "home", "person__ben": "home"}, False),
        # a person in a zone is away; unknown or unavailable is not
        ([ANNA], [], {"person__anna": "work"}, True),
        ([ANNA], [], {"person__anna": "unknown"}, False),
        ([ANNA], [], {"person__anna": "unavailable"}, False),
        # a person that is not chosen does not matter
        ([ANNA], [], {"person__ben": "home"}, True),
        # absence switch only
        ([], [SWITCH], {"input_boolean__nobody_home": "on"}, True),
        ([], [SWITCH], {"input_boolean__nobody_home": "off"}, False),
        ([], [SWITCH], {"input_boolean__nobody_home": "unavailable"}, False),
        # people chosen: the switch plays no role, in either direction
        ([ANNA], [SWITCH], {"input_boolean__nobody_home": "on"}, True),
        ([ANNA], [SWITCH], {"input_boolean__nobody_home": "off"}, True),
        (
            [ANNA],
            [SWITCH],
            {"input_boolean__nobody_home": "on", "person__anna": "home"},
            False,
        ),
        # nothing chosen: never runs
        ([], [], {}, False),
    ],
)
async def test_who_must_be_away(
    hass: HomeAssistant,
    name: str,
    persons: list[str],
    switches: list[str],
    states: dict[str, str],
    expected: bool,
) -> None:
    _states(hass, **states)
    result = await _render(
        hass,
        name,
        _continue_condition(name),
        {"persons": persons, "absence_switch": switches},
    )
    assert result is expected


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize(
    ("sensors", "direction", "value", "mode", "values", "expected"),
    [
        # the end condition is met: the simulation must not run
        ([LUX_1], "above", 100, "all", {LUX_1: "500"}, False),
        ([LUX_1], "above", 100, "all", {LUX_1: "100"}, True),  # strictly above
        ([LUX_1], "above", 100, "all", {LUX_1: "50"}, True),
        ([LUX_1], "below", 10, "all", {LUX_1: "5"}, False),
        ([LUX_1], "below", 10, "all", {LUX_1: "50"}, True),
        # no sensor chosen, or no value: the condition never ends the run
        ([], "above", 100, "all", {}, True),
        ([LUX_1], "above", 100, "all", {LUX_1: "unavailable"}, True),
        ([LUX_1, LUX_2], "above", 100, "all", {LUX_1: "unknown", LUX_2: "500"}, False),
        # several sensors
        ([LUX_1, LUX_2], "above", 100, "all", {LUX_1: "500", LUX_2: "50"}, True),
        ([LUX_1, LUX_2], "above", 100, "any", {LUX_1: "500", LUX_2: "50"}, False),
        ([LUX_1, LUX_2], "above", 100, "all", {LUX_1: "500", LUX_2: "300"}, False),
    ],
)
async def test_the_end_brightness_stops_the_simulation(
    hass: HomeAssistant,
    name: str,
    sensors: list[str],
    direction: str,
    value: int,
    mode: str,
    values: dict[str, str],
    expected: bool,
) -> None:
    _states(hass)
    for entity_id, state in values.items():
        hass.states.async_set(entity_id, state, {"device_class": "illuminance"})
    result = await _render(
        hass,
        name,
        _continue_condition(name),
        {
            "persons": [ANNA, BEN],
            "lux_end_sensors": sensors,
            "lux_end_direction": direction,
            "lux_end_value": value,
            "lux_match": mode,
        },
    )
    assert result is expected


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize(
    ("sensors", "direction", "value", "mode", "values", "expected"),
    [
        ([], "below", 50, "all", {}, True),  # no sensor: only the period decides
        ([LUX_1], "below", 50, "all", {LUX_1: "10"}, True),
        ([LUX_1], "below", 50, "all", {LUX_1: "200"}, False),
        ([LUX_1], "below", 50, "all", {LUX_1: "50"}, False),  # strictly below
        ([LUX_1], "below", 50, "all", {LUX_1: "49.9"}, True),
        ([LUX_1], "above", 50, "all", {LUX_1: "200"}, True),
        ([LUX_1], "above", 50, "all", {LUX_1: "10"}, False),
        # a sensor without a value is ignored; none left does not hold back
        ([LUX_1, LUX_2], "below", 50, "all", {LUX_1: "unavailable", LUX_2: "10"}, True),
        (
            [LUX_1, LUX_2],
            "below",
            50,
            "all",
            {LUX_1: "unavailable", LUX_2: "200"},
            False,
        ),
        ([LUX_1], "below", 50, "all", {LUX_1: "unknown"}, True),
        # several sensors from different rooms
        ([LUX_1, LUX_2], "below", 50, "all", {LUX_1: "10", LUX_2: "200"}, False),
        ([LUX_1, LUX_2], "below", 50, "all", {LUX_1: "10", LUX_2: "20"}, True),
        ([LUX_1, LUX_2], "below", 50, "any", {LUX_1: "10", LUX_2: "200"}, True),
        ([LUX_1, LUX_2], "below", 50, "any", {LUX_1: "300", LUX_2: "200"}, False),
    ],
)
async def test_the_start_waits_until_the_brightness_fits(
    hass: HomeAssistant,
    name: str,
    sensors: list[str],
    direction: str,
    value: int,
    mode: str,
    values: dict[str, str],
    expected: bool,
) -> None:
    _states(hass)
    for entity_id, state in values.items():
        hass.states.async_set(entity_id, state, {"device_class": "illuminance"})
    # Everybody is away, so the wait ends exactly when the brightness fits.
    result = await _render(
        hass,
        name,
        _start_wait(name),
        {
            "persons": [ANNA, BEN],
            "lux_start_sensors": sensors,
            "lux_start_direction": direction,
            "lux_start_value": value,
            "lux_match": mode,
        },
    )
    assert result is expected


@pytest.mark.parametrize("name", FILES)
async def test_the_start_wait_ends_when_the_simulation_must_stop(
    hass: HomeAssistant, name: str
) -> None:
    """Somebody comes home while it is bright: the wait must not hold on."""
    _states(hass, person__anna="home")
    hass.states.async_set(LUX_1, "500", {"device_class": "illuminance"})
    result = await _render(
        hass,
        name,
        _start_wait(name),
        {"persons": [ANNA], "lux_start_sensors": [LUX_1]},
    )
    assert result is True


def _plan(name: str) -> str:
    data = load_yaml_dict(str(BLUEPRINT_DIR / name))
    (text,) = {
        value for key, value in _walk(data) if key == "plan" and isinstance(value, str)
    }
    return text


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize("set_brightness", [False, True])
async def test_only_dimmable_lights_get_a_brightness(
    hass: HomeAssistant, name: str, set_brightness: bool
) -> None:
    template.async_setup(hass)
    hass.states.async_set(
        "light.dimmer", "off", {"supported_color_modes": ["brightness"]}
    )
    hass.states.async_set("light.simple", "off", {"supported_color_modes": ["onoff"]})
    hass.states.async_set("switch.actuator", "off")
    targets = ["light.dimmer", "light.simple", "switch.actuator"]
    seen: dict[str, set[int]] = {}
    for _ in range(60):
        rows = template.Template(_plan(name), hass).async_render(
            {
                "targets": targets,
                "light_sensor": {},
                "excluded": [],
                "max_lights": 6,
                "set_brightness": set_brightness,
                "end_ts": 10**12,
            }
        )
        for row in rows:
            seen.setdefault(row["entity"], set()).add(row["dim"])
    assert seen, "the plan never contained a light"
    for entity, dims in seen.items():
        expected = 1 if (set_brightness and entity == "light.dimmer") else 0
        assert dims == {expected}, entity


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize(
    ("persons", "switches", "lights", "trigger_id", "expected"),
    [
        ([], [], [LIGHT], "ha_start", True),
        ([], [], [LIGHT], "window_start", True),
        ([ANNA], [], [], "ha_start", True),
        # only the two rare triggers report it, not the check every 5 minutes
        ([], [], [LIGHT], "periodic_check", False),
        ([], [], [LIGHT], "presence", False),
        # people or a switch is enough
        ([ANNA], [], [LIGHT], "ha_start", False),
        ([], [SWITCH], [LIGHT], "ha_start", False),
    ],
)
async def test_a_setup_error_is_reported_when_nothing_is_chosen(
    hass: HomeAssistant,
    name: str,
    persons: list[str],
    switches: list[str],
    lights: list[str],
    trigger_id: str,
    expected: bool,
) -> None:
    data = load_yaml_dict(str(BLUEPRINT_DIR / name))
    step = next(a for a in data["actions"] if "if" in a)
    assert step["then"][0]["error"] is True
    assert step["then"][0]["stop"]
    result = await _render(
        hass,
        name,
        step["if"][0]["value_template"],
        {
            "persons": persons,
            "absence_switch": switches,
            "targets": lights,
            "trigger": {"id": trigger_id},
        },
    )
    assert result is expected


async def _use(
    hass: HomeAssistant, name: str, inputs: dict[str, Any]
) -> list[ServiceCall]:
    calls: list[ServiceCall] = []

    def record(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("homeassistant", "turn_on", record)
    hass.services.async_register("light", "turn_on", record)
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
    return calls


def _running(hass: HomeAssistant) -> int:
    (state,) = hass.states.async_all("automation")
    return int(state.attributes["current"])


def _inputs(**override: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "persons": [ANNA, BEN],
        "lights": [LIGHT],
        # Same start and end: the period is the whole day.
        "window_start": "00:00:00",
        "window_end": "00:00:00",
    }
    inputs.update(override)
    return inputs


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize("bright", [False, True])
async def test_a_run_waits_while_everybody_is_away_and_ends_when_somebody_comes_home(
    hass: HomeAssistant, name: str, bright: bool
) -> None:
    _states(hass, person__anna="home", person__ben="home")
    hass.states.async_set(LIGHT, "off", {"supported_color_modes": ["onoff"]})
    hass.states.async_set(
        LUX_1, "500" if bright else "5", {"device_class": "illuminance"}
    )
    calls = await _use(hass, name, _inputs(lux_start_sensors=[LUX_1]))

    # Both are home: the start condition stops the run at once.
    hass.states.async_set(ANNA, "not_home")
    await asyncio.sleep(0.1)
    assert _running(hass) == 0

    # Both are away: the run starts and waits (random pause, or the start
    # brightness condition).
    hass.states.async_set(BEN, "not_home")
    await asyncio.sleep(0.1)
    assert _running(hass) == 1
    assert not calls

    # Somebody comes home: the run ends at once, nothing was switched on.
    hass.states.async_set(ANNA, "home")
    await asyncio.sleep(0.1)
    assert _running(hass) == 0
    assert not calls
    await hass.async_block_till_done()


@pytest.mark.parametrize("name", FILES)
async def test_an_absence_switch_alone_starts_and_stops_a_run(
    hass: HomeAssistant, name: str
) -> None:
    _states(hass, person__anna="home", person__ben="home")
    hass.states.async_set(LIGHT, "off", {"supported_color_modes": ["onoff"]})
    calls = await _use(hass, name, _inputs(persons=[], absence_switch=[SWITCH]))

    hass.states.async_set(SWITCH, "on")
    await asyncio.sleep(0.1)
    assert _running(hass) == 1

    hass.states.async_set(SWITCH, "off")
    await asyncio.sleep(0.1)
    assert _running(hass) == 0
    assert not calls
    await hass.async_block_till_done()


@pytest.mark.parametrize("name", FILES)
async def test_without_people_and_without_a_switch_nothing_runs(
    hass: HomeAssistant, name: str
) -> None:
    _states(hass)
    hass.states.async_set(LIGHT, "off", {"supported_color_modes": ["onoff"]})
    calls = await _use(hass, name, _inputs(persons=[]))

    hass.states.async_set(SWITCH, "on")
    hass.states.async_set(ANNA, "home")
    hass.states.async_set(ANNA, "not_home")
    await asyncio.sleep(0.1)
    assert _running(hass) == 0
    assert not calls
    await hass.async_block_till_done()


@pytest.mark.parametrize("name", FILES)
async def test_the_end_brightness_prevents_a_start_and_ends_a_run(
    hass: HomeAssistant, name: str
) -> None:
    _states(hass)
    hass.states.async_set(LIGHT, "off", {"supported_color_modes": ["onoff"]})
    hass.states.async_set(LUX_1, "500", {"device_class": "illuminance"})
    calls = await _use(
        hass,
        name,
        _inputs(
            lux_end_sensors=[LUX_1],
            lux_end_direction="above",
            lux_end_value=100,
        ),
    )

    # Everybody is away, but it is already bright: the run ends at once.
    hass.states.async_set(ANNA, "home")
    hass.states.async_set(ANNA, "not_home")
    await asyncio.sleep(0.1)
    assert _running(hass) == 0

    # It gets dark: the next trigger starts the run.
    hass.states.async_set(LUX_1, "5", {"device_class": "illuminance"})
    hass.states.async_set(ANNA, "home")
    hass.states.async_set(ANNA, "not_home")
    await asyncio.sleep(0.1)
    assert _running(hass) == 1

    # It gets bright again: the running simulation ends by itself.
    hass.states.async_set(LUX_1, "500", {"device_class": "illuminance"})
    await asyncio.sleep(0.1)
    assert _running(hass) == 0
    assert not calls
    await hass.async_block_till_done()


@pytest.mark.parametrize("name", FILES)
async def test_the_absence_switch_is_ignored_when_people_are_chosen(
    hass: HomeAssistant, name: str
) -> None:
    _states(hass)
    hass.states.async_set(LIGHT, "off", {"supported_color_modes": ["onoff"]})
    calls = await _use(hass, name, _inputs(absence_switch=[SWITCH]))

    # The switch is Off, but both people are away: the run starts anyway.
    hass.states.async_set(ANNA, "home")
    hass.states.async_set(ANNA, "not_home")
    await asyncio.sleep(0.1)
    assert _running(hass) == 1

    # The switch changes nothing: the run goes on (a second one may queue).
    hass.states.async_set(SWITCH, "on")
    hass.states.async_set(SWITCH, "off")
    await asyncio.sleep(0.1)
    assert _running(hass) >= 1
    assert not calls

    hass.states.async_set(BEN, "home")
    await asyncio.sleep(0.1)
    assert _running(hass) == 0
    await hass.async_block_till_done()


def _variable(name: str, key: str) -> str:
    data = load_yaml_dict(str(BLUEPRINT_DIR / name))
    value = data["variables"][key]
    assert isinstance(value, str)
    return value


def _pairs(*rows: tuple[str, str]) -> list[list[str]]:
    padded = [list(row) for row in rows] + [["", ""]] * (6 - len(rows))
    return padded


@pytest.mark.parametrize("name", FILES)
async def test_pairs_add_lights_and_map_sensors(hass: HomeAssistant, name: str) -> None:
    template.async_setup(hass)
    pairs = _pairs((LIGHT, LUX_1), ("light.kitchen", ""), ("", LUX_2))
    targets = template.Template(_variable(name, "targets"), hass).async_render(
        {"lights_in": ["light.kitchen", "light.bath"], "pairs_raw": pairs}
    )
    # pair lights are added, duplicates are removed, a pair without light is ignored
    assert sorted(targets) == ["light.bath", "light.hall", "light.kitchen"]
    mapping = template.Template(_variable(name, "light_sensor"), hass).async_render(
        {"pairs_raw": pairs}
    )
    assert mapping == {LIGHT: LUX_1}
    empty = template.Template(_variable(name, "light_sensor"), hass).async_render(
        {"pairs_raw": _pairs()}
    )
    assert empty == {}


@pytest.mark.parametrize("name", FILES)
async def test_the_plan_carries_the_sensor_of_each_light(
    hass: HomeAssistant, name: str
) -> None:
    template.async_setup(hass)
    hass.states.async_set(LIGHT, "off", {"supported_color_modes": ["onoff"]})
    seen: set[str] = set()
    for _ in range(20):
        rows = template.Template(_plan(name), hass).async_render(
            {
                "targets": [LIGHT],
                "light_sensor": {LIGHT: LUX_1},
                "excluded": [],
                "max_lights": 6,
                "set_brightness": False,
                "end_ts": 10**12,
            }
        )
        seen |= {row["sensor"] for row in rows}
    assert seen == {LUX_1}


def _switch_on_condition(name: str) -> str:
    (text,) = {
        text
        for key, text in _templates(name, "repeat.item.t_on - 3")
        if key == "value_template"
    }
    return text


def _keep_on_wait(name: str) -> str:
    (text,) = {
        text for key, text in _templates(name, "set own_end") if key == "wait_template"
    }
    return text


async def _item_render(
    hass: HomeAssistant, name: str, text: str, sensor: str, value: str, **extra: Any
) -> Any:
    _states(hass)
    hass.states.async_set(LIGHT, extra.pop("light", "off"))
    if sensor:
        hass.states.async_set(sensor, value, {"device_class": "illuminance"})
    item = {"entity": LIGHT, "sensor": sensor, "t_on": 0, "t_off": 10**12, "dim": 0}
    return await _render(
        hass,
        name,
        text,
        {"persons": [ANNA, BEN], "repeat": {"item": item}, **extra},
    )


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize(
    ("sensor", "value", "direction", "expected"),
    [
        ("", "", "below", True),  # no own sensor: the general rules apply
        (LUX_1, "10", "below", True),
        (LUX_1, "200", "below", False),  # too bright for this light
        (LUX_1, "unavailable", "below", True),  # no value: the light is not held back
        (LUX_1, "200", "above", True),
        (LUX_1, "10", "above", False),
    ],
)
async def test_a_light_with_its_own_sensor_waits_for_it(
    hass: HomeAssistant,
    name: str,
    sensor: str,
    value: str,
    direction: str,
    expected: bool,
) -> None:
    result = await _item_render(
        hass,
        name,
        _switch_on_condition(name),
        sensor,
        value,
        lux_start_direction=direction,
        lux_start_value=50,
    )
    assert result is expected


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize(
    ("sensor", "value", "expected"),
    [
        ("", "", False),  # still on, nothing ends the turn
        (LUX_1, "10", False),
        (LUX_1, "500", True),  # bright for this light: its turn ends
        (LUX_1, "unavailable", False),
    ],
)
async def test_the_own_sensor_ends_the_turn_of_its_light(
    hass: HomeAssistant, name: str, sensor: str, value: str, expected: bool
) -> None:
    result = await _item_render(
        hass,
        name,
        _keep_on_wait(name),
        sensor,
        value,
        light="on",
        lux_end_direction="above",
        lux_end_value=100,
    )
    assert result is expected


@pytest.mark.parametrize("name", FILES)
def test_the_pair_inputs_are_optional_and_limited_to_the_right_entities(
    name: str,
) -> None:
    data = load_yaml_dict(str(BLUEPRINT_DIR / name))
    inputs = data["blueprint"]["input"]["lux_pair_section"]["input"]
    assert len(inputs) == 12
    for n in range(1, 7):
        light = inputs[f"lux_pair_{n}_light"]
        sensor = inputs[f"lux_pair_{n}_sensor"]
        assert light["default"] == "" and sensor["default"] == ""
        assert light["selector"]["entity"]["filter"] == [
            {"domain": ["light", "switch"]}
        ]
        assert sensor["selector"]["entity"]["filter"] == [
            {"domain": "sensor", "device_class": "illuminance"}
        ]
