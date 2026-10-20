<div align="center">

<img src="https://raw.githubusercontent.com/kegelmeier/ha-opus-greennet/main/custom_components/opus_greennet/brand/logo.png" alt="Opus GreenNet Bridge" width="180">

# Opus GreenNet Bridge

Home Assistant integration for the **Opus GreenNet Bridge**, controlling
**EnOcean** devices through the gateway's EnOcean over IP REST API.

[![HACS Custom][hacs-shield]][hacs]
[![GitHub Release][release-shield]][releases]
[![License][license-shield]][license]
![HA Min Version][ha-shield]

[![Open in HACS][hacs-repo-badge]][hacs-repo]

</div>

A custom Home Assistant integration for the Opus GreenNet Bridge, enabling control
of EnOcean devices. Home Assistant talks to the gateway directly over its EnOcean
over IP REST API: no MQTT broker and no broker bridge are needed.

## Features

- **Auto-discovery**: Automatically discovers EnOcean devices connected to your Opus GreenNet Bridge
- **Real-time updates**: One persistent event stream from the gateway delivers every telegram as it happens, typically within a few hundredths of a second, including local switch operation, rocker presses and commands from other controllers such as HomeKit
- **Connection recovery**: Checks that the gateway responds, marks entities unavailable during outages, and resynchronises every device after reconnection or a gateway restart
- **Command feedback**: Waits for gateway acknowledgements, reports rejected or timed-out commands, and follows accepted commands with channel-specific status checks
- **Bidirectional control**: Send commands to actuators (lights, switches, covers, thermostats)
- **Climate control**: HeatArea thermostat support for Valve, CosiTherm, and Electro Heating areas
- **Sensors**: Humidity, temperature, power consumption, and signal strength monitoring
- **Binary sensors**: Water leak detection, window open detection, actuator error states, battery monitoring
- **Events**: Per-button rocker switch press/release events (`buttonA0`, `buttonAI`, `buttonB0`, `buttonBI`, `multipleButtons`)
- **Device services**: ReCom API access for advanced device configuration and diagnostics
- **UI Configuration**: Set up via Home Assistant's Integrations page

## Supported Device Types

| Entity Type | EEP Profiles | Description |
|-------------|--------------|-------------|
| **Light** | D2-01-02, D2-01-03, D2-01-06, D2-01-07, D2-01-0A, D2-01-0B, D2-01-0F, D2-01-10, D2-01-12, A5-38-08, A5-38-09 | Dimmable lights and gateway switching |
| **Switch** | D2-01-00, D2-01-01, D2-01-04, D2-01-05, D2-01-08, D2-01-09, D2-01-0C, D2-01-0D, D2-01-0E, D2-01-11 | On/Off switches and actuators |
| **Cover** | D2-05-00, D2-05-01, D2-05-02 | Blinds, shades, and shutters |
| **Climate** | D1-4B-05, D1-4B-06, D1-4B-07 | OPUS HeatArea thermostats (Valve, CosiTherm, Electro Heating) |
| **Sensor** | A5-04-01, A5-04-02, A5-04-03, A5-07-01, A5-07-03, _(from climate devices)_ | Temperature and humidity (A5-04-01/02/03, including the OPUS motion detector), illuminance, supply voltage, battery level, humidity, feed temperature, power consumption, signal strength |
| **Binary Sensor** | D5-00-01, F6-05-01, F6-05-02, A5-07-01, A5-07-03, F6-02-01 + A5-04-01, _(from climate devices)_ | Contact open/closed state, SMS motion detection, OPUS motion detector (`buttonAI` = motion, `buttonA0` = no motion), water leak detection, RWM smoke alarm and low battery, window open, actuator errors, battery low |
| **Handle sensors** | D2-06-40, F6-10-00, D2-03-10 | Handle position plus separate open, tilted and closed binary sensors |
| **Lock** | D2-06-40 | Read-only AutoLock status, unlock-request sensor and mechanics-fault binary sensor |
| **Event** | F6-02-01, F6-02-02, F6-02-03, F6-03-01, F6-03-02 | Rocker switch press/release events, per button (`buttonA0_pressed`, `buttonA0_released`, …, `multipleButtons_released`) with `button` and `action` event attributes |

