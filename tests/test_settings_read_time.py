"""When the settings shown for a device were last read from it.

The gateway reads the configuration of a device itself only now and then, and a
device that does not answer keeps its old values. The sensor shows the gateway's
cache time (``configuration.lastUpdateTime``) or the time of the last "Read
settings", so an old stand is visible, for example after a restore from a
backup. Nothing is requested or repeated for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.sensor import OpusGreenNetSettingsReadSensor
from tests.fake_gateway import device_object
from tests.test_coordinator_commands import (
    connected_coordinator as connected_coordinator,
)
from tests.test_coordinator_commands import radio as radio

EAG_ID = "AABB0011"
BLIND = "D2-05-02"
CACHE_TIME = "2026-10-31T04:08:51.321+0100"
CACHE_TIME_UTC = datetime(2026, 10, 31, 3, 8, 51, 321000, tzinfo=UTC)


def _configuration(**fields: object) -> dict:
    return {
        "parameters": [{"key": "maxHoldingTime", "value": 12.0}],
        **fields,
    }


# ── reading the gateway's time ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (CACHE_TIME, CACHE_TIME_UTC),
        ("2026-10-31T04:08:51", None),  # no offset: the zone is never guessed
        ("not a time", None),
        ("", None),
        (None, None),
        (1761880131, None),
    ],
)
def test_the_gateway_cache_time_is_read_or_unknown(value, expected) -> None:
    data = {"configuration": _configuration(lastUpdateTime=value)}
    assert protocol.configuration_read_time(data) == expected


@pytest.mark.parametrize("data", [{}, {"configuration": None}, {"configuration": []}])
def test_a_device_without_a_configuration_has_no_time(data) -> None:
    assert protocol.configuration_read_time(data) is None


# ── keeping the newest time ────────────────────────────────────────────────


def _device(read_at: datetime | None) -> EnOceanDevice:
    device = EnOceanDevice("DEV1", "Blind", [{"eep": BLIND}])
    device.settings_read_at = read_at
    return device


def test_a_newer_cache_time_replaces_the_known_one() -> None:
    device = _device(CACHE_TIME_UTC)
    newer = datetime(2026, 11, 1, 3, 8, tzinfo=UTC)
    device.apply_listed_metadata(_device(newer))
    assert device.settings_read_at == newer


def test_an_older_cache_time_never_replaces_a_later_read() -> None:
    read_by_button = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
    device = _device(read_by_button)
    device.apply_listed_metadata(_device(CACHE_TIME_UTC))
    assert device.settings_read_at == read_by_button


def test_a_list_without_a_time_keeps_the_known_one() -> None:
    device = _device(CACHE_TIME_UTC)
    device.apply_listed_metadata(_device(None))
    assert device.settings_read_at == CACHE_TIME_UTC


# ── coordinator ────────────────────────────────────────────────────────────


@pytest.fixture
def blind(connected_coordinator):
    device = connected_coordinator.devices["DEV1"] = EnOceanDevice(
        "DEV1", "Blind", [{"eep": BLIND}]
    )
    device.get_or_create_channel().max_holding_time = 12.0
    return device


def _list(**fields: object) -> dict:
    return {
        "devices": [
            device_object(
                "DEV1", BLIND, configuration=_configuration(**fields), lastSeen=None
            )
        ]
    }


def test_a_device_list_carries_the_cache_time_to_the_device(
    connected_coordinator, blind
) -> None:
    connected_coordinator._apply_device_list(
        _list(lastUpdateTime=CACHE_TIME), authoritative=False
    )
    assert blind.settings_read_at == CACHE_TIME_UTC


async def test_a_read_sets_the_time_of_the_read(
    radio, connected_coordinator, blind
) -> None:
    radio.response = {
        "header": {"httpStatus": 200},
        "configuration": _configuration(),
    }
    before = datetime.now(UTC)

    await connected_coordinator.async_reread_device_settings("DEV1")

    assert blind.settings_read_at is not None
    assert blind.settings_read_at >= before
    # The next device list carries the older cache time and does not move it back.
    read_at = blind.settings_read_at
    connected_coordinator._apply_device_list(
        _list(lastUpdateTime=CACHE_TIME), authoritative=False
    )
    assert blind.settings_read_at == read_at


async def test_an_answer_without_settings_leaves_the_time(
    radio, connected_coordinator, blind
) -> None:
    blind.settings_read_at = CACHE_TIME_UTC
    radio.response = {"header": {"httpStatus": 200}, "configuration": {}}

    await connected_coordinator.async_reread_device_settings("DEV1")

    assert blind.settings_read_at == CACHE_TIME_UTC


# ── sensor ─────────────────────────────────────────────────────────────────


def test_the_sensor_shows_the_time_and_is_a_diagnostic_timestamp() -> None:
    device = _device(CACHE_TIME_UTC)
    sensor = OpusGreenNetSettingsReadSensor(MagicMock(), EAG_ID, "GW", device)
    assert sensor.native_value == CACHE_TIME_UTC
    assert sensor.device_class is SensorDeviceClass.TIMESTAMP
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.unique_id == f"{EAG_ID}_DEV1_settings_read"
    # It changes about once a day, so it is enabled for every new installation.
    assert sensor.entity_registry_enabled_default is True


def test_the_sensor_is_unknown_while_no_time_is_listed() -> None:
    sensor = OpusGreenNetSettingsReadSensor(MagicMock(), EAG_ID, "GW", _device(None))
    assert sensor.native_value is None
