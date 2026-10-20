# OPUS EnOcean over IP REST protocol contract

This document describes the gateway interface the integration relies on and the
behaviour of the integration at that boundary. It replaces the MQTT contract of
versions up to 0.7 (see git history for `v0.7.0b1`). Entity identities, command
payloads and value validation are unchanged from that contract.

Verification legend: **[Code]** read from source, **[Test]** covered by tests,
**[HW]** observed on a real gateway (OPUS-GW v1.21.31, API 2.6.3.0, 65 devices),
**[Ref]** per vendor or EnOcean Alliance documentation.

## 1. Transport

- Plain HTTP on port 8080, basic authentication. Vendor default user `admin`,
  password = gateway EURID in upper case. Wrong credentials: HTTP 401 with an
  empty body. **[HW][Code][Test]**
- Every JSON answer carries `header.httpStatus`; `200` and `201` (SoftSmartAck)
  count as success. `201` means the gateway queued a state command until the
  sleeping device wakes up and fetches it: lights, switches and covers keep
  their reported state (no estimate, no travel estimate) and no status checks
  follow; the device's report arrives on the stream later. **[Ref][Code][Test]**
  Which devices answer `201` is unverified on hardware. Error answers carry `header.code` and `header.message`
  (e.g. 400 `no matching group found` for an unknown function, 500
  `internal error (java.lang.NullPointerException)` for an unknown device).
  `decode_response` rejects anything else; the gateway message becomes the
  `reason` of a `request_rejected` error. **[HW][Code][Test]**
- The HTTP status line is checked separately from `header.httpStatus`: both
  must report success. A non-2xx status line fails the request even if the
  body reports 200 (`reason` "HTTP status <h>, gateway status <g>");
  `OpusResponseError` carries `http_status` and `gateway_status`. Requests are
  never repeated. **[Code][Test]**
- Shared Home Assistant aiohttp session (`async_get_clientsession`). Request
  bound 10 s (connect 10 s); ReCom requests 45 s. **[Code]**

## 2. Endpoints

| Method and path | Use | Notes |
|-----------------|-----|-------|
| `GET /system/info` | Config flow, setup | `systemInfo.eurid` = EAG identifier; also version, serial number, base ID and the HomeKit setup code (redacted in diagnostics) **[HW]** |
| `GET /system/uptime` | Setup, health check every 60 s | `systemUptimeResponse.uptime` in seconds; a decrease means a gateway restart **[HW][Code][Test]** |
| `GET /devices` | Setup, device list refresh every 30 min | Same device objects as the stream snapshot **[HW]** |
| `GET /devices/stream` | Event stream | See §3 **[HW]** |
| `GET /devices/{id}` | Late discovery of a device that sends telegrams but was missing from the list | Retried at most once per hour per device **[Code][Test]** |
| `PUT /devices/{id}/state` | Commands, status queries | Body `{"state": {"functions": [...]}}`; answers in ~30 ms **[HW]** |
| `GET/PUT /devices/{id}/configuration` | Services (ReCom) | Several seconds over the radio (8 s observed) **[HW]** |
| `GET /devices/{id}/parameters` | Service (ReCom) | **[HW]** |

Device identifiers are URL-quoted. **[Code][Test]**

## 3. Event stream

`GET /devices/stream` keeps the response open. The gateway writes
pretty-printed JSON documents back to back without delimiters
(`JsonDocumentSplitter` cuts them by tracking nesting and strings; maximum
8 MB per document, also for a document that arrives complete in one chunk).
**[HW][Code][Test]**

1. The first document is always the complete device list
   (`header.content = "devices"`). It is applied as authoritative state: every
   new stream is a full resynchronisation. **[HW][Code][Test]** At setup it
   is the only state snapshot (stream first): setup reads `/system/info` and
   `/system/uptime`, opens the stream and waits up to 30 s for this list
   (`FIRST_DEVICE_LIST_TIMEOUT`); without it, or when the first attempt fails
   or ends before it, setup raises `ConfigEntryNotReady` (401/403 →
   `ConfigEntryAuthFailed`). Measured on OPUS-IQ-DOT 1.21.30 with 53 devices:
   0.076 s; diagnostics show `gateway.first_device_list_seconds`.
   **[HW][Code][Test]**
   The streamed list carries states and battery but **no `configuration`
   block** (cover movement and rotation times). Setup therefore reads
   `GET /devices` once right after the first list, non-authoritatively
   (configuration and battery only). **[HW][Code][Test]**
2. Then one document per telegram (`header.content = "telegram"`) with
   `telegram.deviceId`, `friendlyId`, `timestamp`, `direction` (`from` = device
   report, `to` = command sent by the gateway), `functions` (list; per-function
   `channel` on multi-channel actuators) and `telegramInfo` (`dbm`, `rorg`,
   `data`, `status`). **[HW]**
