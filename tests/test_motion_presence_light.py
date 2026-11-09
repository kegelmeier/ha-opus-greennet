"""Behaviour of the blueprint ``motion_presence_light`` in a running automation.

The blueprint is independent of the integration (it works with any maker's
sensors and lights), so these tests use plain states and recorded services.
Delays are given in milliseconds to keep the tests short. The automation keeps
one run alive per visit; tests therefore wait with short sleeps and never call
``async_block_till_done`` while a run is waiting.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import condition, template, translation, trigger
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT_DIR = ROOT / "blueprints/automation/opus_greennet"
EN = "motion_presence_light.yaml"
DE = "motion_presence_light.de.yaml"

MOTION = "binary_sensor.hall_motion"
MOTION_2 = "binary_sensor.hall_presence"
LUX = "sensor.hall_lux"
DIM = "light.hall_dimmer"
PLAIN = "light.hall_plain"
SWITCH = "switch.hall_lamp"
NIGHT_LIGHT = "light.hall_night"
NIGHT_SWITCH = "switch.hall_night_lamp"
LOCK = "input_boolean.hall_lock"
HELPER = "input_boolean.night_mode"
DELAY = {"milliseconds": 300}
NIGHT_DELAY = {"milliseconds": 150}


def _inputs(**override: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "motion_sensors": [MOTION],
        "lights": [PLAIN],
        "off_delay": DELAY,
    }
    inputs.update(override)
    return inputs


def _night(**override: Any) -> dict[str, Any]:
    inputs = _inputs(
        night_mode="same",
        night_start="22:00:00",
        night_end="06:00:00",
        night_off_delay=NIGHT_DELAY,
    )
    inputs.update(override)
    return inputs


async def _use(
    hass: HomeAssistant,
    name: str,
    inputs: dict[str, Any],
    *,
    restart: bool = False,
) -> None:
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
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await asyncio.sleep(0.05)


class Calls:
    """Fake light and switch services that also move the states."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.on: list[ServiceCall] = []
        self.off: list[ServiceCall] = []
        for domain in ("light", "switch"):
            hass.services.async_register(domain, "turn_on", self._turn_on(hass))
            hass.services.async_register(domain, "turn_off", self._turn_off(hass))

    def _turn_on(self, hass: HomeAssistant) -> Any:
        def handler(call: ServiceCall) -> None:
            self.on.append(call)
            for entity_id in call.data["entity_id"]:
                attributes = dict(hass.states.get(entity_id).attributes)
                hass.states.async_set(entity_id, "on", attributes)

        return handler

    def _turn_off(self, hass: HomeAssistant) -> Any:
        def handler(call: ServiceCall) -> None:
            self.off.append(call)
            for entity_id in call.data["entity_id"]:
                attributes = dict(hass.states.get(entity_id).attributes)
                hass.states.async_set(entity_id, "off", attributes)

        return handler

    @property
    def switched_on(self) -> list[str]:
        return [e for call in self.on for e in call.data["entity_id"]]

    @property
    def switched_off(self) -> list[str]:
        return [e for call in self.off for e in call.data["entity_id"]]


def _world(hass: HomeAssistant) -> Calls:
    """Create the entities every test uses and the fake services."""
    for entity in (MOTION, MOTION_2):
        hass.states.async_set(entity, "off", {"device_class": "motion"})
    hass.states.async_set(PLAIN, "off", {"supported_color_modes": ["onoff"]})
    hass.states.async_set(DIM, "off", {"supported_color_modes": ["brightness"]})
    hass.states.async_set(NIGHT_LIGHT, "off", {"supported_color_modes": ["onoff"]})
    hass.states.async_set(SWITCH, "off")
    hass.states.async_set(NIGHT_SWITCH, "off")
    hass.states.async_set(LUX, "300", {"device_class": "illuminance"})
    hass.states.async_set(LOCK, "off")
    hass.states.async_set(HELPER, "off")
    return Calls(hass)


async def _settle(seconds: float = 0.1) -> None:
    await asyncio.sleep(seconds)


