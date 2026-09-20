"""Tests for EnOceanDevice data model."""

from __future__ import annotations

import pytest

from custom_components.opus_greennet.enocean_device import (
    EnOceanChannel,
    EnOceanDevice,
)

# ── Device Properties ──────────────────────────────────────────────────


class TestDeviceProperties:
    """Tests for computed properties on EnOceanDevice."""

    def test_primary_eep_returns_first(self, make_device):
        dev = make_device("D2-01-02")
        assert dev.primary_eep == "D2-01-02"

    def test_primary_eep_empty_returns_none(self):
        dev = EnOceanDevice(device_id="X", friendly_id="X", eeps=[])
        assert dev.primary_eep is None

    @pytest.mark.parametrize(
        "eep,expected",
        [
            ("D2-01-00", "switch"),
            ("D2-01-01", "switch"),
            ("D2-01-02", "light"),
            ("D2-01-03", "light"),
            ("D2-01-04", "switch"),
            ("D2-01-06", "light"),
            ("D2-01-11", "switch"),
            ("D2-01-12", "light"),
            ("D2-05-00", "cover"),
            ("D2-05-01", "cover"),
            ("D2-05-02", "cover"),
            ("D1-4B-05", "climate"),
            ("D1-4B-06", "climate"),
            ("D1-4B-07", "climate"),
            ("A5-38-08", "light"),
            ("A5-38-09", "light"),
            ("F6-02-01", "event"),
            ("F6-02-02", "event"),
            ("F6-03-01", "event"),
            ("F6-05-01", "binary_sensor"),
        ],
    )
    def test_entity_type(self, make_device, eep, expected):
        assert make_device(eep).entity_type == expected

    def test_entity_type_unknown_eep(self, make_device):
        assert make_device("XX-YY-ZZ").entity_type is None

    @pytest.mark.parametrize(
        "eep",
        [
            "D2-01-02",
            "D2-01-03",
            "D2-01-06",
            "D2-01-07",
            "D2-01-0A",
            "D2-01-0B",
            "D2-01-0F",
            "D2-01-10",
            "D2-01-12",
            "A5-38-08",
        ],
    )
    def test_is_dimmable_true(self, make_device, eep):
        assert make_device(eep).is_dimmable is True

    @pytest.mark.parametrize("eep", ["D2-01-00", "D2-01-01", "D2-01-04", "D2-01-11"])
    def test_is_dimmable_false(self, make_device, eep):
        assert make_device(eep).is_dimmable is False

    @pytest.mark.parametrize("eep", ["D2-05-00", "D2-05-01", "D2-05-02"])
    def test_is_cover(self, make_device, eep):
        assert make_device(eep).is_cover is True

    def test_is_cover_false(self, make_device):
        assert make_device("D2-01-00").is_cover is False

    def test_supports_tilt_true(self, make_device):
        assert make_device("D2-05-00").supports_tilt is True
        assert make_device("D2-05-02").supports_tilt is True

    def test_supports_tilt_false(self, make_device):
        assert make_device("D2-05-01").supports_tilt is False

    @pytest.mark.parametrize("eep", ["D1-4B-05", "D1-4B-06", "D1-4B-07"])
    def test_is_climate(self, make_device, eep):
        assert make_device(eep).is_climate is True

    def test_is_climate_false(self, make_device):
        assert make_device("D2-01-00").is_climate is False

    @pytest.mark.parametrize(
        "eep,expected",
        [("D1-4B-05", "valve"), ("D1-4B-06", "cositherm"), ("D1-4B-07", "electro")],
    )
    def test_heat_area_type(self, make_device, eep, expected):
        assert make_device(eep).heat_area_type == expected

    def test_heat_area_type_none(self, make_device):
        assert make_device("D2-01-00").heat_area_type is None

    def test_setpoint_step_valve(self, make_device):
        assert make_device("D1-4B-05").setpoint_step == 0.5

    @pytest.mark.parametrize("eep", ["D1-4B-06", "D1-4B-07"])
    def test_setpoint_step_others(self, make_device, eep):
        assert make_device(eep).setpoint_step == 0.1

    @pytest.mark.parametrize(
        "eep,expected",
        [
            ("D2-01-00", 1),
            ("D2-01-02", 1),
            ("D2-01-04", 2),
            ("D2-01-06", 2),
            ("D2-01-08", 4),
            ("D2-01-0A", 4),
            ("D2-01-0D", 8),
            ("D2-01-0F", 8),
            ("XX-YY-ZZ", 1),
        ],
    )
    def test_channel_count(self, make_device, eep, expected):
        assert make_device(eep).channel_count == expected


