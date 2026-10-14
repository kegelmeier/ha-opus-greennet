# OPUS/EnOcean MQTT Protocol Contract (current behaviour)

Baseline: `3e52fce31e7a2d503eef4820f4c706ebcfc751ab` (`main`), recorded in phase 1.
This document describes the **actual** behaviour of the code. It is the
reference for all following phases: everything listed here stays unchanged
unless a phase explicitly permits a proven bug fix.

Vendor protocol source: `opus_mqtt_services_reference.md`. Where the code goes
beyond that reference (gateway alias keys, Mosquitto bridge topic), this is noted.

Verification legend: **[Code]** read from source, **[Test]** covered by existing
tests, **[Ref]** per vendor reference, **[unverified]** supported neither by
hardware observation nor by the reference.

## 1. Topics

Base: `EnOcean/<EAG_ID>/…` (`TOPIC_BASE = "EnOcean"`). `<EAG_ID>` is 8 hex
characters, normalised to upper case by the config flow.

### 1.1 Coordinator subscriptions

| Group | Topic filter | QoS | Handler | Note |
|-------|--------------|-----|---------|------|
| Control (setup phase 1) | `opus_greennet/<EAG_ID>/bridge/status` | 1 | `_handle_bridge_status` | Mosquitto bridge notification, not an OPUS topic **[Code]** |
| Control | `EnOcean/<EAG_ID>/getAnswer/config/system/uptime` | 1 | `_handle_system_uptime` | also a request manager answer route |
| Control | `EnOcean/<EAG_ID>/getAnswer/config/system/info` | 1 | `_handle_system_info` | optional; not every firmware answers |
| Control | `EnOcean/<EAG_ID>/putAnswer/devices/+/state` | 1 | `_handle_put_answer_state` | also an answer route |
| Stream (setup phase 2, one at a time with SUBACK) | `EnOcean/<EAG_ID>/getAnswer/devices/#` | 1 | `_handle_get_answer_devices` | discovery answers |
| Stream | `EnOcean/<EAG_ID>/stream/devices/#` | 0 | `_handle_device_property_message` | startup snapshot, flattened |
| Stream | `EnOcean/<EAG_ID>/stream/device/#` | 0 | `_handle_device_stream_message` | live deltas |
| Stream | `EnOcean/<EAG_ID>/stream/telegram/#` | 0 | `_handle_telegram_property_message` | radio telegrams |

SUBACKs of the stream group are advisory only (`required=False`); a missing
SUBACK does not fail the health probe. **[Code][Test]**

Temporary subscriptions (QoS 1) are created by `MQTTRequestManager` only for
answer topics not covered by a registered route (profile, configuration,
parameters). Registered routes: the uptime answer and `putAnswer/devices/+/state`. **[Code]**

### 1.2 Published topics

| Topic | Payload | QoS / retain | Trigger |
|-------|---------|--------------|---------|
| `…/get/config/system/uptime` | `""` | 1 / false | setup, health tick, reconnect, config flow |
| `…/get/devices` | `""` | 1 / false | resync only |
| `…/get/config/system/info` | `""` | 1 / false | resync only, answer not awaited |
| `…/put/devices/<DEV>/state` | `{"state": {"functions": [...]}}` | 1 / false | commands and status queries |
| `…/get/devices/<DEV>/profile` | `""` | 1 / false | `async_get_device_profile` (currently no caller in the integration code) |
| `…/get/devices/<DEV>/configuration` | `""` | 1 / false | service `get_device_configuration` |
| `…/put/devices/<DEV>/configuration` | `json.dumps(config)` | 1 / false | service `set_device_configuration` |
| `…/get/devices/<DEV>/parameters` | `""` | 1 / false | service `get_device_parameters` |

Unused constants in `const.py`: `TOPIC_STREAM_TELEGRAM`, `TOPIC_STREAM_DEVICE`,
`TOPIC_GET_/PUT_(ANSWER_)LINK_TABLES`, `TOPIC_SUB_TELEGRAM_FROM`,
`TOPIC_SUB_TELEGRAM_TO`, `TOPIC_SUB_DEVICE`, `TOPIC_SUB_DEVICES`,
`TOPIC_SUB_GET_ANSWER`. **[Code]**

## 2. Answers and status

