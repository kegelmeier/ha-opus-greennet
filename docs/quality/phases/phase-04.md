# Phase 4 - Background efficiency

- **Status:** completed (local commit; push only on request)
- **Branch:** `fix/opus-q04-background-efficiency`
- **Predecessor branch:** `fix/opus-q03-event-lifecycle`
- **Base SHA:** `8ece6eaf7be8ce7ccdbf5c9cf54cb93449a10ea7` (local and remote
  identical, clean working tree, branch did not exist before)

## Scope

Less superseded background work, fewer idle objects and fewer redundant
entity state writes. No higher polling rate, no new replay semantics.

## A. Reconciliation

**Before:** the existing 5 s / 20 s checks are timers per `(device, channel)`.
Feedback or a newer command cancelled the timers, but a check whose timer had
already fired and that was still waiting in the send queue was published
anyway. `_reconciling` already coalesced to one queued check per channel.

**Change (`coordinator.py`):**

- New set `_reconcile_needed`. A timer callback adds its channel; the existing
  coalescing via `_reconciling` is unchanged.
- `_cancel_reconciliation_queries` (called by confirming feedback, a newer
  command's `_schedule_reconciliation_queries`, STOP, failed PUT answer,
  device removal) also removes the channel from `_reconcile_needed`.
- `_async_reconcile_status` passes `still_needed` to the background query.
  `async_send_command` evaluates it inside the existing `before_publish` hook,
  i.e. while holding the send slot, immediately before publish. A superseded
  check raises `request_cancelled` (reason `superseded`) and is not sent; the
  error is handled internally and only logged at debug level.
- The flag is consumed at publish time, so a later timer that fires while an
  old check is still queued revives that queued request instead of being lost.
- Unload clears `_reconcile_needed`.

**Unchanged:** delays `(5, 20)` and `COVER_RECONCILIATION_DELAYS`, triggers,
priorities, timeouts, number of timers per command. User commands and STOP
are not deduplicated (`still_needed` is only used with `background=True`).
Partial feedback (other fields) keeps the check.

## B. Queue

- Ownership: `MQTTRequestManager` owns the per-answer-topic locks, the global
  send lock, waiters and cleanups; the coordinator owns its tasks.
- Growth: background admission is already bounded (at most one queued check
  per `(device, channel)` via `_reconciling`, one gateway refresh task). User
  commands are intentionally unbounded and never dropped. No admission limit
  was added.
- **Change (`mqtt_transport.py`):** an idle per-answer-topic lock (not held,
  no waiter) is removed from `_locks` after the request. This resolves the
  phase 1 residual risk "locks per answer topic are never cleaned up".
- Unchanged: global request serialisation, priorities, user command timeouts,
  no quarantine and no extra wait after timeout.
- Not changed (negligible): `_device_generations` keeps one integer per
  removed device for request invalidation.

## C. Discovery timers (measured, not changed)

Synthetic retained snapshot, 100 devices x 20 properties = 2000 messages
through `_handle_device_property_message`:

| Metric | Before | After |
|---|---|---|
| Timers created | 2000 | 2000 |
| Timers cancelled | 1999 | 1999 |
| Total handling time for 2000 messages | 8.1 ms | 7.9 ms |
| Raw `loop.call_later` + `cancel` for 2000 timers | 2.35 ms | 3.06 ms |

The churn is one timer per message, but the measured cost is a few
milliseconds for a full 100-device snapshot. There is no evidenced need, so
the timer logic and the completeness rules are unchanged. Wall-clock values
are from one local run in the sandbox and only indicate the order of magnitude.

## D. Entity state writes

**Change (`entity.py`, `OpusGreenNetEntity`):**

- `_handle_state_update` computes the visible projection with Home
  Assistant's `_async_calculate_state()` (state string incl. `unavailable`,
  all attributes incl. `supported_features`, capability and extra attributes)
  and freezes it with `freeze_projection()` into an immutable,
  order-independent tuple.
- If the projection equals the one written by the previous dispatch, the
  write is skipped.
- Every other write path (commands, availability handler, HA registry
  updates) goes through the overridden `_async_write_ha_state`, which clears
  the stored projection; the next dispatch is therefore always written.
- Fail-open: if the projection cannot be computed (no `hass`, `force_update`,
  exception), the entity writes as before.
- Event entities override `_handle_state_update` and are not affected; events
  are not deduplicated.

## Measurements (before = `8ece6ea`, after = this branch)

Same synthetic flows, run with the production request manager and a mocked
broker:

| Flow | Before | After |
|---|---|---|
| Command, no feedback, both timers fire (status queries) | 2 | 2 |
| 10x: feedback arrives while the 5 s check is queued | 10 | 0 |
| 10x: newer command supersedes the queued 5 s check | 10 | 0 |
| Answer-topic locks after requests to 100 devices | 101 | 0 |
| Cover + switch, 50 RSSI-only updates each (entity writes) | 50 + 50 | 1 + 1 |

The single remaining write per entity is the first dispatch after setup.
No flow produces more queries than before.

## Tests (executed)

- `python -m pytest -q --cov --cov-report=term-missing`: **948 passed,
  0 failed**, total coverage 92 %.
- `python -m ruff check .`: passed. `python -m ruff format --check .`: passed
  (56 files).
- `python -m pip check`: only an unrelated sandbox package conflict
  (`modal` / `protobuf`), not a project dependency.
- `python -m mypy custom_components/opus_greennet` (information only):
  27 errors before and after.
- New file `tests/test_background_efficiency.py` (14 tests): delays unchanged,
  identical flow keeps both checks, feedback drops a queued check, partial
  feedback keeps it, newer command supersedes, revived check is not lost,
  STOP and user commands not deduplicated, unload leaves no waiters, locks do
  not accumulate, RSSI-only update skips the cover write, feature change is
  written, state change after a skipped write is written, availability change
  is written, two identical button presses still fire two events.
- Before/after: on base `8ece6ea`, 6 of the 14 new tests fail (the efficiency
  assertions); all 14 pass after the change. Existing tests were not changed.
- Not executed: `hassfest` (GitHub Action only). All tests use a mocked MQTT
  layer; there is no hardware verification.

## Exclusion check A1-A5

- A1: no new poller, interval, query time or timer; delays asserted `(5, 20)`.
- A2: uptime, health and availability code unchanged; availability is only
  an input to the write comparison.
- A3: no quarantine, drain phase, topic lock or cooldown; timeouts unchanged.
- A4: no timestamp/sequence evaluation, no replay generation, retained
  handling unchanged. `_reconcile_needed` tracks only whether a scheduled
  check is still wanted.
- A5: entity set, unique IDs, options and defaults unchanged.

## Compatibility and residual risks

- `_async_calculate_state()` is a Home Assistant internal method. If its
  signature changes, the code falls back to writing as before (fail-open).
- A superseded check is dropped only before publish; a check already in
  flight completes as before.
- HA itself may still write when the entity registry changes (e.g. supported
  features); this is HA behaviour and unchanged.
- O1 (ACK 201 treated like 200) and B1 (hypothesis) remain open.

## Notes for the next phase

Base the next phase on the final HEAD of this branch. Remaining proposals in
the plan: typing, protocol/parsing module, coordinator structure, platform
consolidation, documentation and quality gates.
