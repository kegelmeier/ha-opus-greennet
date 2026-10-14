# Phase 1 – Reproducible Baseline

## Status and scope

- **Status:** completed. Documentation only, no runtime code change.
- **Scope:** analysis of data flow, state mutation, resource ownership, the 5 s /
  20 s reconciliation checks, event emission, typing, test and quality baseline;
  documentation of exclusions A1–A5; review of the following phases against the
  actual code.
- **Out of scope:** any change under `custom_components/`, `tests/`,
  `pyproject.toml`, `requirements_test.txt`, CI, `manifest.json`, `CHANGELOG.md`.

## Branch and base

| Field | Value |
|-------|-------|
| Repository | `fubu2k/ha-opus-greennet` (`origin`, verified via `git remote -v` and `gh repo view`) |
| Branch | `feat/opus-q01-baseline` |
| Predecessor branch | `main` |
| Base SHA | `3e52fce31e7a2d503eef4820f4c706ebcfc751ab` ("fix: ignore retained device discovery answers (#73)") |
| Pre-checkout check | fresh clone, `git status --porcelain` empty, `origin/main == HEAD == 3e52fce…`, remote had only `refs/heads/main`, branch existed neither locally nor remotely |
| Commit identity | repository-local `fubu2k <48277447+fubu2k@users.noreply.github.com>` (from `gh api user`; no identity was configured) |

The final HEAD SHA of this phase is not part of its own commits. It is
reported in the chat and recorded as the base SHA in `phase-02.md`.

## Sources read

`CLAUDE.md`, `README.md`, `opus_mqtt_services_reference.md`, `pyproject.toml`,
`requirements_test.txt`, `.github/workflows/tests.yml`, `.github/workflows/release-asset.yml`,
`hacs.json`, `manifest.json`, `services.yaml`, all 17 Python modules under
`custom_components/opus_greennet/` in full, plus `tests/conftest.py`,
`tests/ha_helpers.py` and the test files relevant to the tasks
(`test_event_entity.py`, `test_scalar_telemetry.py`, `test_coordinator_parsing.py`,
`test_coordinator_mqtt.py` (reconciliation), `test_reconciliation_feedback.py`,
`test_cover_movement.py`, `test_cover_stop.py` by search).

Project rules from `CLAUDE.md`: `feat/…` branches, `pytest -v` must pass, PRs
without version or changelog changes, entities never touch MQTT directly, device
state lives in `EnOceanChannel`. The upstream URLs (`kegelmeier`) in `CLAUDE.md`,
`README.md` and `manifest.json` are historical and do not change the target
`fubu2k/ha-opus-greennet`.

## 1. Actual data flow

```
MQTT (HA mqtt component)
 │
 ├─ stream/devices/#  ─► _handle_device_property_message
 │     _set_nested_property(_device_data[dev], path, payload)
 │     ├─ known device + states|transmitModes|configuration/parameters/<n>/…
 │     │     └─► _buffer_device_stream_property ──(0.02 s)──► _finalize_device_stream
 │     ├─ known device + states/<key> (KNOWN_STATE_KEYS, no button keys)
 │     │     └─► _apply_known_device_state_property ─► update_from_telegram ─► dispatch
 │     └─ otherwise: _pending_devices + discovery timer (2 s, restarted per message)
 │                                  └─► _finalize_discovery ─► _create_device_from_data
 │
 ├─ stream/device/#   ─► _handle_device_stream_message
 │     ├─ batteryLevel | dbm (known device) ─► _apply_scalar_telemetry ─► dispatch (on change only)
 │     └─ otherwise ─► _buffer_device_stream_property ──(0.02 s)──► _finalize_device_stream
 │           _merge_device_data(_device_data) → indexed deltas with cached context
 │           → _device_state_functions / _configuration_state_functions
 │           → _record_confirmed_feedback → update_from_telegram → dispatch
 │
 ├─ stream/telegram/# ─► _handle_telegram_property_message (retained: discarded)
 │     complete JSON: immediate _finalize_telegram
 │     flattened: _telegram_data + timer (0.15 s); flush on direction change / path repeat
 │     _finalize_telegram: from/to selection → complete functions
 │       ├─ from: _record_confirmed_feedback → update_from_telegram → dispatch
 │       └─ to (no local ACK waiter): _start_cover_commands → update_from_telegram
 │              → dispatch → _schedule_reconciliation_queries
 │
 ├─ getAnswer/devices/# ─► _handle_get_answer_devices (retained: discarded)
 │     _device_data[dev] = object, _pending_devices, discovery timer (2 s)
 │
 ├─ putAnswer/devices/+/state ─► MQTTRequestManager.async_route (ACK future)
 │     + _handle_put_answer_state: set/clear last_command_error → dispatch
 │
 └─ getAnswer/config/system/uptime|info, bridge/status ─► gateway state / availability

Domain state: coordinator.devices[dev] : EnOceanDevice
              └─ channels[n] : EnOceanChannel (state_revision, fields, transient button fields)

Dispatcher:
  SIGNAL_DEVICE_DISCOVERED_<EAG>          → platform callbacks create entities
  SIGNAL_DEVICE_STATE_UPDATE_<EAG>_<DEV>  → OpusGreenNetEntity._handle_state_update
                                             (self._device = device; async_write_ha_state)
                                           → OpusGreenNetEvent._handle_state_update (event)
  SIGNAL_AVAILABILITY_UPDATE_<EAG>        → async_write_ha_state only
  SIGNAL_DEVICE_REMOVED_<EAG>             → sensor/binary_sensor/lock release reservations

Entity: properties read live from self._device.channels[...]; commands via
coordinator.async_* → async_send_command → MQTTRequestManager (global queue).
```

