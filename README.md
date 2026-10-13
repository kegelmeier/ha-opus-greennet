<div align="center">

<img src="https://raw.githubusercontent.com/kegelmeier/ha-opus-greennet/main/custom_components/opus_greennet/brand/logo.png" alt="Opus GreenNet Bridge" width="180">

# Opus GreenNet Bridge

Home Assistant integration for the **Opus GreenNet Bridge**, controlling
**EnOcean** devices via MQTT following the EnOcean over IP specification.

[![HACS Custom][hacs-shield]][hacs]
[![GitHub Release][release-shield]][releases]
[![License][license-shield]][license]
![HA Min Version][ha-shield]

[![Open in HACS][hacs-repo-badge]][hacs-repo]

</div>

A custom Home Assistant integration for the Opus GreenNet Bridge, enabling control of EnOcean devices via MQTT following the EnOcean over IP specification.

## Features

- **Auto-discovery**: Automatically discovers EnOcean devices connected to your Opus GreenNet Bridge
- **Immediate telemetry**: Flat battery-level and signal-strength updates bypass the multipart state debounce
- **Real-time updates**: Receives state changes via MQTT push notifications, including device deltas, local-control telegrams, and bridge-originated command telegrams
- **Connection recovery**: Checks that the gateway responds, marks entities unavailable during outages, and refreshes discovery and state after reconnection
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
| **Sensor** | A5-07-01, A5-07-03, _(from climate devices)_ | Illuminance, supply voltage, battery level, humidity, feed temperature, power consumption, signal strength |
| **Binary Sensor** | F6-05-01, F6-05-02, A5-07-01, A5-07-03, _(from climate devices)_ | SMS motion detection, water leak detection, RWM smoke alarm and low battery, window open, actuator errors, battery low |
| **Handle sensors** | D2-06-40, F6-10-00, D2-03-10 | Handle position plus separate open, tilted and closed binary sensors |
| **Lock** | D2-06-40 | Read-only AutoLock status, unlock-request sensor and mechanics-fault binary sensor |
| **Event** | F6-02-01, F6-02-02, F6-02-03, F6-03-01, F6-03-02 | Rocker switch press/release events, per button (`buttonA0_pressed`, `buttonA0_released`, …, `multipleButtons_released`) with `button` and `action` event attributes |

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
lock/unlock actions raise an error without publishing MQTT commands. The
reporter's tested MQTT write path disrupted the gateway bridge, and control
remains disabled pending manufacturer clarification. AutoLock configuration and
reported lock state are separate; a closed handle does not imply a locked handle.

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
formats remain supported. Battery percentages accept an optional `%` suffix in
snapshots, indexed updates, and the immediate telemetry path, with finite 0–100
validation. Unknown values are never interpreted as no alarm or a closed handle.

### Large installations and gateway recovery

Awaited gateway requests share one queue across devices. Control commands take
priority over status queries, and each acknowledgement timeout starts after the
request is published. Large batches can take longer to finish because requests
are sent one at a time. A missing acknowledgement fails that action; gateway
availability is determined by connection events and the independent health probe.

Setup checks the gateway before subscribing to the large retained device streams.
Device discovery then continues in the background. Stream subscriptions use QoS 0
to reduce acknowledgement traffic during retained replay; answer subscriptions
remain at QoS 1. Discovery resumes if a reconnect interrupts startup, while rapid
reconnects avoid requesting repeated complete snapshots. An outage cancels the
integration's queued commands rather than replaying them after recovery.

Retained `getAnswer/devices` responses are ignored to prevent stale RPC replies
from restoring old sensor states. Retained `stream/devices` snapshots remain
supported.

F6-05-01 leak sensors remain `unknown` until a valid `liquidDetected` reading is
received. Missing data does not imply that a sensor is dry. Invalid updates and
rediscovery preserve a previously received alarm state.

## Prerequisites

1. **Home Assistant** with MQTT integration configured (e.g., Mosquitto add-on)
2. **Opus GreenNet Bridge** with its built-in MQTT broker
3. **EnOcean devices** paired with your bridge
4. **MQTT Bridge** configured between your HA broker and the Opus GreenNet broker (see below)

