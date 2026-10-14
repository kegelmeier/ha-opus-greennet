"""Phase 5: typed MQTT/JSON boundaries keep the existing wire contract.

These tests pin the behaviour of the typed boundary helpers and parsers:
valid string and numeric representations, channel zero, ``notAvailable``,
proprietary HeatArea profiles and unknown additive fields are accepted
exactly as before, while wrong container/field types, booleans in numeric
fields, NaN/Infinity and malformed JSON are rejected per field.

They are unit/mock tests. They do not verify real OPUS hardware.
"""

from __future__ import annotations

import json
import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.enocean_device import EnOceanDevice
from custom_components.opus_greennet.mqtt_transport import (
    decode_response,
    response_status,
)
from custom_components.opus_greennet.wire import (
    as_list,
    as_object,
    indexed_objects,
    object_items,
    parse_json,
    payload_text,
    string_key,
)


@pytest.fixture
def coord(monkeypatch: pytest.MonkeyPatch) -> OpusGreenNetCoordinator:
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_call_later", MagicMock()
    )
    monkeypatch.setattr(
        "custom_components.opus_greennet.coordinator.async_dispatcher_send",
        MagicMock(),
    )
    return OpusGreenNetCoordinator(MagicMock(), "AABB0011")


def message(topic: str, payload: str | bytes) -> SimpleNamespace:
    return SimpleNamespace(topic=topic, payload=payload, retain=False, qos=0)


def apply(eep: str, *functions: dict[str, object]) -> EnOceanDevice:
    device = EnOceanDevice("DEV", "Device", [{"eep": eep}])
    device.update_from_telegram({"functions": list(functions)})
    return device


# ── Wire helpers ────────────────────────────────────────────────────────────


def test_payload_text_decodes_bytes_and_keeps_text():
    assert payload_text("on") == "on"
    assert payload_text(b"on") == "on"
    assert payload_text(bytearray(b"50")) == "50"


def test_parse_json_keeps_non_finite_literals_for_field_validation():
    # json.loads accepts NaN/Infinity; per-field parsers reject them later.
    decoded = parse_json('{"v": NaN}')
    assert isinstance(decoded, dict)
    assert isinstance(decoded["v"], float)
    assert math.isnan(decoded["v"])
    assert parse_json('[1, 2.5, "x", true, null]') == [1, 2.5, "x", True, None]
    with pytest.raises(json.JSONDecodeError):
        parse_json("{")


def test_container_helpers_reject_wrong_container_types():
    assert as_object({"a": 1}) == {"a": 1}
    assert as_object([("a", 1)]) is None
    assert as_object("{}") is None
    assert as_object({1: "x"}) is None
    assert as_list([1]) == [1]
    assert as_list({"0": 1}) is None
    assert object_items([{"a": 1}, "x", None, 5, [], {"b": 2}]) == [
        {"a": 1},
        {"b": 2},
    ]
    assert object_items({"0": {"a": 1}}) == []


def test_indexed_objects_orders_flattened_indices_numerically():
    mapping = {"10": {"i": 10}, "2": {"i": 2}, "x": {"i": "x"}, "1": "skip"}
    assert indexed_objects(mapping) == [{"i": 2}, {"i": 10}, {"i": "x"}]
    assert indexed_objects([{"i": 0}, 1]) == [{"i": 0}]
    assert indexed_objects("x") == []


def test_string_key_only_returns_strings():
    data = {"s": "notAvailable", "n": 5, "b": True, "none": None}
    assert string_key(data, "s") == "notAvailable"
    assert string_key(data, "n") is None
    assert string_key(data, "b") is None
    assert string_key(data, "missing") is None


# ── Acknowledgement status contract ─────────────────────────────────────────


