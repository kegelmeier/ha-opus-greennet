# How it works

- [Discovery](#discovery)
- [Updates and availability](#updates-and-availability)
- [Commands and acknowledgements](#commands-and-acknowledgements)
- [Large installations](#large-installations)
- [Known limitations](#known-limitations)
- [Gateway REST API](#gateway-rest-api)

## Discovery

OPUS gateways announce the service `_enocean._tcp` (instance `opusshgw`) over
mDNS. The announcement contains no EURID and no credentials, so the
integration reads `/system/info` of each announced gateway:

- **Configured gateway at a new address:** the entry's credentials are tried
  at the announced address. If the gateway reports the entry's EURID, the
  address is updated and the entry reloads.
- **Entry of the former MQTT setup** (EURID only): the vendor default
  credentials (`admin`, password = EURID) are tried. On a match the entry gets
  address and credentials and loads without any input. The same search also
  runs once when such an entry is set up.
- **New gateway:** offered under **Discovered** with its address; the user
  enters the password.

Discovery only reads `/system/info`; it sends no radio telegram. IPv6-only
announcements are ignored.

## Updates and availability

The integration is a local push integration. It keeps one HTTP event stream
to the gateway open (`GET /devices/stream`):

- **Every stream starts with the complete device list**, which resynchronises
  all state, and then delivers each telegram as the gateway handles it,
  typically within a few hundredths of a second. This includes local switch
  operation, rocker presses and commands from other controllers such as
  HomeKit.
- **At startup** this first list is the setup snapshot. If it does not arrive
  within 30 seconds, Home Assistant retries the setup later. The streamed list
  has no cover movement times, so the device list is read once more right
  after startup for these settings; it never changes live state.
- **Reconnects.** If the stream ends, it is reopened after a pause that starts
  at 1 second, doubles up to 60 seconds and gets up to 25 % random jitter. A
  stream that closes within 10 seconds counts as a failed attempt, so a
  gateway that closes every connection cannot cause a reconnect loop.
  Rejected credentials are not retried; they start a re-authentication.
- **Health check.** The gateway uptime is read about every 60 seconds. Entities
  become unavailable when the gateway stops answering, and the stream is
  reopened after a gateway restart.
- **Device list refresh.** Every 30 minutes the device list is read again for
  values the stream does not carry (battery level, signal strength, cover
  movement times) and to add newly paired devices. A telegram arriving
  meanwhile keeps its newer signal strength and last-seen time.
- Batteryless EnOcean devices are not marked unavailable just because they are
  quiet.

### Stream format and fallback

The stream is requested with `delimited=newLine` (one JSON document per line).
This was verified on a gateway with firmware 1.21.30; the other stream
parameters (`direction`, `output`, `levelOfDetail`) are deliberately not
requested. Firmware that ignores the parameter answers in the older multi-line
format, which the same reader handles. If the gateway answers the request
with any other status than 200 (except rejected credentials and 429), the
integration asks once more without the parameter. Once that plain stream
delivers, the choice is remembered until the gateway restarts, when line
delimiting is offered again. Timeouts and connection errors never switch the
format. Telegrams in the `to` direction (commands of other controllers, such as
Apple Home) were observed on a gateway with firmware 1.21.30, each followed by
the actuator's own report about 0.1 s later.

### Connections and state writes

All requests use Home Assistant's shared HTTP session; the integration neither
creates nor closes a session of its own. The event stream occupies one
connection, and requests to the gateway share one radio queue (see
[Large installations](#large-installations)).

Every telegram is applied to the device state at once. A telegram that changes
nothing the entity shows does not write the entity state again; rocker events,
availability changes and command feedback are never delayed or merged. The
integration does not delay or combine state updates, so Home Assistant sees
every changed value as the gateway reports it.

## Commands and acknowledgements

Commands are HTTP requests (`PUT /devices/<id>/state`); the gateway's response
is the acknowledgement:

| Status | Meaning |
|--------|---------|
| `200` | The gateway sent the telegram |
| `201` | Accepted for deferred delivery (for example to a sleeping device) |
| `202` | Target state stored by the gateway (HOPPE AutoLock) |
| other | The action fails with an error |

An acknowledgement confirms the gateway, not the radio actuator. Home Assistant
shows the expected state and checks it with channel-specific status queries 5
and 20 seconds later (for covers 10 and 20 seconds after the estimated end of
the movement). Only a valid value for the requested field on its channel
confirms a command; interim `unknown` / `invalid` readings do not cancel the
checks.

Commands sent by another controller (HomeKit, the gateway's own logic) also
appear on the event stream. They are shown immediately and verified the same
way.

If an action is not acknowledged within 10 seconds it fails, but the gateway
may still execute it: check the physical state before repeating it. Commands
and configuration writes are never retried automatically.

## Large installations

Gateway requests that use the radio share one queue: exactly one is in flight
at a time. Control commands go before status queries, and delayed background
checks wait behind both, so large batches take longer. A gateway outage
cancels the queued commands instead of replaying them after recovery.

## Known limitations

- An acknowledgement (`200`, `201`) confirms the gateway, not the actuator.
  Physical completion is only known from device feedback or a status query.
- Cover positions during movement are estimates from the configured travel
  time.
- A HOPPE AutoLock answer queued for the sleeping handle takes effect when the
  handle wakes up (for example at its next button press).
- Discovery needs mDNS between Home Assistant and the gateway. Without it, a
  changed gateway address has to be entered with **Reconfigure**.
- The gateway API before WebUI 2026 is plain HTTP with basic authentication, as
  provided by the manufacturer. Keep the gateway in a trusted network. Gateways
  with WebUI 2026 use HTTPS with a pinned certificate and a token (see the
  README).
- Battery level and signal strength are refreshed every 30 minutes.
- The signal strength is the direct link to the gateway, without repeaters. The
  gateway reports no radio path, so the integration cannot show which repeater
  serves a device (see [Devices](devices.md)).
- Devices cannot be added to or removed from the gateway from Home Assistant
  yet, and device settings are only available as actions (see the
  [roadmap](../README.md#roadmap)).
- Quiet devices are never removed automatically.
- The automated tests use a simulated gateway; physical devices are verified in
  beta testing.

## Gateway REST API

The integration implements the client side of the EnOcean Alliance
[EnOcean over IP REST API](https://www.enocean-alliance.org/ip/) with the
gateway's basic authentication. Every answer is a JSON document with a
`header.httpStatus`; the integration checks the HTTP status and the gateway
status separately.

```
GET  /system/info                      # Gateway identity (EURID) and version
GET  /system/uptime                    # Health check
GET  /devices                          # Device list with current state
GET  /devices/stream                   # Event stream: device list, then telegrams
GET  /devices/{deviceId}               # One device (late discovery)
GET  /devices/{deviceId}/profile       # Gateway profile (switch/dimmer, channels)
PUT  /devices/{deviceId}/state         # Commands and status queries
GET  /devices/{deviceId}/configuration # ReCom: stored configuration
PUT  /devices/{deviceId}/configuration # ReCom: write configuration
GET  /devices/{deviceId}/parameters    # ReCom: supported parameters
GET  /devices/{deviceId}/linkTables    # ReCom: link tables (read only)
```

Each telegram on the stream is one JSON document (fictional values):

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
    "telegramInfo": {"data": "0000000A", "dbm": -65, "rorg": "A5"}
  }
}
```

`direction` is `from` for telegrams sent by a device and `to` for telegrams the
gateway sent to it. The full interface contract, including the payload
variants of older firmware, is in
[`docs/quality/protocol-contract.md`](quality/protocol-contract.md).
