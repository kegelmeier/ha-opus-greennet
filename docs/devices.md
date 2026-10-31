# Devices

How each device type appears in Home Assistant. For the list of supported
profiles see [Supported devices](../README.md#supported-devices).

- [Settings entities by device type](#settings-entities-by-device-type)
- [Switches and dimmers (D2-01)](#switches-and-dimmers-d2-01)
- [Buttons and rocker switches](#buttons-and-rocker-switches)
- [Blinds and shutters (D2-05)](#blinds-and-shutters-d2-05)
- [Presence detectors (A5-07)](#presence-detectors-a5-07)
- [HOPPE window handles](#hoppe-window-handles)
- [Water sensors (F6-05-01)](#water-sensors-f6-05-01)
- [Single input contacts (D5-00-01)](#single-input-contacts-d5-00-01)
- [Sensor values](#sensor-values)
- [Removing or replacing a device](#removing-or-replacing-a-device)
- [Actions](#actions)

## Settings entities by device type

The settings stored on actuators, as far as the gateway's parameter catalog
(read from one device per type) lists them. All are configuration entities and
**disabled by default**; the details are in the sections below.

| Device | Number entities | Select entities | Not offered |
|--------|-----------------|-----------------|-------------|
| Blinds (D2-05-02) | Vertical movement time, Rotation time, Maximum holding time | – | Function of rocker A and B, routing time |
| Dimmers (D2-01-03) | Ramping time, Short-press time, Long-press time | Repeater mode | – |
| Switches (D2-01-01) | Timer duration, Timer duration (long press), Switch-off warning, Switch-off warning signal, Illumination threshold | Operation mode, LED colour, External input function, ViNET Router, Repeater mode | Function of rocker A and B |
| 2-channel switches (D2-01-11) | as D2-01-01 | as D2-01-01, plus LED channel | as D2-01-01 |

How a setting is written, for every device type:

- A setting is **unavailable until the device reported it**; a setting the
  device never reported is never written.
- The value is rounded to the parameter's step, written with one ReCom request
  and **read back** from the device. Nothing is repeated automatically.
- If the read-back shows the written value, it is adopted. If it shows another
  value, Home Assistant shows that value (it is what the device now holds) and
  reports the difference. If the read-back fails, the previous value stays and
  an error says that the outcome is unknown.
- Changes to one device are made one after the other.
- The gateway may list the previous value until it reads the device again
  (usually at night). For up to 26 hours after a confirmed write, a device list
  with an older value is ignored for that setting; a change made in the OPUS
  app in that time shows up once the gateway lists it.

Which of these settings were written on real hardware, and which only read, is
listed in the [README](../README.md#what-was-verified-on-hardware).

**Stored values.** Every device that has settings also shows their current
values as read-only diagnostic sensors, **enabled by default** and named like
the setting plus *(stored)*, for example *Vertical movement time (stored)* or
*LED colour (stored)*. They show the values at a glance on the device page
without enabling anything. The number and select entities that change a
setting stay disabled: enable the one you want to change, and the sensor keeps
showing the stored value alongside it. The values come from the last device
list, from the last write the device confirmed, or from the *Read settings*
button below. A value of a select that the gateway's catalog does not list
shows as *unknown*.

**Read settings button.** Every device that has one of these settings also gets
a diagnostic button *Read settings* (enabled by default). Pressing it reads
the settings stored on the device once over the radio, which takes a few
seconds, and shows what the device reports. Use it after a write whose outcome
was unclear, or after a setting was changed with the OPUS app. Nothing is
written, nothing is repeated, and the integration never presses it itself. A
setting the device does not report keeps its known value. A press while the
device is being read is ignored, and an answer that arrives after a write to
the same device started or ended meanwhile is dropped, because the write is
newer. The button is offered for blinds, D2-01-03 dimmers, D2-01-01/-11
switches and A5-07-03 presence detectors, the devices that have settings; reading is verified on hardware as the
read-back after a write, not yet as a button press.

**Contradicting settings are refused.** The OPUS config tool warns about
settings that contradict each other. The integration goes one step further: it
refuses a write that would create or worsen such a combination, before anything
is sent to the gateway, and shows an error that names the combination. The
combinations are a blind's maximum holding time shorter than its vertical
movement time (the blind would stop before it is fully open), a vertical
movement time shorter than the rotation time, a switch's timer duration (long
press) shorter than its timer duration, and a switch-off warning longer than the
timer duration. A write that keeps or reduces an existing conflict is allowed,
so a wrong combination can be fixed step by step, and a value that is not known
yet is never compared. The check uses the values Home Assistant shows; if one of
them is outdated, press *Read settings* and try again. Change the other setting
first when a value is refused, for example raise the maximum holding time before
the vertical movement time.

Every actuator whose device list contains a link table also has the diagnostic
sensor *Wall switch links* (see [Sensor values](#sensor-values)). The
functions of rocker A and B are left out until a text
write has been tested on hardware; a test in the repository fails when a
catalog parameter has neither an entity nor an entry in that list of omissions.

## Switches and dimmers (D2-01)

**Switch or dimmer, and channel count.** At setup the integration reads one
gateway profile per EEP combination (`GET /devices/<id>/profile`, gateway data
without a radio telegram):

- A profile that accepts `dimValue` makes a light, one that accepts `switch` a
  switch.
- A `channel` key in the profile gives the channel count.
- When no profile can be read, the EEP specification decides (see the table in
  the README). Devices paired later use a profile already read for the same
  EEPs, otherwise the table until the next reload. Diagnostics show the source
  per device.

Earlier releases derived type and channel count from the EEP number. A
single-channel actuator that was created as channel 0 keeps its entity ID and
settings. Entities of channels or of a platform the device does not have are
removed (with a log warning) only when the gateway profile confirmed the
outputs; update dashboards and automations that used them.

**Dimming.** Dimmers switch to the new brightness at once, like commands from
Apple Home. An action with a `transition` (any duration) uses the dimmer's own
configured fade instead; the requested duration itself cannot be passed to the
device.

**Dimmer times.** D2-01-03 dimmers get three number entities for the times
stored on the dimmer, in seconds: *Ramping time* (the fade of switching on and
off, 0 to 25.5, step 0.1), *Short-press time* and *Long-press time* (0 to 2.55,
step 0.01). They are **disabled by default** and are configuration entities.
The values come from the device configuration that the gateway reads itself
(setup and every device list refresh, no extra radio telegram), so a change
made by another controller shows up with the next refresh and a value can be
up to a day old. A new value is written with the ReCom configuration request
and adopted only after it was read back from the dimmer; if the dimmer does not
confirm it, the previous value stays and nothing is repeated automatically.
Writing these three times was **not tested on hardware** (only the vertical
movement time and the maximum holding time of blinds were), so try a single
dimmer first. The ranges are those of the parameter catalog read from one
D2-01-03; other dimmer types get no such entities.

**Switch settings.** D2-01-01 and D2-01-11 switches get five number entities
for the settings stored on the switch: *Timer duration* (60 to 7200 s) and
*Timer duration (long press)* (60 to 18000 s), which only act when the
operation mode of the switch is *timer* (the second one when it was switched
on by a long press), *Switch-off warning* (0 to 45 s, how long a warning is
shown before the switch-off), *Switch-off warning signal* (0.2 to 2 s, step
0.001, the duration of that warning signal) and *Illumination threshold* (0 to
1500 lx, for linked light sensors). All steps are 1 except the warning signal.
The settings belong to the whole device: a 2-channel switch has one set, not
one per channel. They behave like the dimmer times above (configuration
entities, **disabled by default**, values from the device configuration, a new
value is adopted only after it was read back, nothing is repeated
automatically). The warning signal is rounded to three decimals, all other
values written by the integration to two. On one switch all these values were
**written on hardware and read back**, but through the gateway's REST API, not
through these entities, so try a single switch first. The ranges are those of
the parameter catalog read from one D2-01-01 and one D2-01-11; other switch
types get no such entities.

**Device information.** The device page shows the product ID (*model ID*), the
software version, the hardware version from the gateway's device list. The
device ID is shown as the *serial number* and, in addition, as the diagnostic
sensor **Device ID** of every device (disabled by default).

**Routing and repeater mode.** The gateway stores the routing as *off* or a time
in seconds (0.3 to 2.55); only *off* has been seen. The integration shows it
as *off* or *on* (any time counts as on). Writing *on* sends the smallest time,
0.3 s, which is an **assumption** until a device that routes was read; writing
the state a device already shows sends nothing. The repeater mode is not a
ReCom parameter but the device resource `/devices/{id}/repeaterState` (`off`,
`level_1`, `level_2`); it is written and read back there. *Read settings*
reads both. The device list carries the repeater mode too, so it is shown after a restart without a radio request. Dimmers (D2-01-03) report it as well and get the same entities (the dimmer catalog has no routing); writing it on a dimmer is untested. Each has a diagnostic sensor (*ViNET Router (stored)*, *Repeater mode
(stored)*) and a select in the configuration category (disabled by default).
Writing the repeater mode was tested on one D2-01-01 switch (level 1 and back
to off) through the gateway interface, not through these entities.

**Display.** The stored switch values (timer durations, switch-off warning,
illumination threshold) are shown without decimals, the warning signal with
one decimal (0.5 s). The units stay seconds; minutes can be chosen in the
entity settings of an entity.

**Switch selects.** D2-01-01 and D2-01-11 switches also get select entities for
the settings that have a fixed set of values: *Operation mode* (bottom on,
timer, toggle, top on; the timer durations above only act in *timer*), *LED
colour* (all, green, off, red), *LED channel* (2-channel switches only: which
channel the LED shows) and *External input function* (channel 1 and inactive,
for a 2-channel switch also channel 2 and channels 1 + 2), *ViNET Router* (off or on)
and *Repeater mode* (off, level 1, level 2). The same rules apply
as for the numbers: configuration entities, **disabled by default**, one set
per device, values from the device configuration, a new value is written with
the ReCom configuration request (`{"key": ..., "value": "<value>"}`) and
adopted only after it was read back, nothing is repeated automatically. A value
the gateway reports that is not in the list shows as *unknown* and is never
changed by the integration. Writing a text setting is **verified on hardware
for the LED colour of one D2-01-01 switch** (changed, read back and restored
with gateway firmware 1.21.30). The *Operation mode* of one switch was written
and read back through the gateway's REST API, not through this entity; the other
selects were **not tested on hardware**, so try a single switch first.
The function of the rocker buttons A and B is not offered: a switch reports a
value there that its own catalog does not list, and a wrong choice would change
which button switches which channel.

**Local control.** D2-01 actuators that report local control get a diagnostic
*Local control* binary sensor, disabled by default.

## Buttons and rocker switches

Rocker switches (F6-02, F6-03) get one event entity with an event per button
and action: `buttonA0_pressed`, `buttonA0_released`, …,
`multipleButtons_released`, with the attributes `button` and `action`. F6-03
rockers also report `buttonC0` … `buttonDI`.
A button the gateway reports on another channel than 0 fires the same event with
an additional `channel` attribute; events of channel 0 have none.

Many OPUS actuators also announce a rocker profile (F6-03-01) for their own
buttons. They get an event entity named *Buttons* with the unique ID suffix
`_buttons`, so it never collides with the switch, light or cover entity of the
same device.

All button event entities are meant for automations: they are enabled but
hidden by default, so they do not appear in automatically generated
dashboards. This applies to newly created entities; existing ones keep their
visibility.

## Blinds and shutters (D2-05)

**Position while moving.** Blinds report position and angle as `unknown` while
they move; Home Assistant keeps the last known value until the end report
arrives. Command targets of `unknown` are ignored.

**Estimated movement.** Covers with a positive configured `verticalMovementTime`
show `opening` / `closing` while travelling instead of jumping to the target:

- The estimate uses the distance from the last known position (a full travel
  when the start is unknown) plus five seconds of grace, or seven seconds when
  the target is fully open or closed (a run into the end stop is reported
  about 1.4 s later). It is an estimate, not a movement sensor; a wrong
  configured time or a stalled motor makes it inaccurate.
- A new target replaces the estimate. A numeric position report, Stop or a
  rejected command ends it immediately.
- Status checks run 10 and 20 seconds after the estimate ends. Example: for a
  25-second full travel the UI shows movement for up to 30 seconds and checks
  the status at 40 and 50 seconds.
- Without feedback, the target becomes the assumed position when the estimate
  ends. Covers without a configured travel time just show the reported state.

**Tilt.** Tilt controls are hidden when the gateway reports `rotationTime` as `0`
or `noRotation` (plain roller shutters). Without a valid rotation time, tilt
support follows the EEP.

**Stop** sends the profile's stop command and then checks the reported position
and, where supported, tilt. It does not assume a new position.

**Diagnostic controls** (D2-05-00 and D2-05-02; the time settings D2-05-02 only):

| Entity | What it does |
|--------|--------------|
| **Blockage** switch | For automations such as wind protection or an open door. On sends `lockingMode = block`, off sends `unblock`. A D2-05-00 `alarm` counts as blocked; the reported mode is the `locking_mode` attribute. **Disabled by default** because it stops the blind. |
| **Reference run via top / bottom** buttons | Drive the blind to an end position first (`repositioning = firstUp` / `firstDown`) and then back to its last known position. This recalibrates the blind's position tracking. |
| **Last run time** sensor (every blind) | Seconds from a position command (from HA, another controller or a reference run) to the blind's report at the target. Attributes: `direction` (`up`, `down`, `reference_up`, `reference_down`), start and end position (100 = open) and `full_travel` (true when the run covered the whole height). The value is the time from sending the command to the blind's end report, not the mechanical travel time: it includes radio and gateway delay and whatever the blind does before it reports, so it is longer than the configured `verticalMovementTime`. Measured on 24 D2-05-02 blinds in one installation (firmware 1.21.30): about 1.4 s are added to every run, and about 5 s more when the run ends at an end stop (fraction of the travel × stored time + 1.4 s, or + 6.4 s at an end stop). The values follow the stored time, not the physical stop: blinds whose stored time was much longer than their real travel (46.4 s for runs of 12–28 s) still reported the stored time plus about 5 s, so the blind reports by its own timer and the sensor cannot show whether the stored time is right (derived from one installation; vendor documentation not seen). Compare only runs of the same kind with each other, not with the stored time. Runs ended by Stop, without an end report or only confirmed by a status query record no time. The last value is kept across Home Assistant restarts. |
| **Vertical movement time**, **Rotation time** and **Maximum holding time** numbers | The travel time and slat rotation time stored on the blind, in seconds (0 to 655.35, step 0.01), and the maximum holding time (0 to 65535 seconds, step 1; the longest time a run may take). D2-05-02 only. The first two are used for the estimated movement; the maximum holding time is only shown and written, it changes nothing in Home Assistant. A value smaller than the vertical movement time may end a run early; this was not tested. Written and read back as described in [Settings entities by device type](#settings-entities-by-device-type). **Disabled by default.** The write (`{"configuration": {"parameters": [{"key": ..., "value": ...}]}}`) was verified for the vertical movement time and the maximum holding time on one blind each (firmware 1.21.30): the gateway answered 200, the changed value was read back and the other parameters stayed unchanged. The rotation time was not tested on hardware, so try a single blind first. |

Movement and rotation times always come from the device list (seconds). The
milliseconds of a D2-05-00 "Set parameters" command sent by another controller
are not taken over.

## Presence detectors (A5-07)

- `motionDetected = false` means the detector is *uncertain of occupancy*, not
  that the room is certainly empty; Home Assistant shows it as `off`.
- An illumination above the sensor range (`overRange`, above 1000 lx) is shown
  as the range maximum with the attribute `over_range: true`.
- The diagnostic *Sensor error* binary sensor is on while the detector reports
  an error code (251–255).
- A5-07-01 has no illumination sensor, so it gets no illuminance entity.
- A5-07-03 detectors also show three of their settings as read-only diagnostic
  sensors, enabled by default: *Alive cycle (stored)* (minutes),
  *PIR off time (stored)* and *ReCom readiness cycle (stored)* (seconds), in the
  unit the gateway reports. The values come from the gateway's cached device
  list (it reads them from the detector itself, so they can be up to about a day
  old) and from the *Read settings* button. Nothing writes them: there is no
  number entity, because a battery detector answers a ReCom request only while it
  is ready (the readiness cycle, four hours on the gateway this was read from)
  and a write has not been tried on hardware. The detector sensitivity is not
  listed by the gateway, so it is not shown.
- The *Read settings* button of a detector is enabled like the others, but it
  asks the detector over the radio, which is answered only inside the readiness
  window. Outside it the gateway reports *no response from device* after up to
  45 seconds, and the radio queue is busy meanwhile, so press it once and wait.
  One press reads once and is never repeated. That the detector answers such a
  read has not been verified on hardware.
- The OPUS motion detector (F6-02-01 + A5-04-01) reports motion as
  `buttonAI` and no motion as `buttonA0`; it also gets temperature and
  humidity sensors.

## HOPPE window handles

All three handle profiles (D2-06-40, F6-10-00, D2-03-10) provide:

- a **Handle position** sensor (the main entity),
- diagnostic **Open**, **Tilted** and **Closed** binary sensors for automations
  (enabled and visible); for Cover Control Automation, assign Open and Tilted
  to its window-contact inputs,
- a diagnostic battery level from the device list.

`tilt` (D2-03-10) and `up` (F6-10-00) are normalized to `tilted`. Missing or
invalid positions, including the gateway's `unkown`, stay unknown. The sensors
report the handle position, not an independent measurement of the sash.

**D2-06-40 AutoLock.** The handle additionally reports its lock state, an unlock
request, a mechanics fault and a diagnostic **Lock bolt** binary sensor (on =
unlocked). The **AutoLock** lock entity sets the answer the gateway gives the
handle when its button is pressed and it asks to be unlocked
(`unlock = requested`, attribute `unlock_requested`):

| Lock entity | Stored answer | Handle |
|-------------|---------------|--------|
| *Unlocked* | `unlock = allowed` | Button lights green, handle can be turned |
| *Locked* | `unlock = notAllowed` | Button lights red, handle stays locked |

The gateway stores the answer as the handle's target state (status 202) and
uses it at every request, also while the handle sleeps. The entity shows the
stored answer, read from the device list at start-up; the attribute
`bolt_locked` repeats the reported lock bolt. Quick changes settle for 10
seconds (*locking* / *unlocking*) and only the last one is sent, because the
gateway queues every answer for the sleeping handle.

## Water sensors (F6-05-01)

The leak sensor sends only when its state changes, and the gateway lists no
state for it. Until it has reported water, the binary sensor shows *dry*
instead of *unknown*; a gateway outage still shows *unavailable*.

## Single input contacts (D5-00-01)

A *Contact* binary sensor with the opening device class: `open` / `closed`
appear as `on` / `off`. Missing or unrecognized values stay `unknown`. No
battery percentage is invented when the gateway publishes none.

## Sensor values

- Measurements reported as `notAvailable` become unknown. Unknown values are
  never read as "no alarm" or "closed handle".
- Lights and switches stay unknown until their state is reported or a command
  is accepted.
- Battery percentages accept an optional `%` suffix (the gateway reports for
  example `96%`) and must be within 0–100.
- Battery level and signal strength come from the device list and are
  refreshed every 30 minutes; the signal strength sensor is enabled by
  default for new installations (an entity that was disabled before stays
  disabled until you enable it).
- Every device also has a diagnostic **Signal quality** sensor rated from the
  signal strength: *Good* above -75 dBm, *Sufficient* from -75 to -85 dBm,
  *Poor* below -85 dBm (a repeater, usually level 1 halfway, is recommended).
  The icon follows the rating; colours are not offered by the integration
  (a dashboard card can colour the value by these limits).
- Every device also has a diagnostic **Last seen** sensor (enabled by
  default for new installations; an entity that was disabled before stays
  disabled until you enable it): the time of its newest report, from the telegram timestamp or the
  gateway's device list. It is a point in time, not a health verdict; a
  value without a UTC offset or in an unknown format stays unknown. Devices
  that only send on a change (for example leak sensors) can legitimately stay
  unchanged for a long time.
- Actuators whose device list contains a link table also have a diagnostic
  **Wall switch links** sensor (disabled by default): the number of wall
  switches the actuator listens to. The sensor appears as soon as the gateway
  lists a table for the device, also when that happens after the device was
  discovered. The table is read from the device list
  every 30 minutes without a radio request and reflects what the gateway has
  stored (it reads the tables from the devices itself, typically at night). A
  table without inbound entries is shown as 0; if the gateway lists no table,
  the value stays unknown instead of 0. The downloaded diagnostics list the
  same table per device (device addresses redacted); the integration never
  writes link tables.
- Climate activity uses actuator feedback where available; enabling a heating
  zone does not by itself mean it is heating.

## Removing or replacing a device

Open **Settings → Devices & services → Opus GreenNet Bridge → Devices**, select
the device and choose **Delete**. The gateway itself cannot be removed this
way; remove the integration entry instead.

- This removes only the Home Assistant device and its entities. Nothing is
  unpaired or deleted on the gateway (see the [roadmap](../README.md#roadmap)).
- If the gateway still lists the device, it comes back after a reload or
  reconnection. Quiet devices are never deleted automatically, because the
  gateway cannot confirm that a device is gone.
- When replacing a device, first remove the old one in the OPUS app, then
  delete it in Home Assistant. Automations that reference the old entities are
  not migrated.

## Actions

Available in **Developer Tools → Actions**. `device_id` is the EURID of the
device (fictional example: `AABB1122`). Select a gateway with
`config_entry_id` when more than one gateway is set up.

| Action | Parameters | Description |
|--------|------------|-------------|
| `opus_greennet.set_cover_position` | `device_id`, `position` (100 = open), optional `repositioning` (`direct`, `first_up`, `first_down`), `channel`, `config_entry_id` | Move a D2-05 blind, optionally after a reference run |
| `opus_greennet.get_device_configuration` | `device_id` | Admin only. Read the configuration stored on a device (ReCom) |
| `opus_greennet.set_device_configuration` | `device_id`, `configuration` | Admin only. Write configuration to a device (ReCom). The integration checks only that the object can be sent as JSON (string keys, finite numbers, at most 16 levels and 64 KiB) and refuses anything else before a request is queued; the content is not interpreted |
| `opus_greennet.get_device_parameters` | `device_id` | Admin only. Read the parameters a device supports (ReCom) |
| `opus_greennet.get_device_link_tables` | `device_id` | Admin only. Read the link tables stored on a device (ReCom): `inbound` lists the wall switches it listens to (with rocker and central on/off flags), `outbound` the devices it reports to. The gateway's own address is shown as `gateway`. Read only: there is deliberately no action to write link tables, because a wrong table can cut a device off its wall switches |
| `opus_greennet.reload_entry` | optional `config_entry_id` | Admin only. Reload the integration without restarting Home Assistant |

The read actions return their data directly as the action response. ReCom
requests travel over the radio and may take several seconds (timeout 45 s).
