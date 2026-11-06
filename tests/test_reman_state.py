"""Remote management (ReMan) state of a device as a diagnostic sensor.

The gateway lists ``remanState`` for each device. The sensor shows it as an
enum so that a failing *Read settings* can be explained. A value the
integration does not know is unknown, a list without the field keeps the state
that is known, and nothing is requested for it.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.opus_greennet import protocol
from custom_components.opus_greennet.const import REMAN_STATES
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.sensor import OpusGreenNetRemanStateSensor
from tests.fake_gateway import device_object

MODULE = "custom_components.opus_greennet.coordinator"
EAG_ID = "AABB0011"
DEVICE = "0507C89C"


@pytest.fixture
def coord() -> Iterator[OpusGreenNetCoordinator]:
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), EAG_ID)
    coordinator._gateway_available = True  # noqa: SLF001
    with patch(f"{MODULE}.async_dispatcher_send"):
        yield coordinator


def _list(**fields: object) -> dict:
    return {
        "header": {"content": "devices"},
        "devices": [device_object(DEVICE, "D2-05-02", **fields)],
    }


# ── reading the gateway's value ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("wire", "option"),
    [
        ("NotSeen", "not_seen"),
        ("NoResponse", "no_response"),
        ("SuccessfullLearnedIn", "learned_in"),
        ("UnsupportedEEP", "unsupported_eep"),
        ("QueryStarting", "query_starting"),
        ("QueryInProgress", "query_in_progress"),
        ("SuccessfullQueried", "queried"),
        ("UnsupportedProductId", "unsupported_product_id"),
        ("InvalidProductId", "invalid_product_id"),
        ("UnlockFailed", "unlock_failed"),
        ("WrongSecureCode", "wrong_secure_code"),
    ],
)
def test_every_state_of_the_gateway_has_an_option(wire, option) -> None:
    assert protocol.reman_state({"remanState": wire}) == option
    assert option in REMAN_STATES.values()


def test_the_table_holds_exactly_the_known_states() -> None:
    assert len(REMAN_STATES) == 11
    assert len(set(REMAN_STATES.values())) == 11


@pytest.mark.parametrize(
    "value",
    ["", "notseen", "SomethingNew", "WrongSecureCode ", None, 3, True, [], {}],
)
def test_an_unknown_or_malformed_value_is_unknown(value) -> None:
    assert protocol.reman_state({"remanState": value}) is None


def test_a_device_without_the_field_is_unknown() -> None:
    assert protocol.reman_state({}) is None


# ── device list ────────────────────────────────────────────────────────────


def test_the_device_list_sets_the_state(coord) -> None:
    coord._apply_device_list(  # noqa: SLF001
        _list(remanState="WrongSecureCode"), authoritative=True
    )
    assert coord.devices[DEVICE].reman_state == "wrong_secure_code"


def test_a_later_list_replaces_the_state_in_the_same_object(coord) -> None:
    coord._apply_device_list(  # noqa: SLF001
        _list(remanState="NoResponse"), authoritative=True
    )
    device = coord.devices[DEVICE]

    coord._apply_device_list(  # noqa: SLF001
        _list(remanState="SuccessfullQueried"), authoritative=True
    )

    assert coord.devices[DEVICE] is device
    assert device.reman_state == "queried"


@pytest.mark.parametrize("later", [{}, {"remanState": "SomethingNew"}])
def test_a_list_without_a_usable_state_keeps_the_known_one(coord, later) -> None:
    """A missing field is not an instruction to forget the state."""
    coord._apply_device_list(  # noqa: SLF001
        _list(remanState="NoResponse"), authoritative=True
    )

    coord._apply_device_list(_list(**later), authoritative=True)  # noqa: SLF001

    assert coord.devices[DEVICE].reman_state == "no_response"


def test_a_device_without_any_state_stays_unknown(coord) -> None:
    coord._apply_device_list(_list(), authoritative=True)  # noqa: SLF001
    assert coord.devices[DEVICE].reman_state is None


# ── sensor ─────────────────────────────────────────────────────────────────


def _sensor(state: str | None) -> OpusGreenNetRemanStateSensor:
    device = EnOceanDevice("DEV1", "Device", [{"eep": "D2-05-02"}])
    device.reman_state = state
    return OpusGreenNetRemanStateSensor(MagicMock(), EAG_ID, "GW", device)


def test_the_sensor_shows_the_state() -> None:
    assert _sensor("wrong_secure_code").native_value == "wrong_secure_code"


def test_the_sensor_is_unknown_without_a_state() -> None:
    assert _sensor(None).native_value is None


def test_the_sensor_metadata_and_default() -> None:
    sensor = _sensor("queried")
    assert sensor.unique_id == f"{EAG_ID}_DEV1_reman_state"
    assert sensor.translation_key == "reman_state"
    assert sensor.device_class is SensorDeviceClass.ENUM
    assert sensor.options == list(REMAN_STATES.values())
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    # It matters only when settings cannot be read: enabled on request.
    assert sensor.entity_registry_enabled_default is False


def test_every_state_is_translated_and_has_an_icon_entry() -> None:
    import json
    from pathlib import Path

    base = Path(__file__).parents[1] / "custom_components" / "opus_greennet"
    options = set(REMAN_STATES.values())
    for name in ("strings.json", "translations/en.json", "translations/de.json"):
        data = json.loads((base / name).read_text(encoding="utf-8"))
        entry = data["entity"]["sensor"]["reman_state"]
        assert set(entry["state"]) == options, name
    icons = json.loads((base / "icons.json").read_text(encoding="utf-8"))
    icon_states = icons["entity"]["sensor"]["reman_state"]["state"]
    assert set(icon_states) <= options