async def _set(hass: HomeAssistant, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await _settle()


async def _past_the_delay(extra: float = 0.0) -> None:
    await asyncio.sleep(0.5 + extra)


def _at(hour: int, minute: int = 0) -> Any:
    return patch.object(
        dt_util,
        "now",
        return_value=datetime(2026, 11, 9, hour, minute, tzinfo=dt_util.UTC),
    )


# ── basics ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", [EN, DE])
async def test_light_follows_the_sensor_and_switches_off_after_the_delay(
    hass: HomeAssistant, name: str
) -> None:
    calls = _world(hass)
    await _use(hass, name, _inputs())

    await _set(hass, MOTION, "on")
    assert calls.switched_on == [PLAIN]
    assert "brightness_pct" not in calls.on[0].data

    await _set(hass, MOTION, "off")
    assert not calls.off  # extra time not over yet
    await _past_the_delay()
    assert calls.switched_off == [PLAIN]


async def test_a_light_that_was_already_on_is_left_alone(hass: HomeAssistant) -> None:
    calls = _world(hass)
    hass.states.async_set(PLAIN, "on", {"supported_color_modes": ["onoff"]})
    hass.states.async_set(SWITCH, "off")
    await _use(hass, EN, _inputs(lights=[PLAIN], extra_switches=[SWITCH]))

    await _set(hass, MOTION, "on")
    assert calls.switched_on == [SWITCH]  # only what was off
    await _set(hass, MOTION, "off")
    await _past_the_delay()
    assert calls.switched_off == [SWITCH]  # never the light that was on before


async def test_several_sensors_are_one_room(hass: HomeAssistant) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs(motion_sensors=[MOTION, MOTION_2]))

    await _set(hass, MOTION, "on")
    await _set(hass, MOTION_2, "on")
    assert len(calls.on) == 1  # the second sensor does not switch again
    await _set(hass, MOTION, "off")
    await _past_the_delay()
    assert not calls.off  # the other sensor is still active
    await _set(hass, MOTION_2, "off")
    await _past_the_delay()
    assert calls.switched_off == [PLAIN]


async def test_new_activity_during_the_extra_time_cancels_the_switch_off(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs())

    await _set(hass, MOTION, "on")
    await _set(hass, MOTION, "off")
    await asyncio.sleep(0.2)
    await _set(hass, MOTION, "on")  # inside the 0.3 s
    await asyncio.sleep(0.3)
    assert not calls.off
    await _set(hass, MOTION, "off")
    await asyncio.sleep(0.2)
    assert not calls.off  # the extra time restarted
    await asyncio.sleep(0.3)
    assert calls.switched_off == [PLAIN]
    assert len(calls.on) == 1  # no second switch on within the same visit


async def test_a_short_blip_during_the_extra_time_restarts_the_whole_extra_time(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs())
    await _set(hass, MOTION, "on")
    await _set(hass, MOTION, "off")  # the extra time of 0.3 s starts
    await asyncio.sleep(0.1)
    hass.states.async_set(MOTION, "on")  # active and quiet again at once
    hass.states.async_set(MOTION, "off")
    await asyncio.sleep(0.15)  # the first extra time would be over by now
    assert not calls.off
    await asyncio.sleep(0.3)
    assert calls.switched_off == [PLAIN]


async def test_a_zero_extra_time_switches_off_as_soon_as_all_are_quiet(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs(off_delay={"seconds": 0}))
    await _set(hass, MOTION, "on")
    await _set(hass, MOTION, "off")
    await asyncio.sleep(0.2)  # no extra time, only the shortest wait
    assert calls.switched_off == [PLAIN]


async def test_unavailable_sensor_counts_as_quiet_and_comes_back_as_activity(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs(off_delay={"milliseconds": 200}))
    await _set(hass, MOTION, "on")
    await _set(hass, MOTION, "unavailable")
    await _past_the_delay()
    assert calls.switched_off == [PLAIN]
    await _set(hass, MOTION, "on")  # unavailable -> on is a new visit
    assert calls.switched_on == [PLAIN, PLAIN]