D2-01 dimmers switch to the new brightness at once, like commands from Apple
Home. An action with a `transition` (any duration) uses the dimmer's own
configured fade instead; the requested duration itself cannot be passed to the
device.

Cover tilt controls are hidden when the bridge reports `rotationTime` as `0` or
`noRotation`, as used for plain roller shutters. The integration uses the existing
device discovery and state updates, without extra configuration queries. When no
valid rotation time is available, tilt support follows the device's EEP profile.

Covers with a positive configured `verticalMovementTime` show estimated
`opening` / `closing` while travelling, instead of immediately showing the target
position. The estimate uses the distance from the last known position, or a full
travel when the starting position is unknown, plus five seconds of grace. This
is an estimate, not a physical movement sensor; an incorrect configured time or
a stalled motor can make it inaccurate. A new target replaces the estimate.
Numeric position feedback, Stop, or a rejected command ends it immediately.

Status checks for these movements wait until 10 and 20 seconds after the estimate
ends, to avoid early cached target responses. For a 25-second full travel, the UI
shows movement for up to 30 seconds and checks status at 40 and 50 seconds.
Without feedback, the target becomes the assumed position when the movement
timer expires. Covers without a configured travel time retain their existing
behaviour. Existing entity IDs, tilt support, and Stop commands are unchanged.

### HOPPE window handles

All three handle profiles provide a position sensor and separate Open, Tilted,
and Closed binary sensors. `tilt` is normalized to `tilted`; missing or invalid
positions stay unknown. These report handle position, not an independent
measurement of the window sash. For Cover Control Automation, assign the Open
and Tilted binary sensors to its respective window-contact inputs.

D2-06-40 also exposes the reported lock state independently of handle position,
an unlock-request sensor, and a mechanics-fault sensor. AutoLock is read-only:
lock/unlock actions raise an error without sending a command to the gateway. A
reporter's tested write path disrupted the gateway, and control remains disabled
pending manufacturer clarification. AutoLock configuration and
reported lock state are separate; a closed handle does not imply a locked handle.

### Single input contacts

D5-00-01 contacts expose a Contact binary sensor with the opening device class.
The gateway values `open` and `closed` appear as `on` and `off`. Missing or
unrecognized contact values remain `unknown`; a gateway outage makes the entity
`unavailable`. No battery percentage is inferred when the gateway publishes none.

## Removing or replacing a device

Use **Settings → Devices & services → Opus GreenNet Bridge → Devices**, open the
child device, and choose **Delete**. The gateway itself cannot be removed this
way; remove its integration entry to remove the gateway.

This only removes the Home Assistant device and its entities. It sends no unpair
or deletion command to OPUS. If the gateway reports the device again, it may be
rediscovered, including after a reload or reconnection. There is no persistent
ignore list, and quiet devices are never automatically deleted.

When replacing a smoke detector or other device, first remove the old device
from the gateway's configuration where supported, then delete its stale Home
Assistant device. The replacement and other devices stay operational. Review
and update automations referencing the old entities; references are not migrated
automatically.

Binary-sensor discovery submits each entity ID once per loaded gateway. Repeated
discovery can still add newly discovered sensor types. Removing a device releases
its IDs after its live entities finish removal, so rediscovery and reload can
create them again without duplicate submissions.

### Upgrading from community test versions

German translations include “Beleuchtungsstärke” for illuminance and readable
HOPPE unlock-request states. Custom entity names remain unchanged.
The unlock-request sensor uses `requested` / `not_requested`; update automations
that previously compared its state with boolean text.

The integration migrates the community fork's `_illumination` and
`_window_handle_lock` unique IDs to the upstream equivalents while preserving
the existing Home Assistant entity IDs, custom names, and settings. If both the
fork and beta entries exist for the same device and gateway, the older fork
entity is preserved and the duplicate beta entry is removed. References to the
removed beta entity must be updated manually; the integration logs both entity
IDs. Entries belonging to another gateway or device are left untouched.