@pytest.mark.parametrize("status", [200, 201, 200.0, "200", "201", " 200 "], ids=repr)
def test_valid_status_representations_are_accepted(status):
    payload = json.dumps({"header": {"httpStatus": status}, "extra": {"x": 1}})
    data = decode_response(payload, require_status=True)
    assert data["extra"] == {"x": 1}
    assert response_status(data) in (200, 201)


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ('{"header": {"httpStatus": true}}', "invalid status"),
        ('{"header": {"httpStatus": null}}', "invalid status"),
        ('{"header": {"httpStatus": "200.0"}}', "invalid status"),
        ('{"header": {"httpStatus": 200.5}}', "invalid status"),
        ('{"header": {"httpStatus": NaN}}', "invalid status"),
        ('{"header": {"httpStatus": Infinity}}', "invalid status"),
        ('{"header": {"httpStatus": [200]}}', "invalid status"),
        ('{"header": {"httpStatus": 400}}', "status 400"),
        ('{"header": "200"}', "missing its status"),
        ('{"header": {}}', "invalid status"),
        ("[]", "invalid response object"),
        ("{}", "invalid response object"),
        ('"text"', "invalid response object"),
        ("{", "invalid JSON"),
        ("", "invalid JSON"),
    ],
)
def test_invalid_status_container_and_json_are_rejected(raw, error):
    with pytest.raises(ValueError, match=error):
        decode_response(raw, require_status=True)


def test_status_is_optional_without_header_and_bytes_are_accepted():
    assert decode_response('{"device": {"deviceId": "D"}}') == {
        "device": {"deviceId": "D"}
    }
    assert decode_response(b'{"header": {"httpStatus": 201}}') == {
        "header": {"httpStatus": 201}
    }
    assert decode_response(bytearray(b'{"a": 1}')) == {"a": 1}
    with pytest.raises(ValueError, match="missing its status"):
        decode_response('{"a": 1}', require_status=True)
    with pytest.raises(ValueError, match="invalid JSON"):
        decode_response(b"\xff")


# ── Valid fixtures of all supported profile families ────────────────────────


@pytest.mark.parametrize(
    ("eep", "function", "attribute", "expected"),
    [
        ("D2-01-00", {"key": "switch", "value": "on"}, "is_on", True),
        ("D2-01-04", {"key": "switch", "value": "off"}, "is_on", False),
        ("D2-01-02", {"key": "dimValue", "value": "50"}, "brightness", 50),
        ("A5-38-08", {"key": "dimValue", "value": 50.0}, "brightness", 50),
        ("D2-01-01", {"key": "power", "value": "12.5"}, "power", 12.5),
        ("D2-05-00", {"key": "position", "value": "30"}, "position", 30),
        ("D2-05-00", {"key": "angle", "value": 20}, "angle", 20),
        ("D2-05-01", {"key": "position", "value": "0"}, "position", 0),
        ("D1-4B-05", {"key": "temperature", "value": "21.5"}, "temperature", 21.5),
        (
            "D1-4B-06",
            {"key": "temperatureSetpoint", "value": 22},
            "temperature_setpoint",
            22.0,
        ),
        (
            "D1-4B-07",
            {"key": "heaterMode", "value": "heating"},
            "heater_mode",
            "heating",
        ),
        (
            "F6-02-01",
            {"key": "buttonA0", "value": "pressed"},
            "last_button",
            "buttonA0",
        ),
        (
            "F6-03-01",
            {"key": "buttonBI", "value": "released"},
            "last_button_action",
            "released",
        ),
        ("F6-05-02", {"key": "smokeAlarm", "value": "on"}, "smoke_alarm", True),
        ("A5-07-01", {"key": "motionDetected", "value": True}, "motion", True),
        ("A5-07-03", {"key": "illumination", "value": "200"}, "illuminance", 200.0),
        (
            "A5-07-01",
            {"key": "batteryLevel", "value": " 82.5 % "},
            "battery_level",
            82.5,
        ),
        (
            "D2-06-40",
            {"key": "handleState", "value": "tilted"},
            "handle_state",
            "tilted",
        ),
        ("F6-10-00", {"key": "handle", "value": "closed"}, "handle_state", "closed"),
        ("D2-03-10", {"key": "handle", "value": "open"}, "handle_state", "open"),
        (
            "F6-05-01",
            {"key": "liquidDetected", "value": "true"},
            "liquid_detected",
            True,
        ),
    ],
)
def test_valid_profile_fixture_updates_domain_model(eep, function, attribute, expected):
    device = apply(eep, function)
    assert getattr(device.channels[0], attribute) == expected