- `decode_response`: the payload must be a non-empty JSON object. If `header`
  is present or `require_status=True`, `header.httpStatus` must be an integer
  (no `bool`, no non-integral `float`) equal to **200 or 201** (201 = SoftSmartAck
  per reference). **[Code][Test][Ref]**
- Uptime: `systemUptimeResponse.uptime` must be `str`, `int` or `float` (no `bool`),
  finite and ≥ 0; stored as `str(value)`. **[Code][Test]**
- Bridge status: only payloads `"0"` and `"1"` are evaluated; anything else is ignored. **[Code][Test]**
- `getAnswer/devices`: accepts a list, `{"devices": [...]}`, a single object and
  entries shaped `{"device": {...}}`. Sub-paths more than one level below
  `getAnswer/devices/` (profile, configuration) are ignored. **[Code][Test]**

## 3. Command payloads (wire values, immutable)

Format: `json.dumps({"state": {"functions": functions}})`; values are always strings.

| Action | functions | Source |
|--------|-----------|--------|
| Switch on/off | `[{"key": "switch", "value": "on"/"off"}]` | `async_turn_on/off` |
| Dimmer on without brightness | `[{"key": "dimValue", "value": "100"}]` | reference 5.3 |
| Dimmer off | `[{"key": "dimValue", "value": "0"}]` | reference 5.3 |
| Dimmer brightness | `[{"key": "dimValue", "value": str(max(1, round(brightness_to_value((1, 100), b))))}]`; HA brightness 0 → off | `light.py` |
| Cover open / close | `position` `"0"` / `"100"` | OPUS 0 = open, 100 = closed |
| Cover position | `position` `str(100 - ha_position)` | **cover inversion** |
| Cover tilt | `angle` `str(ha_tilt)` (not inverted) | `cover.py` |
| Cover STOP | `[{"key": "stop", "value": "true"}]` | **STOP payload**, never `position: "stop"` **[Ref][Test]** |
| Status query | `[{"key": "query", "value": "status"}]` | reconciliation, climate query |
| HeatArea setpoint | `[{"key": "temperatureSetpoint", "value": str(temperature)}]` | no channel selector |
| HeatArea off | `[{"key": "heaterMode", "value": "off"}]` | |
| HeatArea on | D1-4B-06: `"on"`; D1-4B-05 and D1-4B-07: `"heating"` | **proprietary HeatArea profiles** |

### 3.1 Channel selector (channel zero)

`_with_channel_if_needed` prepends `{"key": "channel", "value": str(channel)}`
when `channel > 0` **or** `channel_count > 1`. Multi-channel devices therefore
**send channel 0 explicitly**; single-channel devices get no selector. Climate
commands never send a selector. **[Code][Test]**

Inbound: without a selector the default channel is 0. A malformed selector
(negative, non-integral, `bool`) does **not** fall back to channel 0; functions
without their own valid channel are then dropped (`_parse_channel_id`). **[Code][Test]**

## 4. Inbound values (validation in `EnOceanDevice.update_from_telegram`)