### Gateway payload compatibility

Detector feedback accepts both the beta's descriptive keys and the gateway keys
reported on OPUS IQ-DOT firmware 1.21.30: `alarm` (on/off), boolean `batteryLow`,
`motionDetected` (boolean), `illumination`, and HOPPE `handle`, `unlock`
(requested/notRequested), and `mechanics` (error/ok). The existing key/value
formats remain supported. Battery percentages accept an optional `%` suffix
(the gateway reports for example `96%`), with finite 0–100 validation. Unknown values are never interpreted as no alarm or a closed handle.

### Large installations and gateway recovery

Gateway requests that use the radio share one queue across devices: exactly one
is in flight at a time. Control commands take priority over status queries, and
delayed background status checks wait behind both. Large batches can take
longer to finish because requests are sent one at a time. A missing
acknowledgement fails that action; gateway availability is determined by the
event stream and the independent health probe. An outage cancels the
integration's queued commands rather than replaying them after recovery.

F6-05-01 leak sensors remain `unknown` until a valid `liquidDetected` reading is
received. Missing data does not imply that a sensor is dry. Invalid updates and
rediscovery preserve a previously received alarm state.

## Prerequisites

1. **Home Assistant** 2026.10.1 or newer
2. **Opus GreenNet Bridge** reachable from Home Assistant in your network. Give it
   a fixed IP address (DHCP reservation) so the configured address stays valid.
3. **EnOcean devices** paired with the gateway
4. The gateway's **API credentials**: user `admin`, password = the gateway's
   EURID in capital letters (printed on the gateway; fictional example:
   `A1B2C3D4`), unless you changed them

No MQTT broker, MQTT integration or Mosquitto bridge is needed.

## Upgrading from an MQTT version (0.x)

Versions up to 0.7 reached the gateway through a Mosquitto bridge and the Home
Assistant MQTT integration. From 1.0 on the integration connects to the gateway
directly. v0.7.0 is the last MQTT release; you can return to it at any time
(entities keep their IDs in both directions). Your devices, entities, entity IDs, names, areas, dashboards and
automations are kept; only the connection changes.

