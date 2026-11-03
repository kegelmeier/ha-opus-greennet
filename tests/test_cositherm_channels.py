"""Per-channel readings of the CosiTherm heating actuator (D2-34-10).

The telegram carries a ``channel`` (1 to 30). Each channel that reported gets
its own sensors, the system report (heater mode, error, channel count) stays on
the device. Read-only; the channel count of the device limits the channels.
"""

from __future__ import annotations

import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.opus_greennet.const import DOMAIN
from tests.fake_gateway import FakeGateway, device_object, telegram
from tests.ha_helpers import setup_bridge, wait_for_entity, wait_until

EEP = "D2-34-10"
EAG_ID = "AABB0011"


def _channel(number: int, **values) -> list[dict]:
    functions = [{"key": "channel", "value": str(number)}]
    functions += [{"key": key, "value": value} for key, value in values.items()]
    return functions


def test_channel_values_are_kept_per_channel(make_device, make_telegram) -> None:
    device = make_device(EEP)
    device.update_from_telegram(
        make_telegram(
            _channel(
                3,
                temperature="21.4",
                temperatureSetpoint="22.0",
                heatingCircuitState="on",
                operationMode="on",
            )
        )
    )
    device.update_from_telegram(
        make_telegram(_channel(5, temperature="19.0", operationMode="emergency"))
    )

    assert device.channels[3].readings == {
        "temperature": 21.4,
        "temperatureSetpoint": 22.0,
        "heatingCircuitState": "on",
        "operationMode": "on",
    }
    assert device.channels[5].readings == {
        "temperature": 19.0,
        "operationMode": "emergency",
    }
    assert 0 not in device.channels or not device.channels[0].readings


def test_the_channel_can_also_sit_on_each_function(make_device, make_telegram) -> None:
    device = make_device(EEP)
    device.update_from_telegram(
        make_telegram(
            [
                {"key": "temperature", "value": "20.5", "channel": 2},
                {"key": "heatingCircuitState", "value": "off", "channel": 2},
            ]
        )
    )

    assert device.channels[2].readings == {
        "temperature": 20.5,
        "heatingCircuitState": "off",
    }


def test_unavailable_channels_show_unknown(make_device, make_telegram) -> None:
    device = make_device(EEP)
    device.update_from_telegram(
        make_telegram(_channel(4, temperature="21.0", operationMode="on"))
    )
    device.update_from_telegram(
        make_telegram(
            _channel(
                4,
                temperature="unknown",
                temperatureSetpoint="notAvailable",
                operationMode="notAvailable",
            )
        )
    )

    readings = device.channels[4].readings
    assert readings["temperature"] is None
    assert readings["temperatureSetpoint"] is None
    assert readings["operationMode"] is None


def test_malformed_channel_values_keep_the_known_one(
    make_device, make_telegram
) -> None:
    device = make_device(EEP)
    device.update_from_telegram(
        make_telegram(_channel(1, temperature="21.0", heatingCircuitState="on"))
    )
    for bad in ("41", "-1", "abc", None, True, {}):
        device.update_from_telegram(
            make_telegram(_channel(1, temperature=bad, heatingCircuitState=bad))
        )
    assert device.channels[1].readings == {
        "temperature": 21.0,
        "heatingCircuitState": "on",
    }


def test_all_channels_is_no_single_channel(make_device, make_telegram) -> None:
    device = make_device(EEP)
    device.update_from_telegram(
        make_telegram(_channel("all", temperature="21.0", operationMode="on"))  # type: ignore[arg-type]
    )

    assert not any(channel.readings for channel in device.channels.values())


def test_the_system_report_stays_on_the_device(make_device, make_telegram) -> None:
    device = make_device(EEP)
    device.update_from_telegram(
        make_telegram(
            [
                {"key": "channelNumber", "value": "6"},
                {"key": "error", "value": "noError"},
                {"key": "heaterMode", "value": "heating"},
            ]
        )
    )

    assert device.channels[0].readings == {
        "channelNumber": 6.0,
        "error": "noError",
        "heaterMode": "heating",
    }


