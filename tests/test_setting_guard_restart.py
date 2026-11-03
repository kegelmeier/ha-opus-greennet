"""The protection of a confirmed write survives a restart of Home Assistant.

A device list carries the gateway's cached copy of the configuration, which the
gateway refreshes on its own schedule (nightly). The protection against that
older copy (``SettingGuard``) was kept in memory only, so a restart, for example
after an update, let the list bring the value from before the write back. What
is saved is the bookkeeping of the protection, never a value Home Assistant
shows: after a restart the setting is unknown until the gateway lists a value
that is not the old one, or until *Read settings* reads it.
"""

from __future__ import annotations

import asyncio
from time import monotonic, time
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.opus_greennet import (
    _setting_guard_store,
    async_remove_entry,
)
from custom_components.opus_greennet.const import (
    CONF_EAG_ID,
    SETTING_CACHE_GUARD_SECONDS,
    SETTING_GUARD_SAVE_DELAY,
    SETTING_GUARD_STORE_KEY,
)
from custom_components.opus_greennet.coordinator import (
    OpusGreenNetCoordinator,
    SettingGuard,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from tests.fake_gateway import device_object
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

EAG_ID = "AABB0011"
BLIND = "D2-05-02"
KEY = "maxHoldingTime"


class FakeStore:
    """Home Assistant's ``Store`` as far as the coordinator uses it."""

    def __init__(self, saved: object = None) -> None:
        self.saved = saved
        self.pending: tuple[object, float] | None = None
        self.direct_saves: list[object] = []
        self.load_error: Exception | None = None
        self.save_error: Exception | None = None

    async def async_load(self) -> object:
        if self.load_error is not None:
            raise self.load_error
        return self.saved

    def async_delay_save(self, data_func, delay: float) -> None:
        self.pending = (data_func, delay)

    async def async_save(self, data: object) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.direct_saves.append(data)

    def flush(self) -> object:
        """Write what a delayed save would write now."""
        assert self.pending is not None
        self.saved = self.pending[0]()
        self.pending = None
        return self.saved


def _list(**values: object) -> dict:
    parameters = [{"key": key, "value": value} for key, value in values.items()]
    return {
        "devices": [
            device_object("DEV1", BLIND, configuration={"parameters": parameters})
        ]
    }


@pytest.fixture
def blind(connected_coordinator):
    device = connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Blind", [{"eep": BLIND}]
    )
    device.get_or_create_channel().max_holding_time = 12.0
    return device


@pytest.fixture
async def restarted(radio):
    """A second coordinator, as Home Assistant creates it after a restart."""
    hass = MagicMock()
    hass.async_create_background_task.side_effect = (
        lambda target, name, eager_start=True: asyncio.create_task(target)
    )
    coord = OpusGreenNetCoordinator(hass, radio, EAG_ID)  # type: ignore[arg-type]
    coord._gateway_available = True
    try:
        yield coord
    finally:
        await coord.async_unload()


async def _confirmed_write(coord, radio, store: FakeStore) -> None:
    await coord.async_restore_setting_guards(store)  # type: ignore[arg-type]
    radio.response = {
        "header": {"httpStatus": 200},
        "configuration": {"parameters": [{"key": KEY, "value": 29.0}]},
    }
    await coord.async_set_device_parameter("DEV1", KEY, 29, step=1, minimum=0)


def _first_list(coord, **values: object) -> EnOceanDevice:
    coord._apply_device_list(_list(**values), authoritative=True)
    return coord.devices["DEV1"]


async def test_a_confirmed_write_is_saved_soon(
    radio, connected_coordinator, blind
) -> None:
    store = FakeStore()
    await _confirmed_write(connected_coordinator, radio, store)

    assert store.pending is not None and store.pending[1] == SETTING_GUARD_SAVE_DELAY
    entry = store.flush()["guards"]["DEV1"][KEY]  # type: ignore[index]
    assert entry["stale"] == [12.0]
    assert entry["written"] == 29.0
    assert entry["written_at"] == pytest.approx(time(), abs=5)


async def test_after_a_restart_the_old_value_does_not_come_back(
    radio, connected_coordinator, blind, restarted
) -> None:
    """The reported case: write, restart, then the gateway still lists the old value."""
    store = FakeStore()
    await _confirmed_write(connected_coordinator, radio, store)
    store.flush()

    await restarted.async_restore_setting_guards(store)  # type: ignore[arg-type]
    device = _first_list(restarted, maxHoldingTime=12.0)

    assert device.setting_reading(KEY) is None