# ── update_from_telegram ───────────────────────────────────────────────


class TestCoverTiltCapability:
    """Cover parameters refine the EEP capability without affecting other channels."""

    @pytest.mark.parametrize("eep", ["D2-05-00", "D2-05-01", "D2-05-02"])
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, 0.0),
            (0.0, 0.0),
            ("0", 0.0),
            ("0.0", 0.0),
            ("noRotation", 0.0),
            (" noRotation ", 0.0),
            (3, 3.0),
            (0.25, 0.25),
            ("1.5", 1.5),
        ],
    )
    def test_rotation_time_refines_eep_capability(
        self, make_device, make_telegram, eep, value, expected
    ):
        dev = make_device(eep)
        dev.update_from_telegram(
            make_telegram([{"key": "rotationTime", "value": value}])
        )

        assert dev.channels[0].rotation_time == expected
        expected_tilt = eep != "D2-05-01" and expected > 0
        assert dev.supports_tilt is expected_tilt
        assert dev.supports_tilt_for_channel(0) is expected_tilt

    @pytest.mark.parametrize("previous", [None, 0.0, 1.5])
    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            False,
            "",
            "invalid",
            "NoRotation",
            [],
            {},
            -1,
            "-0.5",
            float("nan"),
            float("inf"),
            "NaN",
            "Infinity",
            "-Infinity",
        ],
    )
    def test_invalid_rotation_time_preserves_last_state(
        self, make_device, make_telegram, previous, value
    ):
        dev = make_device("D2-05-00")
        dev.channels[0] = EnOceanChannel(channel_id=0, rotation_time=previous)
        dev.update_from_telegram(
            make_telegram([{"key": "rotationTime", "value": value}])
        )

        assert dev.channels[0].rotation_time == previous
        assert dev.supports_tilt is (previous != 0)

    @pytest.mark.parametrize("previous", [None, 0.0, 1.5])
    def test_no_change_preserves_rotation_time(
        self, make_device, make_telegram, previous
    ):
        dev = make_device("D2-05-02")
        dev.channels[0] = EnOceanChannel(channel_id=0, rotation_time=previous)
        dev.update_from_telegram(
            make_telegram([{"key": "rotationTime", "value": "noChange"}])
        )

        assert dev.channels[0].rotation_time == previous
        assert dev.supports_tilt is (previous != 0)

    @pytest.mark.parametrize("embedded_channel", [False, True])
    def test_rotation_time_is_specific_to_reported_channel(
        self, make_device, make_telegram, embedded_channel
    ):
        dev = make_device("D2-05-00")
        dev.channels[0] = EnOceanChannel(channel_id=0, rotation_time=1.5)
        if embedded_channel:
            functions = [{"key": "rotationTime", "value": "0", "channel": 1}]
        else:
            functions = [
                {"key": "rotationTime", "value": "0"},
                {"key": "channel", "value": "1"},
            ]
        dev.update_from_telegram(make_telegram(functions))

        assert dev.channels[0].rotation_time == 1.5
        assert dev.channels[1].rotation_time == 0.0
        assert dev.supports_tilt is True
        assert dev.supports_tilt_for_channel(1) is False
        assert dev.supports_tilt_for_channel(2) is True