Command path: `Entity.async_*` → snapshot `(channel, state_revision)` →
`coordinator.async_turn_on/…` → `_with_channel_if_needed` → `async_send_command`
(availability check, `_command_waiters++`) → `MQTTRequestManager.async_request`
(per-answer-topic lock → route or temporary subscription → global `PriorityLock` →
`before_publish` = `snapshot_feedback` → publish → 10 s ACK timeout) → unconfirmed
fields → `_schedule_reconciliation_queries` → entity applies its optimistic state
if `state_revision` is unchanged.

## 2. Domain state mutation sites

Domain state means `coordinator.devices` and the fields of `EnOceanDevice` /
`EnOceanChannel`.

| # | Location | Mutation |
|---|----------|----------|
| M1 | `EnOceanDevice.update_from_telegram` | resets `last_button*` on all channels; per function `state_revision += 1` and the field; `last_seen`, `dbm` |
| M2 | `EnOceanDevice._update_numeric_field` | `setattr(channel, attr, …)` (dynamic) |
| M3 | `EnOceanDevice.get_or_create_channel` | creates `channels[n]` |
| M4 | `coordinator._apply_scalar_telemetry` | `device.dbm` directly (**without** M1); battery via M1; `_device_data` cache |
| M5 | `coordinator._finalize_device_stream` | `last_command_error = None` on confirmed feedback; M1; `_mark_and_log_dispatch` |
| M6 | `coordinator._apply_known_device_state_property` | `last_command_error`; M1; metadata |
| M7 | `coordinator._handle_put_answer_state` | set/clear `last_command_error` |
| M8 | `coordinator._start_cover_movement` | `channel.movement`, `state_revision += 1` |
| M9 | `coordinator._finish_cover_movement` | `channel.position = target` (assumed), `state_revision += 1` |
| M10 | `coordinator._clear_cover_movement` | `channel.movement = None` |
| M11 | `coordinator._create_device_from_data` | **replaces** `devices[dev]` with a new object; carries over `channels` (identity preserved), M1 with an empty list (button reset), `profile`, `last_update_*`, `last_command_error`; conditionally `_apply_initial_state`; `rotationTime`/`channel` |
| M12 | `coordinator._apply_initial_state` | M1, `last_update_source = "discovery"` |
| M13 | `coordinator._finalize_telegram` | placeholder device in `devices`; M1; `last_command_error = None` on operational state |
| M14 | `coordinator._mark_and_log_dispatch` | `last_update_source`, `last_update_*_monotonic` |
| M15 | `coordinator.async_get_device_profile` | `device.profile` |
| M16 | `coordinator.async_forget_device` | `devices.pop` plus all caches and timers of the device |
| M17 | `light.py` `async_turn_on/off` | optimistic after ACK: `is_on`, `brightness` (no `state_revision++`) |
| M18 | `switch.py` `async_turn_on/off` | optimistic after ACK: `is_on` |
| M19 | `cover.py` open/close/set_position/set_tilt | optimistic after ACK: `position`, `angle` |
| M20 | `OpusGreenNetEntity._handle_state_update` | reference swap `self._device = device` (entity state, not domain) |

