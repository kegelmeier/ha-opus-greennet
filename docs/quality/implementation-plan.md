# OPUS GreenNet Quality Series – Implementation Plan

This document anchors the sequential quality series for
`fubu2k/ha-opus-greennet` in the repository. It is updated in every phase.
Per-phase details live in `docs/quality/phases/phase-NN.md`; the protocol
contract lives in `docs/quality/protocol-contract.md`.

## Goal

Improve code quality, maintainability, testability, typing and documentation
of the existing OPUS/EnOcean MQTT integration. Functional behaviour is
preserved unless a phase explicitly permits a proven bug fix.

## Repository target

- Target repository: `fubu2k/ha-opus-greennet` (remote `origin`, default branch `main`).
- References to `kegelmeier/ha-opus-greennet` in `CLAUDE.md`, `README.md` and
  `manifest.json` are historical upstream references. They do not change the
  target and are not rewritten in this series (documentation URLs and
  `codeowners` are metadata, not in scope).

## Binding exclusions (A1–A5)

These topics are not implemented as side work, as a prerequisite or as a later
phase. If a goal strictly requires an excluded change, this is documented and
work stops.

| ID | Exclusion | Frozen current values (base `3e52fce`) |
|----|-----------|----------------------------------------|
| A1 | No more frequent status polling: no new pollers, shorter intervals or additional reconciliation slots | `GATEWAY_HEALTH_INTERVAL = 60 s`, `RECONNECT_RESYNC_DEBOUNCE_SECONDS = 60`, `OUTBOUND_STATE_RECONCILIATION_DELAYS = (5, 20)`, `COVER_RECONCILIATION_DELAYS = (10.0, 20.0)` after movement end, `COVER_MOVEMENT_GRACE_SECONDS = 5.0` |
| A2 | No change to uptime, health or availability semantics; bridge uptime is never used to confirm device state | `available = not _unloaded and _gateway_available and _bridge_connected is not False and mqtt.is_connected()`; a command timeout does not change availability |
| A3 | No topic quarantine, drain phase, topic lock or additional cool-down after a timeout | `REQUEST_TIMEOUT = 10 s`, `SUBSCRIPTION_TIMEOUT = 30 s`, `SETUP_REQUEST_TIMEOUT = 30 s`, `SETUP_SUBSCRIPTION_TIMEOUT = 180 s`, `MIN_SEND_INTERVAL = 0.0` |
| A4 | No new replay/freshness hardening (timestamp/sequence evaluation, replay generations, prioritising live over retained data); existing logic is neither removed nor restructured | see "Existing retain/freshness logic" in `protocol-contract.md` §6 |
| A5 | No opt-in/exposure policies against entity bloat; no change to entity count, selection options, enablement defaults or registry configuration | signal strength sensor disabled by default, all others enabled; entity inventory in `protocol-contract.md` §8 |

## Repository rules (summary)

- No direct commits to `main`, no version change, no `CHANGELOG.md` update.
- No release, no merge, no automatic PR, no force-push.
- One-time exception on explicit request: phases 1-5 were merged into `main`
  by fast-forward to `367dd9a` and a `CHANGELOG.md` "Unreleased" entry was
  added in `f400866`. This does not lift the rules for later phases.
- No real device actions or configuration writes to hardware.
- Unique IDs, entity IDs, topic paths, public services and device assignments stay unchanged.
- OPUS wire values, channel zero, `notAvailable`, cover inversion, the STOP payload
  and the proprietary HeatArea profiles are preserved (see `protocol-contract.md`).
- No automatic write retries after a timeout.
- No blanket migration to `DataUpdateCoordinator`.
- No additional dependencies without a concrete need.

## Branch chain

Phase 1 is based on `main` as verified at the start. Branch names are taken
from each phase prompt; later branches are based on `main` after the merge. Each following phase is
based on the completed HEAD of the previous branch. No rebase, no squash, no
deletion of earlier branches. If a branch already exists, it is compared with
this documentation and never overwritten.

| Phase | Branch | Predecessor | Base SHA | Status |
|-------|--------|-------------|----------|--------|
| 1 | `feat/opus-q01-baseline` | `main` | `3e52fce31e7a2d503eef4820f4c706ebcfc751ab` | completed |
| 2 | `fix/opus-q02-state-ownership` | `feat/opus-q01-baseline` | `ff164f9db7adde27dfa8c37ddf6c8cf350b633a0` | completed |
| 3 | `fix/opus-q03-event-lifecycle` | `fix/opus-q02-state-ownership` | `16d9a45777cef8891fe8772963c8827b8a479798` | completed |
| 4 | `fix/opus-q04-background-efficiency` | `fix/opus-q03-event-lifecycle` | `8ece6eaf7be8ce7ccdbf5c9cf54cb93449a10ea7` | completed |
| 5 | `feat/opus-q05-strict-typing` | `fix/opus-q04-background-efficiency` | `bf2ad417559cdaee3b695274a37455476a356e6c` | completed |
| 6 | – (metadata and exposure policy) | – | – | deferred (see below) |
| 7 | `feat/opus-quality-scale-evidence` | `main` (phases 1-5 merged, CHANGELOG `f400866`) | `f4008667c0d42b202554fb0849ee3226b81b011a` | completed |

