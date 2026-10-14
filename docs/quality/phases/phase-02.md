# Phase 2 – State Ownership

## Status and scope

- **Status:** completed.
- **Goal (phase prompt):** give the existing state logic one clear owner,
  without new ACK, uptime, replay or freshness semantics.
- **In scope:** state mutations in the cover, light, switch, climate and lock
  entities; moving independent domain mutations out of the entity layer;
  regression and characterisation tests.
- **Out of scope:** ACK handling (including 200/201), availability,
  reconciliation scheduling, cover travel estimate, dispatch behaviour, wire
  payloads, entity identities, finding F1 (event replay, see
  `implementation-plan.md` "Open findings").

## Branch and base

| Field | Value |
|-------|-------|
| Branch | `fix/opus-q02-state-ownership` |
| Predecessor branch | `feat/opus-q01-baseline` |
| Base SHA | `ff164f9db7adde27dfa8c37ddf6c8cf350b633a0` (final HEAD of phase 1, "docs: translate phase 1 quality baseline to English") |
| Pre-checkout check | `git fetch`; local `HEAD == origin/feat/opus-q01-baseline == ff164f9…`; `git status --porcelain` empty; branch existed neither locally nor on the remote (`git ls-remote --heads origin`) |

The final HEAD SHA of this phase is not part of its own commit. It is reported
in the chat and recorded as the base SHA of phase 3.

## 1. State mutation inventory (before)

| Platform | Mutation in the entity after an accepted ACK | Guard |
|----------|-----------------------------------------------|-------|
| light | `channel.is_on`, `channel.brightness` (dimmer: 100 / requested value / 0; switching light: `is_on` only) | `_channel_state_snapshot` / `_channel_state_matches` in `entity.py` |
| switch | `channel.is_on` | same |
| cover | `channel.position` (open 0, close 100, `100 - ha_position`), `channel.angle` | same; STOP writes nothing |
| climate | none (commands only) | – |
| lock | none (read-only; writes raise `autolock_read_only`) | – |

Each entity also created a missing channel through `get_or_create_channel`.
The coordinator already owned all other command-related state: cover travel
estimate (`_start_/_finish_/_clear_cover_movement`, which advance
`state_revision`), reconciliation fields and timers, and `last_command_error`.
None of these were changed.

## 2. Change

The rules for the optimistic post-ACK state moved into the existing domain
layer (`enocean_device.py`):

- `ChannelStateSnapshot` (frozen, identity based): channel id, channel object, revision.
- `EnOceanDevice.snapshot_channel_state(channel_id)` and
  `EnOceanDevice.channel_state_unchanged(snapshot)`: the former entity guard
  with identical semantics (same channel object **and** same `state_revision`).
- `apply_switch_estimate`, `apply_dim_estimate` (sets `brightness` and
  `is_on = value > 0`), `apply_cover_position_estimate` (OPUS scale),
  `apply_cover_tilt_estimate`. Each returns whether the estimate was applied,
  creates a missing channel exactly as before and does **not** advance
  `state_revision` (unchanged: an estimate is not feedback).

Entities now only translate Home Assistant input (brightness scale, cover
inversion, the "brightness 0 means off" rule, tilt support check), delegate the
command to the coordinator, ask the domain object to apply the estimate and
write their own state if it was applied. `entity.py` lost
`_channel_state_snapshot` and `_channel_state_matches`.

Files: `custom_components/opus_greennet/enocean_device.py`, `entity.py`,
`light.py`, `switch.py`, `cover.py`; new `tests/test_state_ownership.py`;
`docs/quality/implementation-plan.md`, this file.

### Design decisions

- **Domain layer instead of coordinator.** The estimates are pure channel
  state rules. Keeping them out of the coordinator leaves the coordinator
  command API, `async_send_command`, the ACK path and the request manager
  untouched (acceptance: ACK and availability handling unchanged). Existing
  entity tests with a mocked coordinator keep working unchanged.
- **No dispatch for estimates.** The entity still writes only its own state.
  Dispatching `SIGNAL_DEVICE_STATE_UPDATE` instead would add writes on every
  entity of the device and would re-fire the last rocker event on event
  entities of the same device (see F1). Consistency (task 7): the estimated
  fields (`is_on`, `brightness`, `position`, `angle`) are read only by the one
  light/switch/cover entity of that channel (verified by search: no sensor,
  binary sensor, event or diagnostics code reads them), and each channel has
  exactly one such entity.
- **No manager module (task 6).** The logic is six short methods next to the
  data they change; a separate module would not isolate a responsibility any
  better. Cover travel and reconciliation stay in the coordinator (proposal
  for phase 5).
- **Climate.** The HeatArea "on" wire value (`"on"` for D1-4B-06, `"heating"`
  otherwise) is chosen in `climate.py`. It is a translation to the wire and not
  a state mutation, so it was not moved here (noted as a phase 6 candidate).

### Behaviour preserved (protections)

- Feedback, discovery state or a cover travel estimate that arrives while a
  command awaits its ACK advances `state_revision` and therefore suppresses the
  estimate; feedback for another channel does not.
- Rediscovery replaces the device object but shares the channel mapping, so a
  snapshot taken before rediscovery stays valid, as before.
