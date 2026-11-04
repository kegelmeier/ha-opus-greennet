"""Switch platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.components.switch.const import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import (
    CONF_EAG_ID,
    DEFAULT_CHANNEL,
    LOCKING_MODE_ALARM,
    LOCKING_MODE_BLOCK,
    LOCKING_MODE_UNBLOCK,
)
from .coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    CommandDelivery,
    OpusGreenNetCoordinator,
)
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity, migrate_output_entities

# The coordinator serializes commands per device; entities receive pushed state.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet switches from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]
    entity_registry = er.async_get(hass)

    @callback
    def async_add_switch(device: EnOceanDevice) -> None:
        """Add switch entities for a discovered device."""
        if device.supports_locking_mode:
            async_add_entities(
                [
                    OpusGreenNetBlockageSwitch(
                        coordinator=coordinator,
                        eag_id=eag_id,
                        gateway_device_id=gateway_device_id,
                        device=device,
                        channel_id=channel_id,
                    )
                    for channel_id in range(device.channel_count)
                ]
            )
        if device.entity_type != "switch":
            return

        migrate_output_entities(
            entity_registry,
            SWITCH_DOMAIN,
            eag_id,
            device,
        )

        # Create entity for each channel
        entities = []
        for channel_id in range(device.channel_count):
            entities.append(
                OpusGreenNetSwitch(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    gateway_device_id=gateway_device_id,
                    device=device,
                    channel_id=channel_id,
                )
            )

        async_add_entities(entities)

    # Listen for new device discoveries
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}",
            async_add_switch,
        )
    )

    # Add entities for already discovered devices
    for device in coordinator.devices.values():
        async_add_switch(device)


class OpusGreenNetSwitch(OpusGreenNetEntity, SwitchEntity):
    """Representation of an Opus GreenNet switch."""

    _attr_has_entity_name = True
    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        channel_id: int = DEFAULT_CHANNEL,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, eag_id, gateway_device_id, device, channel_id)

        # Entity attributes
        channel_suffix = f"_ch{channel_id}" if device.channel_count > 1 else ""
        self._attr_unique_id = f"{eag_id}_{device.device_id}{channel_suffix}"

        if device.channel_count > 1:
            self._attr_translation_key = "channel"
            self._attr_translation_placeholders = {"channel": str(channel_id)}
        else:
            self._attr_name = None  # Use device name

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        channel = self._device.channels.get(self._channel_id)
        return channel.is_on if channel else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        snapshot = self._device.snapshot_channel_state(self._channel_id)
        delivery = await self._coordinator.async_turn_on(
            self._device.device_id, self._channel_id
        )
        # Queued for a sleeping device (201): keep the reported state.
        if (
            delivery is not CommandDelivery.DEFERRED
            and self._device.apply_switch_estimate(snapshot, True)
        ):
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        snapshot = self._device.snapshot_channel_state(self._channel_id)
        delivery = await self._coordinator.async_turn_off(
            self._device.device_id, self._channel_id
        )
        if (
            delivery is not CommandDelivery.DEFERRED
            and self._device.apply_switch_estimate(snapshot, False)
        ):
            self.async_write_ha_state()


class OpusGreenNetBlockageSwitch(OpusGreenNetEntity, SwitchEntity):
    """Blockage of a D2-05 blind (``lockingMode``), e.g. for automations.

    On sends ``block``, off sends ``unblock``. A D2-05-00 blind can also report
    ``alarm``, which counts as blocked; the reported mode is an attribute.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "blockage"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Blocking stops the blind; only enabled on purpose.
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        channel_id: int = DEFAULT_CHANNEL,
    ) -> None:
        """Initialize the blockage switch."""
        super().__init__(coordinator, eag_id, gateway_device_id, device, channel_id)
        channel_suffix = f"_ch{channel_id}" if device.channel_count > 1 else ""
        self._attr_unique_id = f"{eag_id}_{device.device_id}_blockage{channel_suffix}"

    def _locking_mode(self) -> str | None:
        channel = self._device.channels.get(self._channel_id)
        return channel.locking_mode if channel else None

    @property
    def is_on(self) -> bool | None:
        """Return whether the blind is blocked (``block`` or ``alarm``)."""
        mode = self._locking_mode()
        if mode is None:
            return None
        return mode in (LOCKING_MODE_BLOCK, LOCKING_MODE_ALARM)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return the reported locking mode."""
        mode = self._locking_mode()
        return {} if mode is None else {"locking_mode": mode}

    async def _async_set_mode(self, mode: str) -> None:
        snapshot = self._device.snapshot_channel_state(self._channel_id)
        delivery = await self._coordinator.async_set_cover_locking_mode(
            self._device.device_id, mode, self._channel_id
        )
        if (
            delivery is not CommandDelivery.DEFERRED
            and self._device.apply_locking_mode_estimate(snapshot, mode)
        ):
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Block the blind."""
        await self._async_set_mode(LOCKING_MODE_BLOCK)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Release the blockage."""
        await self._async_set_mode(LOCKING_MODE_UNBLOCK)