async def test_light_that_was_switched_off_by_hand_is_not_switched_on_again(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs(motion_sensors=[MOTION, MOTION_2]))
    await _set(hass, MOTION, "on")
    assert calls.switched_on == [PLAIN]

    hass.states.async_set(PLAIN, "off", {"supported_color_modes": ["onoff"]})
    await _settle()
    await _set(hass, MOTION_2, "on")  # more impulses
    await _set(hass, MOTION_2, "off")
    await _set(hass, MOTION_2, "on")
    assert calls.switched_on == [PLAIN]
    assert not calls.off

    await _set(hass, MOTION, "off")
    await _set(hass, MOTION_2, "off")
    await _past_the_delay()
    assert not calls.off  # nothing to switch off, the light is off already

    await _set(hass, MOTION, "on")  # the next visit starts normally
    assert calls.switched_on == [PLAIN, PLAIN]


async def test_nothing_chosen_writes_an_error_and_switches_nothing(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs(lights=[]))
    await _set(hass, MOTION, "on")
    assert not calls.on
    assert "Setup incomplete" in caplog.text


async def test_start_of_home_assistant_with_an_active_sensor_starts_the_light(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    hass.states.async_set(MOTION, "on", {"device_class": "motion"})
    await _use(hass, EN, _inputs(), restart=True)
    assert calls.switched_on == [PLAIN]


# ── brightness ─────────────────────────────────────────────────────────────


async def test_brightness_goes_only_to_dimmable_lights(hass: HomeAssistant) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _inputs(
            lights=[DIM, PLAIN],
            extra_switches=[SWITCH],
            set_brightness=True,
            brightness=35,
        ),
    )
    await _set(hass, MOTION, "on")
    by_target = {
        tuple(call.data["entity_id"]): call.data.get("brightness_pct")
        for call in calls.on
    }
    assert by_target == {(DIM,): 35, (PLAIN,): None, (SWITCH,): None}


async def test_without_the_brightness_option_no_brightness_is_sent(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs(lights=[DIM], set_brightness=False, brightness=35))
    await _set(hass, MOTION, "on")
    assert calls.switched_on == [DIM]
    assert "brightness_pct" not in calls.on[0].data


# ── only when it is dark ───────────────────────────────────────────────────


def _dark(**override: Any) -> dict[str, Any]:
    return _inputs(only_when_dark=True, lux_sensor=LUX, lux_threshold=50, **override)


async def test_bright_room_keeps_the_light_off_until_it_gets_dark(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _dark())
    await _set(hass, MOTION, "on")  # 300 lx, bright
    assert not calls.on
    await _set(hass, LUX, "49")  # no new movement is needed
    assert calls.switched_on == [PLAIN]


async def test_the_limit_itself_is_not_dark(hass: HomeAssistant) -> None:
    calls = _world(hass)
    await _use(hass, EN, _dark())
    await _set(hass, LUX, "50")
    await _set(hass, MOTION, "on")
    assert not calls.on
    await _set(hass, LUX, "49.9")
    assert calls.switched_on == [PLAIN]


