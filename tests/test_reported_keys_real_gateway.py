"""Setting entities against the keys real devices report.

``fixtures/reported_keys_by_product.json`` holds, for each product ID seen on one
installation (53 devices, gateway cache, no radio query), the EEPs and the setting
keys the gateway lists. Device names and IDs are not kept. All devices of one
product reported the same keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.number import DEVICE_TIMES, PRESENCE_SETTINGS
from custom_components.opus_greennet.select import DEVICE_SELECTS

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "reported_keys_by_product.json").read_text()
)
# The repeater mode is a gateway setting of its own and not part of the keys.
EXEMPT = {"repeaterState"}


def _device(product_id: str) -> EnOceanDevice:
    entry = FIXTURE[product_id]
    device = EnOceanDevice("DEV1", "Device", [{"eep": eep} for eep in entry["eeps"]])
    device.product_id = product_id
    device.reported_settings = frozenset(entry["keys"])
    return device


def _numbers(device: EnOceanDevice) -> set[str]:
    return {
        t.parameter for t in (*DEVICE_TIMES, *PRESENCE_SETTINGS) if t.applies_to(device)
    }


def _selects(device: EnOceanDevice) -> set[str]:
    return {s.parameter for s in DEVICE_SELECTS if s.values_for(device) is not None}


@pytest.mark.parametrize("product_id", sorted(FIXTURE))
def test_only_reported_keys_get_setting_entities(product_id: str) -> None:
    device = _device(product_id)
    created = (_numbers(device) | _selects(device)) - EXEMPT
    assert created <= set(FIXTURE[product_id]["keys"])


def test_the_1_channel_dimmer_keeps_its_three_times() -> None:
    device = _device("004000000003")
    assert not device.is_universal_dimmer
    assert _numbers(device) == {"rampingTime", "shortPress", "longPress"}


@pytest.mark.parametrize("product_id", ["005C0000000A", "004010000029", "004010000040"])
def test_devices_without_settings_get_no_setting_entities(product_id: str) -> None:
    device = _device(product_id)
    assert (_numbers(device) | _selects(device)) - EXEMPT == set()