## MQTT Bridge Setup (Required)

The Opus GreenNet Bridge runs its own MQTT broker. To connect it to Home Assistant, you need to configure an MQTT bridge on your Home Assistant MQTT broker (e.g., Mosquitto).

### For Mosquitto Add-on

1. In Home Assistant, go to **Settings** → **Add-ons** → **Mosquitto broker** → **Configuration**

2. Add the following to the **Customize** section (or edit the Mosquitto config directly):

   Create a file `/share/mosquitto/opus_bridge.conf` with:

   ```
   connection opus_greennet_<EAG-ID>
   address <OPUS_BRIDGE_IP>:1883
   bridge_protocol_version mqttv311
   remote_username <username>
   remote_password <password>
   cleansession true
   keepalive_interval 15
   restart_timeout 5 30
   bridge_outgoing_retain false
   notifications true
   notifications_local_only true
   notification_topic opus_greennet/<EAG-ID>/bridge/status

   topic EnOcean/<EAG-ID>/stream/# in 1
   topic EnOcean/<EAG-ID>/getAnswer/# in 1
   topic EnOcean/<EAG-ID>/putAnswer/# in 1
   topic EnOcean/<EAG-ID>/get/# out 0
   topic EnOcean/<EAG-ID>/put/# out 0
   ```

   Replace:
   - `<OPUS_BRIDGE_IP>` with your Opus GreenNet Bridge's IP address
   - `<EAG-ID>` with its eight-character uppercase identifier, in every occurrence
   - `<username>`: `admin`
   - `<password>`: Your gateway's EURID in uppercase (fictional example: `A1B2C3D4`)

3. Reference this config file in the Mosquitto add-on configuration:

   ```yaml
   customize:
     active: true
     folder: mosquitto
   ```

4. Restart the Mosquitto add-on

### For Standalone Mosquitto

Use the same connection block above in your `mosquitto.conf`, including the
credentials, topic directions, and notification topic.

### Updating an existing MQTT bridge

Replace the old `topic EnOcean/# both 1` rule with the scoped rules above; do not
leave both configurations enabled. Each gateway needs its own connection name
and topic identifier.

`in` carries OPUS updates to your local broker; `out` carries requests to OPUS.
Outbound QoS 0 avoids building a persistent command backlog with Mosquitto's
default `queue_qos0_messages false`. A lost request produces an action timeout;
commands are not automatically retried. Keep that broker default when using this
configuration. `retain=False` alone does not prevent queued QoS 1 commands.
Previously retained commands or an existing queue are not purged by changing
these rules; inspect and clear any obsolete command messages separately before
reconnecting the bridge.

