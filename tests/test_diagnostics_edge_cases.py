"""Edge cases of diagnostics redaction and debug latency logging."""

from __future__ import annotations

import logging

import pytest
from homeassistant.helpers.redact import REDACTED

from custom_components.opus_greennet.diagnostics import (
    _duration_ms,
    _redact_diagnostics,
    log_entity_state_write,
)
from custom_components.opus_greennet.enocean_device import EnOceanDevice


def test_malformed_json_text_is_kept_but_identifiers_are_redacted() -> None:
    """Text that only looks like JSON is not decoded, yet IDs are still hidden."""
    result = _redact_diagnostics(
        {"error": "{not json for AABB0011", "nested": ["[broken AABB0011"]},
        {"AABB0011"},
    )
    assert result == {
        "error": f"{{not json for {REDACTED}",
        "nested": [f"[broken {REDACTED}"],
    }


def test_embedded_json_is_redacted_structurally() -> None:
    result = _redact_diagnostics(
        {"raw": '{"password": "AABB0011", "value": 1}'}, {"AABB0011"}
    )
    assert result == {"raw": f'{{"password": "{REDACTED}", "value": 1}}'}


def test_without_identifiers_keys_and_values_are_unchanged() -> None:
    data = {"Power": 5, "channels": {"0": "on"}, "token": "secret"}
    assert _redact_diagnostics(data, set()) == {
        "Power": 5,
        "channels": {"0": "on"},
        "token": REDACTED,
    }


def test_non_text_keys_are_preserved() -> None:
    assert _redact_diagnostics({"channels": {1: "AABB0011"}}, {"AABB0011"}) == {
        "channels": {1: REDACTED}
    }


def test_duration_formatting() -> None:
    assert _duration_ms(None, 5.0) == "unknown"
    assert _duration_ms(1.0, 1.25) == "250.0"


def test_entity_write_latency_is_logged_only_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("custom_components.opus_greennet.test_latency")
    device = EnOceanDevice("DEV1", "Desk", [{"eep": "D2-01-00"}])
    device.last_update_source = "telegram"
    with caplog.at_level(logging.INFO, logger=logger.name):
        log_entity_state_write(logger, "switch.desk", device, 0)
    assert "entity_write" not in caplog.text
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        log_entity_state_write(logger, "switch.desk", device, 0)
    assert "OPUS update latency entity_write: entity=switch.desk" in caplog.text
    assert "source=telegram dispatch_to_write_ms=unknown" in caplog.text
