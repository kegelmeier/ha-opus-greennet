"""The D2-01-03 parameter set of gateway firmware 1.21.34 (universal dimmer)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from homeassistant.const import PERCENTAGE

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.const import DEVICE_OPTIONS, DEVICE_SETTINGS
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import DIMMER_TIMES, OpusGreenNetTimeNumber
from custom_components.opus_greennet.select import (
    DEVICE_SELECTS,
    OpusGreenNetDeviceSelect,
    option_slug,
)

EAG_ID = "AABB0011"
FIXTURES = Path(__file__).parent / "fixtures"
RAMPS = ("ramping_time_1", "ramping_time_2", "ramping_time_3", "ramping_time_switch")
LIMITS = ("lower_limit", "upper_limit")


def _time(translation_key: str):
    return next(t for t in DIMMER_TIMES if t.translation_key == translation_key)


UNIVERSAL = "00400000001E"  # product ID of the universal dimmer 563.076
OLD_DIMMER = "004000000003"  # product ID of the 1 channel dimmer from 2017
OLD_SET = ("ramping_time", "short_press_time", "long_press_time")


def _fed_device() -> EnOceanDevice:
    document = json.loads(
        (FIXTURES / "configuration_d2_01_03_fw1_21_34.json").read_text("utf-8")
    )
    device = EnOceanDevice("DEV1", "Test", [{"eep": "D2-01-03"}], product_id=UNIVERSAL)
    device.update_from_telegram(
        {"functions": protocol.configuration_state_functions(document)}
    )
    return device


def test_the_configuration_feeds_the_channel_fields() -> None:
    channel = _fed_device().channels[0]
    assert (
        channel.ramping_time_1,
        channel.ramping_time_2,
        channel.ramping_time_3,
        channel.ramping_time_switch,
    ) == (4.0, 3.0, 20.0, 0.5)
    assert (channel.lower_limit, channel.upper_limit) == (25.0, 80.0)
    assert channel.dimmer_mode == "phaseControlLower"
    # The old parameter set is not reported by this firmware: stays unknown.
    assert (channel.ramping_time, channel.short_press_time) == (None, None)
    assert channel.long_press_time is None


@pytest.mark.parametrize("name", RAMPS)
def test_ramp_numbers_follow_the_catalog_range(name: str) -> None:
    entity = OpusGreenNetTimeNumber(
        MagicMock(), EAG_ID, "gateway", _fed_device(), _time(name)
    )
    assert (entity.native_min_value, entity.native_max_value) == (0, 6553)
    assert entity.native_step == 0.1
    assert entity.native_unit_of_measurement == "s"
    assert entity.native_value is not None


@pytest.mark.parametrize("name", LIMITS)
def test_limit_numbers_are_percent_between_15_and_80(name: str) -> None:
    entity = OpusGreenNetTimeNumber(
        MagicMock(), EAG_ID, "gateway", _fed_device(), _time(name)
    )
    assert (entity.native_min_value, entity.native_max_value) == (15, 80)
    assert entity.native_step == 1
    assert entity.native_unit_of_measurement == PERCENTAGE
    assert entity.device_class is None


def test_every_new_number_writes_its_catalog_key() -> None:
    keys = {
        "ramping_time_1": "rampingTimeRocker1",
        "ramping_time_2": "rampingTimeRocker2",
        "ramping_time_3": "rampingTimeRocker3",
        "ramping_time_switch": "rampingTimeSwitch",
        "lower_limit": "lowerLimit",
        "upper_limit": "upperLimit",
    }
    for name, key in keys.items():
        assert _time(name).parameter == key
        assert key in DEVICE_SETTINGS


def test_the_dimmer_mode_select_offers_the_catalog_values() -> None:
    assert DEVICE_OPTIONS["dimmerMode"] == "dimmer_mode"
    select = next(s for s in DEVICE_SELECTS if s.parameter == "dimmerMode")
    device = _fed_device()
    values = select.values_for(device)
    assert values == ("phaseControlLower", "phaseControlUpper")
    entity = OpusGreenNetDeviceSelect(
        MagicMock(), EAG_ID, "gateway", device, select, values
    )
    assert entity.options == [option_slug(v) for v in values]
    assert entity.current_option == option_slug("phaseControlLower")
    # Other dimmer-less profiles get no such select.
    assert select.values_for(EnOceanDevice("DEV2", "S", [{"eep": "D2-01-01"}])) is None


def _entity_names(product_id: str) -> tuple[set[str], bool]:
    device = EnOceanDevice("DEV1", "D", [{"eep": "D2-01-03"}], product_id=product_id)
    numbers = {t.translation_key for t in DIMMER_TIMES if t.applies_to(device)}
    select = next(s for s in DEVICE_SELECTS if s.parameter == "dimmerMode")
    return numbers, select.values_for(device) is not None


@pytest.mark.parametrize(
    "product_id", [UNIVERSAL, UNIVERSAL.lower(), "00400000001B", "00400000001b"]
)
def test_the_universal_dimmer_gets_only_the_new_set(product_id: str) -> None:
    numbers, has_mode = _entity_names(product_id)
    assert numbers == {*RAMPS, *LIMITS}
    assert has_mode


@pytest.mark.parametrize("product_id", [OLD_DIMMER, "", "004000000099"])
def test_every_other_dimmer_keeps_the_old_set(product_id: str) -> None:
    numbers, has_mode = _entity_names(product_id)
    assert numbers == set(OLD_SET)
    assert not has_mode
