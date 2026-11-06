"""Opt-in for writing device settings to the actuators.

A new config entry starts with write access off; it is switched on in the options
dialog after a warning and a confirmation. An entry without the option (set up
before it existed) keeps writing. Reading settings is never affected, and a
notice in Repairs points out an enabled writing entity while write access is off.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.opus_greennet.const import (
    CONF_ALLOW_DEVICE_MANAGEMENT,
    CONF_ALLOW_SETTING_WRITES,
    DOMAIN,
    ISSUE_WRITE_ACCESS_OFF,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.write_access import (
    async_update_write_access_issue,
    setting_writes_allowed,
)
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import configure_bridge, setup_bridge
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components/opus_greennet"
DIRECT_CONFIG = {"configuration": {"parameters": [{"key": "timer", "value": 300}]}}


def _entry(options: dict) -> MagicMock:
    return MagicMock(options=options)


# ── the option ─────────────────────────────────────────────────────────────


def test_an_entry_without_the_option_keeps_writing() -> None:
    assert setting_writes_allowed(_entry({})) is True
    assert setting_writes_allowed(_entry({CONF_ALLOW_SETTING_WRITES: True})) is True
    assert setting_writes_allowed(_entry({CONF_ALLOW_SETTING_WRITES: False})) is False


async def test_a_new_entry_starts_without_write_access(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    result = await configure_bridge(hass, gateway, allow_writes=False)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_ALLOW_SETTING_WRITES: False}
    assert setting_writes_allowed(result["result"]) is False


async def test_the_options_dialog_enables_and_disables_write_access(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    result = await configure_bridge(hass, gateway, allow_writes=False)
    entry = result["result"]
    await hass.async_block_till_done()

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.FORM
    assert flow["step_id"] == "init"
    # The box is cleared as long as write access is off.
    assert flow["data_schema"]({}) == {
        CONF_ALLOW_SETTING_WRITES: False,
        CONF_ALLOW_DEVICE_MANAGEMENT: False,
    }

    done = await hass.config_entries.options.async_configure(
        flow["flow_id"], {CONF_ALLOW_SETTING_WRITES: True}
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ALLOW_SETTING_WRITES] is True

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["data_schema"]({}) == {
        CONF_ALLOW_SETTING_WRITES: True,
        CONF_ALLOW_DEVICE_MANAGEMENT: False,
    }
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {CONF_ALLOW_SETTING_WRITES: False}
    )
    assert entry.options[CONF_ALLOW_SETTING_WRITES] is False


async def test_the_loaded_coordinator_follows_the_options_dialog_without_a_reload(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """The check wired in at setup reads the stored option on every write."""
    result = await configure_bridge(hass, gateway, allow_writes=False)
    entry = result["result"]
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    assert coordinator._write_access_allowed() is False

    for allowed in (True, False):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            flow["flow_id"], {CONF_ALLOW_SETTING_WRITES: allowed}
        )
        await hass.async_block_till_done()
        # The same coordinator answers with the new value: no reload happened.
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.coordinator is coordinator
        assert coordinator._write_access_allowed() is allowed


# ── the refusal ────────────────────────────────────────────────────────────


async def test_a_configuration_write_is_refused_without_write_access(
    radio, connected_coordinator
) -> None:
    connected_coordinator._write_access_allowed = lambda: False

    with pytest.raises(ServiceValidationError) as raised:
        await connected_coordinator.async_set_device_configuration(
            "DEV1", DIRECT_CONFIG
        )

    assert raised.value.translation_key == "write_access_disabled"
    assert radio.sent == []


async def test_a_setting_write_and_the_repeater_mode_are_refused_too(
    radio, connected_coordinator
) -> None:
    coord = connected_coordinator
    device = coord.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Switch", [{"eep": "D2-01-01"}]
    )
    device.get_or_create_channel().repeater = "off"
    coord._write_access_allowed = lambda: False

    for call in (
        coord.async_set_device_parameter("DEV1", "timer", 300, step=1, minimum=60),
        coord.async_set_device_option("DEV1", "ledColor", "green"),
        coord.async_set_device_option("DEV1", "repeaterState", "level_1"),
        coord.async_set_device_option("DEV1", "routingTime", "on"),
    ):
        with pytest.raises(ServiceValidationError) as raised:
            await call
        assert raised.value.translation_key == "write_access_disabled"
    assert radio.sent == []


async def test_without_the_check_or_with_write_access_a_write_is_sent(
    radio, connected_coordinator
) -> None:
    radio.response = {"header": {"httpStatus": 200}}
    await connected_coordinator.async_set_device_configuration("DEV1", DIRECT_CONFIG)
    assert len(radio.sent) == 1

    radio.sent.clear()
    connected_coordinator._write_access_allowed = lambda: True
    await connected_coordinator.async_set_device_configuration("DEV1", DIRECT_CONFIG)
    assert len(radio.sent) == 1


async def test_reading_settings_is_never_refused(radio, connected_coordinator) -> None:
    connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Switch", [{"eep": "D2-01-01"}]
    )
    connected_coordinator._write_access_allowed = lambda: False
    radio.response = {
        "header": {"httpStatus": 200},
        "configuration": {"parameters": []},
    }

    await connected_coordinator.async_get_device_configuration("DEV1")

    assert radio.sent == [("GET", "/devices/DEV1/configuration", None)]


# ── the notice in Repairs ──────────────────────────────────────────────────


def _issue_id(entry) -> str:
    return f"{ISSUE_WRITE_ACCESS_OFF}_{entry.entry_id}"


def _writing_entity(hass: HomeAssistant, entry, unique_id: str, *, disabled: bool):
    return er.async_get(hass).async_get_or_create(
        "number",
        DOMAIN,
        unique_id,
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
    )


async def test_the_notice_follows_the_entity_and_the_option(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ALLOW_SETTING_WRITES: False}
    )
    await hass.async_block_till_done()
    issues = ir.async_get(hass)

    # Write access is off but the writing entity is disabled: no notice.
    _writing_entity(hass, entry, "off_one", disabled=True)
    async_update_write_access_issue(hass, entry)
    assert issues.async_get_issue(DOMAIN, _issue_id(entry)) is None

    # An enabled writing entity without write access: the notice appears when
    # the registry reports that an entity was enabled.
    enabled = _writing_entity(hass, entry, "on_one", disabled=False)
    hass.bus.async_fire(
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        {
            "action": "update",
            "entity_id": enabled.entity_id,
            "changes": {"disabled_by": er.RegistryEntryDisabler.USER},
        },
    )
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, _issue_id(entry))
    assert issue is not None
    assert issue.translation_key == ISSUE_WRITE_ACCESS_OFF
    assert issue.is_fixable is False
    assert issue.severity is ir.IssueSeverity.WARNING

    # Confirming the options dialog with write access on clears it; clearing the
    # box again shows it.
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {CONF_ALLOW_SETTING_WRITES: True}
    )
    assert issues.async_get_issue(DOMAIN, _issue_id(entry)) is None

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {CONF_ALLOW_SETTING_WRITES: False}
    )
    assert issues.async_get_issue(DOMAIN, _issue_id(entry)) is not None


async def test_other_registry_changes_leave_the_notice_alone(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ALLOW_SETTING_WRITES: False}
    )
    await hass.async_block_till_done()
    enabled = _writing_entity(hass, entry, "on_one", disabled=False)

    # A rename of the entity and a change of a sensor are no reason to look.
    for data in (
        {"action": "update", "entity_id": enabled.entity_id, "changes": {"name": "x"}},
        {"action": "create", "entity_id": enabled.entity_id},
        {
            "action": "update",
            "entity_id": "sensor.opus_other",
            "changes": {"disabled_by": None},
        },
    ):
        hass.bus.async_fire(er.EVENT_ENTITY_REGISTRY_UPDATED, data)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(entry)) is None


async def test_unloading_the_entry_removes_the_notice(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ALLOW_SETTING_WRITES: False}
    )
    _writing_entity(hass, entry, "on_one", disabled=False)
    async_update_write_access_issue(hass, entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(entry)) is not None

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(entry)) is None


# ── texts ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["strings.json", "translations/en.json", "translations/de.json"]
)
def test_the_warning_texts_exist_in_every_language(name: str) -> None:
    texts = json.loads((INTEGRATION / name).read_text(encoding="utf-8"))
    step = texts["options"]["step"]["init"]
    assert step["title"]
    assert step["description"]
    assert step["data"][CONF_ALLOW_SETTING_WRITES]
    assert step["data_description"][CONF_ALLOW_SETTING_WRITES]
    issue = texts["issues"][ISSUE_WRITE_ACCESS_OFF]
    assert issue["title"]
    assert "{name}" in issue["description"]
    assert "{device_id}" in texts["exceptions"]["write_access_disabled"]["message"]