Climate and lock write no optimistic state. M17–M19 only run if
`_channel_state_matches(snapshot)` holds.

## 3. Ownership of tasks, timers, subscriptions, futures and locks

| Resource | Owner | Created by | Released by |
|----------|-------|------------|-------------|
| Background tasks (`_refresh_task`, reconciliation tasks) | `coordinator._tasks` via `_create_task` | refresh, reconciliation | `_cancel_background_work` (cancel), `async_unload` (cancel + gather) |
| Binary sensor removal tasks | config entry (`entry.async_create_background_task`) | `async_removing` | HA on entry unload |
| Discovery timer (2 s) | `coordinator._discovery_timer` | discovery messages | set to `None` on expiry; `async_unload`; **not** in `_cancel_background_work` |
| Telegram timers (0.15 s) | `_pending_telegrams[dev]` | flattened telegrams | finalisation, forget, disconnect, unload |
| Delta timers (0.02 s) | `_pending_device_streams[dev]` | deltas | finalisation, forget, disconnect, unload |
| Reconciliation timers | `_pending_reconciliation_queries[(dev, ch)]` (list) | `_schedule_reconciliation_queries` | `_cancel_reconciliation_queries` (see section 4) |
| Cover movement timer | `CoverMovement.cancel` in `_cover_movements` | `_start_cover_movement` | `_clear_cover_movement` |
| Health interval (60 s) | `coordinator._subscriptions` | `async_setup` | `async_unload` |
| MQTT subscriptions (control, stream, connection status) | `coordinator._subscriptions` + topic lists | setup, refresh | `async_unload`; disconnect only clears routes, not subscriptions (HA resubscribes) |
| Temporary answer subscriptions and `on_subscribe_done` | `MQTTRequestManager._cleanups` via `_own_cleanup` | `_async_request_locked` | request `finally`, `async_cancel_pending` |
| Pending SUBACK callbacks | local in `async_wait_for_subscriptions` | | `finally` |
| Request futures (`subscribed`, `response`) | `MQTTRequestManager._waiters` | per request | failed via `async_cancel_pending`/`async_cancel_device`; `finally` removes them and retrieves `exception()` |
| Answer routes | `MQTTRequestManager._routes` / `_route_patterns` | request / setup | `finally`; disconnect and unload clear the patterns |
| Per-answer-topic lock | `MQTTRequestManager._locks` | `setdefault` | **never removed** (grows with devices × request kinds) |
| Global send lock | `MQTTRequestManager._send_lock` | | `release` in `finally` |
| Generations | `_generation`, `_device_generations` | | invalidate stale requests |
| Dispatcher connections | entity (`async_on_remove`), platform (`entry.async_on_unload`) | | HA |

## 4. Reconciliation checks after 5 and 20 seconds

- **Implementation:** `OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)` in
  `coordinator.py`. `_schedule_reconciliation_queries(dev, ch, fields=…)` merges
  still-pending fields with the new ones, cancels existing timers and creates one
  `async_call_later` per delay. If `position` is expected and a cover estimate is
  active, `remaining travel + (10.0, 20.0)` is used instead (remaining includes the
  5 s grace).
- **Triggers:** (a) `async_send_command` after a successful ACK, only for fields
  without new feedback since `snapshot_feedback` (per-field revision); (b)
  `_finalize_telegram` for outbound `to` telegrams without a local ACK waiter.
  Fields come from `_command_fields_by_channel`: `dimValue`, `position`, `angle`,
  `temperatureSetpoint`, `switch`, `heaterMode`; STOP expects `position` and, with
  tilt support, `angle`. Status queries (`query`) produce no fields and therefore
  trigger **no** follow-up reconciliation (no cascade).
- **Execution:** the callback skips if `not available` or a reconciliation for
  `(dev, ch)` is already running (`_reconciling`); otherwise it starts a task
  `async_query_device_status(background=True)` with `PRIORITY_BACKGROUND`.
  `HomeAssistantError` is only logged. No retry.
- **Cancellation:** complete feedback in `_record_confirmed_feedback`, rejected PUT
  answer (all channels of the device), STOP (coordinator and `to` telegram),
  replacement by a new schedule, replacement of a cover movement (pending fields
  are kept), a failed command with a started movement, `async_forget_device`,
  disconnect / bridge down (`_cancel_background_work`), unload.
- **Tests:** the literal values `[5, 20]` are pinned in `test_cover_stop.py:95`,
  `test_cover_movement.py:188/233` and `test_reconciliation_feedback.py:107/166/223/318/396`;
  the count in `test_coordinator_mqtt.py:105/131` (against the constant); the cover
  offset in `test_cover_movement.py:429`.
