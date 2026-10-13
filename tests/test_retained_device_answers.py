"""Retained RPC answers must not supply leak sensor state or discovery."""

import pytest

from tests.ha_helpers import configure_bridge, wait_for_entity

BASE = "EnOcean/AABB0011"
DEVICE = {
    "deviceId": "LEAK1",
    "friendlyId": "Leak sensor",
    "eeps": [{"eep": "F6-05-01"}],
}


async def finalize(hass, coordinator):
    """Advance the discovery debounce, then publish actual HA entity state."""
    if coordinator._discovery_timer:
        coordinator._discovery_timer()
    coordinator._finalize_discovery()
    await hass.async_block_till_done()


@pytest.mark.parametrize("reconnect", [False, True])
@pytest.mark.parametrize("answer_suffix", ["", "/LEAK1"])
@pytest.mark.parametrize("sequence", ["retained_first", "fresh_first", "missing_state"])
async def test_retained_answer_cannot_supply_entity_state(
    hass, mqtt_transport, reconnect, answer_suffix, sequence
):
    mqtt_transport.devices = [dict(DEVICE, states=[])]
    result = await configure_bridge(hass)
    await hass.async_block_till_done()
    coordinator = result["result"].runtime_data.coordinator
    await finalize(hass, coordinator)
    entity_id = await wait_for_entity(
        hass, "binary_sensor", "AABB0011_LEAK1_liquid_detected"
    )
    assert hass.states.get(entity_id).state == "unknown"
    if reconnect:
        await mqtt_transport.set_connected(False)
        assert hass.states.get(entity_id).state == "unavailable"
        await mqtt_transport.set_connected(True)
        await finalize(hass, coordinator)
        assert hass.states.get(entity_id).state == "unknown"

    def answer(value, retained=False):
        states = [] if value is None else [{"key": "liquidDetected", "value": value}]
        mqtt_transport.receive(
            f"{BASE}/getAnswer/devices{answer_suffix}",
            {"header": {"httpStatus": 200}, "devices": [dict(DEVICE, states=states)]},
            retain=retained,
        )

    if sequence == "fresh_first":
        answer(True)
        await finalize(hass, coordinator)
        assert hass.states.get(entity_id).state == "on"

    answer(False, retained=True)
    await finalize(hass, coordinator)
    expected = "on" if sequence == "fresh_first" else "unknown"
    if sequence != "missing_state":
        assert hass.states.get(entity_id).state == expected
        assert coordinator._device_data["LEAK1"]["states"] != [
            {"key": "liquidDetected", "value": False}
        ]

    answer(None if sequence == "missing_state" else True)
    await finalize(hass, coordinator)
    assert hass.states.get(entity_id).state == (
        "unknown" if sequence == "missing_state" else "on"
    )


async def test_retained_answer_does_not_discover_but_retained_stream_does(
    hass, mqtt_transport
):
    result = await configure_bridge(hass)
    await hass.async_block_till_done()
    coordinator = result["result"].runtime_data.coordinator
    mqtt_transport.receive(
        f"{BASE}/getAnswer/devices",
        {"devices": [dict(DEVICE, states=[{"key": "liquidDetected", "value": False}])]},
        retain=True,
    )
    await finalize(hass, coordinator)
    assert coordinator.get_device("LEAK1") is None
    for path, value in (
        ("eeps/0/eep", "F6-05-01"),
        ("states/0/key", "liquidDetected"),
        ("states/0/value", "true"),
    ):
        mqtt_transport.receive(
            f"{BASE}/stream/devices/LEAK1/{path}", value, retain=True
        )
    await finalize(hass, coordinator)
    entity_id = await wait_for_entity(
        hass, "binary_sensor", "AABB0011_LEAK1_liquid_detected"
    )
    assert hass.states.get(entity_id).state == "on"
