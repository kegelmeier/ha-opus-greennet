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

**Write access.** A new entry starts with write access to device settings off:
enabling one of these entities is not enough, and a change is refused (nothing is
sent) until you enable write access under *Settings → Devices & services → Opus
GreenNet Bridge → Configure*, after a warning and a confirmation. Reading the
settings (*Read settings*, the daily read, *Settings read*) always works. The same
applies to the repeater mode, ViNET and the action
`opus_greennet.set_device_configuration`. Entries set up before the option existed
keep writing. While write access is off and one of these entities is enabled, a
notice appears under *Settings → System → Repairs*.

| Device | Number entities | Select entities | Not offered |
|--------|-----------------|-----------------|-------------|
| Blinds (D2-05-02) | Vertical movement time, Rotation time, Maximum holding time | ViNET Router, Repeater mode | Function of rocker A and B |
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
- That protection survives a restart of Home Assistant (for example after an
  update). Home Assistant saves only its own bookkeeping for it, not a value it
  shows, in a small file per gateway under `.storage`; the file is removed with
  the integration entry. After a restart the setting is therefore shown as
  unknown, rather than with the old value, until the gateway lists another
  value (usually at night) or *Read settings* has read it from the device.

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

**Settings read.** Every device with settings also has the diagnostic timestamp
sensor *Settings read* (enabled by default). It shows when the values above were
last read from the device: the time of the gateway's cached configuration
(`configuration.lastUpdateTime` of the device list), or the time of your last
*Read settings* press, whichever is later. The gateway reads the configuration
itself only now and then (nightly in the E0 catalog), and a device that does
not answer keeps its old values; on 2026-10-31 (firmware 1.21.30, one installation) 34 of 42
devices with a configuration time had been read at about 04:08 that night, five
battery detectors later that morning, and three actuators that did not answer
still showed the day before. An old time, for
example after Home Assistant was restored from a backup, is a reason to press
*Read settings*. The sensor is unknown while the gateway lists no time. It only
shows the time: nothing is requested for it, the integration never reads
settings by itself, and it does not mark a value as wrong.

**Read settings button.** Every device that has one of these settings also gets
a diagnostic button *Read settings* (enabled by default). Pressing it reads
the settings stored on the device once over the radio, which takes a few
seconds, and shows what the device reports. Use it after a write whose outcome
was unclear, or after a setting was changed with the OPUS app. Nothing is
written and nothing is repeated. A
setting the device does not report keeps its known value. A press while the
device is being read is ignored, and an answer that arrives after a write to
the same device started or ended meanwhile is dropped, because the write is
newer. The button is offered for blinds, D2-01-03 dimmers, D2-01-01/-11
switches and A5-07-03 presence detectors, the devices that have settings; reading is verified on hardware as the
read-back after a write, not yet as a button press.

**Daily read.** Every day at 05:00 local time (default) the integration reads
the stored settings of all these devices once, like the button, one after the
other. The gateway device has two configuration entities for it: the switch
*Daily settings read* (on by default) and the time *Daily settings read time*
(local time, to the minute). Both are restored after a restart of Home
Assistant; a change takes effect at once. This
keeps what Home Assistant shows in line with the devices, for example after a
Home Assistant backup was restored or a setting was changed with the OPUS app.
The read uses the lowest radio priority, so commands and status queries go
first; nothing is written and a failed read is not repeated. The run ends
after three failed reads in a row. Presence detectors are left out because
their battery powered radio answers only in a short wake window; use the
button for them. If Home Assistant is not running at the chosen time, that
day's read is skipped. Nothing is written to the devices either way. The
diagnostic sensor *Last daily settings read* on the gateway device shows when
the last run ended; its attributes `read`, `failed` and `stopped` tell how many
devices were read, how many reads failed and whether the run ended early (`failures`: three
reads in a row failed, `unavailable`: the gateway was not reachable). The last
result is restored after a restart, and the diagnostics download has a
`daily_settings_read` block with the schedule and the last result.

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

**Names in the OPUS config tool.** The OPUS config tool (JD Config Tool, which
talks to the devices directly by radio through a USB stick) names the settings
differently. The texts below are from the tool's own text file (German
interface, with the English text where the tool has one). The values were
compared on 2026-10-31 for 23 switches and 10 blinds of one installation
(project read by the config tool on 2026-10-30, gateway firmware 1.21.30): the
units and scaling match.