async def test_already_dark_room_switches_on_with_the_first_movement(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    hass.states.async_set(LUX, "3")
    await _use(hass, EN, _dark())
    await _set(hass, MOTION, "on")
    assert calls.switched_on == [PLAIN]


@pytest.mark.parametrize("reading", ["unavailable", "unknown", "", "dark"])
async def test_an_invalid_brightness_reading_counts_as_dark(
    hass: HomeAssistant, reading: str
) -> None:
    calls = _world(hass)
    hass.states.async_set(LUX, reading)
    await _use(hass, EN, _dark())
    await _set(hass, MOTION, "on")
    assert calls.switched_on == [PLAIN]


async def test_a_brighter_room_does_not_switch_a_running_light_off(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    hass.states.async_set(LUX, "3")
    await _use(hass, EN, _dark())
    await _set(hass, MOTION, "on")
    await _set(hass, LUX, "900")  # our own light, or the sun
    await _past_the_delay()
    assert not calls.off
    await _set(hass, MOTION, "off")
    await _past_the_delay()
    assert calls.switched_off == [PLAIN]


async def test_nobody_there_when_it_gets_dark_switches_nothing(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _dark())
    await _set(hass, MOTION, "on")
    await _set(hass, MOTION, "off")  # the run ends, nothing was switched
    await _set(hass, LUX, "10")
    assert not calls.on
    await _set(hass, MOTION, "on")  # dark now: normal start
    assert calls.switched_on == [PLAIN]


async def test_a_light_switched_off_by_hand_stays_off_when_it_gets_darker(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    hass.states.async_set(LUX, "3")
    await _use(hass, EN, _dark())
    await _set(hass, MOTION, "on")
    hass.states.async_set(PLAIN, "off", {"supported_color_modes": ["onoff"]})
    await _set(hass, LUX, "60")
    await _set(hass, LUX, "2")
    assert calls.switched_on == [PLAIN]


async def test_dark_option_without_a_sensor_is_a_setup_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs(only_when_dark=True))
    await _set(hass, MOTION, "on")
    assert not calls.on
    assert "Setup incomplete" in caplog.text


# ── lock ───────────────────────────────────────────────────────────────────


async def test_lock_prevents_a_start_and_unlocking_starts_while_someone_is_there(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    hass.states.async_set(LOCK, "on")
    await _use(hass, EN, _inputs(lock_entity=LOCK))
    await _set(hass, MOTION, "on")
    assert not calls.on
    await _set(hass, LOCK, "off")
    assert calls.switched_on == [PLAIN]


async def test_unlocking_checks_the_brightness_too(hass: HomeAssistant) -> None:
    calls = _world(hass)
    hass.states.async_set(LOCK, "on")
    await _use(hass, EN, _dark(lock_entity=LOCK))
    await _set(hass, MOTION, "on")
    await _set(hass, LOCK, "off")
    assert not calls.on  # unlocked but bright
    await _set(hass, LUX, "10")
    assert calls.switched_on == [PLAIN]


async def test_lock_set_during_a_visit_does_not_stop_the_normal_switch_off(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _inputs(lock_entity=LOCK))
    await _set(hass, MOTION, "on")
    await _set(hass, LOCK, "on")
    await _set(hass, MOTION, "off")
    await _past_the_delay()
    assert calls.switched_off == [PLAIN]


@pytest.mark.parametrize("state", ["unavailable", "unknown", "off"])
async def test_a_lock_that_is_not_on_does_not_lock(
    hass: HomeAssistant, state: str
) -> None:
    calls = _world(hass)
    hass.states.async_set(LOCK, state)
    await _use(hass, EN, _inputs(lock_entity=LOCK))
    await _set(hass, MOTION, "on")
    assert calls.switched_on == [PLAIN]


# ── night light ────────────────────────────────────────────────────────────


async def test_same_lights_at_night_use_the_night_brightness_and_delay(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _night(
            lights=[DIM],
            set_brightness=True,
            brightness=90,
            night_set_brightness=True,
            night_brightness=15,
        ),
    )
    with _at(23, 30):
        await _set(hass, MOTION, "on")
    assert calls.on[0].data["brightness_pct"] == 15
    await _set(hass, MOTION, "off")
    await asyncio.sleep(0.25)  # the night time is 0.15 s, not 0.3 s
    assert calls.switched_off == [DIM]


async def test_by_day_the_normal_settings_are_used(hass: HomeAssistant) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _night(
            lights=[DIM],
            set_brightness=True,
            brightness=90,
            night_set_brightness=True,
            night_brightness=15,
        ),
    )
    with _at(12, 0):
        await _set(hass, MOTION, "on")
    assert calls.on[0].data["brightness_pct"] == 90
    await _set(hass, MOTION, "off")
    await asyncio.sleep(0.2)
    assert not calls.off  # 0.3 s by day
    await asyncio.sleep(0.25)
    assert calls.switched_off == [DIM]


async def test_night_without_a_night_brightness_only_switches_on(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _night(lights=[DIM], set_brightness=True, brightness=90),
    )
    with _at(23, 30):
        await _set(hass, MOTION, "on")
    assert "brightness_pct" not in calls.on[0].data


@pytest.mark.parametrize(
    ("hour", "minute", "night"),
    [(21, 59, False), (22, 0, True), (23, 59, True), (0, 0, True), (5, 59, True)]
    + [(6, 0, False), (12, 0, False)],
)
async def test_the_night_may_reach_past_midnight(
    hass: HomeAssistant, hour: int, minute: int, night: bool
) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _night(
            night_mode="other",
            night_lights=[NIGHT_LIGHT],
        ),
    )
    with _at(hour, minute):
        await _set(hass, MOTION, "on")
    assert calls.switched_on == ([NIGHT_LIGHT] if night else [PLAIN])


async def test_equal_start_and_end_mean_no_night(hass: HomeAssistant) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _night(
            night_mode="other",
            night_lights=[NIGHT_LIGHT],
            night_start="00:00:00",
            night_end="00:00:00",
        ),
    )
    with _at(3, 0):
        await _set(hass, MOTION, "on")
    assert calls.switched_on == [PLAIN]


