"""REST phase 02: domain model.

- E6 / finding B-4: a device list updates the existing ``EnOceanDevice`` in
  place; the field list is complete (every field is either list metadata or
  runtime state of this integration).
- The last vertical run duration and the origin of each cover position are
  diagnostic values only.
- Regression tests for the field baseline: rocker, ``unknown`` and end
  position on a cover (R7); 6x press/release and a long press (R8).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import Event, HomeAssistant, callback

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.coordinator import (
    CoverMovement,
    OpusGreenNetCoordinator,
)
from custom_components.opus_greennet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

MODULE = "custom_components.opus_greennet.coordinator"
COVER = "0507C89C"

# Fields that belong to this integration, not to a gateway device list. A new
# EnOceanDevice field must be added here or to LISTED_METADATA_FIELDS.
RUNTIME_FIELDS = {
    "device_id",
    "last_update_source",
    "last_update_received_monotonic",
    "last_update_dispatched_monotonic",
    "last_command_error",
    "channels",
}


@pytest.fixture
def coord() -> Iterator[OpusGreenNetCoordinator]:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coordinator._gateway_available = True  # noqa: SLF001
    with patch(f"{MODULE}.async_dispatcher_send"):
        yield coordinator


def _cover(**fields_: object) -> dict:
    return device_object(
        COVER,
        ["D2-05-02", "F6-03-01"],
        states=[{"key": "position", "value": 100}],
        **fields_,
    )


def _list(*devices: dict) -> dict:
    return {"header": {"content": "devices"}, "devices": list(devices)}


# ── E6 / B-4: in-place update ──────────────────────────────────────────────


def test_every_device_field_is_classified() -> None:
    """A new field must state whether a device list replaces it."""
    names = {field_.name for field_ in fields(EnOceanDevice)}
    listed = set(EnOceanDevice.LISTED_METADATA_FIELDS)
    assert not listed & RUNTIME_FIELDS
    assert names == listed | RUNTIME_FIELDS


def test_list_updates_the_same_object_and_keeps_runtime_state(coord) -> None:
    coord._apply_device_list(  # noqa: SLF001
        _list(_cover(dbm=-70, lastSeen="2026-10-20T10:00:00")), authoritative=True
    )
    device = coord.devices[COVER]
    channel = device.channels[0]
    device.last_command_error = "status 500"
    device.last_update_received_monotonic = 12.5

    coord._apply_device_list(  # noqa: SLF001
        _list(
            _cover(
                friendlyId="Kitchen blind",
                manufacturer="Other",
                physicalDevice="JD-1",
                firstSeen="2026-01-01T00:00:00",
                lastSeen="2026-10-20T11:00:00",
                dbm=-60,
            )
        ),
        authoritative=False,
    )

    assert coord.devices[COVER] is device
    assert device.channels[0] is channel
    assert (device.friendly_id, device.manufacturer, device.physical_device) == (
        "Kitchen blind",
        "Other",
        "JD-1",
    )
    assert (device.first_seen, device.last_seen, device.dbm) == (
        "2026-01-01T00:00:00",
        "2026-10-20T11:00:00",
        -60,
    )
    assert device.last_command_error == "status 500"
    assert device.last_update_received_monotonic == 12.5


def test_error_on_a_held_device_is_not_lost(coord) -> None:
    """B-4: a command holding the device writes to the object the entry keeps."""
    coord._apply_device_list(_list(_cover()), authoritative=True)  # noqa: SLF001
    held = coord.devices[COVER]
    coord._apply_device_list(_list(_cover()), authoritative=True)  # noqa: SLF001
    held.last_command_error = "request_timeout"
    assert coord.devices[COVER].last_command_error == "request_timeout"


def test_missing_list_metadata_keeps_the_stored_values(coord) -> None:
    coord._apply_device_list(  # noqa: SLF001
        _list(_cover(dbm=-70, lastSeen="2026-10-20T10:00:00")), authoritative=True
    )
    coord._apply_device_list(_list(_cover()), authoritative=False)  # noqa: SLF001
    device = coord.devices[COVER]
    assert (device.dbm, device.last_seen) == (-70, "2026-10-20T10:00:00")


def test_list_never_replays_a_button_occurrence(coord) -> None:
    coord._apply_device_list(_list(_cover()), authoritative=True)  # noqa: SLF001
    channel = coord.devices[COVER].channels[0]
    channel.last_button, channel.last_button_action = "buttonA0", "pressed"
    coord._apply_device_list(_list(_cover()), authoritative=True)  # noqa: SLF001
    assert (channel.last_button, channel.last_button_action) == (None, None)


def test_device_without_profiles_is_discovered_once_profiles_arrive(coord) -> None:
    with patch(f"{MODULE}.async_dispatcher_send") as send:
        coord._apply_device_object(  # noqa: SLF001
            {"deviceId": "LATE", "eeps": []}, authoritative=True
        )
        coord._apply_device_object(  # noqa: SLF001
            device_object("LATE", "D2-01-01"), authoritative=True
        )
        coord._apply_device_object(  # noqa: SLF001
            device_object("LATE", "D2-01-01"), authoritative=True
        )
    discovered = [c for c in send.call_args_list if "discovered" in c.args[1]]
    assert len(discovered) == 2  # no profiles yet, then once with profiles
    assert coord.devices["LATE"].entity_type == "switch"


# ── Last vertical run duration (diagnostics only) ─────────────────────────


def _configured_cover(last: object, channel: object = 0) -> dict:
    return _cover(
        configuration={
            "channel": channel,
            "parameters": [
                {"key": "verticalMovementTime", "value": 25.42},
                {"key": "lastVerticalMovementTime", "value": last},
            ],
        }
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [(31.14, {0: 31.14}), ("26.37", {0: 26.37}), (-1, {}), ("x", {}), (True, {})],
)
def test_last_vertical_movement_time_parsing(value, expected) -> None:
    assert protocol.last_vertical_movement_times(_configured_cover(value)) == expected


def test_last_vertical_movement_time_per_channel_parameter() -> None:
    data = _cover(
        configuration={
            "parameters": [
                {"key": "channel", "value": 1},
                {"key": "lastVerticalMovementTime", "value": 20.0},
            ]
        }
    )
    assert protocol.last_vertical_movement_times(data) == {1: 20.0}
    assert protocol.last_vertical_movement_times(_cover()) == {}


def test_last_run_duration_is_stored_but_never_used_as_state(coord) -> None:
    coord._apply_device_list(  # noqa: SLF001
        _list(_configured_cover(31.14)), authoritative=False
    )
    channel = coord.devices[COVER].channels[0]
    assert coord.last_vertical_movement_times == {COVER: {0: 31.14}}
    # The configured movement time still drives the estimate.
    assert channel.vertical_movement_time == 25.42
    # A later list without the key keeps the last known value.
    coord._apply_device_list(_list(_cover()), authoritative=False)  # noqa: SLF001
    assert coord.last_vertical_movement_times == {COVER: {0: 31.14}}
    coord.async_forget_device(COVER)
    assert coord.last_vertical_movement_times == {}


async def test_diagnostics_show_the_last_run_duration(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_configured_cover(31.14)]
    entry = await setup_bridge(hass, gateway)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["devices"][0]["last_vertical_movement_time"] == {0: 31.14}
    # No request beyond the usual device list read.
    assert len(gateway.requests_to("GET", "/devices")) == 1


# ── Origin of the position (diagnostics only) ─────────────


def _report(coord, value: object, *, direction: str = "from") -> None:
    coord._handle_telegram(  # noqa: SLF001
        telegram(COVER, [{"key": "position", "value": value}], direction=direction)[
            "telegram"
        ]
    )


def test_position_origin_follows_the_source(coord) -> None:
    assert coord.position_origin(COVER, 0) is None
    coord._apply_device_list(_list(_cover()), authoritative=True)  # noqa: SLF001
    assert coord.position_origin(COVER, 0) == "device list"
    _report(coord, 40)
    assert coord.position_origin(COVER, 0) == "reported"
    # "unknown" changes neither the position nor its origin.
    _report(coord, "unknown")
    assert coord.devices[COVER].channels[0].position == 40
    assert coord.position_origin(COVER, 0) == "reported"


def test_foreign_command_and_entity_estimate_are_estimated(coord) -> None:
    coord._apply_device_list(_list(_cover()), authoritative=True)  # noqa: SLF001
    with patch.object(coord, "_schedule_reconciliation_queries"):
        _report(coord, 30, direction="to")
    assert coord.devices[COVER].channels[0].position == 30
    assert coord.position_origin(COVER, 0) == "estimated"
    _report(coord, 30)
    assert coord.position_origin(COVER, 0) == "reported"
    # An entity estimate writes the channel directly: no record matches.
    coord.devices[COVER].channels[0].position = 70
    assert coord.position_origin(COVER, 0) == "estimated"


def test_elapsed_movement_is_assumed_and_forget_clears(coord) -> None:
    coord._apply_device_list(_list(_cover()), authoritative=True)  # noqa: SLF001
    movement = CoverMovement(100, 20, 0.0, 1.0)
    coord._cover_movements[(COVER, 0)] = movement  # noqa: SLF001
    coord._finish_cover_movement(COVER, 0, movement)  # noqa: SLF001
    assert coord.devices[COVER].channels[0].position == 20
    assert coord.position_origin(COVER, 0) == "assumed"
    coord.async_forget_device(COVER)
    assert coord._position_origins == {}  # noqa: SLF001


def test_device_document_is_reported(coord) -> None:
    coord._apply_device_list(_list(_cover()), authoritative=True)  # noqa: SLF001
    coord._apply_device_object(  # noqa: SLF001
        device_object(COVER, ["D2-05-02"], states=[{"key": "position", "value": 60}]),
        authoritative=True,
        position_origin="reported",
    )
    assert coord.position_origin(COVER, 0) == "reported"


async def test_diagnostics_show_the_position_origin(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [_cover()]
    entry = await setup_bridge(hass, gateway)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["devices"][0]["position_origin"] == {0: "device list"}
    await gateway.push(telegram(COVER, [{"key": "position", "value": 50}]))
    await hass.async_block_till_done()
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["devices"][0]["position_origin"] == {0: "reported"}


# ── Regression tests for the field baseline (R7 and R8) ─────────


async def test_rocker_then_unknown_then_end_position(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """R7: local rocker telegrams, an interim ``unknown``, then the end report."""
    gateway.devices = [_cover()]
    await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "cover", f"AABB0011_{COVER}")
    assert hass.states.get(entity_id).attributes["current_position"] == 0
    for action in ("pressed", "released"):
        await gateway.push(telegram(COVER, [{"key": "buttonA0", "value": action}]))
    await gateway.push(telegram(COVER, [{"key": "position", "value": "unknown"}]))
    await hass.async_block_till_done()
    # Neither the rocker nor "unknown" changes or confirms the position.
    assert hass.states.get(entity_id).attributes["current_position"] == 0
    await gateway.push(telegram(COVER, [{"key": "position", "value": 0}]))
    await wait_until(
        lambda: hass.states.get(entity_id).attributes["current_position"] == 100
    )
    assert hass.states.get(entity_id).state == "open"


def _record_writes(hass: HomeAssistant, entity_id: str) -> list[str]:
    writes: list[str] = []

    @callback
    def only_entity(event_data) -> bool:
        return event_data["entity_id"] == entity_id

    @callback
    def record(event: Event) -> None:
        new_state = event.data.get("new_state")
        writes.append(new_state.attributes.get("event_type") if new_state else "")

    for event_type in ("state_changed", "state_reported"):
        hass.bus.async_listen(event_type, record, event_filter=only_entity)
    return writes


async def test_six_press_release_pairs_write_once_per_telegram(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """R8: 6 press and 6 release telegrams, exactly one event write each."""
    gateway.devices = [device_object("ROCKER01", "F6-02-01")]
    await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "event", "AABB0011_ROCKER01")
    await hass.async_block_till_done()
    writes = _record_writes(hass, entity_id)
    for _ in range(6):
        for action in ("pressed", "released"):
            await gateway.push(
                telegram("ROCKER01", [{"key": "buttonA0", "value": action}])
            )
    await wait_until(lambda: len(writes) == 12)
    assert writes == ["buttonA0_pressed", "buttonA0_released"] * 6
    # A resynchronising device list afterwards replays nothing.
    await gateway.close_streams()
    await gateway.wait_for_stream()
    await hass.async_block_till_done()
    assert len(writes) == 12


async def test_long_press_is_one_press_and_one_release(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """R8: a held rocker reports press, then release; no repeats in between."""
    gateway.devices = [device_object("ROCKER01", "F6-02-01")]
    await setup_bridge(hass, gateway)
    entity_id = await wait_for_entity(hass, "event", "AABB0011_ROCKER01")
    await hass.async_block_till_done()
    writes = _record_writes(hass, entity_id)
    await gateway.push(telegram("ROCKER01", [{"key": "buttonAI", "value": "pressed"}]))
    await wait_until(lambda: len(writes) == 1)
    # Held: other traffic (the uptime probe, other devices) writes nothing here.
    await gateway.push(telegram("OTHER", [{"key": "switch", "value": "on"}]))
    await hass.async_block_till_done()
    assert writes == ["buttonAI_pressed"]
    await gateway.push(telegram("ROCKER01", [{"key": "buttonAI", "value": "released"}]))
    await wait_until(lambda: len(writes) == 2)
    assert writes == ["buttonAI_pressed", "buttonAI_released"]