| Home Assistant (gateway key) | OPUS config tool (German / English text) | Notes |
|------------------------------|------------------------------------------|-------|
| Timer duration (`timer`) | Nachlaufzeit / Switch-on time | seconds |
| Timer duration, long press (`timerLong`) | **Nachlaufzeit Reinigungsmodus** / Switch-on time cleaning mode | seconds; the same setting under another name |
| Switch-off warning (`switchOffWarning`) | Zeitpunkt der Abschaltwarnung (Zeitdauer vor Abschaltung) / Time of switch-off warning (time before switch-off) | seconds |
| Switch-off warning signal (`switchOffWarningSignal`) | Dauer der Abschaltwarnung (kurzes Abschalten) / Duration of switch-off warning (short switch off) | milliseconds in the tool, seconds here |
| Illumination threshold (`illuminationThreshold`) | Helligkeitsschwelle: Bewegungsmelder schaltet unter dem Level EIN / Brightness level: movement sensor switches ON below this brightness level | lux; a linked motion detector switches the light on **below** this level |
| Operation mode (`operationMode`) | Betriebsart / Mode | stored as a number in the tool (0 = toggle, 3 = timer) |
| LED colour (`ledColor`) | LED | stored as a number in the tool (0 = off, 1 = green, 2 = red); the label was not checked |
| Vertical movement time (`verticalMovementTime`) | Fahrzeit für vollen Weg [ms] / Drive time for full way [ms] | the tool shows milliseconds |
| Rotation time (`rotationTime`) | Rotationszeit 100% [ms] / Rotation time 100% [ms] | the tool shows milliseconds |
| Maximum holding time (`maxHoldingTime`) | Haltezeit [s] (Maximale Haltezeit in the run-time dialog) / Switch-on time [s] | seconds |
| ViNET Router (`routingTime`) | ViNET | on or off in the tool; *on* is stored as 300 ms |
| Repeater mode | Repeater | off, level 1, level 2 |

The tool warns about combinations that make little sense: a cleaning mode time
shorter than the run-on time, a switch-off warning time longer than the run-on
time, a blind's drive time shorter than its rotation time, and a maximum
holding time shorter than the drive time. It also notes that a run-on time under
30 s needs actuator firmware 2.0.7.0 or newer. The integration refuses a write
that creates or worsens one of these four combinations (see above); the
firmware note is not checked.

**Settings only the config tool has.** The gateway's REST API does not offer the
following settings, so the integration can neither show nor change them; use the
config tool. This list comes from a config tool project, not from the gateway.
- Dimmers (D2-01-03): the dimming table (four brightness steps), the dim mode,
  the lower and upper dim limit, three ramp times and a switching ramp, and
  ViNET. The gateway lists *Ramping time*, *Short-press time* and *Long-press
  time* for a dimmer, and how they relate to the config tool's ramp times is not
  established, so a value changed there may not be the ramp the dimmer uses.
- Switches (D2-01-01/-11): the status delay (minimum and maximum), the ViNET
  filter and delay, an "SMS lock time" (milliseconds, meaning not established)
  and which light sensor is linked.
- Presence detectors (A5-07-03): about 45 parameters, among them the PIR
  repetitions and sensitivity, the temperature, humidity and light reporting and
  the wind values. The integration shows three of them.
- Window contacts: the alive cycle and the debounce times.


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
Writing these times is **not confirmed on hardware**. On one dimmer
(D2-01-03, software 0.1.0.3) a write of *Ramping time* with 0.5 s and with 1.0 s
was not stored: the dimmer kept 0, and the integration reported that the device
stored a different value and kept the previous one. Writing the value the dimmer
already had (0) was accepted. *Short-press time* and *Long-press time* were not
tested. Only the vertical movement time and the maximum holding time of blinds
were confirmed, so try a single dimmer first. The ranges are those of the parameter catalog read from one
D2-01-03; other dimmer types get no such entities.

**Two parameter sets for D2-01-03, chosen by product ID.** The 1 channel dimmer
from 2017 (product ID `004000000003`, article 563.035) has *Ramping time*,
*Short-press time* and *Long-press time*. The universal dimmer 563.076 (product
IDs `00400000001E` and, in the earlier version, `00400000001B`, as listed in the
product catalog of the OPUS config tool) has *Ramping time 1* to *3*,
*Switching ramp time*, *Lower limit*, *Upper limit* and *Dimming principle*
instead. Both use the EEP D2-01-03, so the integration decides by the product ID
the gateway reports: the universal dimmer gets only the second set, every other
dimmer only the first. The universal dimmer's parameters were read from a tester's
gateway (firmware 1.21.34); whether a 563.076 on another gateway firmware reports
the same keys is not verified.

