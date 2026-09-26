# Proposed changes: additional EnOcean devices and state handling

This document describes the changes in `feat/new_devices` relative to this fork's `main` (`7fee4673132d63fafb3e8eae21ca6e53bee009a2`). It is intended as review context for an upstream pull request, not a claim that every device has been validated on hardware. The integration code is in `custom_components/opus_greennet/`; this branch does not change the repository's installation layout.

## Why

The existing integration covers actuators and several sensor types, but does not create the intended entities for the HOPPE window-handle profiles, Jaeger Direkt smoke detector, or OPUS SMS presence sensor represented by the additional EEPs in this branch. The extension keeps the existing gateway/MQTT integration and maps incoming device states to Home Assistant entities rather than introducing a separate broker or cloud path. It also aims to let setup proceed when the optional gateway system-info endpoint does not answer even though Home Assistant's MQTT client is connected. These are code-level motivations; hardware compatibility remains to be established by tests and field reports.

## What changed

| EEP | Device family | Proposed entities / state inputs |
| --- | --- | --- |
| `D2-06-40` | HOPPE AutoLock window handle | Lock-status entity (`lock`), handle-position sensor (`handle`), mechanics-fault binary sensor (`mechanics`), and unlock-request sensor (`unlock`). The lock command limitation below is essential. |
| `F6-10-00`, `D2-03-10` | HOPPE window handles | Handle-position sensor, reporting closed/open/tilted when the gateway supplies `handle`. |
| `F6-05-02` | Jaeger Direkt smoke detector | Smoke-alarm and low-battery binary sensors from `alarm` and `batteryLow`. |
| `A5-07-03` | OPUS SMS presence sensor | Motion binary sensor and illuminance, supply-voltage and battery-level sensors from `motionDetected`, `illumination`, `supplyVoltage` and `batteryLevel`. |

`const.py` supplies EEP-to-primary-platform mappings and state keys; `enocean_device.py` parses the corresponding channel/device fields. `entity_descriptions.py` defines supplemental entities, while `sensor.py`, `binary_sensor.py`, and the new `lock.py` instantiate matching descriptions for discovered devices. `__init__.py` adds `Platform.LOCK` to the forwarded platforms. Recognition is based on the gateway-reported **primary** EEP; an entry in the mapping does not by itself prove a physical device has been discovered. See [`const.py`](custom_components/opus_greennet/const.py), [`enocean_device.py`](custom_components/opus_greennet/enocean_device.py), [`entity_descriptions.py`](custom_components/opus_greennet/entity_descriptions.py), and [`__init__.py`](custom_components/opus_greennet/__init__.py).

The coordinator also interprets indexed key/value state fragments in `states` and `transmitModes` and handles live `batteryLevel`/`dbm` deltas. This is relevant to the new sensor reports: the chain is gateway MQTT data -> coordinator parsing -> `EnOceanDevice` state -> discovery/update signals -> entity state. The configuration flow treats `HomeAssistantError` from the setup-time system-info probe as a warning while still requiring a connected MQTT client; this is intended for bridges that do not forward that diagnostic response. See [`coordinator.py`](custom_components/opus_greennet/coordinator.py) and [`config_flow.py`](custom_components/opus_greennet/config_flow.py).

## MQTT behavior and an important limitation

This branch continues to use Home Assistant's MQTT integration and the existing EnOcean gateway topic families for discovery, state updates and ordinary device commands. It extends parsing of received state; it does **not** establish a new cloud service or a generally new communication transport. The root `manifest.json` within the integration retains the `mqtt` dependency and version `0.3.3b0`. See [`coordinator.py`](custom_components/opus_greennet/coordinator.py), [`mqtt_transport.py`](custom_components/opus_greennet/mqtt_transport.py), and [`manifest.json`](custom_components/opus_greennet/manifest.json).

**HOPPE AutoLock is not remotely controllable in this revision.** `lock.py` creates a `LockEntity` that can display the reported `lock` state, but its lock/unlock calls reach `async_set_window_handle_lock()` in the coordinator, which deliberately sends no MQTT publish. The entity then optimistically changes its local displayed state; a click must not be interpreted as physical locking or unlocking. Topic constants and comments about `targetState/0/key|value` are not an active write implementation. The code comments describe previous gateway/bridge instability; that history is not independently validated by this document. Reviewers may prefer read-only presentation or deferring the LockEntity until a safe confirmed write path and state feedback exist. See [`lock.py`](custom_components/opus_greennet/lock.py) and [`coordinator.py`](custom_components/opus_greennet/coordinator.py).

## Review and validation requested

- Please review the EEP assumptions and the actual gateway payloads, especially which EEP is primary and the `states`/`transmitModes` fragment formats.
- Please review whether a clickable but non-commanding AutoLock entity should be included at all. It must not be presented as a finished safety or access-control feature.
- Please add focused tests for discovery, entity creation, status parsing, malformed/unknown values, MQTT fragment ordering and the setup probe behavior before considering the new devices verified.
- `tests/` and `.github/workflows/tests.yml` are present again but unchanged from `main`; their presence is **not** evidence that new-device tests pass. No test run, CI result, MQTT capture or physical-device verification is claimed here.
- The integration manifest's version is unchanged. Release/versioning and user-facing documentation should be decided during review.

The branch comparison is available at https://github.com/fubu2k/ha-opus-greennet/compare/main...feat/new_devices . This document is limited to changes in this fork and does not assume that another upstream repository's current `main` is identical.