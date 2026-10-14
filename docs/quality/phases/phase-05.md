# Phase 5 - Strict typing

- **Status:** completed (local commit; push only on request)
- **Branch:** `feat/opus-q05-strict-typing`
- **Predecessor branch:** `fix/opus-q04-background-efficiency`
- **Base SHA:** `bf2ad417559cdaee3b695274a37455476a356e6c` (local and remote
  identical, clean working tree, branch did not exist before)

## Scope

Strict type checking of all integration modules without changing valid OPUS
payloads or domain semantics. Wire data is separated from the normalised
domain model and validated at the MQTT/JSON boundaries.

## Type checker

- Checker: **mypy 2.4.0 (compiled: yes)**, Python 3.14.3.
- Configuration (`pyproject.toml`):

  ```toml
  [tool.mypy]
  python_version = "3.14"
  files = ["custom_components/opus_greennet"]
  strict = true
  warn_unreachable = true
  ```

  No `ignore_errors`, no per-module overrides, no `ignore_missing_imports`.
- Test dependency: `mypy==2.4.0` in `requirements_test.txt`. Concrete need:
  the phase prompt requires strict type checking as a gate, and mypy is the
  checker already used for information in phases 1-4. Not a runtime
  dependency (`manifest.json` unchanged).
- CI: new step "Check strict typing" (`python -m mypy`) in the `test` job of
  `.github/workflows/tests.yml`, after the ruff checks and before pytest. It
  runs against the same resolved latest stable Home Assistant as the tests.

Starting point on base `bf2ad41` (same flags, no config in the base tree):

| Command | Result |
|---|---|
| `python -m mypy --python-version 3.14 custom_components/opus_greennet` | 27 errors in 9 files |
| `python -m mypy --strict --warn-unreachable --python-version 3.14 custom_components/opus_greennet` | 74 errors in 15 files (17 modules) |
| `python -m mypy` on this branch | **Success: no issues found in 18 source files** |

Phase 1 recorded 72 strict errors on `3e52fce`; phases 2-4 added code, so the
measured base value for this phase is 74.

## Changes

### Wire data vs. domain model (`wire.py`, new)

- Type aliases `JsonScalar`, `JsonValue`, `JsonObject`, `JsonList` for raw
  JSON, and covariant read-only views `WireMapping` / `WireFunction`
  (`Mapping[str, object]`) for functions and telegrams that are built in code.
- Boundary helpers that only check container and field types and never
  reinterpret values: `payload_text`, `parse_json`, `as_object`, `as_list`,
  `object_items`, `indexed_objects`, `string_key`.
- The domain model stays in `enocean_device.py` (`EnOceanDevice`,
  `EnOceanChannel`); raw caches in the coordinator (`_device_data`,
  `_telegram_data`, `_device_stream_data`, `gateway_info`) are typed as
  `JsonObject`.

### Boundary validation

- `mqtt_transport.decode_response` accepts `str | bytes | bytearray`, returns
  `JsonObject` and validates through the new `response_status()`. Accepted
  status representations are unchanged: int, integral float or numeric string
  of 200/201. Booleans, fractional/non-finite floats, other types and other
  codes are rejected as before.
- `_handle_put_answer_state` reads the validated status via
  `response_status()` instead of indexing the raw header.
- `_handle_get_answer_devices`, the complete-telegram path and the telegram
  merge path use `parse_json` / `as_object` / `as_list` instead of untyped
  indexing.
- `_set_nested_property` is a typed walk over `JsonObject | JsonList`. It
  raises `ValueError` on a container conflict exactly where the untyped walk
  failed before (previously `TypeError`, `AttributeError` or `KeyError`); the
  handlers still log and drop the property via `except Exception`.
- `_merge_device_data` narrows with `isinstance` instead of indexing unions.
- `_apply_scalar_telemetry` separates the raw value, the parsed number and the
  cached wire value.
- `EnOceanDevice.update_from_telegram(WireMapping)`: functions via
  `object_items`, `timestamp` via `string_key`, `telegramInfo` via `as_object`,
  `dbm` via the new `_parse_integer`.

### Callable, Future, Task, Coroutine

- Timer handles `Callable[[], None]`, MQTT handlers `MessageHandler =
  Callable[[ReceiveMessage], None]`, `asyncio.Task[None]`,
  `Coroutine[object, object, None]`, `asyncio.Future[None]` /
  `asyncio.Future[JsonObject]` (`_RequestFuture`), all timer callbacks typed
  `(_now: datetime) -> None`.