class TestUpdateFromTelegram:
    """Tests for EnOceanDevice.update_from_telegram."""

    def _device(self) -> EnOceanDevice:
        return EnOceanDevice(
            device_id="DEV1", friendly_id="Test", eeps=[{"eep": "D2-01-02"}]
        )

    def test_switch_on(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": "switch", "value": "on"}]))
        assert dev.channels[0].is_on is True

    def test_switch_off(self, make_telegram):
        dev = self._device()
        dev.channels[0] = EnOceanChannel(channel_id=0, is_on=True)
        dev.update_from_telegram(make_telegram([{"key": "switch", "value": "off"}]))
        assert dev.channels[0].is_on is False

    def test_dim_value(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": "dimValue", "value": "75"}]))
        assert dev.channels[0].brightness == 75
        assert dev.channels[0].is_on is True

    def test_dim_value_zero_is_off(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": "dimValue", "value": "0"}]))
        assert dev.channels[0].brightness == 0
        assert dev.channels[0].is_on is False

    def test_position(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": "position", "value": "50"}]))
        assert dev.channels[0].position == 50

    def test_angle(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": "angle", "value": "45"}]))
        assert dev.channels[0].angle == 45

    def test_temperature(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "temperature", "value": "21.5"}])
        )
        assert dev.channels[0].temperature == 21.5

    def test_temperature_not_available(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "temperature", "value": "notAvailable"}])
        )
        assert dev.channels[0].temperature is None

    def test_temperature_setpoint(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "temperatureSetpoint", "value": "22.0"}])
        )
        assert dev.channels[0].temperature_setpoint == 22.0

    def test_temperature_setpoint_not_available(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "temperatureSetpoint", "value": "notAvailable"}])
        )
        assert dev.channels[0].temperature_setpoint is None

    def test_heater_mode(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "heaterMode", "value": "heating"}])
        )
        assert dev.channels[0].heater_mode == "heating"

    def test_humidity(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": "humidity", "value": "55"}]))
        assert dev.channels[0].humidity == 55.0

    def test_humidity_not_available(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "humidity", "value": "notAvailable"}])
        )
        assert dev.channels[0].humidity is None

    def test_window_open_string(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "windowOpen", "value": "true"}])
        )
        assert dev.channels[0].window_open is True

    def test_window_open_bool(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": "windowOpen", "value": True}]))
        assert dev.channels[0].window_open is True

    def test_window_closed(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "windowOpen", "value": "false"}])
        )
        assert dev.channels[0].window_open is False

    def test_summer_mode(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "summerMode", "value": "true"}])
        )
        assert dev.channels[0].summer_mode is True

    def test_feed_temperature(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "feedTemperature", "value": "35.5"}])
        )
        assert dev.channels[0].feed_temperature == 35.5

    def test_feed_temperature_not_available(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "feedTemperature", "value": "notAvailable"}])
        )
        assert dev.channels[0].feed_temperature is None

    def test_energy_consumption(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "energyConsumption", "value": "1.5"}])
        )
        assert dev.channels[0].energy_consumption == 1.5

    def test_power_state(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "powerState", "value": "active"}])
        )
        assert dev.channels[0].power_state == "active"

    def test_local_control(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "localControl", "value": "on"}])
        )
        assert dev.channels[0].local_control is True

    def test_energy_and_power(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram(
                [
                    {"key": "energy", "value": "1234.5"},
                    {"key": "power", "value": "56.7"},
                ]
            )
        )
        assert dev.channels[0].energy == 1234.5
        assert dev.channels[0].power == 56.7

    @pytest.mark.parametrize("value", [True, "true", " TRUE "])
    def test_liquid_detected(self, make_telegram, value):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "liquidDetected", "value": value}])
        )
        assert dev.channels[0].liquid_detected is True

    @pytest.mark.parametrize("value", [False, "false", " FALSE "])
    def test_liquid_cleared(self, make_telegram, value):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "liquidDetected", "value": value}])
        )
        assert dev.channels[0].liquid_detected is False

    def test_invalid_liquid_value_preserves_last_valid_state(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "liquidDetected", "value": True}])
        )
        dev.update_from_telegram(
            make_telegram([{"key": "liquidDetected", "value": "unknown"}])
        )
        assert dev.channels[0].liquid_detected is True

    def test_invalid_initial_liquid_value_remains_unknown(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": "liquidDetected", "value": 1}]))
        assert dev.channels[0].liquid_detected is None

    def test_multi_channel_routing(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram(
                [
                    {"key": "switch", "value": "on"},
                    {"key": "channel", "value": "1"},
                ]
            )
        )
        # Channel 0 should not be affected
        assert 0 not in dev.channels or dev.channels[0].is_on is False
        # Channel 1 should be on
        assert dev.channels[1].is_on is True

    def test_embedded_function_channel_routing(self, make_telegram):
        """OPUS telegrams can carry channel directly on the state function."""
        dev = self._device()
        dev.update_from_telegram(
            make_telegram(
                [
                    {"key": "switch", "value": "on", "channel": 1},
                ]
            )
        )

        assert 0 not in dev.channels or dev.channels[0].is_on is False
        assert dev.channels[1].is_on is True

    def test_mixed_embedded_channels_update_independently(self, make_telegram):
        """One telegram can report separate functions for different channels."""
        dev = self._device()
        dev.update_from_telegram(
            make_telegram(
                [
                    {"key": "switch", "value": "off", "channel": 1},
                    {"key": "switch", "value": "on", "channel": 0},
                ]
            )
        )

        assert dev.channels[0].is_on is True
        assert dev.channels[1].is_on is False

    def test_actuator_error_states(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram(
                [
                    {"key": "actuatorNotResponding", "value": "warning"},
                    {"key": "actuatorLowBattery", "value": "warning"},
                    {"key": "missingTemperature", "value": "info"},
                ]
            )
        )
        ch = dev.channels[0]
        assert ch.actuator_not_responding == "warning"
        assert ch.actuator_low_battery == "warning"
        assert ch.missing_temperature == "info"

    def test_invalid_dim_value_no_crash(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": "dimValue", "value": "abc"}]))
        assert dev.channels[0].brightness is None

    def test_timestamp_updates(self):
        dev = self._device()
        dev.update_from_telegram(
            {
                "functions": [{"key": "switch", "value": "on"}],
                "timestamp": "2024-06-01T12:00:00",
            }
        )
        assert dev.last_seen == "2024-06-01T12:00:00"

    def test_dbm_updates(self):
        dev = self._device()
        dev.update_from_telegram(
            {
                "functions": [{"key": "switch", "value": "on"}],
                "telegramInfo": {"dbm": -72},
            }
        )
        assert dev.dbm == -72

    def test_multiple_functions(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram(
                [
                    {"key": "switch", "value": "on"},
                    {"key": "dimValue", "value": "80"},
                ]
            )
        )
        ch = dev.channels[0]
        # dimValue processed after switch, sets is_on=True and brightness=80
        assert ch.is_on is True
        assert ch.brightness == 80

    @pytest.mark.parametrize(
        "key,value",
        [
            ("buttonA0", "pressed"),
            ("buttonA0", "released"),
            ("buttonAI", "pressed"),
            ("buttonB0", "pressed"),
            ("buttonBI", "released"),
            ("multipleButtons", "pressed"),
        ],
    )
    def test_rocker_button_recorded(self, make_telegram, key, value):
        dev = self._device()
        dev.update_from_telegram(make_telegram([{"key": key, "value": value}]))
        ch = dev.channels[0]
        assert ch.last_button == key
        assert ch.last_button_action == value

    def test_rocker_button_cleared_on_next_non_button_telegram(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "buttonA0", "value": "pressed"}])
        )
        assert dev.channels[0].last_button == "buttonA0"

        dev.update_from_telegram(make_telegram([{"key": "switch", "value": "on"}]))
        ch = dev.channels[0]
        assert ch.last_button is None
        assert ch.last_button_action is None
        assert ch.is_on is True

    def test_rocker_button_overwritten_on_next_button_telegram(self, make_telegram):
        dev = self._device()
        dev.update_from_telegram(
            make_telegram([{"key": "buttonA0", "value": "pressed"}])
        )
        dev.update_from_telegram(
            make_telegram([{"key": "buttonB0", "value": "released"}])
        )
        ch = dev.channels[0]
        assert ch.last_button == "buttonB0"
        assert ch.last_button_action == "released"