- **Observations (no change in phase 1):**
  - B1: after both timers expire without feedback, `_pending_reconciliation_fields`
    and the list of expired handles remain. A later command on the same channel
    merges the old fields into its expected set. Effect: the later schedule is
    only ended early by feedback for all fields. The number of slots per command
    stays at most 2, so frequency does not increase. Assessment: maintainability
    hypothesis, not a proven bug.
  - Any change of timing values or frequency is excluded (A1).

## 5. Event emission and stale `last_button` fields

- `last_button`/`last_button_action` are only set in M1 and are reset on all
  channels at the start of every M1 call. `OpusGreenNetEvent._handle_state_update`
  fires an event on **every** `SIGNAL_DEVICE_STATE_UPDATE` if channel 0 has both
  fields set.
- Existing protection: retained telegrams are discarded; button keys from
  `stream/devices` are ignored; rediscovery resets the fields (M11); availability
  updates only call `async_write_ha_state`
  (`test_availability_changes_do_not_replay_last_rocker_event`).
- Dispatch paths **without** a preceding M1 reset:
  1. `_apply_scalar_telemetry` with `dbm` (M4). **Finding F1, code path proven.**
  2. `_dispatch_cover_estimate`: cover devices only; not reachable for rockers.
  3. `_handle_put_answer_state`: only after commands; rockers have no command path.
- **F1 reproduction** (script outside the repository, not committed, against base
  `3e52fce`): rocker `F6-02-01`, flattened `from` telegram `buttonA0`/`pressed`,
  then two live messages `stream/device/R1/dbm` (`-61`, `-62`). All three
  dispatches carry `('buttonA0', 'pressed')`, so the event entity would fire
  `buttonA0_pressed` three times.
- **Status:** code path and unit-level reproduction proven. Whether the gateway
  publishes `stream/device/<DEV>/dbm` live for rockers is **unverified** (no
  hardware verification). No test covers the case. A fix is proposed for phase 2,
  subject to explicit approval as a bug fix.

## 6. Typing and dynamic paths

- All modules use `from __future__ import annotations` except `lock.py` and
  `binary_sensor_descriptions.py`. There is no `py.typed`, no mypy configuration
  and no type-checking gate in CI.
- `mypy 2.4.0` for information (local, not a project dependency): default mode
  **27 errors**, `--strict` **72 errors in 15 files** (36 of them in `coordinator.py`).
  Main classes: payload type `str | bytes | bytearray` vs. `decode_response(str | bytes)`;
  `int | None` vs. `int` (`default_channel_id`, `heat_area_type`); `float` in `int`
  variables (cover progress, `delays`); `dict` without `append` in
  `_set_nested_property`; `"None" not callable` (`_pending_telegrams`);
  `entity_id: str | None` passed to `log_entity_state_write`;
  `_attr_supported_features = 0` in `lock.py`; `voluptuous.Schema` vs.
  `probatio.Schema` (HA 2026.10 stubs).
- `Any`: 70 occurrences (`coordinator.py` 23, `enocean_device.py` 12, `diagnostics.py` 9, …).
  Unparameterised types: `Callable` (6×), `dict`/`list` as parameters (9×).
- `getattr`: `msg.retain`/`msg.topic` (8×, duck typing for test namespaces);
  string-based entity attribute access in `sensor.py` (2×), `binary_sensor.py` (1×),
  `binary_sensor_descriptions.py` (2×); `entity.hass` (2×).
  `setattr`: `_update_numeric_field` (2×).
- One `# noqa: SLF001` (`queue_depth`).

## 7. Tests, coverage and quality commands (baseline)

Environment: Python 3.14.3, virtualenv outside the repository (`/home/user/venv-opus`),
`homeassistant==2026.10.1` (latest stable on PyPI at execution time, matching the
CI resolution), `paho-mqtt==2.1.0`, `pytest==9.1.1`, `pytest-asyncio==1.4.0`,
`pytest-cov==7.1.0`, `ruff==0.16.3`, and for information `mypy==2.4.0`.