- Default-argument lambdas replaced by `functools.partial` (subscribe-done
  callback, binary sensor removal callback).
- `_bridge_reported_offline()` re-reads the bridge status after an `await`
  (mypy otherwise narrows the attribute across the await).

### Typed ConfigEntry

- `OpusGreenNetConfigEntry` is used consistently, including diagnostics
  (`TYPE_CHECKING` import to avoid an import cycle). The two `cast()` calls in
  `_loaded_entry` were removed; `ConfigEntry[Any]` is assignable.

### Dynamic getters/setters replaced

- `EnOceanDevice._update_numeric_field` (`setattr` by name) replaced by
  `_float_reading` / `_integer_reading` with explicit field assignments.
- `getattr(channel, name)` in `sensor.py`, `binary_sensor.py` and
  `binary_sensor_descriptions.py` replaced by typed accessor maps
  (`MEASUREMENT_READINGS`, `BOOLEAN_CHANNEL_STATES`) and typed lambdas. An
  unknown name now fails at entity construction instead of returning `None`;
  all used names are covered by the existing inventory tests.

### Imports and constants

- Home Assistant symbols are imported from the modules that export them
  (`*.const`, `helpers.device_registry.DeviceInfo`, `const.EntityCategory`).
- `import probatio as vol` in `__init__.py` and `config_flow.py` instead of
  `import voluptuous as vol`. Since Core 2026.9 Home Assistant validates with
  Probatio and aliases `voluptuous` to it at startup
  ([HA developer blog](https://developers.home-assistant.io/blog/2026/09/30/probatio-validation-engine/)).
  Home Assistant 2026.9.4 (the project minimum) requires `probatio==0.11.4`
  (PyPI metadata). Verified locally with HA 2026.10.1: after importing
  Home Assistant, `voluptuous.Schema`, `Required`, `Optional`, `Invalid`,
  `All`, `In` and `Coerce` are the identical Probatio objects, so runtime
  behaviour is unchanged. This resolves the phase 1 "voluptuous vs. probatio
  Schema" limitation without a runtime change.
- `LockEntityFeature(0)` instead of `0` (same value).

## Justified remaining exceptions

No `cast`, no `type: ignore`, no `ignore_errors` in the integration.

| Location | Construct | Reason |
|---|---|---|
| Entity overrides in `light.py`, `switch.py`, `cover.py`, `lock.py`, `climate.py` | `**kwargs: Any` | Signature fixed by the Home Assistant base classes |
| `config_flow.py` | `dict[str, Any]` | `ConfigFlow.async_step_user` signature |
| `diagnostics.py` | `-> dict[str, Any]` | Home Assistant diagnostics platform signature |
| `wire.parse_json` | `json.loads` result annotated as `JsonValue` | `json.loads` without hooks only produces JSON types; single documented boundary |
| `binary_sensor.py` (2x) | `getattr(entity, "hass", None)` | HA declares `Entity.hass: HomeAssistant` although it is `None` before add; a typed check would be reported as unreachable |
| `coordinator.py` (6x), `mqtt_transport.py` (2x) | `getattr(msg, "retain", False)`, `getattr(msg, "topic", "")` | Constant attribute names with typed defaults; defensive against message objects without the attribute; unchanged |

## Behaviour notes (typing-motivated, documented)

Valid payloads are unaffected. The following edge cases changed only in the
representation or in the exception type:

1. A numeric `friendlyId` / `manufacturer` / `physicalDevice` from a
   flattened topic (e.g. `"123"`, parsed to `123`) is stored as the text
   `"123"` instead of the int. Other non-text values (null, booleans,
   containers) become `""`; the device name then falls back to the device ID
   as before.
2. A numeric path below an existing object (e.g. an unknown top-level
   property `.../DEV/0`) is stored under the key `"0"` instead of the int `0`.
   Diagnostics serialise both identically.
3. Flattened path conflicts raise `ValueError` instead of `TypeError` /
   `AttributeError` / `KeyError`; accepted and dropped paths are identical.
4. `primary_eep` returns `None` for a non-text `eep` (previously the raw
   value, which never matched `EEP_MAPPINGS`; same entity outcome).
5. EEP lists keep object entries only; a dict of EEPs with mixed numeric and
   text keys is ordered (numbers first) instead of raising `TypeError`.
6. `payload_text` decodes `bytearray` (previously `str(bytearray)`). Home
   Assistant delivers `str` or `bytes`, so this is not reachable in practice.
7. `from_device_object` (not used in production) maps wrong field types to
   `""` / filtered EEPs.
8. `freeze_projection` maps an unknown unhashable value to a fresh object, so
   the entity writes (fail-open).

## Tests (executed)

New file `tests/test_strict_typing_boundaries.py` (146 test cases): wire
helpers; valid status representations and rejected status/container/JSON
variants; valid fixtures for all profile families (switch, dimmer incl.
A5-38, energy, covers, all three HeatArea profiles incl. type and setpoint
step, rockers F6-02/F6-03, RWM, SMS presence, window handles D2-06-40 /
F6-10-00 / D2-03-10, leakage); equivalent numeric representations (`"50"`,
`50`, `50.0`, `" 50 "`); channel zero as `0`, `"0"`, `0.0`; channel selector;
malformed channels; bool, NaN, Infinity, containers and text in numeric
fields; wrong function containers; `telegramInfo.dbm` and `timestamp`
types; `notAvailable` per the existing contract (measurements clear, actuator
states ignore it); unknown additive fields; flattened discovery; path
conflicts; complete-telegram representations and malformed JSON;
`getAnswer/devices` containers; PUT answer status; scalar `dbm` telemetry.

Before/after: the behaviour tests (141, without the tests for the new
`wire`/`response_status` API) were run against base `bf2ad41`: 137 passed,
4 failed. The 4 failures are exactly notes 1, 3, 4 and 7 above. Existing
tests were not changed.

Differential corpus (local script, not committed): 955,385 inputs
(39 EEPs x 48 keys incl. aliases and one unknown key x 51 values x 10
channel representations = 954,720 telegrams with unknown additive fields;
324 function/`telegramInfo`/`timestamp` container variants; 28
`decode_response` payloads x `require_status`; 169 flattened path pairs;
flattened discovery and stream scenarios; `getAnswer/devices` answers;
complete and flattened telegrams)
were run through base `bf2ad41` and this branch. Result: identical except 35
cases, all covered by notes 1 and 2; the accepted/rejected pattern is
identical in every case.

Commands (sandbox, Python 3.14.3, Home Assistant 2026.10.1):

| Command | Result |
|---|---|
| `python -m mypy --version` | `mypy 2.4.0 (compiled: yes)` |
| `python -m mypy` | Success: no issues found in 18 source files |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 60 files already formatted |
| `python -m pip check` (clean venv: `pip install -r requirements_test.txt "homeassistant==2026.10.1"`) | No broken requirements found |
| `python -m pytest -q --cov --cov-report=term-missing` (clean venv) | **1094 passed, 0 failed**, coverage 92.76 % |

In the shared sandbox interpreter `pip check` reports only unrelated sandbox
packages (`modal`/`protobuf`, internal packages); the clean venv above is the
CI-equivalent result.

Not executed: `hassfest` (GitHub Action only), the CI workflow itself, and
Home Assistant 2026.9.4 (the minimum version was checked through its PyPI
metadata only). All tests use a mocked MQTT layer; there is no hardware
verification.

## Exclusion check A1-A5

- A1: no new poller, interval, delay or timer; only annotations on the
  existing timers and delay tuples.
- A2: uptime, health and availability logic unchanged;
  `_bridge_reported_offline()` evaluates the same expression.
- A3: no quarantine, drain phase, topic lock or cooldown; timeouts unchanged.
- A4: no timestamp/sequence evaluation, no replay generation; all
  `retain` checks unchanged.
- A5: entity set, unique IDs, options, enablement defaults and registry
  configuration unchanged.

## Compatibility and residual risks

- CI type-checks against the latest stable Home Assistant. A future HA
  release can introduce new type errors in HA stubs without a code change in
  this repository; the step then fails visibly.
- `probatio` is imported directly; it is part of Home Assistant since 2026.9
  and pinned by HA itself.
- Typed accessor maps raise `KeyError` at entity construction for an unknown
  name; all names are fixed in code and covered by tests.
- The `getattr` exceptions listed above remain by design.
- O1 (ACK 201 treated like 200) and B1 (hypothesis) remain open.

## Notes for the next phase

Base the next phase on the final HEAD of this branch. `python -m mypy` is now
a CI gate; new code must keep strict typing. Remaining proposals in the plan:
protocol/parsing module, coordinator structure, platform consolidation,
documentation and quality gates.
