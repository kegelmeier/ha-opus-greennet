"""Button platform: reference runs, reading device settings and the learn mode."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpusGreenNetConfigEntry
from .const import (
    CONF_EAG_ID,
    DEFAULT_CHANNEL,
    DOMAIN,
    REPOSITIONING_FIRST_DOWN,
    REPOSITIONING_FIRST_UP,
)
from .coordinator import SIGNAL_DEVICE_DISCOVERED, OpusGreenNetCoordinator
from .enocean_device import EnOceanDevice
from .entity import OpusGreenNetEntity
from .number import DEVICE_TIMES, PRESENCE_SETTINGS
from .select import DEVICE_SELECTS

# Commands are serialized by the coordinator.
PARALLEL_UPDATES = 0

# Translation key, repositioning value and OPUS end position of each run.
# OPUS positions: 0 = up (open), 100 = down (closed).
REFERENCE_RUNS: tuple[tuple[str, str, int], ...] = (
    ("reference_run_up", REPOSITIONING_FIRST_UP, 0),
    ("reference_run_down", REPOSITIONING_FIRST_DOWN, 100),
)


def has_settings(device: EnOceanDevice) -> bool:
    """Return whether the device has settings offered as number or select."""
    return any(
        time.applies_to(device) for time in (*DEVICE_TIMES, *PRESENCE_SETTINGS)
    ) or any(select.values_for(device) is not None for select in DEVICE_SELECTS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up reference run buttons and the read settings button."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]

    @callback
    def async_add_buttons(device: EnOceanDevice) -> None:
        if has_settings(device):
            async_add_entities(
                [
                    OpusGreenNetReadSettingsButton(
                        coordinator, eag_id, gateway_device_id, device
                    )
                ]
            )
        if not device.supports_repositioning:
            return
        async_add_entities(
            [
                OpusGreenNetReferenceRunButton(
                    coordinator,
                    eag_id,
                    gateway_device_id,
                    device,
                    run,
                    channel_id,
                )
                for run in REFERENCE_RUNS
                for channel_id in range(device.channel_count)
            ]
        )

    async_add_entities([OpusGreenNetLearnModeButton(coordinator, eag_id)])
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}", async_add_buttons
        )
    )
    for device in coordinator.devices.values():
        async_add_buttons(device)


class OpusGreenNetReferenceRunButton(OpusGreenNetEntity, ButtonEntity):
    """Drive a blind to an end position, then back to its current position.

    The blind recalibrates its position tracking on the way (``repositioning``
    ``firstUp``/``firstDown`` of the "Go to Position and Angle" command). An
    unknown position returns to the end position itself.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        run: tuple[str, str, int],
        channel_id: int = DEFAULT_CHANNEL,
    ) -> None:
        """Initialize the reference run button."""
        super().__init__(coordinator, eag_id, gateway_device_id, device, channel_id)
        key, self._repositioning, self._end_position = run
        channel_suffix = f"_ch{channel_id}" if device.channel_count > 1 else ""
        self._attr_translation_key = key
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{key}{channel_suffix}"

    async def async_press(self) -> None:
        """Start the reference run and return to the last known position."""
        channel = self._device.channels.get(self._channel_id)
        position = channel.position if channel is not None else None
        await self._coordinator.async_set_cover_position(
            self._device.device_id,
            self._end_position if position is None else position,
            self._channel_id,
            repositioning=self._repositioning,
        )


class OpusGreenNetReadSettingsButton(OpusGreenNetEntity, ButtonEntity):
    """Read the settings stored on the device and show them.

    One radio read per press, never repeated and never started by itself. Use
    it to see what the device holds after a write whose outcome was unclear, or
    after a setting was changed with another controller.
    """

    _attr_has_entity_name = True
    # Enabled by default: it only reads, and it sits next to the other
    # diagnostic values of the device. A battery presence detector answers a
    # ReCom read only while it is ready, so a press there may end in "no
    # response" (see docs/devices.md).
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "read_settings"

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the read settings button."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, DEFAULT_CHANNEL
        )
        self._attr_unique_id = f"{eag_id}_{device.device_id}_read_settings"

    async def async_press(self) -> None:
        """Read the settings of the device."""
        await self._coordinator.async_reread_device_settings(self._device.device_id)


class OpusGreenNetLearnModeButton(ButtonEntity):
    """Let new devices teach in to the gateway for a fixed time.

    Disabled by default and refused until device management is switched on in
    the options. One press sends one request; the learn mode ends by itself
    (see ``learn_mode.py``).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "learn_mode_start"

    def __init__(self, coordinator: OpusGreenNetCoordinator, eag_id: str) -> None:
        """Initialize the button for the gateway ``eag_id``."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{eag_id}_learn_mode_start"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, eag_id)})

    async def async_press(self) -> None:
        """Start the learn mode."""
        await self._coordinator.learn_mode.async_start()