1. Update the integration and restart Home Assistant.
2. The gateway entry reports **Re-authentication required** (it does not know
   the gateway's address yet). Open the repair notification or **Settings →
   Devices & services → Opus GreenNet Bridge → Reconfigure** and enter the
   gateway's IP address. Port (`8080`), user (`admin`) and password (the EURID)
   are prefilled with the vendor defaults.
3. The entry loads again with all existing entities.
4. Optional cleanup once everything works: remove the OPUS connection block
   from your Mosquitto configuration (for example
   `/share/mosquitto/opus_bridge.conf`) and restart the broker. Keep the MQTT
   broker and integration only if other devices use them.

The integration checks that the address answers as the configured gateway
(same EURID) and rejects a different gateway.

## Installation

### HACS (Recommended)

1. Make sure [HACS](https://hacs.xyz) is installed.
2. Add this repository as a **custom repository** (category **Integration**):

   [![Open in HACS][hacs-repo-badge]][hacs-repo]

   …or in HACS go to **⋮ → Custom repositories**, paste
   `https://github.com/kegelmeier/ha-opus-greennet`, choose **Integration**, and add it.
3. Search for **Opus GreenNet Bridge**, install, and **restart Home Assistant**.

Home Assistant 2026.10.1 or newer is required. To test a beta, enable pre-release
versions for this repository in HACS before selecting the beta version.

### Manual Installation

> **Home Assistant 2026.10.1 or newer is required, also for manual installs.**
> HACS checks this for you; a manual copy does not. On an older version, setup
> fails with `ImportError: cannot import name 'ATTR_BRIGHTNESS' from
> 'homeassistant.components.light.const'`. Update Home Assistant first.

1. Copy the `custom_components/opus_greennet` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

After installing, add the integration:

[![Add Integration][config-flow-badge]][config-flow]

…or go to **Settings → Devices & Services → Add Integration → “Opus GreenNet Bridge”**, then enter:

| Field | Description |
|-------|-------------|
| **Host** | IP address or host name of the gateway |
| **Port** | Port of the REST API, `8080` by default |
| **Username** | API user, `admin` by default |
| **Password** | API password, by default the gateway's EURID in capital letters |

Setup reads the gateway's system information and uses its EURID as the stable
identifier of the entry, its devices and entities. If setup fails, check the
address, the credentials and that Home Assistant can reach port 8080 of the
gateway. Existing entries automatically retry when the gateway is unavailable
during startup. To change the address or credentials later, use **Reconfigure**
on the integration entry; rejected credentials start a re-authentication.

## Availability and command behavior

The integration keeps one HTTP event stream to the gateway open
(`GET /devices/stream`). Every stream starts with the complete device list,
which resynchronises all state, and then delivers each telegram as the gateway
handles it. At startup this first list is the setup snapshot; if it does not
arrive within 30 seconds, Home Assistant retries the setup later. Because the
streamed list does not contain the cover movement times, the device list is
read once more right after startup for these settings; it never changes live
state.

If the stream ends, it is reopened after a short pause; if the gateway cannot
be reached, entities become unavailable. The pause starts at 1 second, doubles
after every attempt up to 60 seconds and is lengthened by a random share of up
to 25 %. A stream that closes within 10 seconds of connecting counts as a
failed attempt, so a gateway that closes every connection at once cannot cause
a reconnect loop; a stream that stayed open longer starts the pauses again at
1 second. Rejected credentials are not retried.

A health check reads the gateway uptime about every 60 seconds. It marks
entities unavailable when the gateway stops answering, and reopens the stream
after a gateway restart. Every 30 minutes the device list is read again to
refresh slowly changing values that the stream does not carry (battery level,
signal strength, cover movement times) and to add newly paired devices. A
telegram that arrives while the list is being read keeps its newer signal
strength and last-seen time.
Batteryless EnOcean devices are not marked offline just because they are quiet.

Commands are HTTP requests; the gateway's response is the acknowledgement.
Status `200` means the gateway sent the telegram; `201` means it accepted
deferred delivery. Neither confirms that the physical actuator has completed the
operation. Device reports and subsequent channel-specific status queries
reconcile the state. Requests fail when the gateway is known to be unavailable
and are cancelled during reload. Interim `unknown`/`invalid` readings and
unrelated metadata do not cancel the 5- and 20-second checks. Confirmation must
report a valid value for the requested field on its channel. Cover Stop sends
the profile's dedicated stop command and then checks the reported position and,
when supported, tilt; it does not assume a new position.

Commands sent to a device by another controller (for example Apple HomeKit or
the gateway's own logic) also appear on the event stream. They are shown
immediately and verified by the same status checks.

Measurements explicitly reported as `notAvailable` become unknown. Lights and
switches remain unknown until their state is reported or a command is accepted.
Climate activity uses actuator feedback where available; enabling a heating
zone does not by itself prove that it is currently heating.

## Known limitations

- An acknowledgement (`200` or `201`) confirms the gateway, not the radio
  actuator. Physical completion is only known from device feedback or a later
  status query.
- Cover positions during movement are estimates based on the reported travel
  time. The final position comes from the device report or status query.
- The HOPPE AutoLock entity is read-only; lock and unlock actions are rejected.
- The gateway is not discovered automatically; its address is entered once.
  Give the gateway a fixed address, or update it with **Reconfigure**.
- The gateway's API is plain HTTP with basic authentication, as provided by the
  manufacturer. Keep the gateway in a trusted network.
- Battery level and signal strength are refreshed from the device list every
  30 minutes; the event stream does not carry them.
- Quiet or batteryless devices are never removed automatically, because the
  gateway cannot confirm that a device is gone (see
  [Removing or replacing a device](#removing-or-replacing-a-device)).
- Commands and configuration writes are never retried automatically after a
  timeout or an outage.
- Automated tests run against a simulated gateway. They do not verify physical
  hardware.

## Services

The integration exposes the following administrator-only actions for advanced
device management, accessible via **Developer Tools** → **Actions**:

| Service | Parameters | Description |
|---------|-----------|-------------|
| `opus_greennet.get_device_configuration` | `device_id` | Retrieve the stored configuration for an EnOcean device via ReCom API |
| `opus_greennet.set_device_configuration` | `device_id`, `configuration` | Write configuration to an EnOcean device via ReCom API |
| `opus_greennet.get_device_parameters` | `device_id` | Retrieve DDF parameters for an EnOcean device via ReCom API |
| `opus_greennet.reload_entry` | _(optional)_ `config_entry_id` | Re-run integration setup/teardown without restarting HA |

The `device_id` is the EURID of the target device (fictional example: `AABB1122`). Select a
gateway when more than one Opus GreenNet config entry is loaded. The two read
actions return their response data directly in Home Assistant.

## REST API

The integration uses these endpoints of the gateway's EnOcean over IP REST API
(basic authentication, JSON documents with a `header.httpStatus`):

```
GET  /system/info                      # Gateway identity (EURID) and version
GET  /system/uptime                    # Health check
GET  /devices                          # Device list with current state
GET  /devices/stream                   # Event stream: device list, then telegrams
GET  /devices/{Device-Identifier}      # One device (late discovery)
PUT  /devices/{Device-Identifier}/state          # Commands and status queries
GET  /devices/{Device-Identifier}/configuration  # ReCom: stored configuration
PUT  /devices/{Device-Identifier}/configuration  # ReCom: write configuration
GET  /devices/{Device-Identifier}/parameters     # ReCom: supported parameters
```

## Telegram Payload Format

Each telegram on the event stream is one JSON document. The identifiers and
values below are fictional examples.

```json
{
  "header": {"content": "telegram"},
  "telegram": {
    "deviceId": "AABB1122",
    "friendlyId": "Example_Light",
    "timestamp": "2024-01-15T10:30:00.000+0100",
    "direction": "from",
    "functions": [
      {"key": "switch", "value": "on"},
      {"key": "dimValue", "value": "75"}
    ],
    "telegramInfo": {
      "data": "0000000A",
      "dbm": -65,
      "rorg": "A5"
    }
  }
}
```

## Troubleshooting

### Devices not appearing

1. Check that the integration entry is loaded and the gateway is available
2. Verify that the device is paired with the gateway and listed in the
   gateway's own app
3. Check the Home Assistant log for messages from `custom_components.opus_greennet`
4. Newly paired devices appear on the next stream start or device list refresh
   (at the latest after 30 minutes); reloading the entry adds them at once

### Network, gateway or radio actuator

Independent links are involved. Identify which one fails before changing the
configuration:

| Link | Symptom | Check |
|------|---------|-------|
| Network to the OPUS gateway | All entities unavailable; actions fail with "The OPUS gateway is unavailable" | Gateway power and address; `http://<gateway>:8080/system/uptime` must answer (with the API credentials); the health check runs about every 60 seconds |
| Gateway credentials | Entry asks for re-authentication | User and password of the gateway API |
| EnOcean radio actuator | Entities available, but the physical device does not follow | Range, power and pairing of the actuator; the debug log of the follow-up status queries |

A gateway outage makes the entities unavailable and cancels queued commands.
They are not replayed after recovery. A quiet radio device does not make any
entity unavailable.

### Acknowledgements 200 and 201

Every command is answered by the gateway's HTTP response. `200` means the gateway
sent the telegram; `201` means it accepted the command for deferred delivery.
Both count as an accepted command. Neither proves that the actuator executed
it. The integration then shows the expected state and checks it with status
queries 5 and 20 seconds later; for covers, 10 and 20 seconds after the
estimated end of the movement. Any other status fails the action.

### Estimated values

Cover position and tilt during a movement are estimated from the reported
travel time plus a 5-second grace period. After an accepted command the
expected state is shown until a device report or status query confirms or
corrects it.

### Timeouts

If an action is not acknowledged within 10 seconds, the gateway may still
execute it. Check the physical state before repeating the command. The
integration does not retry automatically. ReCom configuration requests travel
over the radio and may take several seconds; they wait up to 45 seconds.

### Recovery

- If the gateway does not answer at startup, Home Assistant retries the setup
  automatically.
- After a connection loss or a gateway restart, the event stream is reopened
  and every device is resynchronised from the gateway's device list.
- Run the `opus_greennet.reload_entry` action to restart the integration
  without restarting Home Assistant. Pending requests are cancelled.
- Download diagnostics (see below) before reporting a problem.

### Enable debug logging

For temporary troubleshooting, open **Settings → Devices & services →
Opus GreenNet Bridge → three-dot menu → Enable debug logging**. Disable it
after reproducing the problem and save the downloaded log.

For persistent logging, add to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.opus_greennet: debug
```

### Debug update lag

When debug logging is enabled, every applied telegram is logged as
`OPUS telegram/from <device> ... gateway_to_ha_ms=<n>`: the time from the
gateway's telegram timestamp until Home Assistant received it (clock offsets
between gateway and Home Assistant affect this value). Entity writes are logged
as `OPUS update latency entity_write` with the time since the telegram was
received.

Download a redacted diagnostic report from **Settings → Devices & services →
Opus GreenNet Bridge → three-dot menu → Download diagnostics**. It includes the
gateway status, discovered devices, channel state, last update source, and the
latest command error without adding volatile diagnostic fields to every entity
state. Addresses, credentials, identifiers and the gateway's HomeKit setup code
(also as its QR setup payload) are redacted. The gateway section shows
`first_device_list_seconds`, the time from opening the event stream to its
first device list at startup. For covers each device shows
`last_vertical_movement_time` (the last run duration the gateway reports, if
any) and `position_origin` per channel: `device list`, `reported`,
`estimated` or `assumed` (the travel estimate ended without a report).

For local switch tests, press the physical control and compare
`gateway_to_ha_ms` with the `entity_write` timing. If the gateway latency is
already high, the delay is in the gateway or the network.

## Removing the integration

1. Go to **Settings → Devices & services → Opus GreenNet Bridge**, open the
   three-dot menu of the entry and select **Delete**. Home Assistant removes the
   entry, its devices and entities. The gateway and the pairing of the EnOcean
   devices are not changed.
2. Remove the integration in HACS, or delete
   `custom_components/opus_greennet` for a manual installation, and restart
   Home Assistant.

## Development

### Project Structure

```
custom_components/opus_greennet/
├── __init__.py           # Integration setup and service registration
├── manifest.json         # Integration metadata and version
├── config_flow.py        # UI configuration, reauthentication and reconfiguration
├── api.py                # REST client: requests, error mapping, event stream splitting
├── protocol.py           # Pure helpers that read gateway documents
├── coordinator.py        # Event stream, health check, device state and commands
├── const.py              # Constants and EEP mappings
├── wire.py               # Typed JSON boundary helpers
├── enocean_device.py     # Device and channel data model
├── entity.py             # Shared entity and device-registry behavior
├── diagnostics.py        # Redacted integration diagnostics
├── lock.py               # Read-only HOPPE AutoLock status
├── light.py              # Light entity platform
├── switch.py             # Switch entity platform
├── cover.py              # Cover entity platform
├── climate.py            # Climate entity platform (HeatArea)
├── sensor.py             # Sensor entity platform
├── binary_sensor.py      # Binary sensor entity platform
├── binary_sensor_descriptions.py # Moisture and HeatArea sensor definitions
├── event.py              # Event entity platform (rocker switches)
├── services.yaml         # HA service definitions
├── strings.json          # UI, service, and exception translation source
└── translations/
    ├── en.json           # English translations
    └── de.json           # German translations
tests/
├── conftest.py                  # Shared fixtures (real HA core, fake gateway)
├── fake_gateway.py              # In-process OPUS gateway serving the REST API over HTTP
├── ha_helpers.py                # Config flow and waiting helpers for real HA tests
├── test_api.py                  # REST client, error mapping, stream splitter, priority lock
├── test_protocol.py             # Pure protocol helpers
├── test_coordinator_stream.py   # Setup, event stream, availability, health check, refresh
├── test_coordinator_stream_backoff.py # Stream backoff with jitter, short-lived streams, connection generation
├── test_coordinator_stream_first.py   # Stream-first setup, first device list deadline, fresh list metadata
├── test_coordinator_status_checks.py  # HTTP vs. gateway status as seen by commands and the health probe
├── test_api_hardening.py        # HTTP vs. gateway status, no retries, no credentials in errors
├── test_coordinator_devices.py  # Device lists: discovery, resync and metadata refresh
├── test_coordinator_domain_model.py # In-place device update, last run duration, position origin, rocker and cover regression cases
├── test_coordinator_telegrams.py # Device reports, outbound commands and their effects
├── test_coordinator_requests.py # Request queue, acknowledgements and error mapping
├── test_coordinator_commands.py # Command acknowledgements, outages and unload
├── test_coordinator_edge_cases.py # Malformed answers, failures and lifecycle
├── test_rest_lifecycle.py       # End-to-end smoke test over real HTTP
├── test_enocean_device.py       # Device model tests
├── test_gateway_payload_compatibility.py # Reported IQ-DOT payload regression tests
├── test_state_ownership.py      # State ownership between reports and commands
├── test_strict_typing_boundaries.py # Typed wire-value boundaries
├── test_release_transport_safety.py # Outage and queue fault recovery
├── test_background_efficiency.py # Superseded background work and redundant writes
├── test_beta_entity_migration.py # Fork IDs, duplicate cleanup, and translations
├── test_binary_sensor_discovery.py # Repeated discovery and removal ordering
├── test_binary_sensor_descriptions.py # Stable sensor inventory and metadata
├── test_detector_support.py     # RWM and presence detectors end to end
├── test_temperature_humidity_sensor.py # A5-04-03 readings, ranges, and HA entities
├── test_contact_sensor.py       # D5-00-01 states, live updates, and reload
├── test_opus_motion_detector.py # OPUS motion detector (F6-02-01 + A5-04-01)
├── test_scalar_telemetry.py     # Battery level and signal strength
├── test_window_handles.py       # Handle positions and protected AutoLock actions
├── test_device_removal.py       # Child removal, rediscovery and request isolation
├── test_cover_stop.py           # Stop command encoding and follow-up queries
├── test_cover_movement.py       # Estimated travel, feedback, timing and cleanup
├── test_reconciliation_feedback.py # Valid feedback, channels, and command timing
├── test_event_entity.py         # Rocker switch event entity tests
├── test_event_lifecycle.py      # Transient button fields are never replayed
├── test_entities.py             # Entity state and Home Assistant service tests
├── test_platform_edge_cases.py  # Platform setup and entity edge cases
├── test_init.py                 # Entry setup, migration errors, unloading, services
├── test_service_actions.py      # Actions through the real Home Assistant service registry
├── test_diagnostics.py          # Diagnostic redaction tests
├── test_diagnostics_edge_cases.py # Diagnostics for partial and missing data
├── test_ha_lifecycle.py         # Real Home Assistant lifecycle against the fake gateway
├── test_config_flow.py          # User, reauth (MQTT migration) and reconfigure flows
├── test_translations.py         # Translation, exception key and service consistency
├── test_quality_scale.py        # quality_scale.yaml schema and rule evidence
└── test_coverage_gate.py        # Per-module coverage gate
script/
└── check_coverage.py            # Per-module coverage gate used by CI
```

### Testing

```bash
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
python -m mypy
pytest -v --cov --cov-report=json:coverage.json
python script/check_coverage.py coverage.json
```

Use Python 3.14 for development. Tests cover device properties, telegram parsing,
ordered multi-channel commands, gateway errors, entity behavior, rocker events,
diagnostic redaction, and real Home Assistant configuration and lifecycle paths.
The v0.5.0 release validation covers 852 tests on Home Assistant 2026.8.2,
2026.9.4, and 2026.10.1, including interrupted startup and queue fault recovery.
The v0.6.0b0 candidate adds 24 A5-04-03 tests and 33 D5-00-01 tests, for 909 total
tests validated on Home Assistant 2026.8.2 and 2026.10.1.
Thirteen additional regression cases cover retained answers at startup and
reconnect, message ordering, missing state, and retained device-stream discovery.
The stable v0.6.0 release has 922 tests validated on Home Assistant 2026.9.4.
The v0.7.0b0 motion-detector beta adds 20 cases, for 942 tests on HA 2026.9.4.
Motion starts unknown until a valid telegram arrives after restart.
The v0.7.0b1 quality beta requires HA 2026.10.1 or newer, including for manual installation.
Its combined suite contains 1,315 tests, with strict typing and per-module coverage gates.
Users on older HA versions should keep stable v0.6.0 or upgrade HA first.
Version 1.0 replaces the MQTT transport with the gateway's REST API. Its suite
contains 1,535 tests that run against an in-process gateway serving the REST API
and its event stream over real HTTP (`tests/fake_gateway.py`).
CI resolves the latest stable Home Assistant release from PyPI on each run and
tests that exact version, recorded in the workflow summary. It runs on pull
requests, pushes to main, every Monday at 06:17 UTC, and manual dispatch. Beta
releases and older HA versions are excluded from the test matrix. If a future
stable release requires a newer Python version, CI fails explicitly until the
runner is updated rather than silently testing an older HA release. CI enforces
Ruff, dependency compatibility, strict mypy typing, Hassfest, a total coverage of
at least 97 %, more than 95 % for every integration module and 100 % for the
config flow (`script/check_coverage.py`). Lifecycle and race regression suites
run three more times. `quality_scale.yaml` is validated against the official
rule keys and schema by `tests/test_quality_scale.py`; it is a self-assessment,
not an official quality scale rating (see
[the evidence matrix](docs/quality/quality-scale-evidence.md)). The gateway is
simulated; physical-device verification remains part of beta testing.

### Beta testing

Enable pre-release versions for this repository in HACS, select the beta, and
restart Home Assistant. Existing entity IDs and rocker event names are preserved.
Check physical controls, low brightness, both actuator channels, cover position
and tilt, climate activity, and rapid rocker presses. Also test a gateway outage
(power cycle) and reconnection, then reload the integration during a pending
request. Report
your gateway firmware, device EEP, and the observed result with redacted
diagnostics.

## References

- [EnOcean over IP Specification (REST API)](https://www.enocean-alliance.org/ip/)
- [EnOcean Equipment Profiles (EEP)](https://www.enocean-alliance.org/eep/)
- [Home Assistant Developer Documentation](https://developers.home-assistant.io/)

## License

Released under the [MIT License](LICENSE).

<!-- badges -->
[hacs]: https://hacs.xyz
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[releases]: https://github.com/kegelmeier/ha-opus-greennet/releases
[release-shield]: https://img.shields.io/github/v/release/kegelmeier/ha-opus-greennet?style=for-the-badge
[license]: https://github.com/kegelmeier/ha-opus-greennet/blob/main/LICENSE
[license-shield]: https://img.shields.io/github/license/kegelmeier/ha-opus-greennet?style=for-the-badge
[ha-shield]: https://img.shields.io/badge/Home%20Assistant-2026.10.1%2B-41BDF5.svg?style=for-the-badge&logo=home-assistant&logoColor=white
[hacs-repo]: https://my.home-assistant.io/redirect/hacs_repository/?owner=kegelmeier&repository=ha-opus-greennet&category=integration
[hacs-repo-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-flow]: https://my.home-assistant.io/redirect/config_flow_start/?domain=opus_greennet
[config-flow-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