@pytest.mark.parametrize(
    ("eep", "heat_area_type", "step"),
    [
        ("D1-4B-05", "valve", 0.5),
        ("D1-4B-06", "cositherm", 0.1),
        ("D1-4B-07", "electro", 0.1),
    ],
)
def test_proprietary_heat_area_profiles_are_preserved(eep, heat_area_type, step):
    device = EnOceanDevice("HA", "Heat area", [{"eep": eep}])
    assert device.is_climate
    assert device.heat_area_type == heat_area_type
    assert device.setpoint_step == step


@pytest.mark.parametrize("value", ["50", 50, 50.0, " 50 "], ids=repr)
def test_equivalent_numeric_representations(value):
    assert (
        apply("D2-01-02", {"key": "dimValue", "value": value}).channels[0].brightness
        == 50
    )


@pytest.mark.parametrize("channel", [0, "0", 0.0], ids=repr)
def test_channel_zero_representations_select_channel_zero(channel):
    device = apply("D2-01-04", {"key": "switch", "value": "on", "channel": channel})
    assert set(device.channels) == {0}
    assert device.channels[0].is_on is True


def test_channel_selector_function_and_explicit_channel():
    device = apply(
        "D2-01-04",
        {"key": "channel", "value": "1"},
        {"key": "switch", "value": "on"},
    )
    assert device.channels[1].is_on is True
    device = apply("D2-01-04", {"key": "switch", "value": "off", "channel": 1})
    assert device.channels[1].is_on is False


@pytest.mark.parametrize("channel", [-1, "x", True, 1.5, None, [], {}], ids=repr)
def test_malformed_channel_is_ignored_instead_of_channel_zero(channel):
    device = apply("D2-01-04", {"key": "switch", "value": "on", "channel": channel})
    assert device.channels == {}


# ── Wrong types, booleans, non-finite numbers ───────────────────────────────


@pytest.mark.parametrize(
    "value",
    [True, False, None, float("nan"), float("inf"), "NaN", "Infinity", [], {}, "x"],
    ids=repr,
)
def test_invalid_numeric_values_keep_previous_reading(value):
    device = apply("D2-01-02", {"key": "dimValue", "value": "40"})
    device.update_from_telegram({"functions": [{"key": "dimValue", "value": value}]})
    assert device.channels[0].brightness == 40


@pytest.mark.parametrize(
    "value", [True, "NaN", "-Infinity", float("nan"), [], {"v": 1}], ids=repr
)
def test_invalid_temperature_is_rejected(value):
    device = apply("D1-4B-05", {"key": "temperature", "value": "21.5"})
    device.update_from_telegram({"functions": [{"key": "temperature", "value": value}]})
    assert device.channels[0].temperature == 21.5


@pytest.mark.parametrize("functions", [None, "x", 5, [], ["str", None, 5]], ids=repr)
def test_wrong_function_containers_are_ignored(functions):
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-00"}])
    device.update_from_telegram({"functions": functions})
    assert device.channels == {}


def test_single_function_object_and_mixed_list_are_tolerated():
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-00"}])
    device.update_from_telegram({"functions": {"key": "switch", "value": "on"}})
    assert device.channels[0].is_on is True
    device.update_from_telegram(
        {"functions": ["junk", None, {"key": "switch", "value": "off"}]}
    )
    assert device.channels[0].is_on is False


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"dbm": -70}, -70),
        ({"dbm": "-70"}, -70),
        ({"dbm": -70.0}, -70),
        ({"dbm": -70.5}, -60),
        ({"dbm": True}, -60),
        ({"dbm": "NaN"}, -60),
        ("not an object", -60),
        ([], -60),
    ],
    ids=repr,
)
def test_telegram_info_dbm_is_validated(info, expected):
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-00"}], dbm=-60)
    device.update_from_telegram({"functions": [], "telegramInfo": info})
    assert device.dbm == expected


@pytest.mark.parametrize(
    ("timestamp", "expected"), [("T1", "T1"), (5, ""), (["T"], "")]
)
def test_timestamp_must_be_text(timestamp, expected):
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-00"}])
    device.update_from_telegram({"functions": [], "timestamp": timestamp})
    assert device.last_seen == expected


# ── notAvailable and unknown additive fields ────────────────────────────────