| Key | Valid values | On `notAvailable` | On invalid value |
|-----|--------------|-------------------|------------------|
| `switch`, `localControl` | `on` / `off` | ignored | ignored |
| `dimValue` | integer 0–100; sets `is_on = value > 0` | ignored | ignored |
| `position`, `angle` | integer 0–100 | ignored | ignored |
| `verticalMovementTime` | ≥ 0 | ignored | ignored |
| `rotationTime` | `noRotation` → 0; number ≥ 0 | ignored | ignored |
| `energy`, `power` | finite number | ignored | ignored |
| `liquidDetected`, `windowOpen`, `summerMode` | bool or `true`/`false` | ignored | ignored |
| `smokeAlarm`, RWM `alarm` | `on`/`off`, `alarm`/`idle`, bool | – | **sets `None`** |
| `batteryLow` | `low`/`ok`, bool | – | **sets `None`** |
| `batteryLevel` | 0–100, optional `%` suffix | **sets `None`** | ignored |
| `motionDetector`, SMS `motionDetected` | `detected`/`noMotion`, bool | – | **sets `None`** |
| `illuminance`, SMS `illumination`, `supplyVoltage` | ≥ 0 | **sets `None`** | ignored |
| `handleState`, handle `handle` | `open`/`closed`/`tilted`, `tilt` → `tilted` | – | **sets `None`** |
| `lock` | `locked`/`unlocked` | – | **sets `None`** |
| `unlockRequest`, AutoLock `unlock` | `requested`/`notRequested`, bool | – | **sets `None`** |
| `mechanicsFault`, AutoLock `mechanics` | `error`/`ok`, bool | – | **sets `None`** |
| `temperature`, `temperatureSetpoint` | 0–40 | **sets `None`** | ignored |
| `humidity` | 0–100 | **sets `None`** | ignored |
| `feedTemperature` | 0–80 | **sets `None`** | ignored |
| `energyConsumption` | 0–10 | **sets `None`** | ignored |
| `heaterMode` | `heating`, `on`, `off`, `autoOff`, `configIncomplete`, `error` | ignored | ignored |
| `thermalMode` | `heating` / `cooling` | ignored | ignored |
| `powerState` | `active` / `inactive` | ignored | ignored |
| `temperatureOrigin` | `external` / `internal` | ignored | ignored |
| `actuatorDeactivated` | `info` / `reset` | ignored | ignored |
| `actuatorLowBattery` | `warning` / `reset` | ignored | ignored |
| `actuatorNotResponding` | `warning` / `error` / `reset` | ignored | ignored |
| `missingTemperature` | `info` / `warning` / `error` / `reset` | ignored | ignored |
| `circuitInUse` | `error` / `reset` | ignored | ignored |
| `buttonA0`, `buttonAI`, `buttonB0`, `buttonBI`, `multipleButtons` | `pressed` / `released` | – | ignored |

Profile-bound aliases (IQ-DOT 1.21.30): `alarm` only for F6-05-02,
`motionDetected`/`illumination` only for A5-07-01/03, `handle` only for handle
profiles, `unlock`/`mechanics` only for D2-06-40. **[Code][Test]**

Fast path (`stream/device/<DEV>/batteryLevel|dbm`): no debounce; `dbm` as an
integer, `notAvailable` → `None`; invalid values are dropped; an older buffered
scalar is discarded so it cannot overwrite the newer one. **[Code][Test]**

RWM (F6-05-02): value-only slots in `transmitModes` are resolved as `smokeAlarm`
(index 0) and `batteryLow` (index 1). **[Code][Test]**

## 5. Telegrams (`stream/telegram`)

- Flattened: `…/telegram/<DEV>/from/functions/<i>/key|value`, likewise `to/…`,
  or flat with a `direction` field.
- Complete JSON: `…/telegram/<DEV>`, `…/from`, `…/to`; optionally wrapped in
  `telegram`; `state` is flattened.
- A frame ends after 0.15 s of silence, on a direction change, or when a path
  repeats while a complete pair already exists (fast press/release is preserved).
- `from` with `direction = "to"` is discarded.
- `to` (outbound) is discarded while a local command awaits its ACK
  (`_command_waiters`); otherwise it is applied optimistically (bridge-native or
  HomeKit commands), restricted to `KNOWN_STATE_KEYS`, `channel` and `stop`, and
  reconciliation is scheduled afterwards.
- Unknown device with a telegram: a placeholder device is created; a discovery
  signal is only sent if an `entity_type` can be determined. **[Code][Test]**

## 6. Retain behaviour (existing logic, frozen by A4)

| Input | Retained message |
|-------|------------------|
| `stream/devices/#` | processed (snapshot, discovery); raw debug log suppressed |
| `stream/device/#` | processed; raw debug log suppressed; latency log only for live messages |
| `stream/telegram/#` | **discarded** (no events, no state) |
| `getAnswer/devices/#` | **discarded** |
| `putAnswer/devices/+/state` | routed to the request manager (which ignores retained), no state |
| Request answers in general | `handle_response` ignores retained |
| Uptime answer | routed; `gateway_uptime` is updated from the payload (display only) |
| System info, bridge status | evaluated without a retain check |

Additional existing freshness rule: on rediscovery of a known device, the
snapshot state is only applied if the last live update is not newer than
`_resync_requested_at`; otherwise only `rotationTime`/`channel` are applied.
Button keys from `stream/devices` are never treated as occurrences. **[Code][Test]**