async def test_channels_get_their_entities_when_they_report(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("COSI1", EEP)]
    entry = await setup_bridge(hass, gateway)
    registry = er.async_get(hass)

    def unique_ids() -> set[str]:
        return {
            entity.unique_id
            for entity in registry.entities.values()
            if entity.platform == DOMAIN
            and entity.unique_id.startswith(f"{EAG_ID}_COSI1_")
        }

    # The system report is there, no channel yet.
    await wait_for_entity(hass, "sensor", f"{EAG_ID}_COSI1_heater_mode")
    assert not any(re.search(r"_channel_\d+_", uid) for uid in unique_ids())

    await gateway.push(telegram("COSI1", [{"key": "channelNumber", "value": "2"}]))
    await gateway.push(
        telegram(
            "COSI1",
            _channel(
                2, temperature="21.5", heatingCircuitState="on", operationMode="on"
            ),
        )
    )
    await wait_until(lambda: f"{EAG_ID}_COSI1_channel_2_temperature" in unique_ids())
    assert {
        f"{EAG_ID}_COSI1_channel_2_{suffix}"
        for suffix in (
            "temperature",
            "temperature_setpoint",
            "circuit_state",
            "operation_mode",
        )
    } <= unique_ids()
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{EAG_ID}_COSI1_channel_2_temperature"
    )
    await wait_until(lambda: hass.states.get(entity_id).state == "21.5")
    assert (
        hass.states.get(entity_id)
        .attributes["friendly_name"]
        .endswith("Channel 2 temperature")
    )

    # Channel 5 lies beyond the device's channel count: no entities.
    await gateway.push(telegram("COSI1", _channel(5, temperature="18.0")))
    await hass.async_block_till_done()
    assert f"{EAG_ID}_COSI1_channel_5_temperature" not in unique_ids()
    assert entry.runtime_data.coordinator.devices["COSI1"].channels[5].readings


async def test_known_channels_get_their_entities_back_after_a_restart(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("COSI1", EEP)]
    entry = await setup_bridge(hass, gateway)
    registry = er.async_get(hass)
    await gateway.push(telegram("COSI1", [{"key": "channelNumber", "value": "4"}]))
    await gateway.push(
        telegram("COSI1", _channel(3, temperature="20.5", heatingCircuitState="on"))
    )
    unique_id = f"{EAG_ID}_COSI1_channel_3_temperature"
    entity_id = await wait_for_entity(hass, "sensor", unique_id)
    await wait_until(lambda: hass.states.get(entity_id).state == "20.5")

    # A restart: the new coordinator has seen no telegram of any channel yet.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unknown"
    assert "restored" not in state.attributes
    for suffix in (
        "temperature_setpoint",
        "circuit_state",
        "operation_mode",
    ):
        other = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{EAG_ID}_COSI1_channel_3_{suffix}"
        )
        assert hass.states.get(other).state == "unknown"

    # The next report fills the same entity.
    await gateway.push(telegram("COSI1", _channel(3, temperature="21.0")))
    await wait_until(lambda: hass.states.get(entity_id).state == "21.0")


async def test_a_removed_channel_is_not_brought_back(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("COSI1", EEP)]
    entry = await setup_bridge(hass, gateway)
    registry = er.async_get(hass)
    await gateway.push(telegram("COSI1", _channel(1, temperature="20.5")))
    entity_id = await wait_for_entity(
        hass, "sensor", f"{EAG_ID}_COSI1_channel_1_temperature"
    )
    for entry_ in list(registry.entities.values()):
        if "_COSI1_channel_1_" in entry_.unique_id:
            registry.async_remove(entry_.entity_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) is None

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, f"{EAG_ID}_COSI1_channel_1_temperature"
        )
        is None
    )