@pytest.mark.parametrize(
    ("eep", "key", "attribute", "initial", "expected"),
    [
        # Contract (protocol-contract.md section 4): measurements clear...
        ("D1-4B-05", "temperature", "temperature", "21.5", None),
        ("D1-4B-06", "humidity", "humidity", "40", None),
        ("A5-07-01", "batteryLevel", "battery_level", "80", None),
        ("A5-07-01", "illumination", "illuminance", "200", None),
        # ...while actuator states ignore notAvailable and keep the reading.
        ("D2-05-00", "position", "position", "30", 30),
        ("D2-01-02", "dimValue", "brightness", "40", 40),
        ("D2-01-00", "switch", "is_on", "on", True),
    ],
)
def test_not_available_follows_the_existing_contract(
    eep, key, attribute, initial, expected
):
    device = apply(eep, {"key": key, "value": initial})
    device.update_from_telegram({"functions": [{"key": key, "value": "notAvailable"}]})
    assert getattr(device.channels[0], attribute) == expected


def test_unknown_additive_fields_are_ignored_not_rejected():
    device = EnOceanDevice("DEV", "Device", [{"eep": "D2-01-02", "future": 1}])
    device.update_from_telegram(
        {
            "functions": [
                {"key": "dimValue", "value": "60", "unit": "%", "meta": {"x": [1]}},
                {"key": "futureKey", "value": {"nested": True}},
            ],
            "telegramInfo": {"dbm": -55, "repeater": 2},
            "futureTopLevel": [1, 2],
        }
    )
    assert device.channels[0].brightness == 60
    assert device.dbm == -55
    assert device.primary_eep == "D2-01-02"


def test_primary_eep_requires_text():
    assert EnOceanDevice("D", "N", [{"eep": 5}]).primary_eep is None
    assert EnOceanDevice("D", "N", []).primary_eep is None
    assert EnOceanDevice("D", "N", [{"eep": 5}]).heat_area_type is None


def test_from_device_object_tolerates_wrapper_and_wrong_field_types():
    device = EnOceanDevice.from_device_object(
        {
            "device": {
                "deviceId": "X",
                "friendlyId": 5,
                "eeps": [{"eep": "D1-4B-06"}, "junk"],
                "dbm": "-40",
                "manufacturer": None,
                "unknown": {"a": 1},
            }
        }
    )
    assert device.device_id == "X"
    assert device.friendly_id == ""
    assert device.eeps == [{"eep": "D1-4B-06"}]
    assert device.dbm == -40
    assert device.manufacturer == ""
    assert device.heat_area_type == "cositherm"


# ── Coordinator MQTT boundaries ─────────────────────────────────────────────


def test_flattened_discovery_keeps_string_and_numeric_representations(coord):
    base = "EnOcean/AABB0011/stream/devices/DEV"
    for path, payload in (
        ("eeps/0/eep", "D2-05-00"),
        ("friendlyId", "123"),
        ("manufacturer", "OPUS"),
        ("dbm", "-61"),
        ("states/0/key", "position"),
        ("states/0/value", "notAvailable"),
        ("states/1/key", "angle"),
        ("states/1/value", "40"),
        ("futureField", "kept"),
    ):
        coord._handle_device_property_message(message(f"{base}/{path}", payload))
    coord._finalize_discovery()
    device = coord.devices["DEV"]
    # A numeric name arrives parsed as a number and is rendered as text again.
    assert device.friendly_id == "123"
    assert device.manufacturer == "OPUS"
    assert device.dbm == -61
    assert device.channels[0].position is None
    assert device.channels[0].angle == 40
    assert coord._device_data["DEV"]["futureField"] == "kept"


def test_flattened_path_conflicts_are_dropped_like_before(coord):
    data: dict[str, object] = {"deviceId": "D", "dbm": 5}
    with pytest.raises(ValueError, match="conflicts"):
        coord._set_nested_property(data, "dbm/value", "1")  # scalar mid-path
    data["eeps"] = [{"eep": "D2-01-00"}]
    with pytest.raises(ValueError, match="conflicts"):
        coord._set_nested_property(data, "eeps/eep", "1")  # key into a list
    assert data == {"deviceId": "D", "dbm": 5, "eeps": [{"eep": "D2-01-00"}]}
    # A numeric property below an object is kept under its index text.
    coord._set_nested_property(data, "0", "7")
    assert data["0"] == 7