**Settings follow what the device reports.** When the gateway lists a device with a
configuration, number and select entities for settings are created only for the keys
the device reports in it (the product ID decides only where the keys are not enough,
as for the two dimmers above). A device listed without any configuration keeps the
sets chosen by its EEP. Writing a setting the device does not report is refused
with the message *setting not reported* before anything is sent over the radio, also in
a configuration passed to the *set device configuration* action; the
routing and repeater mode are not affected. Entities that already exist for a key the
device does not report are not removed and stay unavailable.

- *Ramping time 1* to *3* and *Switching ramp time*: numbers in seconds, 0 to
  6553 s in steps of 0.1 s. According to the manual of the 563.076 the factory
  values are 4 s (dimming) and 0.5 s (switching). That *Ramping time 1* is the
  manual dimming time is taken from these defaults and the config tool; the
  use of ramps 2 and 3 is not verified.
- *Lower limit* and *Upper limit*: numbers in percent, 15 to 80 (defaults
  25 and 80 in the catalog). The dimmer scales the range; the upper limit may
  not be set below the lower limit (refused before anything is sent).
- *Dimming principle* (select): `phase control lower` (trailing edge, suited for
  LEDs) or `phase control upper` (leading edge). The manual describes switching
  the principle at the device itself; **writing it through the gateway is not
  verified on hardware.** Change it only with a matching load connected. The
  catalog default (`phaseControlUpper`) differs from the manual's factory default
  (trailing edge), so defaults are not used as stored values.
- Not offered yet: ViNET routing time of dimmers (unit and value for "on" not
  verified), LED colour and the function of rocker A and B.

**Dimmer times: what is not verified.** Which setting of the OPUS configuration
tool each of the three numbers corresponds to has **not been verified**. The
gateway calls them "Rampenzeit Ein/Aus", "Zeit ShortPress" and "Zeit
LongPress". In the texts of the configuration tool (3.0.5.54), *Ramping time*
is probably the tool's "Rampenzeit für Schalten" (the fade of switching on and
off); this is an assumption. The dimmer dialog of the tool has no setting that
matches the short-press and long-press time, and what they do on the dimmer is
not known. The three ramp times that dim commands can select (`rampingTime1` to
`rampingTime3`; the tool calls them "Rampenzeit manuell", "Rampenzeit 2" and
"Rampenzeit 3") are not offered by the gateway as settings, so the integration
cannot read or change them. A `transition` uses the first of them, so its
duration is probably not the *Ramping time* entity. Set these ramp times in the
configuration tool.

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
in seconds (0.3 to 2.55). *Off* is the text `off`; a device that routes reports
a number. A D2-01-01 switch with ViNET set to *on* in the OPUS config tool
reported `routingTime` = 0.3 in the gateway's device list (2026-10-31, gateway
firmware 1.21.30, one device), and the integration shows it as *on*. The
integration shows *off* or *on* (any time counts as on). Writing *on* sends
0.3 s, the value seen on that device. The config tool offers only *on* and
*off* (reported by the user), so it is not known whether other times are valid.
The project the config tool read from the installation (JD Config Tool 2.1.0.0,
read 2026-10-30) stores the same value as the field `iViNET` = 300 (milliseconds)
for that switch, so 0.3 s is confirmed as the value the config tool writes for
*on*. A tester of a second installation (gateway firmware 1.21.30) set the
*ViNET Router* select to *on* on 28 of 28 switches; the gateway accepted 0.3 s
and reported *on* each time. Whether routing actually takes place was not
verified. On the 24 blinds of that installation, 0.3 s was written through the
`set_device_configuration` action: 22 accepted it at once and returned it on
reading, two did not answer radio configuration requests at first (see
[Troubleshooting](troubleshooting.md)). Writing the state a device
already shows sends nothing. The repeater mode is not a
ReCom parameter but the device resource `/devices/{id}/repeaterState` (`off`,
`level_1`, `level_2`); it is written and read back there. *Read settings*
reads both. The device list carries the repeater mode too, so it is shown after a restart without a radio request. Switches have a diagnostic sensor (*ViNET Router (stored)*, *Repeater mode
(stored)*) and a select in the configuration category (disabled by default)
for each, and so do blinds (D2-05-02), whose parameter catalog lists the same
*ViNET Router* setting as the switches. Dimmers (D2-01-03) report the repeater
mode as well and get the *Repeater mode* sensor and select only (no *ViNET
Router* entities); writing it on a dimmer is untested.
Writing the repeater mode was tested on one D2-01-01 switch and on one D2-05-02
blind (off, level 1 and back to off) through the gateway interface, not through
these entities. The gateway accepted the value and returned it on reading. A
tester of a second installation (gateway firmware 1.21.30) then used the
*Repeater mode* select on its 24 blinds: four blinds that had a repeater (two at
level 2, two at level 1) were set to *off*, and one blind was set to level 2 and
back to *off*. The gateway returned each value, *Read settings* ran without
errors, and the repeater indicator of the MyOPUS app disappeared on the four
blinds. The direct signal strength stayed within about 5 dB. Whether a blind
changes its radio behaviour at level 2 was not verified.