| Command (repository root) | Result |
|---------------------------|--------|
| `pip install -r requirements_test.txt` | succeeded |
| `python -m pip check` | "No broken requirements found." (rc 0) |
| `ruff check .` | "All checks passed!" (rc 0) |
| `ruff format --check .` | "48 files already formatted" (rc 0) |
| `pytest -v --cov --cov-report=term-missing -p no:cacheprovider` | **865 passed**, 0 failed, 1 warning (aiohttp deprecation inside HA), 115.5 s, rc 0 |
| Total coverage | **91.76 %** (threshold 75 %) |
| `mypy custom_components/opus_greennet --python-version 3.14` | rc 1, 27 errors (informational, not a gate) |
| `mypy custom_components/opus_greennet --strict` | rc 1, 72 errors (informational, not a gate) |
| `hassfest` | **not executed** (GitHub Action only) |

`-p no:cacheprovider` only avoids creating `.pytest_cache` in the working tree.

Coverage per module: `__init__.py` 72 %, `light.py` 85 %, `diagnostics.py` 89 %,
`coordinator.py` 90 %, `mqtt_transport.py` 91 %, `cover.py` 94 %, `config_flow.py` 95 %,
`binary_sensor.py` 95 %, `lock.py` 95 %, `enocean_device.py` 97 %, `entity.py` 98 %,
`sensor.py` 98 %, `climate.py` 99 %, `binary_sensor_descriptions.py`/`const.py`/
`event.py`/`switch.py` 100 %.

Passed tests per file: enocean_device 245, coordinator_parsing 65, entities 65,
coordinator_transport 53, gateway_payload_compatibility 53, coordinator_mqtt 51,
reconciliation_feedback 51, cover_movement 45, coordinator_helpers 42,
binary_sensor_descriptions 35, detector_support 27, ha_lifecycle 17,
binary_sensor_discovery 13, retained_device_answers 13, beta_entity_migration 12,
init 12, device_removal 11, event_entity 11, scalar_telemetry 11,
release_transport_safety 9, window_handles 9, cover_stop 6, config_flow 5, diagnostics 4.

All tests are unit or HA integration tests with a simulated MQTT boundary
(`tests/ha_helpers.MQTTTransport`). They are **not** hardware verification.

## 8. Exclusion check A1–A5

Diff of this phase: only files under `docs/quality/`.

| ID | Result |
|----|--------|
| A1 | compliant: no pollers, intervals or slots changed; no roadmap item for it |
| A2 | compliant: uptime, health and availability code unchanged; no use of uptime as state evidence planned |
| A3 | compliant: no quarantine, drain phase, lock or cool-down; timeouts unchanged |
| A4 | compliant: existing retain/freshness logic only documented (`protocol-contract.md` §6), not changed; F1 concerns transient event fields and is not freshness hardening |
| A5 | compliant: entity inventory only documented (`protocol-contract.md` §8); no exposure policy planned |

## 9. Compatibility and residual risks

- Compatibility: no runtime change, hence no effect on unique IDs, entity IDs,
  topics, services or devices.
- R1: CI tests against the latest stable HA release; this baseline applies to
  HA 2026.10.1 and may drift with later HA releases.
- R2: `hassfest` was not executed locally.
- R3: all protocol statements are derived from code, tests and the vendor
  reference, not from hardware observation (see `protocol-contract.md` §11).
- R4: F1 is proven in code but unverified on hardware; B1 is a hypothesis.
- R5: `_locks` in the request manager is never pruned (bounded by the number of
  devices; low risk).

## 10. Review of the following phases against the actual code

See `implementation-plan.md`, sections "Phase overview" and "Already solved".
Every proposed phase names concrete target modules; no phase contains work
related to A1–A5.

## 11. Implementation notes for phase 2

- Base: HEAD of `feat/opus-q01-baseline` (reported in the chat), not `main`.
- Before starting: verify the branch name from the phase prompt; `git status` must be clean.
- If phase 2 follows the "event contract" proposal: first write a failing
  regression test for F1 (rocker, telegram, then `stream/device/<DEV>/dbm`; event
  at most once), then the minimal fix (e.g. reset the transient fields before the
  dispatch in the scalar path). Leave `state_revision` and the "dispatch only on
  change" condition untouched. Do not touch retain logic (A4).
- Phase 1 reproduction script (not in the repository): rocker `F6-02-01`,
  `_handle_telegram_property_message` with `from/functions/0/key=buttonA0` and
  `from/functions/0/value=pressed`, `_finalize_telegram`, then
  `_handle_device_stream_message` with `stream/device/R1/dbm`; `async_call_later`
  and `async_dispatcher_send` patched.
- Quality commands as in section 7; `pytest` takes about 2 minutes.
