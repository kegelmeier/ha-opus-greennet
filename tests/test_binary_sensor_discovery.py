"""Binary discovery submissions and real HA removal ordering (#57)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.opus_greennet import async_remove_config_entry_device
from custom_components.opus_greennet.binary_sensor import (
    OpusGreenNetBaseBinarySensor,
    async_setup_entry,
)
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import SIGNAL_DEVICE_DISCOVERED
from tests.ha_helpers import configure_bridge, wait_for_entity

PROFILES = [
    ("D5-00-01", 1),
    ("F6-05-01", 1),
    ("D1-4B-05", 5),
    ("D1-4B-06", 4),
    ("D1-4B-07", 3),
    ("F6-05-02", 2),
    ("A5-07-01", 1),
    ("A5-07-03", 1),
    ("D2-06-40", 4),
    ("F6-10-00", 3),
    ("D2-03-10", 3),
]


def entry_for(coordinator):
    return SimpleNamespace(
        data={"eag_id": coordinator.eag_id},
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(
            coordinator=coordinator, gateway_device_id="gateway"
        ),
        async_on_unload=MagicMock(),
    )


@pytest.mark.parametrize("eep,count", PROFILES)
async def test_repeated_discovery_submits_each_id_once(
    hass, coordinator, make_device, eep, count
):
    device = make_device(eep)
    coordinator.devices[device.device_id] = device
    entry = entry_for(coordinator)
    add = MagicMock()
    await async_setup_entry(hass, entry, add)
    # Add callback only records submissions; HA entity setup has not happened yet.
    for _ in range(5):
        async_dispatcher_send(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{coordinator.eag_id}", device
        )
    await hass.async_block_till_done()
    ids = [entity.unique_id for call in add.call_args_list for entity in call.args[0]]
    assert len(ids) == count
    assert len(set(ids)) == count
    for call in entry.async_on_unload.call_args_list:
        call.args[0]()
    async_dispatcher_send(
        hass, f"{SIGNAL_DEVICE_DISCOVERED}_{coordinator.eag_id}", device
    )
    await hass.async_block_till_done()
    assert add.call_count == 1


async def test_new_profile_entities_and_other_devices_remain_addable(
    hass, coordinator, make_device
):
    device = make_device("D1-4B-07")
    coordinator.devices[device.device_id] = device
    add = MagicMock()
    entry = entry_for(coordinator)
    await async_setup_entry(hass, entry, add)
    device.eeps = [{"eep": "D1-4B-05"}]
    for dev in (device, device, make_device("F6-05-01", device_id="OTHER")):
        async_dispatcher_send(
            hass, f"{SIGNAL_DEVICE_DISCOVERED}_{coordinator.eag_id}", dev
        )
    await hass.async_block_till_done()
    ids = [e.unique_id for call in add.call_args_list for e in call.args[0]]
    assert len(ids) == len(set(ids)) == 6
    for call in entry.async_on_unload.call_args_list:
        call.args[0]()
    # A new platform lifecycle may submit the original entities again.
    reloaded = MagicMock()
    await async_setup_entry(hass, entry, reloaded)
    assert len(reloaded.call_args.args[0]) == 5
    for call in entry.async_on_unload.call_args_list[2:]:
        call.args[0]()


async def test_rediscovery_waits_for_live_binary_removal(hass, mqtt_transport, caplog):
    definition = {
        "deviceId": "RWM",
        "eeps": [{"eep": "F6-05-02"}],
        "states": {"alarm": "off", "batteryLow": False},
    }
    mqtt_transport.devices = [definition]
    entry = (await configure_bridge(hass))["result"]
    old_id = await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM_smoke_alarm")
    coordinator = entry.runtime_data.coordinator
    device = coordinator.devices["RWM"]
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def slow_remove(self):
        if self.unique_id == "AABB0011_RWM_smoke_alarm":
            entered.set()
            await finish.wait()

    registry = dr.async_get(hass)
    child = registry.async_get_device_by_identifier(
        (DOMAIN, "AABB0011_RWM"), entry.entry_id
    )
    with patch.object(
        OpusGreenNetBaseBinarySensor, "async_will_remove_from_hass", slow_remove
    ):
        assert await async_remove_config_entry_device(hass, entry, child)
        registry.async_remove_device(child.id)
        async with asyncio.timeout(3):
            await entered.wait()
        coordinator.devices["RWM"] = device
        async_dispatcher_send(hass, "opus_greennet_device_discovered_AABB0011", device)
        # Yield to discovery callbacks while removal remains blocked.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "already exists" not in caplog.text
        finish.set()
        await hass.async_block_till_done()
    assert (
        await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM_smoke_alarm")
        == old_id
    )
    assert hass.states.get(old_id).state == "off"
    assert "does not generate unique IDs" not in caplog.text
    assert "already exists" not in caplog.text
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert (
        await wait_for_entity(hass, "binary_sensor", "AABB0011_RWM_smoke_alarm")
        == old_id
    )


async def test_gateway_discovery_and_tracking_are_isolated(hass, make_device):
    device = make_device("F6-05-01")
    entries = []
    additions = []
    for eag_id in ("AABB0011", "CCDD0022"):
        coordinator = SimpleNamespace(eag_id=eag_id, devices={device.device_id: device})
        entry = entry_for(coordinator)
        add = MagicMock()
        await async_setup_entry(hass, entry, add)
        entries.append(entry)
        additions.append(add)
    async_dispatcher_send(hass, "opus_greennet_device_discovered_AABB0011", device)
    await hass.async_block_till_done()
    assert [add.call_count for add in additions] == [1, 1]
    assert (
        additions[0].call_args.args[0][0].unique_id
        != additions[1].call_args.args[0][0].unique_id
    )
    for entry in entries:
        for call in entry.async_on_unload.call_args_list:
            call.args[0]()
