"""Delete one device from the gateway (device management, stage 3).

``DELETE /devices/<id>`` removes the device from the gateway's own list. For a
device that answers on the radio the gateway also takes itself out of the
device's link table first. This follows the handler in the gateway software
(findings for 1.21.30), not a recording of a request (see ``docs/devices.md``).

The rules, in order:

* only with the device management option, only for a device the integration
  knows, and not in the nightly restart window;
* ``forceDelete`` is never sent. A gateway that refuses to delete leaves the
  choice to the user;
* the ``DELETE`` is sent once and never repeated, whatever the outcome. Its
  answer is not trusted: the gateway's device list, read afterwards, decides;
* only when the device is gone from that list is it forgotten in Home
  Assistant. Otherwise nothing local changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .api import OpusApiError
from .const import DOMAIN, LEARN_MODE_BLOCKED_HOURS
from .wire import as_object, object_items

if TYPE_CHECKING:
    from .coordinator import OpusGreenNetCoordinator

_LOGGER = logging.getLogger(__name__)


async def _async_listed_ids(coordinator: OpusGreenNetCoordinator) -> set[str]:
    """Return the device IDs the gateway lists now."""
    document = await coordinator.client.async_get("/devices")
    ids: set[str] = set()
    for entry in object_items(document.get("devices")):
        data = as_object(entry.get("device", entry))
        device_id = None if data is None else data.get("deviceId")
        if isinstance(device_id, str):
            ids.add(device_id)
    return ids


async def async_delete_from_gateway(
    coordinator: OpusGreenNetCoordinator, device_id: str
) -> None:
    """Delete ``device_id`` from the gateway once and forget it locally."""
    if not coordinator.device_management_allowed():
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="device_management_disabled"
        )
    if coordinator.get_device(device_id) is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )
    if not coordinator.available:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="gateway_unavailable"
        )
    if dt_util.now().hour in LEARN_MODE_BLOCKED_HOURS:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="device_delete_night"
        )
    lock = coordinator.gateway_removal_lock
    if lock.locked():
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="device_delete_busy"
        )
    async with lock:
        try:
            await coordinator.client.async_delete(
                coordinator.client.device_path(device_id)
            )
        except OpusApiError as err:
            # Refused, or the answer was lost: the list below decides.
            _LOGGER.warning(
                "Deleting %s from gateway %s was not confirmed by the answer: %s",
                device_id,
                coordinator.eag_id,
                err,
            )
        try:
            still_listed = device_id in await _async_listed_ids(coordinator)
        except OpusApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_delete_unclear",
                translation_placeholders={"device_id": device_id},
            ) from err
        if still_listed:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_delete_not_confirmed",
                translation_placeholders={"device_id": device_id},
            )
        coordinator.async_forget_device(device_id)
        registry = dr.async_get(coordinator.hass)
        if coordinator.config_entry_id is not None and (
            child := registry.async_get_device_by_identifier(
                (DOMAIN, f"{coordinator.eag_id}_{device_id}"),
                coordinator.config_entry_id,
            )
        ):
            registry.async_update_device(
                child.id, remove_config_entry_id=coordinator.config_entry_id
            )