## 7. Availability, health and timing (frozen by A1–A3)

- `coordinator.available` see `implementation-plan.md` (A2); every entity mirrors it.
- Health tick every 60 s, only while MQTT is connected and the bridge is not
  reported as disconnected; `resync = not _gateway_available`.
- Reconnect (MQTT or bridge): refresh; a full snapshot is requested at most once
  per 60 s, and always while setup phase 2 is incomplete.
- Disconnect / bridge down: availability `False`, pending requests cancelled,
  background work discarded (telegram and delta buffers, reconciliation timers,
  cover estimates, tasks).
- Health probe failure (`HomeAssistantError`, `TimeoutError`): availability
  `False`, pending requests cancelled with `gateway_unavailable`.
- A command timeout raises `request_timeout` for that action only; no retry,
  no availability change.
- Debounce times: discovery 2 s after the last message, delta finalisation 0.02 s,
  telegram finalisation 0.15 s.
- Reconciliation after a command: 5 s and 20 s; covers with a movement estimate:
  remaining travel incl. 5 s grace, then +10 s and +20 s.

## 8. Entity inventory and identities (frozen by A5)

Device identifier: `(opus_greennet, "<EAG>_<DEV>")`; gateway: `(opus_greennet, "<EAG>")`.

| Platform | Condition | Unique ID | Default |
|----------|-----------|-----------|---------|
| light | `entity_type == "light"`, per channel | `<EAG>_<DEV>`, with `channel_count > 1`: `<EAG>_<DEV>_ch<n>` | enabled |
| switch | `entity_type == "switch"`, per channel | as light | enabled |
| cover | `entity_type == "cover"`, per channel | as light | enabled |
| climate | `entity_type == "climate"` | `<EAG>_<DEV>` | enabled |
| event | `entity_type == "event"` (F6-02-xx, F6-03-xx) | `<EAG>_<DEV>` | enabled |
| lock | D2-06-40 | `<EAG>_<DEV>_autolock` (read-only) | enabled |
| sensor | HeatArea: `humidity`; D1-4B-05: `feed_temperature`; D1-4B-07: `energy_consumption`; SMS: `illuminance`, `supply_voltage`, `battery_level`; handle: `handle_state`; D2-06-40: `unlock_request` | `<EAG>_<DEV>_<suffix>` | enabled |
| sensor | **every** discovered device: `signal_strength` | `<EAG>_<DEV>_signal_strength` | **disabled** |
| binary_sensor | SMS: `motion`; handle: `handle_open`, `handle_tilt`, `handle_closed`; D2-06-40: `mechanics_fault`; RWM: `smoke_alarm`, `battery_low`; descriptions: `liquid_detected` (F6-05-01), `window_open`, `actuator_not_responding`, `missing_temperature` (HeatArea), `actuator_low_battery`, `actuator_deactivated` (D1-4B-05), `circuit_in_use` (D1-4B-06) | `<EAG>_<DEV>_<key>` | enabled |

Registry migrations (existing, unchanged): legacy multi-channel
`<EAG>_<DEV>` → `<EAG>_<DEV>_ch0` (light, switch); fork suffixes
`_illumination` → `_illuminance` and `_window_handle_lock` → `_autolock`.

## 9. Services (public, unchanged)

`opus_greennet.get_device_configuration` (response only),
`opus_greennet.set_device_configuration`, `opus_greennet.get_device_parameters`
(response only), `opus_greennet.reload_entry`. All are admin services with an
optional `config_entry_id`.

## 10. Dispatcher signals (internal)

`opus_greennet_device_discovered_<EAG>` (payload: device),
`opus_greennet_device_state_update_<EAG>_<DEV>` (payload: device),
`opus_greennet_availability_update_<EAG>` (no payload),
`opus_greennet_device_removed_<EAG>` (payload: `device_id`).

## 11. Not verifiable in this environment

- Real gateway behaviour (firmware variants, retain flags, QoS, ordering and
  timing of topics) is **[unverified]**; every statement above comes from code,
  tests and the vendor reference, not from hardware observation.
- Whether the gateway publishes `stream/device/<DEV>/dbm` for rockers (relevance
  of finding F1) is **[unverified]**.
- Whether the Mosquitto bridge status is published as retained is **[unverified]**.