A phase's HEAD SHA is never written into its own commit. It is reported in the
chat and recorded as the base SHA in the next phase's documentation.

## Quality commands (CI equivalent)

Taken from `.github/workflows/tests.yml`:

```bash
python -m pip install --upgrade -r requirements_test.txt "homeassistant==<latest stable>"
python -m pip check
ruff check .
ruff format --check .
python -m mypy
pytest -v --cov --cov-report=term-missing --cov-report=json:coverage.json
python script/check_coverage.py coverage.json
# plus three repetitions of the lifecycle and race regression suites
```

Since phase 7 the total coverage threshold is `fail_under = 97`
(`pyproject.toml`, previously 75) and `script/check_coverage.py` requires more
than 95 % for every integration module and 100 % for `config_flow.py`. `hassfest` only
runs in CI (`home-assistant/actions/hassfest@master`) and cannot be executed
locally. Since phase 5, `python -m mypy` (strict, configured in
`[tool.mypy]` of `pyproject.toml`, `mypy==2.4.0` in `requirements_test.txt`)
is a CI gate; in phases 1-4 it was run for information only.

## Phase overview

Phase 2 was defined by its phase prompt (state ownership) and differs from the
phase 1 proposal (event contract). The contents of phases 3–7 remain
proposals derived from the phase 1 analysis. The explicit prompt for each
phase is authoritative; if it differs, the prompt wins and this plan is
updated in that phase.

### Phase 2 – State ownership (completed)

- Optimistic post-ACK state rules moved from the light, switch and cover
  entities into the domain layer (`EnOceanDevice.snapshot_channel_state`,
  `channel_state_unchanged`, `apply_switch_estimate`, `apply_dim_estimate`,
  `apply_cover_position_estimate`, `apply_cover_tilt_estimate`,
  `ChannelStateSnapshot`). Climate and lock had no state mutation.
- No change to ACK, availability, reconciliation, cover travel estimate,
  dispatch or wire payloads; no new query or timer. Details in `phases/phase-02.md`.

### Open findings (not scheduled; require an explicit phase prompt)

- F1 (from phase 1, fixed in phase 3): later dispatches (`dbm`, PUT answers,
  metadata) replayed the previous rocker event because `last_button` /
  `last_button_action` stayed set. Fixed by
  `EnOceanDevice.clear_button_occurrence()` after the telegram dispatch.