@pytest.mark.parametrize(
    "payload",
    [
        '{"functions": [{"key": "dimValue", "value": "50"}]}',
        '{"functions": [{"key": "dimValue", "value": 50}]}',
        '{"telegram": {"functions": [{"key": "dimValue", "value": 50.0}]}}',
        '{"state": {"functions": [{"key": "dimValue", "value": "50"}]}}',
        '{"functions": {"0": {"key": "dimValue", "value": "50"}}, "future": 1}',
        b'{"functions": [{"key": "dimValue", "value": "50", "unit": "%"}]}',
    ],
)
def test_complete_telegram_representations(coord, payload):
    coord.devices["DEV"] = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}])
    coord._handle_telegram_property_message(
        message("EnOcean/AABB0011/stream/telegram/DEV/from", payload)
    )
    assert coord.devices["DEV"].channels[0].brightness == 50


@pytest.mark.parametrize(
    "payload",
    ["not json", "[]", '"text"', "5", '{"telegram": 5}', '{"functions": "x"}', "{"],
)
def test_malformed_complete_telegram_is_ignored(coord, payload):
    coord.devices["DEV"] = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}])
    coord._handle_telegram_property_message(
        message("EnOcean/AABB0011/stream/telegram/DEV/from", payload)
    )
    assert coord.devices["DEV"].channels == {}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('[{"deviceId": "A1", "eeps": [{"eep": "D2-01-00"}]}]', {"A1"}),
        (
            json.dumps(
                {
                    "devices": [
                        {"device": {"deviceId": "A2", "eeps": [{"eep": "D1-4B-05"}]}}
                    ]
                }
            ),
            {"A2"},
        ),
        ('{"deviceId": "A3", "eeps": [{"eep": "D2-05-00"}], "future": {}}', {"A3"}),
        (
            '{"header": {"httpStatus": 200}, "devices": [{"deviceId": "A4"}]}',
            {"A4"},
        ),
        ('{"header": {"httpStatus": 500}, "devices": [{"deviceId": "A5"}]}', set()),
        ('{"devices": "x"}', set()),
        ('[1, "x", null, {"device": "str"}]', set()),
        ("not json", set()),
        ('"text"', set()),
    ],
)
def test_get_answer_devices_validates_containers(coord, payload, expected):
    coord._handle_get_answer_devices(
        message("EnOcean/AABB0011/getAnswer/devices", payload)
    )
    coord._finalize_discovery()
    assert set(coord.devices) == expected


@pytest.mark.parametrize(
    ("payload", "accepted"),
    [
        ('{"header": {"httpStatus": 200}}', True),
        ('{"header": {"httpStatus": "201"}}', True),
        ('{"header": {"httpStatus": 200.0}, "future": 1}', True),
        ('{"header": {"httpStatus": true}}', False),
        ('{"header": {"httpStatus": 409}}', False),
        ('{"header": "x"}', False),
        ("not json", False),
    ],
)
def test_put_answer_state_status_contract(coord, payload, accepted):
    device = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}])
    device.last_command_error = "previous"
    coord.devices["DEV"] = device
    coord._handle_put_answer_state(
        message("EnOcean/AABB0011/putAnswer/devices/DEV/state", payload)
    )
    assert (device.last_command_error is None) is accepted


@pytest.mark.parametrize(
    ("payload", "expected"),
    [("-55", -55), ("-55.0", -55), ('"-55"', -55), ("NaN", -70), ("true", -70)],
)
def test_scalar_dbm_telemetry_boundary(coord, payload, expected):
    coord.devices["DEV"] = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}], dbm=-70)
    coord._handle_device_stream_message(
        message("EnOcean/AABB0011/stream/device/DEV/dbm", payload)
    )
    assert coord.devices["DEV"].dbm == expected


def test_scalar_not_available_clears_dbm(coord):
    coord.devices["DEV"] = EnOceanDevice("DEV", "Light", [{"eep": "D2-01-02"}], dbm=-70)
    coord._handle_device_stream_message(
        message("EnOcean/AABB0011/stream/device/DEV/dbm", "notAvailable")
    )
    assert coord.devices["DEV"].dbm is None
