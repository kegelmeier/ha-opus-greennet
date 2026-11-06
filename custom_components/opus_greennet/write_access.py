"""Opt-in for writing device settings to the actuators.

Writing a setting sends it by radio to the actuator, and a wrong value can make
the device malfunction. A new config entry therefore starts with write access
off; it is switched on in the options dialog after a warning and a confirmation.
An entry that was set up before the option existed has none and keeps writing,
so nothing changes for existing installations.

Reading settings (*Read settings*) is never affected. A notice
in Repairs points out an enabled entity that changes a setting while write
access is off. Nothing here is sent to the gateway or a device.
"""

from __future__ import annotations

from collections.abc import Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_ALLOW_DEVICE_MANAGEMENT,
    CONF_ALLOW_SETTING_WRITES,
    DOMAIN,
    ISSUE_WRITE_ACCESS_OFF,
)

# The number and select entities of this integration are the ones that change a
# device setting; both are disabled by default.
_WRITING_DOMAINS = (Platform.NUMBER, Platform.SELECT)
_WRITING_PREFIXES = tuple(f"{domain}." for domain in _WRITING_DOMAINS)


def setting_writes_allowed(entry: ConfigEntry) -> bool:
    """Return whether the entry may write device settings.

    An entry without the option (set up before it existed) may.
    """
    return bool(entry.options.get(CONF_ALLOW_SETTING_WRITES, True))


def device_management_allowed(entry: ConfigEntry) -> bool:
    """Return whether the entry may start the learn mode.

    Unlike write access this is off for every entry that did not switch it on,
    existing ones included: it acts on the gateway and the radio environment.
    """
    return bool(entry.options.get(CONF_ALLOW_DEVICE_MANAGEMENT, False))


def _writing_entity_enabled(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    registry = er.async_get(hass)
    return any(
        registered.domain in _WRITING_DOMAINS and registered.disabled_by is None
        for registered in er.async_entries_for_config_entry(registry, entry.entry_id)
    )


@callback
def async_update_write_access_issue(
    hass: HomeAssistant, entry: ConfigEntry, *, allowed: bool | None = None
) -> None:
    """Show or clear the notice for an enabled writing entity without write access.

    ``allowed`` is the value the options dialog is about to store; without it the
    stored option decides. The issue is not persistent and not fixable: every
    setup, every enabling of an entity and every confirmed dialog decides again,
    and only the user can enable write access (options dialog, after the
    warning).
    """
    issue_id = f"{ISSUE_WRITE_ACCESS_OFF}_{entry.entry_id}"
    if allowed is None:
        allowed = setting_writes_allowed(entry)
    if allowed or not _writing_entity_enabled(hass, entry):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_WRITE_ACCESS_OFF,
        translation_placeholders={"name": entry.title},
    )


@callback
def async_setup_write_access_notice(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Keep the notice up to date while the entry is loaded.

    No update listener of the entry is used: Home Assistant treats an entry with
    one differently when the entry is updated by a flow. The options dialog
    updates the notice itself.
    """

    @callback
    def _registry_updated(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        data: Mapping[str, object] = event.data
        changes = data.get("changes")
        entity_id = data.get("entity_id")
        # Only enabling or disabling a number or select entity matters.
        if (
            data.get("action") != "update"
            or not isinstance(changes, Mapping)
            or "disabled_by" not in changes
            or not isinstance(entity_id, str)
            or not entity_id.startswith(_WRITING_PREFIXES)
        ):
            return
        async_update_write_access_issue(hass, entry)

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_updated)
    )
    entry.async_on_unload(
        lambda: ir.async_delete_issue(
            hass, DOMAIN, f"{ISSUE_WRITE_ACCESS_OFF}_{entry.entry_id}"
        )
    )
    async_update_write_access_issue(hass, entry)
