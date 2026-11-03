# Troubleshooting

- [Find the failing link](#find-the-failing-link)
- [Gateway not discovered](#gateway-not-discovered)
- [Devices not appearing](#devices-not-appearing)
- [Recovery](#recovery)
- [Diagnostics](#diagnostics)
- [Debug logging](#debug-logging)
- [Measuring update lag](#measuring-update-lag)

## Find the failing link

Three independent links are involved. Find out which one fails before changing
the configuration:

| Link | Symptom | Check |
|------|---------|-------|
| Network to the gateway | All entities unavailable; actions fail with "The OPUS gateway is unavailable" | Gateway power and address; `http://<gateway>:8080/system/uptime` must answer (with the API credentials). With WebUI 2026 the address is `https://<gateway>/system/uptime` with a login token |
| Gateway credentials | The entry asks for re-authentication | User and password of the gateway API. After a gateway update to WebUI 2026: enter the user and password of its web interface (Home Assistant notices the update by itself after the gateway stayed unreachable for a few minutes, or at the next start of the integration). A *Confirm the gateway certificate* step means the gateway presents another certificate than the remembered one |
| EnOcean radio | Entities available, but the device does not follow | Range, power and pairing of the actuator; the debug log of the follow-up status queries |

A gateway outage makes the entities unavailable and cancels queued commands;
they are not replayed afterwards. A quiet radio device never makes an entity
unavailable.

## Gateway not discovered

Discovery relies on mDNS (`_enocean._tcp`). It does not work when Home
Assistant cannot receive multicast from the gateway, for example in Docker
without host networking, across VLANs without an mDNS repeater, or when the
gateway's announcement is blocked. Add the gateway by its address instead; the
integration works the same.

A gateway that was ignored under **Discovered** stays ignored; remove the
ignored entry to see it again.

### "The gateway address is not in the local network"

Entries that were set up with a public IP address before the gateway address
had to be local keep working, but **Settings → System → Repairs** shows this
notice for them, because the gateway speaks unencrypted HTTP and its login can
be read on the way. Re-authentication and reconfiguration only accept a local
address (error `host_not_local`). Choose **Reconfigure** in the integration's
menu and enter the gateway's address in your local network, or reach the
gateway through a VPN. The notice disappears when the integration is loaded
again with a local address. Only IP addresses are checked when the integration
loads; a host name is checked when you enter it.

## Devices not appearing

1. Check that the integration entry is loaded and the gateway is available.
2. Check that the device is paired with the gateway and visible in the OPUS
   app.
3. Look for messages from `custom_components.opus_greennet` in the log.
4. Newly paired devices appear on the next stream start or device list refresh,
   at the latest after 30 minutes. Reloading the entry adds them at once.

## Recovery

- If the gateway does not answer at startup, Home Assistant retries the setup
  automatically.
- After a connection loss or a gateway restart, the event stream is reopened
  and every device is resynchronised.
- The `opus_greennet.reload_entry` action restarts the integration without
  restarting Home Assistant. Pending requests are cancelled.
- An action that timed out may still have been executed: check the physical
  state before repeating it.
- A change of a device setting that is refused with "Write access to device
  settings is not enabled" was not sent. A new entry starts with write access
  off: enable it under **Settings → Devices & services → Opus GreenNet Bridge →
  Configure** (after the warning). Reading settings is not affected.
- Changing a setting of a device (for example the vertical movement time of a
  blind) is a write followed by a read-back. If the gateway rejects the
  **write** (for example `504`, no response from device), the message says
  the new value could not be written; the device probably did not get it. If
  it rejects the **read-back**, the message says the gateway accepted the
  value but reading it back failed; the value may be stored. In both cases
  Home Assistant keeps the previous value, nothing is repeated automatically,
  and the device configuration should be read before trying again: press the
  device's **Read settings** button (diagnostic) or use the
  `opus_greennet.get_device_configuration` action. A timeout of the write or an
  unreachable gateway keeps its own message; a read-back that fails in any other
  way is reported as not confirmed.
- A `504` for radio configuration requests (read or write) can point to a radio
  reach problem. In one installation two blinds answered none of them at the
  gateway's original position, with the repeaters off and ViNET being switched
  on, although the blinds drove normally; they answered after the gateway was
  moved to another room and again after it was moved back. Position, active
  ViNET and the gateway restart could not be told apart, so this is an
  observation, not a diagnosis.

## Diagnostics

**Settings → Devices & services → Opus GreenNet Bridge → ⋮ → Download
diagnostics** creates a redacted report: gateway status, devices, channel
state, last update source and the latest command error. Addresses,
credentials, identifiers and the gateway's HomeKit setup code (also as QR
payload) are redacted. Attach it when you report a problem.

The same facts are visible in Home Assistant without a download: the gateway
device shows its serial number, and its diagnostic sensors show firmware, app
and API version, received telegrams and the average signal strength. EURID, hostname, IPv4 and IPv6 address have sensors too, but
they are disabled by default (the download itself keeps them redacted).

Useful fields:

- `first_device_list_seconds` (gateway): time from opening the event stream to
  its first device list at startup.
- `versions` (gateway): `firmware`, `app` and `api` version from the gateway's
  `/system/info` (also written to the log at startup as
  `OPUS gateway versions: firmware=… app=… api=…`, and again when a version
  changes). It is read at startup and then every 30 minutes together with
  the device list refresh (a plain gateway read without radio traffic, never
  with the health probe). `receive_counter` (received telegrams) and
  `average_dbm` (mean signal strength) come from the same read, so they are as
  old as `info_read_at`. A field the gateway does not report stays empty.
- `connection` (gateway): `hostname`, `ipv4` and `ipv6` of the configured
  gateway address (IPv6 only if the host name has one), resolved when the download is made. Both are redacted when they have
  a value. An empty value means the name or address could not be resolved; that
  is no error.
- `stream` (gateway): facts about the event stream, read from the integration's
  memory (no request to the gateway):
  - `connected`: whether the stream is open now.
  - `framing_requested`: `line` (one document per line) or `plain` (the
    gateway refused the line request); empty before the first stream. It is
    the request the gateway accepted; firmware that ignores the parameter
    answers in the plain format anyway.
  - `attempts`: opened stream requests since the integration was loaded.
  - `connects` and `reconnects`: streams that delivered a document, and those
    after the first one. Many attempts with few connects point to a
    gateway or network that is often unreachable.
  - `radio_queue_depth`: requests waiting for the gateway's radio queue at the
    moment of the download. Values above 0 for long are worth reporting.
  The counters start at 0 whenever the integration is reloaded.
- `radio_journal` (in the block of every device): what the integration sent to
  that device over the radio, from the last 300 requests of all devices
  together, oldest first. It is kept in memory only (empty after a reload or
  restart). Each entry has:
  - `seq`: a counter that orders the entries across devices.
  - `at`: when the request ended (UTC).
  - `request`: `command` (a state command), `status_query`,
    `configuration_write`, `configuration_read` (also the read-back after a
    write), `parameters_read`, `link_tables_read`, or `feedback`.
  - `priority`, `duration_ms` and `waited_ms` (time in the queue before the
    request was sent).
  - `values`: the keys and values that were sent. Text is shortened, nested
    values show their type.
  - `outcome`: `ok` (the gateway accepted the request), `deferred` (the gateway
    stored it for a sleeping device), `rejected` (with `gateway_status`, the
    optional `http_status` and the gateway's text in `message`), `timeout`,
    `connection_error`, `auth_rejected`, `cancelled` (the caller gave up while
    the request was in flight, so it may still have been sent) or `error`.
  - A `feedback` entry is written when a device reported a field that one of
    our commands was still waiting for (`fields`, `channel`).

  `ok` only means that the gateway accepted the request; it is neither the
  stored value nor the device's state. A configuration write has two entries:
  the write and the read-back. If the write is `ok` and the read-back is
  `rejected` (for example `504`, no response from device), the value may have
  been stored. Nothing is repeated automatically. Requests that were not sent
  (gateway offline, device unknown, queued check dropped) have no entry.
- `last_vertical_movement_time` (covers): the last run duration the gateway
  reports. It is not a measurement of the travel: on 24 D2-05-02 blinds in one
  installation (firmware 1.21.30) it equalled the stored vertical movement time
  plus about 5 s on 20 of them, and a partial run did not change it. Two blinds
  reported 0 and one an older value.
- Calibration tip (vertical movement time): the stored time should equal the real
  moving time, ideally measured from the start of the movement. A value that is too
  large lengthens the blind's own run, the travel estimate and the last run time,
  and may delay reactions to a wall switch.
- `position_origin` (covers, per channel): `device list`, `reported`,
  `estimated` or `assumed` (the travel estimate ended without a report).
- The profile source of every D2-01 device (gateway profile or EEP table).

## Debug logging

For a one-off check use **Settings → Devices & services → Opus GreenNet Bridge
→ ⋮ → Enable debug logging**, reproduce the problem and disable it again to
download the log.

With debug logging every radio request writes one line, `OPUS radio request:
device=… request=… outcome=… gateway_status=… duration_ms=… values=…
message=…`, with the same content as a `radio_journal` entry, and a device
report that confirms a command writes `OPUS device … reported […]`. The
journal in the diagnostics download is filled without debug logging; the log
adds the exact time order with other messages.

For persistent logging add to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.opus_greennet: debug
```

## Measuring update lag

With debug logging every applied telegram is logged as
`OPUS telegram/from <device> ... gateway_to_ha_ms=<n>`: the time from the
gateway's telegram timestamp until Home Assistant received it (clock offsets
between the two affect this value). Entity writes are logged as
`OPUS update latency entity_write` with the time since the telegram arrived.

Press a physical switch and compare both values. If `gateway_to_ha_ms` is
already high, the delay is in the gateway or the network.