async def test_after_a_restart_the_list_ends_the_protection_as_before(
    radio, connected_coordinator, blind, restarted
) -> None:
    store = FakeStore()
    await _confirmed_write(connected_coordinator, radio, store)
    store.flush()
    await restarted.async_restore_setting_guards(store)  # type: ignore[arg-type]
    device = _first_list(restarted, maxHoldingTime=12.0)

    restarted._apply_device_list(_list(maxHoldingTime=29.0), authoritative=False)

    assert device.setting_reading(KEY) == 29.0
    assert restarted._setting_guards == {}
    # The end of the protection is saved too, so a second restart starts clean.
    assert store.flush() == {"guards": {}}


async def test_a_third_value_after_a_restart_is_a_change_made_elsewhere(
    radio, connected_coordinator, blind, restarted
) -> None:
    store = FakeStore()
    await _confirmed_write(connected_coordinator, radio, store)
    store.flush()
    await restarted.async_restore_setting_guards(store)  # type: ignore[arg-type]

    device = _first_list(restarted, maxHoldingTime=40.0)

    assert device.setting_reading(KEY) == 40.0
    assert restarted._setting_guards == {}


async def test_a_saved_protection_is_counted_from_the_write(restarted) -> None:
    written_at = time() - SETTING_CACHE_GUARD_SECONDS + 600
    store = FakeStore(
        {
            "guards": {
                "DEV1": {
                    KEY: {"stale": [12.0], "written": 29.0, "written_at": written_at}
                }
            }
        }
    )
    await restarted.async_restore_setting_guards(store)  # type: ignore[arg-type]

    guard = restarted._setting_guards["DEV1"][KEY]
    # About ten minutes are left, not a fresh 26 hours.
    assert SETTING_CACHE_GUARD_SECONDS - (monotonic() - guard.at) == pytest.approx(
        600, abs=5
    )


async def test_a_saved_protection_older_than_the_maximum_age_is_dropped(
    restarted,
) -> None:
    written_at = time() - SETTING_CACHE_GUARD_SECONDS - 60
    store = FakeStore(
        {
            "guards": {
                "DEV1": {
                    KEY: {"stale": [12.0], "written": 29.0, "written_at": written_at}
                }
            }
        }
    )
    await restarted.async_restore_setting_guards(store)  # type: ignore[arg-type]

    assert restarted._setting_guards == {}
    assert _first_list(restarted, maxHoldingTime=12.0).setting_reading(KEY) == 12.0


async def test_a_write_time_in_the_future_counts_as_just_written(restarted) -> None:
    """A clock set back must not end the protection early or make it endless."""
    store = FakeStore(
        {
            "guards": {
                "DEV1": {
                    KEY: {
                        "stale": [12.0],
                        "written": 29.0,
                        "written_at": time() + 7200,
                    }
                }
            }
        }
    )
    await restarted.async_restore_setting_guards(store)  # type: ignore[arg-type]

    assert KEY in restarted._setting_guards["DEV1"]


def _saved(**entry: object) -> dict:
    """Saved data with one protection; ``entry`` overrides a valid one."""
    valid = {"stale": [12.0], "written": 29.0, "written_at": time()}
    return {"guards": {"DEV1": {KEY: {**valid, **entry}}}}


@pytest.mark.parametrize(
    "saved",
    [
        None,
        [],
        "text",
        {"guards": []},
        {"guards": {"DEV1": []}},
        {"guards": {"DEV1": {KEY: []}}},
        {"guards": {1: _saved()["guards"]["DEV1"]}},
        {"guards": {"DEV1": {2: _saved()["guards"]["DEV1"][KEY]}}},
        _saved(stale=[]),
        _saved(stale=12.0),
        _saved(stale=[True]),
        _saved(stale=[None]),
        _saved(written=None),
        _saved(written=True),
        _saved(written=float("nan")),
        _saved(written_at=None),
        _saved(written_at="now"),
        _saved(written_at=float("nan")),
    ],
)
async def test_saved_data_that_is_not_a_protection_is_ignored(
    restarted, saved: object
) -> None:
    await restarted.async_restore_setting_guards(FakeStore(saved))  # type: ignore[arg-type]

    assert restarted._setting_guards == {}


async def test_values_of_a_selection_are_saved_and_restored(
    connected_coordinator, restarted
) -> None:
    store = FakeStore()
    await connected_coordinator.async_restore_setting_guards(store)  # type: ignore[arg-type]
    connected_coordinator._guard_confirmed_setting("DEV1", "routingTime", "off", 0.3)
    store.flush()

    await restarted.async_restore_setting_guards(store)  # type: ignore[arg-type]

    guard = restarted._setting_guards["DEV1"]["routingTime"]
    assert (guard.stale, guard.written) == (("off",), 0.3)


async def test_a_store_that_cannot_be_read_does_not_stop_the_setup(
    restarted, caplog: pytest.LogCaptureFixture
) -> None:
    store = FakeStore()
    store.load_error = HomeAssistantError("unreadable")

    await restarted.async_restore_setting_guards(store)  # type: ignore[arg-type]

    assert restarted._setting_guards == {}
    assert "could not be read" in caplog.text
    # A later change is still saved.
    restarted._guard_confirmed_setting("DEV1", KEY, 12.0, 29.0)
    assert store.pending is not None