- Failed commands (rejected, malformed, timeout, gateway unavailable) raise as
  before and leave the state untouched; no retry; availability unchanged.

## 3. Tests

New file `tests/test_state_ownership.py` (65 tests):

- **Characterisation (52 tests)** through the real entity → coordinator →
  request manager → parser chain against the deterministic test broker:
  - accepted commands: exact wire payloads (channel selector including
    channel 0 on multi-channel devices, dimmer `dimValue`, switching light,
    cover inversion, tilt), resulting channel state, untouched sibling
    channels, exactly one state write, no coordinator dispatch, reconciliation
    delays exactly `[5, 20]`, exactly one publish;
  - STOP on channel 0 and 1: `{"key": "stop", "value": "true"}`, no estimate, no write;
  - command failure (rejected, malformed, timeout) × five actions: unchanged
    state, no write, one publish (no retry), no delayed query, availability unchanged;
  - gateway unavailable: nothing published;
  - feedback during the ACK wait wins over the estimate (switch, dimmer, cover
    position, tilt): one write from the feedback dispatch, none from the
    estimate, no delayed query;
  - feedback before the command is replaced by the estimate (two writes);
  - other-channel feedback during the ACK wait keeps the own estimate;
  - cover travel estimate: target not assigned while travelling; one write
    from the coordinator estimate dispatch; timer delays unchanged;
  - static check that `entity.py`, `light.py`, `switch.py`, `cover.py`,
    `climate.py` and `lock.py` import nothing from MQTT or `mqtt_transport`.
- **Domain unit tests (13 tests)** for the new `EnOceanDevice` methods.

Before/after comparison: the same test file was run in a temporary
`git worktree` at base `ff164f9` (removed afterwards). Result: the 52
characterisation tests **passed** on the unchanged code; the 13 domain unit
tests failed there only because the new methods do not exist yet. After the
refactoring all 65 pass. No existing test was changed.

### Executed commands

Environment: Python 3.14.3, venv outside the repository, `homeassistant==2026.10.1`,
`pytest==9.1.1`, `pytest-asyncio==1.4.0`, `pytest-cov==7.1.0`, `ruff==0.16.3`,
informational `mypy==2.4.0`.

| Command | Result |
|---------|--------|
| `python -m pip check` | "No broken requirements found." |
| `ruff check .` | "All checks passed!" |
| `ruff format --check .` | "53 files already formatted" (incl. Markdown) |
| `pytest -v --cov --cov-report=term-missing -p no:cacheprovider` | **930 passed**, 0 failed, 1 warning (aiohttp deprecation inside HA), 113.9 s |
| Total coverage | **92.09 %** (phase 1: 91.76 %; threshold 75 %) |
| Coverage of changed modules | `light.py` 95 % (was 85 %), `switch.py` 100 %, `cover.py` 93 %, `entity.py` 98 %, `enocean_device.py` 97 % |
| `pytest tests/test_state_ownership.py` at base `ff164f9` (worktree) | 52 passed, 13 failed (new API missing, expected) |
| `mypy custom_components/opus_greennet --python-version 3.14` | 27 errors (unchanged, informational) |
| `mypy custom_components/opus_greennet --strict` | 72 errors (unchanged, informational) |
| `hassfest` | **not executed** (GitHub Action only) |

All tests use a simulated MQTT boundary. They are **not** hardware verification.

## 4. Exclusion check A1–A5

| ID | Result |
|----|--------|
| A1 | compliant: `coordinator.py` unchanged; no poller, interval, query or timer added; tests pin `[5, 20]` and the cover delays |
| A2 | compliant: uptime, health and availability code unchanged; failed commands still leave availability untouched |
| A3 | compliant: no quarantine, drain, lock or cool-down; timeouts unchanged |
| A4 | compliant: retain/freshness logic untouched; the revision guard is the existing one, moved without change |
| A5 | compliant: no entity, unique ID, translation key or enablement default changed |

Repository rules: no change to topics, services, wire values, channel zero,
`notAvailable`, cover inversion, STOP payload or HeatArea profiles; no version
or `CHANGELOG.md` change; no new dependency; no push, PR or merge.

## 5. Compatibility and risks

- Entity identities, coordinator API, services and payloads are unchanged.
- The private entity helpers `_channel_state_snapshot` / `_channel_state_matches`
  were removed; no code or test used them outside the entities.
- O1 (documented, not changed): an ACK with `httpStatus` 201 (SoftSmartAck,
  "will send later") is treated like 200, so the estimate is applied although
  the device may not have the command yet. Resolving this would change ACK
  semantics, which this phase forbids.
- F1 (from phase 1) remains open and unchanged.
- B1 (from phase 1, hypothesis) remains unchanged.

## 6. Notes for the next phase

- Base: final HEAD of `fix/opus-q02-state-ownership` (reported in the chat).
- `ChannelStateSnapshot` and the `apply_*_estimate` methods are typed and
  mypy-clean; phase 3 (typing) does not need to touch them.
- The characterisation tests in `tests/test_state_ownership.py` should stay
  unchanged in later phases as a regression guard for command behaviour.
