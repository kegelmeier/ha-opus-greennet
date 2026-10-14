"""Shared entities for the Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging
from collections.abc import Hashable, Mapping

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_registry import EntityRegistry

from .const import DOMAIN
from .coordinator import (
    SIGNAL_AVAILABILITY_UPDATE,
    SIGNAL_DEVICE_STATE_UPDATE,
    OpusGreenNetCoordinator,
)
from .diagnostics import log_entity_state_write
from .enocean_device import EnOceanDevice

_LOGGER = logging.getLogger(__name__)


def freeze_projection(value: object) -> Hashable:
    """Return an immutable, order-independent copy for change comparison."""
    if isinstance(value, Mapping):
        return tuple(
            sorted(
                ((str(key), freeze_projection(item)) for key, item in value.items()),
                key=lambda pair: pair[0],
            )
        )
    if isinstance(value, list | tuple):
        return tuple(freeze_projection(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(freeze_projection(item) for item in value)
    # An unknown unhashable value is replaced by a fresh object that never
    # compares equal, so the entity writes (fail-open).
    return value if isinstance(value, Hashable) else object()


@callback
def migrate_legacy_entity_suffix(
    hass: HomeAssistant,
    config_entry_id: str,
    entity_domain: str,
    device_prefix: str,
    legacy_suffix: str,
    current_suffix: str,
) -> None:
    """Preserve fork entity IDs and settings while adopting upstream unique IDs."""
    registry = er.async_get(hass)
    legacy_id = registry.async_get_entity_id(
        entity_domain, DOMAIN, f"{device_prefix}_{legacy_suffix}"
    )
    if legacy_id is None:
        return
    legacy = registry.async_get(legacy_id)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, device_prefix), config_entry_id
    )
    if (
        legacy is None
        or device is None
        or legacy.config_entry_id != config_entry_id
        or legacy.device_id != device.id
    ):
        return
    current_unique_id = f"{device_prefix}_{current_suffix}"
    current_id = registry.async_get_entity_id(entity_domain, DOMAIN, current_unique_id)
    if current_id is not None:
        current = registry.async_get(current_id)
        if (
            current is None
            or current.config_entry_id != config_entry_id
            or current.device_id != device.id
        ):
            return
        registry.async_remove(current_id)
        _LOGGER.warning(
            "Removed duplicate beta entity %s; preserved %s. Update any dashboard "
            "or automation references to the removed entity ID",
            current_id,
            legacy_id,
        )
    registry.async_update_entity(legacy_id, new_unique_id=current_unique_id)


@callback
def migrate_legacy_multichannel_entity(
    entity_registry: EntityRegistry,
    entity_domain: str,
    eag_id: str,
    device: EnOceanDevice,
) -> None:
    """Migrate or remove a stale single-channel registry entry."""
    if device.channel_count <= 1:
        return

    legacy_unique_id = f"{eag_id}_{device.device_id}"
    channel_zero_unique_id = f"{legacy_unique_id}_ch0"
    legacy_entity_id = entity_registry.async_get_entity_id(
        entity_domain, DOMAIN, legacy_unique_id
    )
    if legacy_entity_id is None:
        return

    channel_zero_entity_id = entity_registry.async_get_entity_id(
        entity_domain, DOMAIN, channel_zero_unique_id
    )
    if channel_zero_entity_id is not None:
        entity_registry.async_remove(legacy_entity_id)
        _LOGGER.info(
            "Removed obsolete aggregate entity %s; channel 0 is %s",
            legacy_entity_id,
            channel_zero_entity_id,
        )
        return

    entity_registry.async_update_entity(
        legacy_entity_id,
        new_unique_id=channel_zero_unique_id,
    )
    _LOGGER.info(
        "Migrated legacy entity %s to multi-channel unique ID %s",
        legacy_entity_id,
        channel_zero_unique_id,
    )


class OpusGreenNetEntity(Entity):
    """Base class for entities owned by an Opus GreenNet gateway."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        channel_id: int = 0,
    ) -> None:
        """Initialize common entity state."""
        self._coordinator = coordinator
        self._eag_id = eag_id
        self._gateway_device_id = gateway_device_id
        self._device = device
        self._channel_id = channel_id
        # Immutable projection of the state last written by a coordinator
        # dispatch. Any other write (commands, availability) clears it.
        self._dispatched_projection: Hashable | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._eag_id}_{self._device.device_id}")},
            name=self._device.friendly_id or self._device.device_id,
            manufacturer=self._device.manufacturer or "OPUS / EnOcean",
            model=self._device.primary_eep or "Unknown",
            serial_number=self._device.device_id,
            via_device_id=self._gateway_device_id,
        )

    @property
    def available(self) -> bool:
        """Return whether the MQTT transport is connected."""
        return self._coordinator.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates for this stable device identifier."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DEVICE_STATE_UPDATE}_{self._eag_id}_{self._device.device_id}",
                self._handle_state_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_AVAILABILITY_UPDATE}_{self._eag_id}",
                self._handle_availability_update,
            )
        )

    @callback
    def _handle_availability_update(self) -> None:
        """Refresh availability without interpreting cached data as a new event."""
        self.async_write_ha_state()

    def _visible_projection(self) -> Hashable | None:
        """Return what Home Assistant would store, or None if not comparable."""
        if self.hass is None or self.force_update:
            return None
        try:
            calculated = self._async_calculate_state()
            return freeze_projection((calculated.state, calculated.attributes))
        except Exception:  # noqa: BLE001 - fail open: always write instead
            return None

    def _async_write_ha_state(self) -> None:
        """Forget the dispatch projection whenever any path writes state."""
        self._dispatched_projection = None
        super()._async_write_ha_state()

    @callback
    def _handle_state_update(self, device: EnOceanDevice) -> None:
        """Handle a coordinator state update.

        A dispatch whose visible projection (state, availability, features and
        attributes) equals the last dispatched write is not written again,
        e.g. an RSSI-only update for a cover. Event entities override this
        handler and are not affected.
        """
        self._device = device
        projection = self._visible_projection()
        if projection is not None and projection == self._dispatched_projection:
            return
        log_entity_state_write(
            _LOGGER,
            self.entity_id or self._attr_unique_id,
            self._device,
            self._channel_id,
        )
        self.async_write_ha_state()
        self._dispatched_projection = projection