async def test_a_write_back_to_the_old_value_releases_and_saves(
    radio, connected_coordinator, blind
) -> None:
    store = FakeStore()
    await connected_coordinator.async_restore_setting_guards(store)  # type: ignore[arg-type]
    connected_coordinator._setting_guards["DEV1"] = {
        KEY: SettingGuard((12.0,), 29.0, monotonic())
    }

    connected_coordinator._guard_confirmed_setting("DEV1", KEY, 12.0, 12.0)

    assert connected_coordinator._setting_guards == {}
    assert store.flush() == {"guards": {}}


async def test_a_read_that_reports_the_setting_releases_and_saves(
    radio, connected_coordinator, blind
) -> None:
    store = FakeStore()
    await _confirmed_write(connected_coordinator, radio, store)
    store.flush()

    radio.response = {
        "header": {"httpStatus": 200},
        "configuration": {"parameters": [{"key": KEY, "value": 12.0}]},
    }
    await connected_coordinator.async_reread_device_settings("DEV1")

    assert store.flush() == {"guards": {}}


async def test_forgetting_a_device_releases_and_saves(
    radio, connected_coordinator, blind
) -> None:
    store = FakeStore()
    await _confirmed_write(connected_coordinator, radio, store)
    store.flush()

    connected_coordinator.async_forget_device("DEV1")

    assert store.flush() == {"guards": {}}


async def test_forgetting_a_device_without_protection_saves_nothing(
    radio, connected_coordinator, blind
) -> None:
    store = FakeStore()
    await connected_coordinator.async_restore_setting_guards(store)  # type: ignore[arg-type]

    connected_coordinator.async_forget_device("DEV1")

    assert store.pending is None


async def test_a_list_that_changes_nothing_saves_nothing(
    radio, connected_coordinator, blind
) -> None:
    store = FakeStore()
    await _confirmed_write(connected_coordinator, radio, store)
    store.flush()

    connected_coordinator._apply_device_list(
        _list(maxHoldingTime=12.0), authoritative=False
    )

    assert store.pending is None


async def test_unloading_saves_a_change_that_is_still_waiting(
    radio, connected_coordinator, blind
) -> None:
    """A reload within the save delay must not start from older data."""
    store = FakeStore()
    await _confirmed_write(connected_coordinator, radio, store)

    await connected_coordinator.async_unload()

    assert len(store.direct_saves) == 1
    assert store.direct_saves[0]["guards"]["DEV1"][KEY]["written"] == 29.0  # type: ignore[index]


async def test_unloading_without_a_change_saves_nothing(
    connected_coordinator,
) -> None:
    store = FakeStore()
    await connected_coordinator.async_restore_setting_guards(store)  # type: ignore[arg-type]

    await connected_coordinator.async_unload()

    assert store.direct_saves == []


async def test_a_save_that_fails_on_unload_is_reported_not_raised(
    radio, connected_coordinator, blind, caplog: pytest.LogCaptureFixture
) -> None:
    store = FakeStore()
    await _confirmed_write(connected_coordinator, radio, store)
    store.save_error = OSError("disk full")

    await connected_coordinator.async_unload()

    assert "could not be saved" in caplog.text


async def test_without_a_store_nothing_is_saved_or_failing(
    radio, connected_coordinator, blind
) -> None:
    """Tests and tools that build a coordinator by hand have no store."""
    radio.response = {
        "header": {"httpStatus": 200},
        "configuration": {"parameters": [{"key": KEY, "value": 29.0}]},
    }
    await connected_coordinator.async_set_device_parameter(
        "DEV1", KEY, 29, step=1, minimum=0
    )

    assert connected_coordinator._guard_store is None
    assert KEY in connected_coordinator._setting_guards["DEV1"]


async def test_removing_the_entry_removes_the_saved_protection(hass) -> None:
    store = _setting_guard_store(hass, EAG_ID)
    assert store.key == f"{SETTING_GUARD_STORE_KEY}.{EAG_ID}"
    await store.async_save({"guards": {}})
    entry = MagicMock(entry_id="entry-1", data={CONF_EAG_ID: EAG_ID})

    await async_remove_entry(hass, entry)

    assert await _setting_guard_store(hass, EAG_ID).async_load() is None


async def test_removing_an_entry_without_a_gateway_id_removes_nothing(hass) -> None:
    store = _setting_guard_store(hass, EAG_ID)
    await store.async_save({"guards": {}})

    await async_remove_entry(hass, MagicMock(entry_id="entry-2", data={}))

    assert await _setting_guard_store(hass, EAG_ID).async_load() == {"guards": {}}
