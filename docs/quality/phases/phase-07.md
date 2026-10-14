# Phase 7 - Quality scale evidence and CI gates

- **Status:** completed (local commit; push only on request)
- **Branch:** `feat/opus-quality-scale-evidence` (did not exist locally or remotely)
- **Predecessor:** `main` after the merge of phases 1-5 (fast-forward to
  `367dd9a`) and the CHANGELOG "Unreleased" commit `f400866`
- **Base SHA:** `f4008667c0d42b202554fb0849ee3226b81b011a` (`origin/main`,
  clean working tree)
- Phase 6 (metadata and exposure policy) is deferred, see
  `implementation-plan.md`.

## Scope

Verifiable quality-scale conformity instead of a self-rating: a
`quality_scale.yaml` with the official rule keys, an evidence matrix, CI gates
that enforce the promised rules, and README documentation that matches the
actual behaviour. No change to integration code.

## Official sources

- Rule list, tiers and file schema: hassfest
  `script/hassfest/quality_scale.py` and
  `script/hassfest/quality_scale_validation/*.py`, home-assistant/core commit
  `7ff0e7fbdea4e079ae3f99e048497f5e8c7e3360` (2026-10-14). 54 rules: bronze 20,
  silver 10, gold 21, platinum 3.
- Rule pages: <https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/>
  (relevant here: `test-coverage` "above 95 % test coverage for all
  integration modules"; `config-flow-test-coverage` 100 % with recovery after
  every error; `reconfiguration-flow` exemption "any change would force the
  creation of a new config entry"; `appropriate-polling` does not apply to
  push integrations; `stale-devices` allows manual removal when the
  integration cannot know that a device is gone).
- Brand images for custom integrations (HA 2026.3+):
  <https://developers.home-assistant.io/docs/core/integration/brand_images/>.

## Custom integration status

- Hassfest's `validate_iqs_file` returns early for non-core integrations, so
  CI hassfest does not read `quality_scale.yaml`. `tests/test_quality_scale.py`
  applies the official schema (copied verbatim with `probatio`), the exact
  official rule keys in official order, and the official validators that work
  for a custom integration (`config-flow`, `runtime-data`, `strict-typing`
  typed config entry, `test-before-setup`, `unique-config-entry`,
  `discovery`).
- No official tier is claimed. `manifest.json` has no `quality_scale` key; the
  YAML header and the evidence matrix state that the file is not an official
  rating.

## Result

`quality_scale.yaml`: 40 done, 9 exempt, 5 todo.

- exempt: `appropriate-polling`, `docs-conditions`, `docs-triggers`,
  `docs-configuration-parameters`, `reauthentication-flow`,
  `discovery-update-info`, `reconfiguration-flow`, `async-dependency`,
  `inject-websession` (each with a reason in the YAML and a test where the
  reason is checkable).
- todo: `discovery`, `docs-examples`, `docs-use-cases`, `icon-translations`,
  `repair-issues`.
- Evidence matrix: `docs/quality/quality-scale-evidence.md`. A test fails if a
  rule status in the matrix differs from the YAML.

## Changes and files

| File | Change |
|------|--------|
| `custom_components/opus_greennet/quality_scale.yaml` | new; all 54 rules with status and reasons |
| `docs/quality/quality-scale-evidence.md` | new; rule -> implementation -> tests -> documentation |
| `script/check_coverage.py` | new; per-module coverage gate on the coverage JSON report |
| `pyproject.toml` | `fail_under` 75 -> 97; no `omit`, no new exclusions (`exclude_also` unchanged) |
| `.github/workflows/tests.yml` | JSON coverage report, per-module gate step, three repetitions of the lifecycle/race suites |
| `.gitignore` | `coverage.json` |
| `README.md` | Known limitations, Removing the integration, Troubleshooting (broker / gateway / radio actuator, ACK 200/201, retained state, estimates, missing request ID, recovery), project structure and testing paragraph |
| `docs/quality/implementation-plan.md` | phase 6 deferred, merge exception, phase 7, gates |
| `tests/test_quality_scale.py` | new (24 tests) |
| `tests/test_translations.py` | new (7 tests) |
| `tests/test_coverage_gate.py` | new (6 tests) |
| `tests/test_service_actions.py` | new (9 tests); actions through the real HA service registry |
| `tests/test_config_flow_recovery.py` | new (7 tests); recovery after every error, unique ID |
| `tests/test_coordinator_edge_cases.py` | new (37 tests incl. parametrised cases) |
| `tests/test_transport_edge_cases.py` | new (9 tests) |
| `tests/test_platform_edge_cases.py` | new (9 tests) |
| `tests/test_diagnostics_edge_cases.py` | new (6 tests) |

No file under `custom_components/opus_greennet/` except the new YAML changed.

## Coverage

Branch coverage (`branch = true`), same environment, full test suite.

| Module | Base `f400866` | Phase 7 |
|--------|----------------|---------|
| `__init__.py` | 72.19 | 97.63 |
| `binary_sensor.py` | 94.83 | 97.13 |
| `binary_sensor_descriptions.py` | 100.00 | 100.00 |
| `climate.py` | 98.54 | 98.54 |
| `config_flow.py` | 95.16 | 100.00 |
| `const.py` | 100.00 | 100.00 |
| `coordinator.py` | 90.61 | 96.53 |
| `cover.py` | 93.48 | 100.00 |
| `diagnostics.py` | 87.74 | 99.06 |
| `enocean_device.py` | 97.30 | 97.30 |
| `entity.py` | 95.20 | 100.00 |
| `event.py` | 100.00 | 100.00 |
| `light.py` | 94.74 | 100.00 |
| `lock.py` | 95.56 | 100.00 |
| `mqtt_transport.py` | 94.20 | 98.68 |
| `sensor.py` | 97.01 | 97.01 |
| `switch.py` | 100.00 | 100.00 |
| `wire.py` | 100.00 | 100.00 |
| **Total** | **92.76** | **97.67** |

