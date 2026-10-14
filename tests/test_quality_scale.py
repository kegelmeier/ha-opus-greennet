"""Validate quality_scale.yaml and the evidence behind every claimed rule.

Hassfest skips quality_scale.yaml for custom integrations
(``validate_iqs_file`` returns early when ``integration.core`` is false), so
this module applies the same schema, the official rule list and the official
rule validators that are applicable to this repository. Rule-specific checks
make each ``done`` status verifiable instead of a self-assessment.

Official source: home-assistant/core, script/hassfest/quality_scale.py and
script/hassfest/quality_scale_validation/*.py at commit
7ff0e7fbdea4e079ae3f99e048497f5e8c7e3360 (2026-10-14).
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import probatio
import pytest
from homeassistant.const import Platform
from homeassistant.helpers.entity import Entity
from homeassistant.util.yaml import load_yaml_dict

import custom_components.opus_greennet as integration_package
from custom_components.opus_greennet import (
    binary_sensor,
    climate,
    cover,
    diagnostics,
    event,
    light,
    lock,
    sensor,
    switch,
)
from custom_components.opus_greennet.entity import OpusGreenNetEntity

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components/opus_greennet"
QUALITY_SCALE = INTEGRATION / "quality_scale.yaml"
EVIDENCE = ROOT / "docs/quality/quality-scale-evidence.md"
README = (ROOT / "README.md").read_text(encoding="utf-8")
MANIFEST = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
PLATFORM_MODULES = [binary_sensor, climate, cover, event, light, lock, sensor, switch]

# ALL_RULES of the official hassfest module, in its order and tiers.
OFFICIAL_RULES: dict[str, list[str]] = {
    "bronze": [
        "action-setup",
        "appropriate-polling",
        "brands",
        "common-modules",
        "config-flow",
        "config-flow-test-coverage",
        "dependency-transparency",
        "docs-actions",
        "docs-conditions",
        "docs-high-level-description",
        "docs-installation-instructions",
        "docs-removal-instructions",
        "docs-triggers",
        "entity-event-setup",
        "entity-unique-id",
        "has-entity-name",
        "runtime-data",
        "test-before-configure",
        "test-before-setup",
        "unique-config-entry",
    ],
    "silver": [
        "action-exceptions",
        "config-entry-unloading",
        "docs-configuration-parameters",
        "docs-installation-parameters",
        "entity-unavailable",
        "integration-owner",
        "log-when-unavailable",
        "parallel-updates",
        "reauthentication-flow",
        "test-coverage",
    ],
    "gold": [
        "devices",
        "diagnostics",
        "discovery",
        "discovery-update-info",
        "docs-data-update",
        "docs-examples",
        "docs-known-limitations",
        "docs-supported-devices",
        "docs-supported-functions",
        "docs-troubleshooting",
        "docs-use-cases",
        "dynamic-devices",
        "entity-category",
        "entity-device-class",
        "entity-disabled-by-default",
        "entity-translations",
        "exception-translations",
        "icon-translations",
        "reconfiguration-flow",
        "repair-issues",
        "stale-devices",
    ],
    "platinum": ["async-dependency", "inject-websession", "strict-typing"],
}
ALL_RULES = [rule for rules in OFFICIAL_RULES.values() for rule in rules]

# Copy of the official SCHEMA (script/hassfest/quality_scale.py).
SCHEMA = probatio.Schema(
    {
        probatio.Required("rules"): probatio.Schema(
            {
                probatio.Required(rule): probatio.Any(
                    probatio.In(["todo", "done"]),
                    probatio.Schema(
                        {
                            probatio.Required("status"): probatio.In(["todo", "done"]),
                            probatio.Required("comment"): str,
                        }
                    ),
                    probatio.Schema(
                        {
                            probatio.Required("status"): "exempt",
                            probatio.Required("comment"): str,
                        }
                    ),
                )
                for rule in ALL_RULES
            }
        )
    }
)

DATA = load_yaml_dict(str(QUALITY_SCALE))
STATUS = {
    rule: value["status"] if isinstance(value, dict) else value
    for rule, value in DATA["rules"].items()
}
DONE = {rule for rule, status in STATUS.items() if status == "done"}


def _module(name: str) -> ast.Module:
    return ast.parse((INTEGRATION / f"{name}.py").read_text(encoding="utf-8"))


def _async_function(module: ast.Module, name: str) -> ast.AsyncFunctionDef | None:
    for item in module.body:
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name:
            return item
    return None


def _calls(module: ast.Module, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        for node in ast.walk(module)
    )


# ── File format ──────────────────────────────────────────────────────────────


def test_official_rule_list_has_54_unique_rules() -> None:
    assert len(ALL_RULES) == len(set(ALL_RULES)) == 54


def test_file_matches_the_official_schema() -> None:
    SCHEMA(DATA)


def test_file_uses_exactly_the_official_rule_keys() -> None:
    assert list(DATA["rules"]) == ALL_RULES


def test_exempt_and_todo_rules_are_justified() -> None:
    for rule, value in DATA["rules"].items():
        if STATUS[rule] in ("exempt", "todo"):
            assert isinstance(value, dict), rule
            assert len(value["comment"].strip()) >= 20, rule


def test_custom_integration_declares_no_official_tier() -> None:
    """No self-assigned rating: the manifest carries no quality_scale key."""
    assert "quality_scale" not in MANIFEST
    assert "not an official rating" in QUALITY_SCALE.read_text(encoding="utf-8")


def test_evidence_matrix_lists_every_rule_with_its_status() -> None:
    text = EVIDENCE.read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| `([a-z-]+)` \| (done|todo|exempt) \|", text, re.M))
    assert rows == STATUS


# ── Official validators (applicable to a custom integration) ─────────────────


def test_config_flow_validator() -> None:
    assert "config-flow" in DONE
    assert MANIFEST["config_flow"] is True
    assert (INTEGRATION / "config_flow.py").is_file()


def test_runtime_data_validator() -> None:
    assert "runtime-data" in DONE
    setup = _async_function(_module("__init__"), "async_setup_entry")
    assert setup is not None and len(setup.args.args) == 2
    entry_name = setup.args.args[1].arg
    assert any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == entry_name
        and node.attr == "runtime_data"
        and isinstance(node.ctx, ast.Store)
        for node in ast.walk(setup)
    )


def test_strict_typing_requires_typed_config_entry_everywhere() -> None:
    """runtime-data + strict-typing: every entry point uses *ConfigEntry."""
    assert "strict-typing" in DONE
    annotation = re.compile(r"^[A-Za-z][A-Za-z0-9]+ConfigEntry$")
    functions: dict[str, dict[str, int]] = {
        "__init__": {
            "async_migrate_entry": 2,
            "async_remove_config_entry_device": 2,
            "async_remove_entry": 2,
            "async_setup_entry": 2,
            "async_unload_entry": 2,
        },
        "diagnostics": {
            "async_get_config_entry_diagnostics": 2,
            "async_get_device_diagnostics": 2,
        },
    }
    for platform in Platform:
        functions.setdefault(platform.value, {"async_setup_entry": 2})
    checked = 0
    for module_name, names in functions.items():
        if not (INTEGRATION / f"{module_name}.py").exists():
            continue
        module = _module(module_name)
        for name, position in names.items():
            if (function := _async_function(module, name)) is None:
                continue
            argument = function.args.args[position - 1]
            assert isinstance(argument.annotation, ast.Name), (module_name, name)
            assert annotation.match(argument.annotation.id), (module_name, name)
            checked += 1
    assert checked >= 12


def test_strict_typing_gate_is_configured_and_enforced_in_ci() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    mypy = tomllib.loads(pyproject)["tool"]["mypy"]
    assert mypy["strict"] is True
    assert mypy["files"] == ["custom_components/opus_greennet"]
    assert "ignore_errors" not in pyproject
    assert "python -m mypy" in (ROOT / ".github/workflows/tests.yml").read_text()


def test_test_before_setup_validator() -> None:
    assert "test-before-setup" in DONE
    valid = {"ConfigEntryNotReady", "ConfigEntryAuthFailed", "ConfigEntryError"}
    raised = {
        getattr(getattr(node.exc, "func", node.exc), "id", None)
        for path in INTEGRATION.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Raise) and node.exc is not None
    }
    assert raised & valid


def test_unique_config_entry_validator() -> None:
    assert "unique-config-entry" in DONE
    flow = _module("config_flow")
    assert _calls(flow, "async_set_unique_id")
    assert _calls(flow, "_abort_if_unique_id_configured")


def test_discovery_todo_matches_the_code() -> None:
    """The official discovery validator fails, so the rule must stay todo."""
    manifest_keys = {
        "bluetooth",
        "dhcp",
        "homekit",
        "lorawan",
        "mqtt",
        "ssdp",
        "usb",
        "zeroconf",
    }
    flow_steps = {
        "async_step_bluetooth",
        "async_step_discovery",
        "async_step_dhcp",
        "async_step_hassio",
        "async_step_homekit",
        "async_step_mqtt",
        "async_step_ssdp",
        "async_step_usb",
        "async_step_zeroconf",
    }
    steps = {
        node.name
        for node in ast.walk(_module("config_flow"))
        if isinstance(node, ast.AsyncFunctionDef)
    }
    implemented = bool(manifest_keys & set(MANIFEST) or flow_steps & steps)
    assert implemented is (STATUS["discovery"] == "done")


def test_reconfiguration_exemption_matches_the_code() -> None:
    """Exempt because the only field is the unique ID of the entry."""
    assert STATUS["reconfiguration-flow"] == "exempt"
    flow_source = (INTEGRATION / "config_flow.py").read_text(encoding="utf-8")
    assert "async_step_reconfigure" not in flow_source
    schema_fields = re.findall(r"vol\.(?:Required|Optional)\((\w+)\)", flow_source)
    assert schema_fields == ["CONF_EAG_ID"]
    assert "async_set_unique_id(eag_id)" in flow_source


# ── Rule-specific evidence ───────────────────────────────────────────────────


def test_brands() -> None:
    for name in ("icon.png", "icon@2x.png", "logo.png", "logo@2x.png"):
        data = (INTEGRATION / "brand" / name).read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n"), name


def test_common_modules() -> None:
    assert (INTEGRATION / "coordinator.py").is_file()
    assert (INTEGRATION / "entity.py").is_file()
    for module in PLATFORM_MODULES:
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "from .entity import" in source, module.__name__


def test_no_requirements_for_dependency_rules() -> None:
    """dependency-transparency, async-dependency and inject-websession."""
    assert MANIFEST["requirements"] == []
    forbidden = {"aiohttp", "httpx", "requests", "urllib3"}
    for path in INTEGRATION.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            assert not names & forbidden, (path.name, names)


def test_integration_owner() -> None:
    assert MANIFEST["codeowners"]


def test_parallel_updates_in_every_platform() -> None:
    for module in PLATFORM_MODULES:
        assert module.PARALLEL_UPDATES == 0, module.__name__


def test_every_entity_class_has_entity_name_and_no_polling() -> None:
    classes = [
        value
        for module in PLATFORM_MODULES
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, Entity)
        and value.__module__ == module.__name__
    ]
    assert len(classes) >= 15
    for cls in classes:
        assert issubclass(cls, OpusGreenNetEntity), cls
    # HA turns _attr_* class values into properties, so check the source.
    assignments: dict[str, list[object]] = {}
    for path in INTEGRATION.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name | ast.Attribute)
                and isinstance(node.value, ast.Constant)
            ):
                target = node.targets[0]
                name = target.id if isinstance(target, ast.Name) else target.attr
                if name in ("_attr_has_entity_name", "_attr_should_poll"):
                    assignments.setdefault(name, []).append(node.value.value)
    assert set(assignments["_attr_has_entity_name"]) == {True}
    assert assignments["_attr_should_poll"] == [False]


def test_stale_devices_and_diagnostics_entry_points() -> None:
    assert callable(integration_package.async_remove_config_entry_device)
    assert callable(diagnostics.async_get_config_entry_diagnostics)
    assert {"eagid", "deviceid", "password", "token"} <= diagnostics.TO_REDACT


def test_actions_are_set_up_in_async_setup() -> None:
    setup = _async_function(_module("__init__"), "async_setup")
    assert setup is not None
    assert any(
        isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "_register_services"
        for node in ast.walk(setup)
    )
    entry_setup = _async_function(_module("__init__"), "async_setup_entry")
    assert not any(
        isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "_register_services"
        for node in ast.walk(entry_setup)
    )


@pytest.mark.parametrize(
    ("rule", "heading"),
    [
        ("docs-actions", "## Services"),
        ("docs-high-level-description", "## Features"),
        ("docs-installation-instructions", "## Installation"),
        ("docs-removal-instructions", "## Removing the integration"),
        ("docs-installation-parameters", "## Configuration"),
        ("docs-data-update", "## Availability and command behavior"),
        ("docs-known-limitations", "## Known limitations"),
        ("docs-supported-devices", "## Supported Device Types"),
        ("docs-supported-functions", "## Features"),
        ("docs-troubleshooting", "## Troubleshooting"),
    ],
)
def test_documentation_rules_point_to_existing_sections(rule, heading) -> None:
    assert STATUS[rule] == "done"
    assert re.search(rf"^{re.escape(heading)}$", README, re.M), heading


def test_ci_enforces_the_promised_gates() -> None:
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    for command in (
        "python -m pip check",
        "ruff check .",
        "ruff format --check .",
        "python -m mypy",
        "--cov-report=json:coverage.json",
        "python script/check_coverage.py coverage.json",
        "home-assistant/actions/hassfest@master",
    ):
        assert command in workflow, command
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    fail_under = float(re.search(r"fail_under = ([\d.]+)", pyproject).group(1))
    assert fail_under > 95
    # No file is excluded from measurement to reach the number.
    assert "omit" not in pyproject