**ViNET on dimmers is not available through the REST API.** The OPUS config
tool (v2, reported by the user) can set and read ViNET on a D2-01-03 dimmer, and
the gateway reports the repeater mode of that dimmer (`level_1`). The REST API
shows no routing time for it. On 2026-10-31 (gateway firmware 1.21.30) the
device list of one such dimmer, a configuration read with radio permitted
(the answer carries no read time, so it is not known whether the dimmer was
queried), the dimmer's profile and the parameter catalog in
`tests/fixtures/parameters_d2_01_03.json` listed only ramping time,
short-press time and long-press time; the profile has no settings at all.
**Hypothesis, not verified:** the gateway's EEP profile for D2-01-03 has no
routing parameter, so the value cannot be read or changed through the REST API.
The config tool does not use the gateway: it talks to the dimmer directly by
radio through a USB stick (reported by the user). That config tool project
confirms it: one D2-01-03 dimmer has ViNET on (`iViNET` = 300, repeater mode 1),
exactly like the D2-01-01 switch above, although the gateway lists no routing
for it. Of 39 devices with an `iViNET` field (25 switches, 10 blinds, 4 dimmers)
only that switch and that dimmer have ViNET on. The integration therefore
shows no *ViNET Router* entities for dimmers. This can change with a gateway
firmware that offers the parameter.

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

## Heat Area 3.0 Physical (D1-4B-03)