3. Observed behaviour **[HW]**:
   - status replies that do not change state are not streamed (the gateway's
     own HomeKit status sweeps appear only as `to` "query status" telegrams);
   - dimmers report an interim `dimValue: "invalid"` before the final value;
   - rocker telegrams of actuators with integrated rockers (`buttonA0`
     `pressed`/`released`) are streamed;
   - battery level is not streamed; it comes from the device list
     (`"batteryLevel": "96%"`).
   - Two parallel stream clients are served independently.
6. One reader per entry. Every started reader gets a new connection
   generation; a reader replaced by a restart applies no further document,
   changes no availability and closes its response at once
   (`contextlib.aclosing`). **[Code][Test]**
7. The 30 min device list never replaces the signal strength or last-seen
   time of a telegram received after its request started; stream snapshots
   are ordered with the telegrams and always apply. **[Code][Test]**
4. `content = "device"` (a single device object) is applied like a device list
   entry; other content types are ignored with a debug log. **[Code][Test]**
5. Silent streams are reopened after 15 minutes without data (aiohttp
   `sock_read`); the reopened stream resynchronises. **[Code][Test]**

## 4. Availability (A1–A3 of the former contract, adapted)

- `coordinator.available` = not unloaded and gateway available; every entity
  mirrors it. **[Code][Test]**
- Available after a successful setup and whenever a stream connects.
- Unavailable when a stream connection attempt fails, the health check fails,
  or the credentials are rejected. A stream that ends after it connected does
  not make the gateway unavailable. Every stream end is followed by a pause:
  nominal 1, 2, 4, 8, 16, 32, 60 s (bounded exponential), each lengthened by a
  random 0–25 % (jitter). A stream that ended within 10 s of its first
  document counts as a failed attempt; only a stream open for at least 10 s
  resets the schedule. Rejected credentials end the reader without another
  attempt. **[Code][Test]**
- A failed health check also replaces the stream (it may be half open towards
  a rebooting gateway); a falling uptime replaces the stream as well.
  **[Code][Test]**
- Becoming unavailable discards background work (cover estimates,
  reconciliation timers and queued checks). **[Code][Test]**
- HTTP 401 at runtime (stream, health check or request): unavailable, one
  reauthentication flow, no further stream attempts until reload. **[Code][Test]**

## 5. Requests

- One radio request (`PUT …/state`, ReCom) in flight at a time
  (`PriorityLock`): commands (0) before queries (10) before background status
  checks (20), FIFO within a priority. System and device-list reads bypass the
  queue. **[Code][Test]**
- A queued background status check is dropped before sending if feedback or a
  newer command made it unnecessary (`superseded`). **[Code][Test]**
- Error mapping: gateway error status → `request_rejected` (and
  `device.last_command_error`); timeout after sending → `request_timeout`
  (outcome unknown, no availability change); connection failure →
  `gateway_unavailable`; removed device or unload → `request_cancelled`.
  **[Code][Test]**
- HTTP request/response pairs are correlated by the connection; the former MQTT
  limitation of answers without request IDs no longer exists. **[Code]**

## 6. Command payloads (wire values, unchanged)

Format `{"state": {"functions": functions}}`; values are always strings.

| Action | functions |
|--------|-----------|
| Switch on/off | `[{"key": "switch", "value": "on"/"off"}]` |
| Dimmer on without brightness | `[{"key": "dimValue", "value": "100"}, ramping]` |
| Dimmer off | `[{"key": "dimValue", "value": "0"}, ramping]` |
| Dimmer brightness | `[{"key": "dimValue", "value": str(max(1, round(brightness_to_value((1, 100), b))))}, ramping]`; HA brightness 0 → off |