# ── from_device_object ─────────────────────────────────────────────────


class TestFromDeviceObject:
    """Tests for EnOceanDevice.from_device_object classmethod."""

    def test_basic_creation(self):
        data = {
            "deviceId": "AABB1122",
            "friendlyId": "My Device",
            "eeps": [{"eep": "D2-01-02"}],
            "manufacturer": "OPUS",
            "physicalDevice": "Dimmer",
            "firstSeen": "2024-01-01",
            "lastSeen": "2024-06-01",
            "dbm": -65,
        }
        dev = EnOceanDevice.from_device_object(data)
        assert dev.device_id == "AABB1122"
        assert dev.friendly_id == "My Device"
        assert dev.eeps == [{"eep": "D2-01-02"}]
        assert dev.manufacturer == "OPUS"
        assert dev.dbm == -65

    def test_nested_device_key(self):
        data = {"device": {"deviceId": "CCDD3344", "friendlyId": "Nested"}}
        dev = EnOceanDevice.from_device_object(data)
        assert dev.device_id == "CCDD3344"
        assert dev.friendly_id == "Nested"

    def test_missing_fields_defaults(self):
        dev = EnOceanDevice.from_device_object({})
        assert dev.device_id == ""
        assert dev.friendly_id == ""
        assert dev.eeps == []
        assert dev.dbm is None


# ── get_or_create_channel ──────────────────────────────────────────────


class TestGetOrCreateChannel:
    """Tests for channel management."""

    def test_creates_channel(self):
        dev = EnOceanDevice(device_id="X", friendly_id="X")
        ch = dev.get_or_create_channel(0)
        assert ch.channel_id == 0
        assert 0 in dev.channels

    def test_returns_existing(self):
        dev = EnOceanDevice(device_id="X", friendly_id="X")
        ch1 = dev.get_or_create_channel(0)
        ch1.is_on = True
        ch2 = dev.get_or_create_channel(0)
        assert ch2.is_on is True
        assert ch1 is ch2