The heating zone for Moellenhoff systems (gateway profile "Heat Area 3.0
Physical", product "Heizzone für Möhlenhoff" in the OPUS config tool) appears
as a climate entity. It is an object of the gateway that reports the data of
the room control unit behind it; the device data may name another manufacturer
than Moellenhoff.

- **Setpoint:** 5–30 °C in steps of 0.2 °C; the unit reports its current
  temperature and setpoint.
- **Mode:** the unit reports heating or cooling. The gateway profile has no
  command for it, so the mode is read-only (switching it raises an error) and
  there is no on/off.
- **Valve position** (sensor, %): the share of the next hour the valve will be
  open. It is a plan of the unit and not a measured valve state, so the climate
  entity shows no heating action from it.
- **Diagnostics:** binary sensors for communication with the room control unit,
  heat circuits, battery and malfunction (on = the unit reported the problem).
  The battery warning is disabled by default, because the field is also
  reported by units without a battery; enable it if your unit has one.
- There is no humidity, window or feed temperature for this profile.
- The profile is taken from the gateway catalog (`/profiles/D1-4B-03`). It has
  not been tested with a real device yet.

## Read-only profiles from the gateway catalog

These profiles are taken from the gateway catalog (`GET /profiles/<EEP>`) and
the OPUS config tool product catalog (device names and article numbers from there). They only show what the device reports, except for the commands of D2-35-00 and the LEDs of D1-4B-50. The other profiles need a device to test commands; D2-34-10 would also need the gateway to feed the room temperature, which is a control loop and not a single command.
None of these profiles was tested with a real device yet. A
value the profile lists as "not available" or "error" shows as unknown; a
malformed or out-of-range value keeps the last known one.

| EEP | Device | Entities |
|---|---|---|
| A5-08-01 | Motion and brightness sensor, solar, 230 V or battery powered (561.292.37, 561.6486, 561.462; discontinued) | Illuminance, temperature, motion, occupancy button, supply voltage |
| A5-20-06 | Valve actuator Micropelt HOME (563.060, 563.061), Eurotronic Stella R EnOcean (700268) | Temperature, setpoint, feed temperature, valve position, window, obstruction, energy and radio diagnostics |
| A5-30-03 | WaterSensor con (563.067), 4 digital inputs | Digital inputs, wake signal, temperature |
| D2-11-01 | OPUS room operating panel with LCD, temperature, humidity and 2 buttons (563.107) | Temperature, humidity, base setpoint, fan speed, occupancy |
| D2-14-41 | IoT multisensor: temperature, humidity, vibration, magnet, light, NFC (563.222) | Temperature, humidity, illuminance, contact, acceleration (diagnostic, disabled) |
| D2-14-54 | OPUS 55 SENS temperature and humidity sensor (563.043.xx battery powered, 563.044.xx with power supply; 8 variants) | Temperature, humidity, water leak, leak in the last 14 days |
| D2-32-00, D2-32-02 | A.C. current clamp, 1 A to 60 A, 1 channel (CTV3.868) and 3 channels (CTV3.868.3CH.060A) | Current per channel, power failure |
| D2-34-10 | CosiTherm actuator, 2 or 6 heating circuits (78123, 78124) | Heater mode, system error, channel count; per heating circuit (channel 1 to 30, learned from the telegrams): temperature, setpoint, circuit state (on/off) and mode (on/off/emergency) |
| D2-35-00 | Oekoswiss heating actuator for infrared heating panels (388xxxxxx) | Mode, setpoint, outdoor temperature, window, load, power. A mode select (managed, off, on) and a setpoint number (5–40 °C, 0.5 °C steps) send commands; they show what the device reports, not what was sent (not tested with a real device). The two meanings of `temperature` and the contradicting `presence` text are not mapped |
| D1-4B-00, D1-4B-02 | Moellenhoff actuator (older, 3.0) | Heater mode, versions, IP address, errors (diagnostic, disabled by default) |
| D1-4B-01 | Moellenhoff heat area (older) | Temperature, setpoint |
| D1-4B-04 | Moellenhoff heat area 3.0 (virtual) | Climate as D1-4B-03, plus data mismatch and missing data |
| D1-4B-10 | WirelessMBus virtual device | Device type, manufacturer, serial number, version (diagnostic, disabled); the raw payload is not mapped |
| D1-4B-15 | Pressac power clamp | Total current, total power |
| D1-4B-50 | REG central control unit | LED status, uptime (diagnostic, disabled). A select (disabled by default) switches the LEDs on and off; the firmware reset and update commands are deliberately not offered |

The heating circuits of a CosiTherm actuator (D2-34-10) get their entities with
the first telegram that names the channel, and only up to the channel count the
device reports (30 until it does). A circuit that already has entities in the
entity registry gets them again when Home Assistant starts and shows *unknown*
until it reports; remove its entities in the registry to forget a circuit. This
is not tested with a real device.

D2-05-04 (REG-RJ), D2-70-00 (REG-CPU) and D2-74-20 (REG-FBH) appear in the
config tool product catalog, but the gateway has no profile for them, so there
is nothing to map.

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
  The signal strength is the direct radio link to the gateway, without
  repeaters (information from an OPUS technician, reported by the user). A
  device rated *Poor* can still be reached reliably through a repeater, so check
  whether one already serves it before adding another. The OPUS app shows the
  same values as Home Assistant. The OPUS config tool with a USB stick often
  shows better values, probably because it evaluates the repeater telegrams
  (not verified). The gateway reports no radio path: on 2026-10-31 (firmware
  1.21.30) the device list and the event stream snapshot carried no field for a
  repeater or a hop count. Which telegrams were repeated by which device cannot
  be read from the gateway interface, so the integration cannot draw a network
  plan from it. The radio coverage analysis of the config tool does show the
  repeater level of the received telegrams (see below).
  The radio coverage analysis of the OPUS config tool (version 3.0.5.54, USB
  stick next to the gateway, one installation, 2026-11-01) lists for every
  device the direct link (`D`) and the links through a level 1 (`L1`) and a
  level 2 (`L2`) repeater, each with a signal strength and the number of
  telegrams. Of 68 devices, 43 had telegrams and 6 had values under `L1`; none
  had `L2`, and none was reached only through a repeater. Only two of the six
  columns of the report carried data, and an actuator had between 1 and 25
  telegrams, so one report says little about a path. The numbers grow from the
  first to the second column, so the columns look like cumulative stages of one
  run and not like separate positions (not verified). For two devices with `L1` values the
  `L1` value (about -67 and -68 dBm) is close to the direct value of the only
  two devices in that installation with a repeater switched on (about -68 and
  -69 dBm). This suggests that one of them forwarded the telegrams; it is
  **not verified**, and the report does not name the forwarding device.
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