`ramping` is sent to D2-01 dimmers only: `{"key": "rampingMode", "value": "switch"}`
(new value at once, as Apple HomeKit commands do), or `"rampingTime1"` (the
dimmer's own configured fade) when the action has a `transition`. Without it the
gateway uses `rampingTime1`, a slow fade on many dimmers. The key is not part of
other dimmer profiles (A5-38-08), which the gateway would reject. **[HW]**
| Cover open / close | `position` `"0"` / `"100"` (OPUS 0 = open, 100 = closed) |
| Cover position | `position` `str(100 - ha_position)` |
| Cover tilt | `angle` `str(ha_tilt)` (not inverted) |
| Cover stop | `[{"key": "stop", "value": "true"}]` |
| Status query | `[{"key": "query", "value": "status"}]` |
| HeatArea setpoint | `[{"key": "temperatureSetpoint", "value": str(temperature)}]` |
| HeatArea off / on | `heaterMode` `off`; on: D1-4B-06 `on`, D1-4B-05/07 `heating` |

Channel selector: `{"key": "channel", "value": str(channel)}` is prepended when
`channel > 0` or the device has more than one channel; climate commands never
send it. **[Code][Test]**

## 7. Device list and telegram semantics

- Device list entries: state from `states` (list of `{key, value, channel?}`),
  RWM `transmitModes` value slots (index 0 `smokeAlarm`, 1 `batteryLow`),
  `batteryLevel`, and cover movement times from
  `configuration.parameters` (`verticalMovementTime`, `rotationTime`). Button
  keys in a device list are never events. **[Code][Test]**
- The periodic refresh applies only configuration and battery values plus
  metadata (`dbm`, `lastSeen` kept when missing); live state stays with the
  stream. **[Code][Test]**
- `from` telegrams are device feedback: they confirm pending fields per channel,
  end cover travel estimates on position feedback and clear
  `last_command_error` on operational feedback. **[Code][Test]**
- `to` telegrams of other controllers are applied optimistically (state keys,
  `channel`, `stop` only; `query` is ignored), start cover travel estimates and
  schedule status checks after 5 and 20 s (covers: 10 and 20 s after the
  estimate). While this integration's own command to a device awaits its
  acknowledgement, `to` telegrams for that device are ignored; within 2 s after
  the acknowledgement, a `to` telegram with the same target state (channel,
  key and value) is recognised as the echo of that command and ignored as
  well (the gateway's echo was observed ~40 ms after its HTTP answer).
  **[HW][Code][Test]**
- Value validation per key is unchanged (see `EnOceanDevice.update_from_telegram`
  and the former contract §4). **[Code][Test]**
- A device list updates a known device object in place
  (`EnOceanDevice.apply_listed_metadata`, REST phase 02): only the list
  metadata (`LISTED_METADATA_FIELDS`) is replaced; channels, the last command
  error and update timestamps stay. **[Code][Test]**
- `configuration.parameters` key `lastVerticalMovementTime` (duration of the
  last vertical run, seconds) is kept per device and channel and shown in
  diagnostics as `last_vertical_movement_time`. It is never used for
  estimates, timers or queries and causes no request. Present only in
  `GET /devices`, not in the streamed list. **[Upstream note][Code][Test]**
- Diagnostics show per cover channel `position_origin`: `device list`,
  `reported` (device report or `device` document), `estimated` (foreign
  command or entity estimate after an acknowledgement) or `assumed` (travel
  estimate ended after movement time + 5 s without a report). `unknown`
  changes neither the position nor its origin. **[Code][Test]**

## 8. Entity inventory and identities (A5, unchanged)

Device identifier `(opus_greennet, "<EAG>_<DEV>")`, gateway
`(opus_greennet, "<EAG>")`; entity unique IDs unchanged (`<EAG>_<DEV>`,
`<EAG>_<DEV>_ch<n>`, `<EAG>_<DEV>_<suffix>`). Verified on a real installation:
the REST integration creates exactly the 138 unique IDs that the MQTT version
had registered. **[HW][Test]**

## 9. Configuration entry

- Data: `eag_id`, `host`, `port`, `username`, `password`. Unique ID: EAG ID.
  **[Code][Test]**
- Entries of the former MQTT transport contain only `eag_id`. Their setup raises
  `ConfigEntryAuthFailed` (`gateway_address_required`); the reauthentication
  flow asks for the address and keeps entry, devices and entities.
  **[Code][Test]**
- Reauthentication and reconfiguration reject an address that answers with a
  different EURID (`wrong_gateway`); setup does the same for a stored address.
  **[Code][Test]**

## 10. Services (public, unchanged)

`opus_greennet.get_device_configuration` (response only),
`opus_greennet.set_device_configuration`, `opus_greennet.get_device_parameters`
(response only), `opus_greennet.reload_entry`. All are admin services with an
optional `config_entry_id`.

## 11. Dispatcher signals (internal, unchanged)

`opus_greennet_device_discovered_<EAG>` (payload: device),
`opus_greennet_device_state_update_<EAG>_<DEV>` (payload: device),
`opus_greennet_availability_update_<EAG>` (no payload),
`opus_greennet_device_removed_<EAG>` (payload: `device_id`).

## 12. Not verified on hardware

- Climate (HeatArea), RWM, SMS, HOPPE handles, contacts, temperature/humidity
  sensors and stand-alone rocker switches: not present on the test
  installation; covered by tests with the documented payloads.
- Whether the gateway ever sends `content = "device"` documents on the stream.
- The key `lastVerticalMovementTime` on the maintainer's gateway.
