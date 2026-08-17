# OPUS MQTT Services Reference

> Source: OPUS MQTT Services V1.0 (14.06.2024) by DC Next GmbH — Confidential

## 1. Overview

- Available on OPUS Gateway firmware **1.20.31+**
- The gateway runs a built-in **MQTT broker** (always on, starts at boot)
- The gateway publishes events and subscribes to command topics

### Connection Credentials

| Parameter   | Value                                  |
|-------------|----------------------------------------|
| User        | `admin`                                |
| Password    | `<EURID of the gateway in UPPER CASE>` |
| Port        | `1883`                                 |
| Encryption  | none                                   |

> Note: The doc mentions port 1881 in one place and 1883 in another — use **1883** as the documented credential port.

---

## 2. Protocol

- Communication follows **EnOcean over IP** specifications (EnOcean Alliance)
- Each device has an **EEP (EnOcean Equipment Profile)** that defines its IP representation
- Reference docs: EnOcean over IP Specification v2.1, REST API v1.2, MQTT Implementation v1.0
- IP Profiles viewable via the [EEP Viewer Tool](https://www.enocean-alliance.org/)

### Telegrams FROM devices (inbound)
- JSON format matching the REST API
- JSON is **flattened into MQTT topics** — each endpoint contains the raw value
- Subscribe to these topics from your controller/application

### Telegrams TO devices (outbound)
- Must be encoded as **full JSON structures** (many devices need multiple values at once)
- Topic structure mirrors the REST interface with command path + answer path

---

## 3. MQTT Topic Structure

### Base Path

All topics are prefixed with:

```
EnOcean/<GW_EURID>/
```

Where `<GW_EURID>` is the gateway's EnOcean Unique Radio Identifier (e.g., `0528C9BA`).

---

### 3.1 Stream Topics (live data from gateway)

| Topic | Description |
|-------|-------------|
| `stream/telegram/<DEVICE_EURID>` | All telegrams from and to the device (raw radio traffic) |
| `stream/device/<DEVICE_EURID>` | Device model changes (deltas, as they occur) |
| `stream/devices/<DEVICE_EURID>` | Device model complete set (sent once at startup) |

**Full path example:**
```
EnOcean/0528C9BA/stream/telegram/05985C39
```

The telegram stream has sub-topics for direction:
- `.../to` — JSON as payload (commands sent to device)
- `.../from` — flattened into sub-topics where `key <x>` = topic, `value <x>` = raw payload

---

### 3.2 GET Topics (query data)

Publish to a `get/` topic (no payload needed), receive the answer on the corresponding `getAnswer/` topic.

**General notes:**
- `getAnswer` returns the JSON payload of the equivalent REST call
- On success: HTTP status 200 with data
- On error: error details returned

#### Device Information (from gateway cache)

| Publish to | Subscribe for answer | Description |
|-----------|---------------------|-------------|
| `get/devices` | `getAnswer/devices` | List all available devices |
| `get/devices/<EURID>` | `getAnswer/devices/<EURID>` | Info for specific device |

#### Device Information (from device via ReCom API)

| Publish to | Subscribe for answer | Description |
|-----------|---------------------|-------------|
| `get/devices/<EURID>/parameters` | `getAnswer/devices/<EURID>/parameters` | DDF parameters |
| `get/devices/<EURID>/configuration` | `getAnswer/devices/<EURID>/configuration` | Device configuration stored on device |
| `get/devices/<EURID>/linkTablesMetadata` | `getAnswer/devices/<EURID>/linkTablesMetadata` | Link table metadata |
| `get/devices/<EURID>/linkTables` | `getAnswer/devices/<EURID>/linkTables` | Link tables |
| `get/devices/<EURID>/repeaterState` | `getAnswer/devices/<EURID>/repeaterState` | Repeater state |

#### Device Profile

| Publish to | Description |
|-----------|-------------|
| `get/devices/<EURID>/profile` | Device profile as JSON |
| `get/devices/<EURID>/profile.html` | Device profile as HTML (saveable, good overview of all keys/values) |

The profile contains all transmittable info ("from" direction) and all accepted commands ("to" direction).

#### Gateway System Info

| Publish to | Description |
|-----------|-------------|
| `get/config/system/info` | Gateway system information |
| `get/config/system/uptime` | Gateway uptime |

---

### 3.3 PUT Topics (send commands / configure)

Publish JSON payload to `put/` topic, receive confirmation on `putAnswer/` topic.

**Tip:** The JSON returned from a GET call can be used directly for a PUT call — just modify the desired key/value pairs.

#### Trigger Outgoing Telegram (control device)

| Publish to | Subscribe for answer | Description |
|-----------|---------------------|-------------|
| `put/devices/<EURID>/state` | `putAnswer/devices/<EURID>/state` | Send command telegram to device |

**PUT answer behavior:**
- `httpStatus: 200` — telegram sent successfully
- `httpStatus: 201` — SoftSmartAck (inverse communication), will send later
- Error case: error details returned
- Sent telegrams appear under `stream/telegram/<EURID>` with direction "to" (outbound) and "from" (device response)

#### Configure Device (Remote Commissioning / ReCom API)

| Publish to | Subscribe for answer | Description |
|-----------|---------------------|-------------|
| `put/devices/<EURID>/configuration` | `putAnswer/devices/<EURID>/configuration` | Write device configuration |
| `put/devices/<EURID>/linkTables` | `putAnswer/devices/<EURID>/linkTables` | Write device link tables |

---

## 4. Command Payload Format

All commands to devices use this JSON structure:

```json
{
  "state": {
    "functions": [
      {
        "key": "<function_key>",
        "value": "<value>"
      }
    ]
  }
}
```

Multiple key/value pairs can be sent in the `functions` array simultaneously.

### Success Response Format

```json
{
  "header": {
    "httpStatus": 200,
    "gateway": "OPUS-IQ-DOT v1.21.005",
    "timestamp": "2024-11-14T13:20:50.905+0100"
  }
}
```

---

## 5. Device Examples

### 5.1 OPUS Bridge 1 Channel (EEP D2-01-01)

**Topic:** `EnOcean/<GW_EURID>/put/devices/<DEVICE_EURID>/state`

| Action | Key | Value |
|--------|-----|-------|
| Switch on | `switch` | `on` |
| Switch off | `switch` | `off` |

**Example payload (switch on):**
```json
{
  "state": {
    "functions": [
      { "key": "switch", "value": "on" }
    ]
  }
}
```

---

### 5.2 OPUS Bridge 2 Channel (EEP D2-01-11)

**Topic:** `EnOcean/<GW_EURID>/put/devices/<DEVICE_EURID>/state`

| Action | Keys | Values |
|--------|------|--------|
| Switch on channel 0 | `channel` + `switch` | `0` + `on` |
| Switch off channel 1 | `channel` + `switch` | `1` + `off` |

**Example payload (switch on channel 0):**
```json
{
  "state": {
    "functions": [
      { "key": "channel", "value": "0" },
      { "key": "switch", "value": "on" }
    ]
  }
}
```

---

### 5.3 OPUS Bridge Dimmer (EEP D2-01-03)

**Topic:** `EnOcean/<GW_EURID>/put/devices/<DEVICE_EURID>/state`

| Action | Key | Value |
|--------|-----|-------|
| Set dim level | `dimValue` | `0` to `100` |

**Example payload (50% brightness):**
```json
{
  "state": {
    "functions": [
      { "key": "dimValue", "value": "50" }
    ]
  }
}
```

---

### 5.4 OPUS Bridge Roller Shutter (EEP D2-05-02)

**Topic:** `EnOcean/<GW_EURID>/put/devices/<DEVICE_EURID>/state`

| Action | Key | Value |
|--------|-----|-------|
| Set angle | `angle` | `0` to `100` |
| Set position | `position` | `0` to `100` |

Both can be sent together or individually.

**Example payload (angle 80, position 20):**
```json
{
  "state": {
    "functions": [
      { "key": "angle", "value": "80" },
      { "key": "position", "value": "20" }
    ]
  }
}
```

**Example payload (position only):**
```json
{
  "state": {
    "functions": [
      { "key": "position", "value": "80" }
    ]
  }
}
```

---

### 5.5 OPUS HeatAreas

Three HeatArea types, setup via MyOPUS App. Controllable directly or via calendar/UI.

| Type | EEP |
|------|-----|
| Valve Area | D1-4B-05 |
| CosiTherm Area | D1-4B-06 |
| Electro Heating Area | D1-4B-07 |

> **Note:** These profiles are NOT in the official EnOcean Alliance repository — they are proprietary.

**Topic:** `EnOcean/<GW_EURID>/put/devices/<HEATAREA_EURID>/state`

**Example payload (set temperature setpoint to 22°C):**
```json
{
  "state": {
    "functions": [
      { "key": "temperatureSetpoint", "value": "22" }
    ]
  }
}
```

---

#### 5.5.1 D1-4B-05: Valve Area — Full API Reference

**Direction "to" (commands):**

| Key | Value | Meaning |
|-----|-------|---------|
| `temperature` | `0`–`40` (step 0.1) | Set current temperature via API |
| `temperatureSetpoint` | `0`–`40` (step 0.5) | Set current setpoint via API |
| `heaterMode` | `heating` | Activate heatzone |
| `heaterMode` | `off` | Disable heatzone, set all valves to 0% |
| `query` | `status` | Request device update from heatarea |

**Direction "from" (state reports):**

| Key | Value | Meaning |
|-----|-------|---------|
| `feedTemperature` | `0`–`80` (step 0.5) | Feed temperature from actuators |
| `feedTemperature` | `notAvailable` | Not yet transmitted |
| `heaterMode` | `autoOff` | Temporarily disabled (open window / summer mode) |
| `heaterMode` | `configIncomplete` | Created but no functional actuators assigned |
| `heaterMode` | `error` | No active actuators (empty linktable or unresponsive) |
| `heaterMode` | `heating` | Currently active |
| `heaterMode` | `off` | Disabled, all actuators closed |
| `humidity` | `0`–`100` (step 1) | From external sensors |
| `humidity` | `notAvailable` | Not available |
| `summerMode` | `false` / `true` | Summer mode status |
| `temperature` | `0`–`40` (step 0.1) | Current temperature |
| `temperature` | `notAvailable` | Not yet set |
| `temperatureOrigin` | `external` / `internal` | Source of temperature values |
| `temperatureSetpoint` | `0`–`40` (step 0.5) | Current setpoint |
| `temperatureSetpoint` | `notAvailable` | Not yet set |
| `windowOpen` | `false` / `true` | Window detection status |

**Error/Warning states (direction "from"):**

| Key | Value | Meaning |
|-----|-------|---------|
| `actuatorDeactivated` | `info` | One or more actuators deactivated, manual action needed |
| `actuatorDeactivated` | `reset` | Error cleared |
| `actuatorLowBattery` | `warning` | Critical energy levels |
| `actuatorLowBattery` | `reset` | Error cleared |
| `actuatorNotResponding` | `warning` | Actuator(s) didn't send within timeout |
| `actuatorNotResponding` | `reset` | Error cleared |
| `missingTemperature` | `info` | Sensor(s) didn't send within timeout |
| `missingTemperature` | `warning` | Temperature not updated within fallback timeout; actuators revert to internal |
| `missingTemperature` | `reset` | Error cleared |

---

#### 5.5.2 D1-4B-06: CosiTherm Area — Full API Reference

**Direction "to" (commands):**

| Key | Value | Meaning |
|-----|-------|---------|
| `temperature` | `0`–`40` (step 0.1) | Set current temperature |
| `temperatureSetpoint` | `0`–`40` (step 0.1) | Set temperature setpoint |
| `heaterMode` | `off` | Disable heat zone |
| `heaterMode` | `on` | Enable heat zone |
| `query` | `status` | Query heatarea status |

**Direction "from" (state reports):**

| Key | Value | Meaning |
|-----|-------|---------|
| `heaterMode` | `autoOff` | Temporarily deactivated (open window) |
| `heaterMode` | `configIncomplete` | Recently created, no actuator available |
| `heaterMode` | `error` | Malfunctioning, communication ceased |
| `heaterMode` | `off` | Deactivated |
| `heaterMode` | `on` | Activated |
| `humidity` | `0`–`100` (step 1) / `notAvailable` | Current humidity |
| `temperature` | `0`–`40` (step 0.1) / `notAvailable` | Current temperature |
| `temperatureSetpoint` | `0`–`40` (step 0.1) / `notAvailable` | Current setpoint |
| `thermalMode` | `cooling` | Summer / cooling mode |
| `thermalMode` | `heating` | Winter / heating mode |
| `windowOpen` | `false` / `true` | Window status |

**Error states (direction "from"):**

| Key | Value | Meaning |
|-----|-------|---------|
| `actuatorNotResponding` | `error` | No actuator sent recently |
| `actuatorNotResponding` | `reset` | Error cleared |
| `circuitInUse` | `error` | Heat circuit used by other heatarea |
| `circuitInUse` | `reset` | Error cleared |
| `missingTemperature` | `error` | Actuator entered emergency mode |
| `missingTemperature` | `info` | Sensor timeout |
| `missingTemperature` | `warning` | Temperature not updated within timeout |
| `missingTemperature` | `reset` | Error cleared |

---

#### 5.5.3 D1-4B-07: Electro Heating Area — Full API Reference

**Direction "to" (commands):**

| Key | Value | Meaning |
|-----|-------|---------|
| `temperatureSetpoint` | `0`–`40` (step 0.1) | Set target temperature setpoint |
| `temperature` | `0`–`40` (step 0.1) | Set current temperature |
| `heaterMode` | `heating` | Set heating mode |
| `heaterMode` | `off` | Turn off |
| `heaterMode` | `on` | Turn on |
| `query` | `status` | Query data |

**Direction "from" (state reports):**

| Key | Value | Meaning |
|-----|-------|---------|
| `energyConsumption` | `0`–`10` kW (step 0.005) / `notAvailable` | Combined energy usage |
| `heaterMode` | `autoOff` | Deactivated (summer mode / window open) |
| `heaterMode` | `configIncomplete` / `error` / `heating` / `off` / `on` | Current mode |
| `humidity` | `0`–`100` (step 1) / `notAvailable` | Reported humidity |
| `powerState` | `active` / `inactive` | Linked actuators toggled on/off |
| `temperature` | `0`–`40` (step 0.1) / `notAvailable` | Reported temperature |
| `temperatureSetpoint` | `0`–`40` (step 0.1) / `notAvailable` | Current setpoint |
| `windowOpen` | `false` / `true` | Window status |

**Error states (direction "from"):**

| Key | Value | Meaning |
|-----|-------|---------|
| `actuatorNotResponding` | `error` | Actuator(s) didn't answer query |
| `actuatorNotResponding` | `reset` | Error cleared |
| `missingTemperature` | `error` | Actuator entered emergency mode |
| `missingTemperature` | `info` | Sensor timeout |
| `missingTemperature` | `warning` | Temperature not updated within timeout |
| `missingTemperature` | `reset` | Error cleared |

---

## 6. Key Differences Between HeatArea Types

| Feature | D1-4B-05 (Valve) | D1-4B-06 (CosiTherm) | D1-4B-07 (Electro) |
|---------|-------------------|----------------------|---------------------|
| Setpoint step | 0.5°C | 0.1°C | 0.1°C |
| heaterMode "to" values | `heating` / `off` | `on` / `off` | `heating` / `off` / `on` |
| `feedTemperature` | Yes | No | No |
| `summerMode` | Yes | No | No |
| `thermalMode` | No | Yes (cooling/heating) | No |
| `energyConsumption` | No | No | Yes |
| `powerState` | No | No | Yes |
| `temperatureOrigin` | Yes | No | No |
| `circuitInUse` error | No | Yes | No |
| `actuatorLowBattery` | Yes | No | No |
| `actuatorDeactivated` | Yes | No | No |

---

## 7. Quick Reference: Common Operations

### Switch a device on/off
```
Topic: EnOcean/<GW>/put/devices/<DEVICE>/state
Payload: {"state":{"functions":[{"key":"switch","value":"on"}]}}
```

### Set dimmer level
```
Topic: EnOcean/<GW>/put/devices/<DEVICE>/state
Payload: {"state":{"functions":[{"key":"dimValue","value":"75"}]}}
```

### Set roller shutter position
```
Topic: EnOcean/<GW>/put/devices/<DEVICE>/state
Payload: {"state":{"functions":[{"key":"position","value":"50"}]}}
```

### Set heating setpoint
```
Topic: EnOcean/<GW>/put/devices/<DEVICE>/state
Payload: {"state":{"functions":[{"key":"temperatureSetpoint","value":"21"}]}}
```

### List all devices
```
Topic: EnOcean/<GW>/get/devices
(no payload)
Answer on: EnOcean/<GW>/getAnswer/devices
```

### Get gateway uptime
```
Topic: EnOcean/<GW>/get/config/system/uptime
(no payload)
Answer on: EnOcean/<GW>/getAnswer/config/system/uptime
```

### Subscribe to all device state changes
```
Subscribe: EnOcean/<GW>/stream/device/#
```

### Subscribe to all telegrams for a specific device
```
Subscribe: EnOcean/<GW>/stream/telegram/<DEVICE>/#
```