async def test_other_lights_at_night_include_switches_and_leave_the_normal_ones(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _night(
            night_mode="other",
            lights=[PLAIN],
            extra_switches=[SWITCH],
            night_lights=[NIGHT_LIGHT],
            night_switches=[NIGHT_SWITCH],
        ),
    )
    with _at(23, 0):
        await _set(hass, MOTION, "on")
    assert sorted(calls.switched_on) == sorted([NIGHT_LIGHT, NIGHT_SWITCH])
    await _set(hass, MOTION, "off")
    await asyncio.sleep(0.4)
    assert sorted(calls.switched_off) == sorted([NIGHT_LIGHT, NIGHT_SWITCH])


async def test_other_lights_at_night_without_night_lights_use_the_normal_ones(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(hass, EN, _night(night_mode="other"))
    with _at(23, 0):
        await _set(hass, MOTION, "on")
    assert calls.switched_on == [PLAIN]


async def test_no_night_light_ignores_the_times(hass: HomeAssistant) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _night(night_mode="none", night_lights=[NIGHT_LIGHT]),
    )
    with _at(23, 0):
        await _set(hass, MOTION, "on")
    assert calls.switched_on == [PLAIN]


async def test_the_night_profile_is_kept_until_the_room_is_empty(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _night(night_mode="other", night_lights=[NIGHT_LIGHT]),
    )
    with _at(5, 59):
        await _set(hass, MOTION, "on")
    # It is day now, but the run keeps its night lights.
    await _set(hass, MOTION, "off")
    with _at(6, 5):
        await asyncio.sleep(0.4)
    assert calls.switched_on == [NIGHT_LIGHT]
    assert calls.switched_off == [NIGHT_LIGHT]


@pytest.mark.parametrize(
    ("helper_state", "night"),
    [("on", True), ("off", False), ("unavailable", False), ("unknown", False)],
)
async def test_own_night_mode_switch_replaces_the_times(
    hass: HomeAssistant, helper_state: str, night: bool
) -> None:
    calls = _world(hass)
    hass.states.async_set(HELPER, helper_state)
    await _use(
        hass,
        EN,
        _night(
            night_mode="other",
            night_lights=[NIGHT_LIGHT],
            night_source="helper",
            night_helper=HELPER,
        ),
    )
    with _at(12, 0):  # by the times it would be day
        await _set(hass, MOTION, "on")
    assert calls.switched_on == ([NIGHT_LIGHT] if night else [PLAIN])


async def test_own_night_mode_switch_without_an_entity_means_day(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    await _use(
        hass,
        EN,
        _night(
            night_mode="other",
            night_lights=[NIGHT_LIGHT],
            night_source="helper",
            night_helper="",
        ),
    )
    with _at(23, 0):  # the times are ignored for this choice
        await _set(hass, MOTION, "on")
    assert calls.switched_on == [PLAIN]


# ── targets that fail or cannot be reached ─────────────────────────────────


async def test_a_failing_or_unreachable_target_does_not_stop_the_others(
    hass: HomeAssistant,
) -> None:
    calls = _world(hass)
    hass.states.async_set(DIM, "unavailable")  # cannot be reached: not touched
    hass.states.async_set(PLAIN, "unknown")  # feedback missing: switched on
    seen: list[ServiceCall] = []

    def failing(call: ServiceCall) -> None:
        seen.append(call)
        raise HomeAssistantError("radio gateway did not answer")

    hass.services.async_register("light", "turn_on", failing)
    await _use(hass, EN, _inputs(lights=[DIM, PLAIN], extra_switches=[SWITCH]))

    await _set(hass, MOTION, "on")
    assert [call.data["entity_id"] for call in seen] == [[PLAIN]]
    assert calls.switched_on == [SWITCH]  # the failure did not stop the switch

    await _set(hass, MOTION, "off")
    await _past_the_delay()
    # Still unknown, so the switch-off is sent; the unreachable light is not.
    assert sorted(calls.switched_off) == sorted([PLAIN, SWITCH])
