"""Release bookkeeping of the binary sensor platform that a real removal skips."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.opus_greennet.binary_sensor import (
    OpusGreenNetStateBinarySensor,
    async_setup_entry,
)
from custom_components.opus_greennet.coordinator import (
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
)


def _entry(coordinator, hass):
    return SimpleNamespace(
        data={"eag_id": coordinator.eag_id},
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(
            coordinator=coordinator, gateway_device_id="gateway"
        ),
        async_on_unload=MagicMock(),
        async_create_background_task=(
            lambda hass, coro, name, eager_start=False: hass.async_create_task(coro)
        ),
    )


async def test_release_of_an_entity_that_was_never_added_is_ignored(
    hass, coordinator, make_device
):
    """A pending entity has no hass; its late removal callback frees nothing twice."""
    device = make_device("F6-05-02")
    coordinator.devices[device.device_id] = device
    add = MagicMock()
    await async_setup_entry(hass, _entry(coordinator, hass), add)
    entities = add.call_args.args[0]
    assert len(entities) == 2
    live, pending = entities
    live.hass = hass  # Added to Home Assistant: the removal waits for it.

    async_dispatcher_send(
        hass, f"{SIGNAL_DEVICE_REMOVED}_{coordinator.eag_id}", device.device_id
    )
    await hass.async_block_till_done()

    # The pending entity was released at once; its removal callbacks run later.
    for callback in list(pending._on_remove or []):
        callback()
    await hass.async_block_till_done()

    # The live entity still holds the reservation, so a new discovery is parked.
    async_dispatcher_send(
        hass, f"{SIGNAL_DEVICE_DISCOVERED}_{coordinator.eag_id}", device
    )
    await hass.async_block_till_done()
    assert add.call_count == 1


async def test_an_entity_without_unique_id_is_refused(hass, coordinator, make_device):
    device = make_device("F6-05-02")
    coordinator.devices[device.device_id] = device
    with (
        patch.object(
            OpusGreenNetStateBinarySensor,
            "unique_id",
            property(lambda self: None),
        ),
        pytest.raises(ValueError, match="unique ID"),
    ):
        await async_setup_entry(hass, _entry(coordinator, hass), MagicMock())
