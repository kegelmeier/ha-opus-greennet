"""Edge cases of entity platforms that the scenario tests do not reach."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.opus_greennet.binary_sensor import (
    async_setup_entry as async_setup_binary_sensors,
)
from custom_components.opus_greennet.coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
)
from custom_components.opus_greennet.cover import OpusGreenNetCover
from custom_components.opus_greennet.enocean_device import (
    EnOceanChannel,
    EnOceanDevice,
)
from custom_components.opus_greennet.entity import (
    OpusGreenNetEntity,
    freeze_projection,
    migrate_legacy_multichannel_entity,
)
from custom_components.opus_greennet.light import OpusGreenNetLight
from custom_components.opus_greennet.lock import (
    async_setup_entry as async_setup_locks,
)

EAG_ID = "AABB0011"
GATEWAY = "gateway-registry-id"


def _device(eep: str, device_id: str = "DEV1") -> EnOceanDevice:
    return EnOceanDevice(device_id=device_id, friendly_id="Test", eeps=[{"eep": eep}])


def _coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.available = True
    coordinator.eag_id = EAG_ID
    coordinator.async_set_cover_position = AsyncMock()
    coordinator.async_set_cover_tilt = AsyncMock()
    return coordinator


def test_multichannel_cover_uses_channel_identity() -> None:
    device = _device("D2-05-00")
    with patch.object(
        EnOceanDevice, "channel_count", new_callable=PropertyMock, return_value=2
    ):
        entity = OpusGreenNetCover(_coordinator(), EAG_ID, GATEWAY, device, 1)
    assert entity.unique_id == f"{EAG_ID}_DEV1_ch1"
    assert entity.translation_key == "channel"
    assert entity.translation_placeholders == {"channel": "1"}


def test_cover_without_position_reports_unknown() -> None:
    entity = OpusGreenNetCover(_coordinator(), EAG_ID, GATEWAY, _device("D2-05-00"))
    assert entity.current_cover_position is None
    assert entity.is_closed is None
    entity._device.channels[0] = EnOceanChannel(channel_id=0, position=100)
    assert entity.current_cover_position == 0
    assert entity.is_closed is True


async def test_cover_commands_without_value_publish_nothing() -> None:
    coordinator = _coordinator()
    entity = OpusGreenNetCover(coordinator, EAG_ID, GATEWAY, _device("D2-05-00"))
    entity.async_write_ha_state = MagicMock()
    await entity.async_set_cover_position()
    await entity.async_set_cover_tilt_position()
    coordinator.async_set_cover_position.assert_not_awaited()
    coordinator.async_set_cover_tilt.assert_not_awaited()
    entity.async_write_ha_state.assert_not_called()


def test_light_brightness_edge_values() -> None:
    switching = OpusGreenNetLight(
        _coordinator(), EAG_ID, GATEWAY, _device("A5-38-09"), 0
    )
    assert switching._device.is_dimmable is False
    assert switching.brightness is None

    dimmer = OpusGreenNetLight(_coordinator(), EAG_ID, GATEWAY, _device("D2-01-02"))
    assert dimmer.brightness is None
    dimmer._device.channels[0] = EnOceanChannel(channel_id=0, brightness=0)
    assert dimmer.brightness == 0
    dimmer._device.channels[0].brightness = 100
    assert dimmer.brightness == 255


def test_freeze_projection_is_order_independent_for_sets() -> None:
    assert freeze_projection({"b": {2, 1}, "a": [1]}) == freeze_projection(
        {"a": (1,), "b": frozenset({1, 2})}
    )
    # Unknown unhashable values never compare equal, so the entity writes.
    assert freeze_projection(bytearray(b"x")) != freeze_projection(bytearray(b"x"))


def test_multichannel_migration_without_legacy_entry_is_a_no_op() -> None:
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    migrate_legacy_multichannel_entity(registry, "switch", EAG_ID, _device("D2-01-11"))
    registry.async_get_entity_id.assert_called_once_with(
        "switch", "opus_greennet", f"{EAG_ID}_DEV1"
    )
    registry.async_remove.assert_not_called()
    registry.async_update_entity.assert_not_called()


def test_state_projection_fails_open_on_calculation_errors() -> None:
    entity = OpusGreenNetEntity(_coordinator(), EAG_ID, GATEWAY, _device("D2-01-00"))
    entity.hass = MagicMock()
    with patch.object(
        OpusGreenNetEntity, "_async_calculate_state", side_effect=RuntimeError
    ):
        assert entity._visible_projection() is None


def _entry(coordinator: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="entry-id",
        data={"eag_id": EAG_ID},
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(
            coordinator=coordinator, gateway_device_id=GATEWAY
        ),
        async_on_unload=MagicMock(),
    )


async def test_lock_setup_adds_already_discovered_handles(hass) -> None:
    coordinator = _coordinator()
    coordinator.devices = {"DEV1": _device("D2-06-40"), "DEV2": _device("D2-01-00")}
    add = MagicMock()
    with patch("custom_components.opus_greennet.lock.migrate_legacy_entity_suffix"):
        await async_setup_locks(hass, _entry(coordinator), add)
    add.assert_called_once()
    (entities,) = add.call_args.args
    assert [entity.unique_id for entity in entities] == [f"{EAG_ID}_DEV1_autolock"]


async def test_forgetting_unsubmitted_binary_sensors_allows_rediscovery(hass) -> None:
    """Pending entities without hass release their IDs immediately on removal."""
    coordinator = _coordinator()
    coordinator.devices = {}
    add = MagicMock()
    await async_setup_binary_sensors(hass, _entry(coordinator), add)
    device = _device("F6-05-01")
    async_dispatcher_send(hass, f"{SIGNAL_DEVICE_DISCOVERED}_{EAG_ID}", device)
    await hass.async_block_till_done()
    assert add.call_count == 1

    # Unknown devices are ignored; a known device is released and re-added.
    async_dispatcher_send(hass, f"{SIGNAL_DEVICE_REMOVED}_{EAG_ID}", "UNKNOWN")
    async_dispatcher_send(hass, f"{SIGNAL_DEVICE_REMOVED}_{EAG_ID}", "DEV1")
    await hass.async_block_till_done()
    async_dispatcher_send(hass, f"{SIGNAL_DEVICE_DISCOVERED}_{EAG_ID}", device)
    await hass.async_block_till_done()
    assert add.call_count == 2
    first = [entity.unique_id for entity in add.call_args_list[0].args[0]]
    second = [entity.unique_id for entity in add.call_args_list[1].args[0]]
    assert first == second
