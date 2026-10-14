# Phase 3 - Event lifecycle

- **Status:** completed
- **Branch:** `fix/opus-q03-event-lifecycle`
- **Predecessor branch:** `fix/opus-q02-state-ownership`
- **Base SHA:** `16d9a45777cef8891fe8772963c8827b8a479798`

## Scope
Prevent phantom button events caused by reused transient fields (finding F1).
No general replay/freshness hardening, no new retained-vs-live semantics.

## Changes
- `enocean_device.py`: new `EnOceanDevice.clear_button_occurrence()`;
  `update_from_telegram` uses it for its existing reset.
- `coordinator.py`: `_finalize_telegram` clears the button fields right after
  dispatching the telegram that carried the press. Later dispatches (RSSI,
  battery, metadata, PUT answers, availability) can no longer replay it.
- `tests/test_event_lifecycle.py`: regression tests.

No deduplication by button/action or payload: two identical real presses still
produce two events. The fix relies on the event entity consuming the fields
synchronously inside the dispatcher callback (current `@callback` handler).

## Existing protection (verified, unchanged)
- Retained telegrams are dropped; button keys from the device model are ignored.
- Unload and device removal already cancel timers, subscriptions and pending
  requests via the existing lifecycle mechanisms; no code change required.

## Tests (executed)
- `python -m pytest -q --cov`: 934 passed, 0 failed, coverage 92 %.
- `python -m ruff check .` and `python -m ruff format`: clean.
- Before/after: on base `16d9a45`, 3 of 4 new tests fail; all pass after the fix.
- Not executed: `hassfest` (GitHub Action only). All tests use a mocked MQTT
  layer; no hardware verification.

## Exclusion check A1-A5
A1 no new poller/timer/query. A2 uptime/health/availability unchanged.
A3 no quarantine/cooldown. A4 retained/freshness logic unchanged, no replay
generation. A5 entities and defaults unchanged.

## Residual risks
- Whether the gateway sends `dbm`/`putAnswer` for rocker switches is not
  verified on hardware.
- O1 (ACK 201 treated like 200) and B1 (hypothesis) remain open.

## Notes for the next phase
Base the next phase on the final HEAD of this branch.