- O1 (from phase 2): an ACK with `httpStatus` 201 (SoftSmartAck, "will send
  later" per the vendor reference) is treated like 200, so the optimistic
  estimate is applied although the device may not have received the command
  yet. Changing this would change ACK semantics and is out of scope for the
  phase 2 prompt; documented only.

### Phase 3 – Event lifecycle (completed)

- Transient button fields are cleared right after the dispatch of the
  telegram that carried the press. Details in `phases/phase-03.md`.

### Phase 4 – Background efficiency (completed)

- Queued, not yet published background status checks are dropped when
  feedback or a newer command supersedes them (`_reconcile_needed`).
- Idle per-answer-topic locks are released from `MQTTRequestManager._locks`.
- Dispatch-driven entity writes are skipped when the visible projection is
  unchanged (`OpusGreenNetEntity._handle_state_update`).
- Discovery timer churn was measured; no change (no evidenced need).
- No change to delays (5 s / 20 s), timeouts, priorities, availability or
  retained handling. Details and measurements in `phases/phase-04.md`.

### Phase 5 – Strict typing (completed)

- All 18 integration modules pass `mypy --strict` with `warn_unreachable`
  (base `bf2ad41`: 74 strict errors in 15 files). No `cast`, no
  `type: ignore`, no `ignore_errors`; remaining `Any` only in Home Assistant
  signatures, listed with reasons in `phases/phase-05.md`.
- New `wire.py` separates raw OPUS wire data (`JsonObject`, `WireFunction`)
  from the domain model and validates containers and fields at the MQTT/JSON
  boundaries; `response_status()` centralises the unchanged 200/201 status
  contract.
- Dynamic `setattr`/`getattr` on channel fields replaced by explicit access;
  typed `OpusGreenNetConfigEntry` used consistently; `probatio` imported
  directly (runtime-identical since HA 2026.9).
- `mypy==2.4.0` added as a test dependency and as a CI step. Typing-motivated
  edge-case notes and the differential check against the base are in
  `phases/phase-05.md`. This consumes the former "Typing without behaviour
  change" proposal.

The proposals below keep their original working titles; their phase numbers
are assigned by the next phase prompts.

### Phase 6 – Metadata and exposure policy (deferred)

- Deferred to a later point on request. The phase 6 prompt (entity metadata and
  exposure policy) conflicts with exclusion A5 (no opt-in/exposure policies, no
  change to entity count, enablement defaults or registry configuration). It
  can only be scheduled once A5 is explicitly lifted for it. No phase 6 branch
  exists.

### Phase 7 – Quality scale evidence and CI (completed)

- `custom_components/opus_greennet/quality_scale.yaml` with all 54 official
  rule keys (done / todo / exempt with reasons). Self-assessment of a custom
  integration, no official tier; `manifest.json` declares no `quality_scale`.
- Evidence matrix rule -> implementation -> tests -> documentation in
  `docs/quality/quality-scale-evidence.md`, checked against the YAML by
  `tests/test_quality_scale.py` (official schema, rule keys and the applicable
  hassfest validators).
- Coverage 92.76 % -> above 97 % with new tests only; per-module gate
  (`script/check_coverage.py`), `fail_under = 97`, repeated race suites in CI.
- README: removal, known limitations and troubleshooting (broker / gateway /
  radio actuator, ACK 200/201, retained state, estimates, missing request ID,
  recovery). Details in `phases/phase-07.md`.

### Proposal – Extract a protocol/parsing module

- Move pure functions out of `coordinator.py` into a new module (e.g. `protocol.py`):
  topic regexes, `_set_nested_property`, `_parse_value`, `_merge_device_data`,
  `_device_state_functions`, `_indexed_changed_functions`,
  `_configuration_state_functions`, `_rwm_transmit_modes`,
  `_channel_from_functions`, `_confirmed_fields_by_channel`,
  `_command_fields_by_channel`, payload builder for `state.functions`.
- Keep compatibility wrappers as static methods so existing tests stay unchanged.
- Golden/contract tests against `protocol-contract.md` (channel zero, STOP,
  inversion, HeatArea modes, `notAvailable`).
- Target modules: `coordinator.py`, new protocol module, `const.py` (read only),
  new tests `tests/test_protocol_contract.py`.

### Proposal – Structure ownership and lifecycle in the coordinator

- Move the cover movement estimate (`CoverMovement`, `_start_/_finish_/_clear_cover_movement`)
  and the reconciliation scheduler (`_schedule_/_cancel_reconciliation_queries`,
  `_pending_reconciliation_*`, `_reconciling`) into clearly separated helpers with
  explicit ownership. Timing values and frequency stay unchanged (A1); the literal
  `[5, 20]` tests stay unchanged.
- Consolidate the duplicated cleanup in `_cancel_background_work` and `async_unload`.
- Lifecycle regression tests: no orphaned timers, tasks or futures after disconnect,
  bridge down, forget and unload.
- Target modules: `coordinator.py`, possibly new `cover_movement.py` and
  `reconciliation.py`, tests `test_cover_movement.py`, `test_reconciliation_feedback.py`,
  `test_release_transport_safety.py`.

### Proposal – Platform and entity consolidation

- Move the repeated discovery listener pattern (`light`, `switch`, `cover`,
  `climate`, `event`) into shared helpers in `entity.py`. The optimistic post-ACK
  rules already live in the domain layer since phase 2.
- Candidate: the HeatArea "on" wire value (`"on"` for D1-4B-06, `"heating"`
  otherwise) is chosen in `climate.py`; it could move next to the other
  profile rules in `enocean_device.py` with unchanged values.
- Unique IDs, entity count, translation keys and enablement defaults unchanged (A5);
  inventory tests protect this.
- Test gaps of `__init__.py` were closed in phase 7 (tests only; the
  unreachable `result is None` checks remain and are documented there).
- Target modules: `entity.py`, `light.py`, `switch.py`, `cover.py`, `climate.py`,
  `event.py`, `sensor.py`, `binary_sensor.py`, `__init__.py` (tests only, or the
  dead `result is None` checks after agreement).

### Proposal – Documentation and quality gates

- The quality commands in the README, the coverage gates and the quality-scale
  evidence were delivered in phase 7. Remaining: complete docstrings and
  module descriptions; final report of the series.
- The `mypy` CI step exists since phase 5.
- Target modules: `README.md`, `docs/quality/*`, possibly `.github/workflows/tests.yml`, `pyproject.toml`.

## Already solved (do not re-implement)

Confirmed against the code at base `3e52fce` and existing tests:

- Retained `getAnswer/devices` answers create neither discovery nor state
  (`_handle_get_answer_devices`, `tests/test_retained_device_answers.py`).
- Retained telegrams trigger neither events nor state (`_handle_telegram_property_message`).
- Button keys from `stream/devices` are not treated as new occurrences (`_apply_known_device_state_property`).
- Availability changes do not replay an old rocker event
  (`test_availability_changes_do_not_replay_last_rocker_event`).
- Rediscovery resets the transient button fields (`update_from_telegram({"functions": []})`).
- A command timeout does not change gateway availability and is not retried automatically.
- Global send queue with priorities (command > query > background), exactly one request in flight.
- STOP as a dedicated function `{"key": "stop", "value": "true"}` with channel selector (`tests/test_cover_stop.py`).
- Feedback is matched per channel and field; feedback for an earlier command cannot
  confirm a later one (`tests/test_reconciliation_feedback.py`).
- Idempotent binary sensor discovery and declarative descriptions
  (`tests/test_binary_sensor_discovery.py`, `tests/test_binary_sensor_descriptions.py`).
- Child device removal without MQTT writes and with request isolation (`tests/test_device_removal.py`).