The optional notification topic reports `1` for a connected bridge and `0` for a
disconnected bridge. The integration listens to it for prompt availability
updates. Existing configurations without this topic continue to use periodic
gateway health checks. See the [Mosquitto bridge documentation](https://mosquitto.org/man/mosquitto-conf-5.html)
for connection, notification, and queue settings.

### Verify the Bridge

Use an MQTT client (like MQTT Explorer) connected to your HA broker to verify you can see topics like:
```
EnOcean/<EAG-ID>/stream/telegram/#
```

If you see messages when triggering EnOcean devices, the bridge is working.

## Installation

### HACS (Recommended)

1. Make sure [HACS](https://hacs.xyz) is installed.
2. Add this repository as a **custom repository** (category **Integration**):

   [![Open in HACS][hacs-repo-badge]][hacs-repo]

   …or in HACS go to **⋮ → Custom repositories**, paste
   `https://github.com/kegelmeier/ha-opus-greennet`, choose **Integration**, and add it.
3. Search for **Opus GreenNet Bridge**, install, and **restart Home Assistant**.

Home Assistant 2026.8 or newer is required. To test a beta, enable pre-release
versions for this repository in HACS before selecting the beta version.

### Manual Installation

1. Copy the `custom_components/opus_greennet` folder to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

After installing, add the integration:

[![Add Integration][config-flow-badge]][config-flow]

…or go to **Settings → Devices & Services → Add Integration → “Opus GreenNet Bridge”**, then:

1. Enter your **EAG Identifier** (Bridge ID; fictional example: `A1B2C3D4`)
2. Click **Submit**

Setup waits for a response from that specific gateway. A connected Home Assistant
MQTT broker alone is insufficient. If setup fails, check the identifier, gateway
power, bridge credentials, and the directional topic rules above. Existing
entries automatically retry when the gateway is unavailable during startup.

## Availability and command behavior

The integration uses push updates, with a fresh MQTT uptime health check approximately
every 60 seconds. MQTT disconnects and the optional bridge-status topic update
availability immediately; a failed health check also marks entities unavailable.
Batteryless EnOcean devices are not marked offline just because they are quiet.
On reconnection, the integration requests a fresh device list and gateway data.
Gateway system information is optional: some firmware responds to MQTT uptime
and device requests but does not answer the system-information endpoint. Device
snapshots and indexed stream updates support both map and list state formats.

Commands wait for the gateway's acknowledgement. Status `200` means the gateway
sent the telegram; `201` means it accepted deferred delivery. Neither confirms
that the physical actuator has completed the operation. Device reports and
subsequent channel-specific status queries reconcile the state. Requests fail
when the gateway is known to be unavailable and are cancelled during reload.
Interim `unknown`/`invalid` readings and unrelated metadata do not cancel the
5- and 20-second checks. Confirmation must report a valid value for the requested
field on its channel. Cover Stop sends the profile's dedicated stop command and
then checks the reported position and, when supported, tilt; it does not assume
a new position.

OPUS acknowledgement topics do not include request identifiers. Requests to the
same endpoint are serialized, but a late reply after a timeout or a simultaneous
command from another MQTT client can remain ambiguous. Check the physical state
before retrying a timed-out command.

Measurements explicitly reported as `notAvailable` become unknown. Lights and
switches remain unknown until their state is reported or a command is accepted.
Climate activity uses actuator feedback where available; enabling a heating
zone does not by itself prove that it is currently heating.

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

## MQTT Topic Structure

The integration follows the EnOcean over IP MQTT specification:

```
EnOcean/{EAG-Identifier}/stream/telegram/{Device-Identifier}/from  # Device → Gateway
EnOcean/{EAG-Identifier}/stream/telegram/{Device-Identifier}/to    # Gateway → Device
EnOcean/{EAG-Identifier}/put/devices/{Device-Identifier}/state     # Commands
EnOcean/{EAG-Identifier}/get/devices                               # Device discovery
EnOcean/{EAG-Identifier}/getAnswer/devices/{Device-Identifier}     # Discovery response
```

## Telegram Payload Format

The identifiers and values below are fictional examples.

```json
{
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

1. Check that MQTT integration is connected
2. Verify your EAG Identifier is correct
3. Check Home Assistant logs for MQTT subscription errors
4. Ensure your Opus GreenNet Bridge is publishing to the expected topics

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

When debug logging is enabled, state updates include latency markers for the
main handoff points:

- `OPUS update latency received`: MQTT message reached the integration
- `OPUS update latency finalized`: debounced MQTT fragments were converted into state functions
- `OPUS update latency dispatch`: the coordinator notified Home Assistant entities
- `OPUS update latency entity_write`: the entity wrote its Home Assistant state

Retained device-model properties are not logged individually during reconnects;
large cached snapshots would otherwise flood the log and delay startup. Live
updates keep their latency markers.

Download a redacted diagnostic report from **Settings → Devices & services →
Opus GreenNet Bridge → three-dot menu → Download diagnostics**. It includes the
gateway status, discovered devices, channel state, last update source, and the
latest bridge command error without adding volatile diagnostic fields to every
entity state.

For local switch tests, press the physical control and compare the time between
`received`, `dispatch`, and `entity_write`. If `received` is already delayed, the
lag is before this integration. If `received` is fast but `entity_write` is slow,
the lag is inside Home Assistant or this integration.

## Development

### Project Structure

```
custom_components/opus_greennet/
├── __init__.py           # Integration setup and service registration
├── manifest.json         # Integration metadata and version
├── config_flow.py        # UI configuration
├── const.py              # Constants, EEP mappings, and MQTT topics
├── coordinator.py        # MQTT communication, discovery, and commands
├── mqtt_transport.py     # Confirmed subscriptions, requests, and gateway probing
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
├── conftest.py                  # Shared fixtures
├── ha_helpers.py                # Simulated MQTT boundary for real HA tests
├── test_enocean_device.py       # Device model tests
├── test_coordinator_helpers.py  # Pure helper and command tests
├── test_coordinator_mqtt.py     # MQTT finalization tests
├── test_coordinator_parsing.py  # JSON telegrams, fragments, and late discovery
├── test_retained_device_answers.py # Retained response and entity state regressions
├── test_coordinator_transport.py # Subscription, request, and recovery tests
├── test_release_transport_safety.py # Interrupted startup and queue fault recovery
├── test_gateway_payload_compatibility.py # Reported IQ-DOT payload regression tests
├── test_beta_entity_migration.py # Fork IDs, duplicate cleanup, and translations
├── test_binary_sensor_discovery.py # Repeated discovery and removal ordering
├── test_binary_sensor_descriptions.py # Stable sensor inventory and metadata
├── test_detector_support.py     # Sensor discovery and live MQTT updates
├── test_scalar_telemetry.py      # Immediate battery and signal-strength updates
├── test_window_handles.py        # Handle positions and protected AutoLock actions

├── test_device_removal.py        # Child removal, rediscovery and request isolation
├── test_cover_stop.py           # Stop command encoding and follow-up queries
├── test_cover_movement.py       # Estimated travel, feedback, timing and cleanup
├── test_reconciliation_feedback.py # Valid feedback, channels, and command timing
├── test_event_entity.py         # Rocker switch event entity tests
├── test_entities.py             # Entity state and Home Assistant service tests
├── test_init.py                 # Entry setup, unloading, and service routing
├── test_diagnostics.py          # Diagnostic redaction tests
├── test_ha_lifecycle.py          # Real Home Assistant lifecycle and config flows
└── test_config_flow.py          # Config flow validation tests
```

### Testing

```bash
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
pytest -v --cov
```

Use Python 3.14 for development. Tests cover device properties, telegram parsing,
ordered multi-channel commands, gateway errors, entity behavior, rocker events,
diagnostic redaction, and real Home Assistant configuration and lifecycle paths.
The v0.5.0 release validation covers 852 tests on Home Assistant 2026.8.2,
2026.9.4, and 2026.10.1, including interrupted startup and queue fault recovery.
Thirteen additional regression cases cover retained answers at startup and
reconnect, message ordering, missing state, and retained device-stream discovery.
CI resolves the latest stable Home Assistant release from PyPI on each run and
tests that exact version, recorded in the workflow summary. It runs on pull
requests, pushes to main, every Monday at 06:17 UTC, and manual dispatch. Beta
releases and older HA versions are excluded from the test matrix. If a future
stable release requires a newer Python version, CI fails explicitly until the
runner is updated rather than silently testing an older HA release. Ruff,
dependency compatibility, coverage, and Hassfest checks remain enabled. MQTT
transport is simulated; physical-device verification remains part of beta testing.

### Beta testing

Enable pre-release versions for this repository in HACS, select the beta, and
restart Home Assistant. Existing entity IDs and rocker event names are preserved.
Check physical controls, low brightness, both actuator channels, cover position
and tilt, climate activity, and rapid rocker presses. Also test a gateway outage
and reconnection, then reload the integration during a pending request. Report
your gateway firmware, device EEP, and the observed result with redacted
diagnostics.

## References

- [EnOcean over IP MQTT Specification](https://www.enocean-alliance.org/ip/)
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
[ha-shield]: https://img.shields.io/badge/Home%20Assistant-2026.8%2B-41BDF5.svg?style=for-the-badge&logo=home-assistant&logoColor=white
[hacs-repo]: https://my.home-assistant.io/redirect/hacs_repository/?owner=kegelmeier&repository=ha-opus-greennet&category=integration
[hacs-repo-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-flow]: https://my.home-assistant.io/redirect/config_flow_start/?domain=opus_greennet
[config-flow-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