Applied to the base report, `script/check_coverage.py` fails for eight
modules (`__init__`, `binary_sensor`, `config_flow`, `coordinator`, `cover`,
`diagnostics`, `light`, `mqtt_transport`). The total value alone would have
hidden this. The gate also fails if a module is missing from the report.

## CI gates

| Gate | Enforced by |
|------|-------------|
| Full test suite | `pytest -v --cov ...` |
| Total coverage >= 97 % | `fail_under = 97` |
| > 95 % per integration module | `python script/check_coverage.py coverage.json` |
| 100 % config flow | same script |
| Config flow recovery and unique ID | `tests/test_config_flow_recovery.py` |
| Lifecycle and race regressions, repeated | step "Repeat lifecycle and race regression tests" (3 x 7 files, about 60 s each) |
| Lint and format | `ruff check .`, `ruff format --check .` |
| Strict typing | `python -m mypy` (since phase 5) |
| Dependency compatibility | `python -m pip check` |
| Manifest, services, strings schema | hassfest job |
| Translation consistency | `tests/test_translations.py` (en == strings, de structure and placeholders, exception, config and entity keys, services) |
| Quality-scale file and evidence | `tests/test_quality_scale.py` |

The repetitions run the existing tests unchanged; they add no runtime polling
and no new timing values.

## Reviewed rules (task 11)

- Diagnostics redaction: `TO_REDACT` and identifier redaction inside text and
  embedded JSON; new edge-case tests. No gap found.
- Actions: registered in `async_setup`; validation errors and translated
  request errors tested through the real service registry.
- Options: none; `docs-configuration-parameters` exempt.
- Device removal: manual via `async_remove_config_entry_device`; no automatic
  removal (`stale-devices` done, reason documented).
- Reconfigure: exempt, the only field is the unique ID; checked by a test.
- Discovery: todo; a test ties the status to the absence of discovery
  manifest keys and steps.

## Repair issues (task 13)

Transient broker, bridge or radio outages are deliberately not repair issues:
they recover automatically and are reflected as unavailability. The only
candidate for a permanent user action found is the one-time removal of a
duplicate beta entity during unique-ID migration, which only logs a warning.
Implementing it would add new user-facing behaviour, so `repair-issues` stays
`todo`.

## Findings (documented, not changed)

- Translation key `exceptions.mqtt_setup_failed` in `strings.json` is not used
  by the code. Kept; removing translation strings is outside this phase.
- Unreachable branches: `diagnostics.py` 62->64 and `coordinator.py`
  1509->1511 cannot be reached with valid input; not covered, not excluded.
- `__init__.py` service handlers check `result is None` although the
  coordinator raises on timeout; covered with a mock
  (`test_missing_read_result_raises_timeout_error`), unreachable in production.
- O1 (ACK 201 treated like 200) remains open; it is now documented in the
  README troubleshooting section.

## Exclusion check A1-A5

- A1: no poller, interval, delay or reconciliation slot changed; no
  integration code changed. The CI repetition re-runs tests, not the runtime.
- A2: availability, uptime and health semantics unchanged; the README only
  describes them.
- A3: no quarantine, drain, topic lock or cool-down.
- A4: retain/freshness logic unchanged; only documented.
- A5: entity count, enablement defaults and registry configuration unchanged;
  `entity-disabled-by-default` is marked done for the existing behaviour only.

## Compatibility and risks

- No runtime change; `quality_scale.yaml` is not read by Home Assistant at
  runtime.
- `fail_under = 97` and the per-module gate leave limited headroom
  (`coordinator.py` 96.53 %). New code needs tests; this is intended.
- The repeated race suites add about three minutes of CI time.
- The official rule list can change; `test_quality_scale.py` pins it to the
  cited core commit and has to be updated when the rule set changes.
- Tests simulate MQTT; they are no hardware verification.

## Executed commands

All in Python 3.14.3, Home Assistant 2026.10.1, mypy 2.4.0, ruff 0.16.3.

```bash
python -m ruff format .
python -m ruff check .                  # All checks passed
python -m mypy                          # no issues in 18 source files
python -m pip check                     # no broken requirements
python -m pytest -q -p no:cacheprovider --cov --cov-report=term-missing \
  --cov-report=json:coverage.json       # 1238 passed, total 97.67 %
python script/check_coverage.py coverage.json   # passed
# base f400866 (git worktree): 1094 passed, 92.76 %; gate fails for 8 modules
for run in 1 2 3; do pytest -q -p no:cacheprovider <7 race files>; done
                                        # 81 passed, three times
git diff --check
```

Hassfest runs only in CI and was not executed locally.

## Notes for the next phase

- Open quality-scale items: `discovery`, `docs-examples`, `docs-use-cases`,
  `icon-translations`, `repair-issues`. `icon-translations` and examples are
  candidates without functional impact; discovery and repair issues add
  behaviour and need an explicit prompt.
- Phase 6 needs A5 explicitly lifted before it can start.
