"""The per-module coverage gate rejects what the total value would hide."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from script.check_coverage import check, main

ROOT = Path(__file__).resolve().parents[1]
MODULES = sorted(
    path.name for path in (ROOT / "custom_components/opus_greennet").glob("*.py")
)


def _report(tmp_path: Path, values: dict[str, float]) -> Path:
    report = {
        "files": {
            f"custom_components/opus_greennet/{module}": {
                "summary": {"percent_covered": percent}
            }
            for module, percent in values.items()
        }
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _passing() -> dict[str, float]:
    values = dict.fromkeys(MODULES, 96.0)
    values["config_flow.py"] = 100.0
    return values


def test_passing_report(tmp_path: Path) -> None:
    assert check(_report(tmp_path, _passing()), ROOT) == []


def test_module_at_exactly_95_percent_fails(tmp_path: Path) -> None:
    values = _passing()
    values["coordinator.py"] = 95.0
    assert check(_report(tmp_path, values), ROOT) == [
        "coordinator.py: 95.00 % is not above 95 %"
    ]


def test_config_flow_requires_full_coverage(tmp_path: Path) -> None:
    values = _passing()
    values["config_flow.py"] = 99.9
    assert check(_report(tmp_path, values), ROOT) == [
        "config_flow.py: 99.90 % < required 100 %"
    ]


def test_unmeasured_module_fails(tmp_path: Path) -> None:
    values = _passing()
    del values["wire.py"]
    assert check(_report(tmp_path, values), ROOT) == [
        "wire.py: missing from the coverage report"
    ]


def test_high_total_does_not_hide_a_weak_core_module(tmp_path: Path) -> None:
    """A 99 % average still fails when one core module is weakly tested."""
    values = dict.fromkeys(MODULES, 100.0)
    values["mqtt_transport.py"] = 80.0
    assert sum(values.values()) / len(values) > 98
    assert check(_report(tmp_path, values), ROOT) == [
        "mqtt_transport.py: 80.00 % is not above 95 %"
    ]


def test_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    assert main(["check", str(_report(tmp_path, _passing()))]) == 0
    values = _passing()
    values["entity.py"] = 10.0
    assert main(["check", str(_report(tmp_path, values))]) == 1
