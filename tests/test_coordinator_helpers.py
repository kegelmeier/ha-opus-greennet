"""Tests for coordinator pure helper functions and command building."""
from __future__ import annotations

import pytest

from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator


# ── _parse_value ───────────────────────────────────────────────────────


class TestParseValue:
    """Tests for OpusGreenNetCoordinator._parse_value."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        # _parse_value doesn't use self, so we can use __new__ safely
        self.coord = OpusGreenNetCoordinator.__new__(OpusGreenNetCoordinator)

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
        ],
    )
    def test_booleans(self, input_val, expected):
        assert self.coord._parse_value(input_val) is expected

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("42", 42),
            ("0", 0),
            ("-5", -5),
            ("100", 100),
        ],
    )
    def test_integers(self, input_val, expected):
        result = self.coord._parse_value(input_val)
        assert result == expected
        assert isinstance(result, int)

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("3.14", 3.14),
            ("-0.5", -0.5),
            ("0.0", 0.0),
            ("21.5", 21.5),
        ],
    )
    def test_floats(self, input_val, expected):
        result = self.coord._parse_value(input_val)
        assert result == expected
        assert isinstance(result, float)

    @pytest.mark.parametrize(
        "input_val",
        ["hello", "on", "off", "notAvailable", "", "D2-01-02"],
    )
    def test_strings_passthrough(self, input_val):
        result = self.coord._parse_value(input_val)
        assert result == input_val
        assert isinstance(result, str)


# ── _set_nested_property ──────────────────────────────────────────────


class TestSetNestedProperty:
    """Tests for OpusGreenNetCoordinator._set_nested_property."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.coord = OpusGreenNetCoordinator.__new__(OpusGreenNetCoordinator)

    def test_flat_property(self):
        data = {}
        self.coord._set_nested_property(data, "eep", "D2-01-02")
        assert data == {"eep": "D2-01-02"}

    def test_nested_property(self):
        data = {}
        self.coord._set_nested_property(data, "states/switch", "on")
        assert data["states"]["switch"] == "on"

    def test_array_index(self):
        data = {}
        self.coord._set_nested_property(data, "eeps/0/eep", "D2-01-02")
        assert data["eeps"][0]["eep"] == "D2-01-02"

    def test_multiple_array_elements(self):
        data = {}
        self.coord._set_nested_property(data, "eeps/0/eep", "D2-01-02")
        self.coord._set_nested_property(data, "eeps/1/eep", "D2-01-03")
        assert data["eeps"][0]["eep"] == "D2-01-02"
        assert data["eeps"][1]["eep"] == "D2-01-03"

    def test_functions_structure(self):
        data = {}
        self.coord._set_nested_property(data, "state/functions/0/key", "switch")
        self.coord._set_nested_property(data, "state/functions/0/value", "on")
        assert data["state"]["functions"][0]["key"] == "switch"
        # "on" stays as string (not parsed to bool) because _parse_value treats it as string
        assert data["state"]["functions"][0]["value"] == "on"

    def test_deeply_nested(self):
        data = {}
        self.coord._set_nested_property(data, "a/b/c", "deep")
        assert data["a"]["b"]["c"] == "deep"

    def test_value_parsing_in_nested(self):
        data = {}
        self.coord._set_nested_property(data, "dbm", "-65")
        assert data["dbm"] == -65
        assert isinstance(data["dbm"], int)


# ── Command building ──────────────────────────────────────────────────


class TestCommandBuilding:
    """Tests for coordinator command builder methods."""

    @pytest.mark.asyncio
    async def test_turn_on_switch(self, coordinator):
        await coordinator.async_turn_on("DEV1", channel=0)
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "switch", "value": "on"}]
        )

    @pytest.mark.asyncio
    async def test_turn_on_dimmer_no_brightness(self, coordinator):
        await coordinator.async_turn_on("DEV1", channel=0, is_dimmable=True)
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "dimValue", "value": "100"}]
        )

    @pytest.mark.asyncio
    async def test_turn_on_dimmer_with_brightness(self, coordinator):
        await coordinator.async_turn_on("DEV1", channel=0, brightness=50, is_dimmable=True)
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "dimValue", "value": "50"}]
        )

    @pytest.mark.asyncio
    async def test_turn_off_switch(self, coordinator):
        await coordinator.async_turn_off("DEV1", channel=0)
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "switch", "value": "off"}]
        )

    @pytest.mark.asyncio
    async def test_turn_off_dimmer(self, coordinator):
        await coordinator.async_turn_off("DEV1", channel=0, is_dimmable=True)
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "dimValue", "value": "0"}]
        )

    @pytest.mark.asyncio
    async def test_turn_on_with_channel(self, coordinator):
        await coordinator.async_turn_on("DEV1", channel=2)
        coordinator.async_send_command.assert_called_once_with(
            "DEV1",
            [{"key": "switch", "value": "on"}, {"key": "channel", "value": "2"}],
        )

    @pytest.mark.asyncio
    async def test_turn_on_channel_zero_no_channel_key(self, coordinator):
        await coordinator.async_turn_on("DEV1", channel=0)
        args = coordinator.async_send_command.call_args[0]
        functions = args[1]
        keys = [f["key"] for f in functions]
        assert "channel" not in keys

    @pytest.mark.asyncio
    async def test_set_cover_position(self, coordinator):
        await coordinator.async_set_cover_position("DEV1", 75)
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "position", "value": "75"}]
        )

    @pytest.mark.asyncio
    async def test_set_cover_tilt(self, coordinator):
        await coordinator.async_set_cover_tilt("DEV1", 45)
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "angle", "value": "45"}]
        )

    @pytest.mark.asyncio
    async def test_stop_cover(self, coordinator):
        await coordinator.async_stop_cover("DEV1")
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "position", "value": "stop"}]
        )

    @pytest.mark.asyncio
    async def test_set_climate_setpoint(self, coordinator):
        await coordinator.async_set_climate_setpoint("DEV1", 22.5)
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "temperatureSetpoint", "value": "22.5"}]
        )

    @pytest.mark.asyncio
    async def test_set_climate_mode(self, coordinator):
        await coordinator.async_set_climate_mode("DEV1", "heating")
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "heaterMode", "value": "heating"}]
        )

    @pytest.mark.asyncio
    async def test_query_climate_status(self, coordinator):
        await coordinator.async_query_climate_status("DEV1")
        coordinator.async_send_command.assert_called_once_with(
            "DEV1", [{"key": "query", "value": "status"}]
        )


# ── KNOWN_STATE_KEYS ───────────────────────────────────────────────────


class TestKnownStateKeys:
    """Regression guards for KNOWN_STATE_KEYS membership."""

    def test_includes_rocker_button_keys(self):
        from custom_components.opus_greennet.const import BUTTON_KEYS, KNOWN_STATE_KEYS

        for key in BUTTON_KEYS:
            assert key in KNOWN_STATE_KEYS, f"{key} missing from KNOWN_STATE_KEYS"
