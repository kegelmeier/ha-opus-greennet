"""Learn mode of the gateway (device management, stage 1).

The gateway side is the ``FakeGateway`` endpoint ``/system/receivemode``, whose
request and answer shape is an assumption from names in the gateway software.
Nothing here was run against a real gateway.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.opus_greennet import learn_mode as learn_mode_module
from custom_components.opus_greennet.const import (
    CONF_ALLOW_DEVICE_MANAGEMENT,
    CONF_ALLOW_SETTING_WRITES,
    DOMAIN,
    ISSUE_LEARN_MODE_RESET_FAILED,
    SETTING_GUARD_STORE_KEY,
    SETTING_GUARD_STORE_VERSION,
)
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import setup_bridge, wait_until

PATH = "/system/receivemode"
DAYTIME = datetime(2026, 11, 6, 14, 0, tzinfo=dt_util.get_default_time_zone())
NIGHT = datetime(2026, 11, 6, 4, 5, tzinfo=dt_util.get_default_time_zone())


@pytest.fixture(autouse=True)
def _fast_and_daytime():
    """Short timers and a fixed local time outside the nightly restart."""
    with (
        patch.object(learn_mode_module, "LEARN_MODE_SECONDS", 0.1),
        patch.object(dt_util, "now", return_value=DAYTIME),
    ):
        yield


async def _bridge(
    hass: HomeAssistant, gateway: FakeGateway, *, management: bool = True
):
    entry = await setup_bridge(hass, gateway)
    options = {CONF_ALLOW_SETTING_WRITES: True}
    if management:
        options[CONF_ALLOW_DEVICE_MANAGEMENT] = True
    hass.config_entries.async_update_entry(entry, options=options)
    return entry, entry.runtime_data.coordinator


def _modes(gateway: FakeGateway, mode: str) -> int:
    return sum(
        1 for body in gateway.requests_to("POST", PATH) if body["receiveMode"] == mode
    )


async def test_start_sends_the_learn_mode_once_and_the_timer_ends_it(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)

    await coordinator.learn_mode.async_start()

    assert gateway.receive_mode == "learnMode"
    assert coordinator.learn_mode.state is True
    assert coordinator.learn_mode.pending
    await wait_until(lambda: gateway.receive_mode == "normalMode")
    await wait_until(lambda: not coordinator.learn_mode.pending)
    assert coordinator.learn_mode.state is False
    assert _modes(gateway, "learnMode") == 1
    assert _modes(gateway, "normalMode") == 1


async def test_nothing_is_sent_without_the_option(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway, management=False)

    with pytest.raises(ServiceValidationError) as raised:
        await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "device_management_disabled"
    assert gateway.requests_to("POST", PATH) == []
    assert gateway.requests_to("GET", PATH) == []


async def test_not_in_the_nightly_restart_window(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)

    with (
        patch.object(dt_util, "now", return_value=NIGHT),
        pytest.raises(ServiceValidationError) as raised,
    ):
        await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "learn_mode_night"
    assert gateway.requests_to("POST", PATH) == []


async def test_second_start_while_running_is_refused(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    await coordinator.learn_mode.async_start()

    with pytest.raises(ServiceValidationError) as raised:
        await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "learn_mode_active"
    assert _modes(gateway, "learnMode") == 1


async def test_a_learn_mode_started_elsewhere_is_not_ended(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    gateway.receive_mode = "learnMode"

    with pytest.raises(ServiceValidationError) as raised:
        await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "learn_mode_foreign"
    assert gateway.requests_to("POST", PATH) == []
    await asyncio.sleep(0.3)
    assert gateway.receive_mode == "learnMode"
    assert coordinator.learn_mode.state is True


async def test_start_needs_a_readable_mode(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    gateway.status[PATH] = 500

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "learn_mode_unreadable"
    assert gateway.requests_to("POST", PATH) == []
    assert not coordinator.learn_mode.pending


async def test_a_start_the_gateway_rejects_is_not_repeated_and_is_still_reset(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)

    async def fail_learn(method: str, path: str, body: object) -> None:
        if method == "POST" and path == PATH and body == {"receiveMode": "learnMode"}:
            gateway.status[PATH] = 500

    gateway.on_request = fail_learn
    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.learn_mode.async_start()
    gateway.status.pop(PATH)
    gateway.on_request = None

    assert raised.value.translation_key == "learn_mode_start_unclear"
    assert coordinator.learn_mode.state is None
    await wait_until(lambda: not coordinator.learn_mode.pending)
    assert _modes(gateway, "learnMode") == 1
    assert _modes(gateway, "normalMode") == 1
    assert coordinator.learn_mode.state is False


async def test_a_reset_that_is_not_confirmed_is_not_repeated_but_reported(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)
    await coordinator.learn_mode.async_start()
    gateway.receive_mode_ignores_post = True

    assert await coordinator.learn_mode.async_stop() is False

    assert _modes(gateway, "normalMode") == 1
    assert coordinator.learn_mode.state is None
    assert coordinator.learn_mode.pending
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_LEARN_MODE_RESET_FAILED}_{coordinator.eag_id}"
    )
    assert issue is not None
    assert issue.data == {"config_entry_id": entry.entry_id}


async def test_a_reset_that_works_at_the_next_recovery_clears_the_issue(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    await coordinator.learn_mode.async_start()
    gateway.receive_mode_ignores_post = True
    assert await coordinator.learn_mode.async_stop() is False

    gateway.receive_mode_ignores_post = False
    assert await coordinator.learn_mode.async_stop(read_first=True) is True

    assert gateway.receive_mode == "normalMode"
    assert not coordinator.learn_mode.pending
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"{ISSUE_LEARN_MODE_RESET_FAILED}_{coordinator.eag_id}"
        )
        is None
    )


async def test_unloading_ends_the_learn_mode(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)
    await coordinator.learn_mode.async_start()

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert gateway.receive_mode == "normalMode"
    assert not coordinator.learn_mode.pending


async def test_stopping_home_assistant_ends_the_learn_mode(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    await coordinator.learn_mode.async_start()

    hass.bus.async_fire("homeassistant_stop")
    await hass.async_block_till_done()

    assert gateway.receive_mode == "normalMode"


async def test_the_note_is_saved_before_the_request_and_removed_after_the_reset(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    store = Store(
        hass,
        SETTING_GUARD_STORE_VERSION,
        f"{SETTING_GUARD_STORE_KEY}.{coordinator.eag_id}",
    )
    saved_when_requested: list[object] = []

    async def look(method: str, path: str, body: object) -> None:
        if method == "POST" and body == {"receiveMode": "learnMode"}:
            saved_when_requested.append(await store.async_load())

    coordinator.async_restore_setting_guards  # the entry already did this
    gateway.on_request = look
    await coordinator.learn_mode.async_start()
    gateway.on_request = None

    assert saved_when_requested
    note = saved_when_requested[0]
    assert isinstance(note, dict) and "started_at" in note["learn_mode"]
    await wait_until(lambda: not coordinator.learn_mode.pending)
    # ``pending`` ends before the removal of the note is written: wait for it.
    async with asyncio.timeout(5):
        while "learn_mode" in (await store.async_load() or {}):
            await asyncio.sleep(0.01)


async def test_a_note_from_before_a_restart_ends_a_learn_mode_that_is_still_on(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)
    store = Store(
        hass,
        SETTING_GUARD_STORE_VERSION,
        f"{SETTING_GUARD_STORE_KEY}.{coordinator.eag_id}",
    )
    await store.async_save({"guards": {}, "learn_mode": {"started_at": 1.0}})
    gateway.receive_mode = "learnMode"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new = hass.config_entries.async_get_entry(entry.entry_id).runtime_data.coordinator
    await wait_until(lambda: not new.learn_mode.pending)

    assert gateway.receive_mode == "normalMode"
    assert "learn_mode" not in (await store.async_load() or {})


async def test_a_note_with_the_normal_mode_only_removes_the_note(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, coordinator = await _bridge(hass, gateway)
    store = Store(
        hass,
        SETTING_GUARD_STORE_VERSION,
        f"{SETTING_GUARD_STORE_KEY}.{coordinator.eag_id}",
    )
    await store.async_save({"guards": {}, "learn_mode": {"started_at": 1.0}})
    gateway.requests.clear()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new = hass.config_entries.async_get_entry(entry.entry_id).runtime_data.coordinator
    await wait_until(lambda: not new.learn_mode.pending)

    assert gateway.requests_to("POST", PATH) == []
    assert gateway.receive_mode == "normalMode"


@pytest.mark.parametrize(
    "saved",
    [
        None,
        {},
        {"learn_mode": 5},
        {"learn_mode": {"started_at": "x"}},
        {"learn_mode": {"started_at": True}},
        {"learn_mode": {"started_at": float("nan")}},
    ],
)
def test_a_malformed_note_is_ignored(saved: object) -> None:
    controller = learn_mode_module.LearnModeController(None)  # type: ignore[arg-type]
    controller.restore(saved)
    assert not controller.pending
    assert controller.as_storage() == {}


async def test_entities_exist_and_are_disabled_by_default(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    registry = er.async_get(hass)

    button = registry.async_get(
        registry.async_get_entity_id(
            "button", DOMAIN, f"{coordinator.eag_id}_learn_mode_start"
        )
    )
    sensor = registry.async_get(
        registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{coordinator.eag_id}_learn_mode"
        )
    )

    assert button.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert button.entity_category is EntityCategory.CONFIG
    assert sensor.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC


async def test_the_options_dialog_stores_the_option_only_when_it_is_on(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry, _ = await _bridge(hass, gateway, management=False)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ALLOW_SETTING_WRITES: True, CONF_ALLOW_DEVICE_MANAGEMENT: True},
    )
    assert result["data"][CONF_ALLOW_DEVICE_MANAGEMENT] is True

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ALLOW_SETTING_WRITES: True, CONF_ALLOW_DEVICE_MANAGEMENT: False},
    )
    assert CONF_ALLOW_DEVICE_MANAGEMENT not in result["data"]


def test_a_coordinator_without_the_callback_does_not_allow_it() -> None:
    from unittest.mock import MagicMock

    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    assert coordinator.device_management_allowed() is False


async def test_start_is_refused_while_the_gateway_is_unavailable(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)

    with (
        patch.object(
            OpusGreenNetCoordinator,
            "available",
            new_callable=lambda: property(lambda self: False),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "gateway_unavailable"
    assert gateway.requests_to("POST", PATH) == []


async def test_a_start_during_a_start_is_refused_as_busy(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)

    async with coordinator.learn_mode._lock:
        with pytest.raises(ServiceValidationError) as raised:
            await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "learn_mode_busy"
    assert gateway.requests_to("POST", PATH) == []


async def test_an_answer_without_a_known_mode_is_unreadable(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    gateway.receive_mode = "somethingElse"

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "learn_mode_unreadable"
    assert gateway.requests_to("POST", PATH) == []


async def test_nothing_is_sent_when_the_note_cannot_be_saved(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)

    with (
        patch.object(
            OpusGreenNetCoordinator,
            "async_save_bookkeeping",
            return_value=False,
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "learn_mode_not_saved"
    assert gateway.requests_to("POST", PATH) == []
    assert not coordinator.learn_mode.pending


async def test_stopping_without_a_note_does_nothing(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)

    assert await coordinator.learn_mode.async_stop() is True
    await coordinator.learn_mode.async_shutdown()

    assert gateway.requests_to("POST", PATH) == []


async def test_shutdown_gives_up_after_the_timeout_and_keeps_the_note(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    await coordinator.learn_mode.async_start()

    async def _hang(*args: object, **kwargs: object) -> bool:
        await asyncio.sleep(10)
        return True

    with (
        patch.object(learn_mode_module, "LEARN_MODE_SHUTDOWN_TIMEOUT", 0.05),
        patch.object(coordinator.learn_mode, "async_stop", _hang),
    ):
        await coordinator.learn_mode.async_shutdown()

    assert coordinator.learn_mode.pending


async def test_an_unreadable_mode_on_recovery_still_tries_to_end_it(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    coordinator.learn_mode.restore({"learn_mode": {"started_at": 1.0}})
    gateway.receive_mode = "learnMode"
    gateway.status[PATH] = 500

    assert await coordinator.learn_mode.async_stop(read_first=True) is False


async def test_a_start_shortly_before_the_nightly_window_is_refused(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    _, coordinator = await _bridge(hass, gateway)
    almost = datetime(2026, 11, 6, 2, 59, 30, tzinfo=dt_util.get_default_time_zone())

    with (
        patch.object(learn_mode_module, "LEARN_MODE_SECONDS", 180.0),
        patch.object(dt_util, "now", return_value=almost),
        pytest.raises(ServiceValidationError) as raised,
    ):
        await coordinator.learn_mode.async_start()

    assert raised.value.translation_key == "learn_mode_night"
    assert gateway.requests_to("POST", PATH) == []
