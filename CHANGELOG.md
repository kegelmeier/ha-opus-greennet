# Changelog

All notable changes to this project will be documented in this file.

## [1.2.0b0] - 2026-10-30

First beta of 1.2. Support for gateways with **WebUI 2026**, settings you can **read on request**, **routing and repeater mode** as entities, and much more detail for diagnosing the radio network: a radio journal, gateway versions and address, a signal quality rating. Built on @fubu2k's work (#112). v1.1.1 stays the stable release.

> **WebUI 2026 support is experimental.** No gateway with WebUI 2026 has been tested yet: the login and the token handling follow the documented pattern but are unverified. When the access token expires, the gateway can show as unavailable for up to a minute or one command can fail; this is tracked for a later version. Gateways of the earlier generation (HTTP, port 8080) are not affected.

### Requirement
- **Home Assistant 2026.10.1 or newer**, as before. CI now uses 2026.10.4 as the fixed reference.

### Before you update
- **Nothing is written to your devices by this update.** The new entities that change a setting (*ViNET Router*, *Repeater mode*) are disabled by default, like the other settings entities.
- **New read-only entities are enabled by default** and appear after the update: the stored settings sensors (named *… (stored)*), the *Read settings* button, *Signal quality* and, on the gateway device, firmware, app and API version. Disable the ones you do not want in the entity settings.
- **Disabled by default:** the sensors *Device ID*, and on the gateway device EURID, hostname, IPv4 and IPv6 address. The EURID is the default gateway password, so enabling its sensor makes it visible in the entity history. The diagnostics download keeps the EURID redacted.
- **Tested on hardware:** reading the settings after a write, the repeater mode of one D2-01-01 switch (level 1 and back to off, through the gateway interface, not through these entities), and the stored values of a presence detector from the device list. **Not tested:** a gateway with WebUI 2026 (login endpoint and token header are assumptions), pressing *Read settings* on a presence detector, writing the repeater mode on a dimmer, and writing *ViNET Router* *on* (the smallest routing time is an assumption).

### Added
- **Gateways with WebUI 2026.** These gateways answer the REST API only over HTTPS (port 443) with a user login. They are recognised automatically (`/auth/setup-status` without a login is asked first, so the new password is never sent to port 8080 in clear text). The config flow asks for the web interface user and password, remembers the gateway's self-signed certificate and asks you to confirm it when it changes. The access token is kept in memory only; a rejected token is requested again with the next request, and the request that met the rejection is not repeated.
  - An existing entry whose gateway is updated asks to sign in again. Devices and entities are kept, and entries of earlier gateways are not changed (no migration).
  - A gateway that stays unreachable for three health probes in a row is asked, at most every ten minutes and without credentials or radio requests, whether a WebUI 2026 gateway answers at its address. A gateway that only restarts is left alone.
  - Credentials are never sent in clear text to a TLS service on another port.
- **Read settings** button (diagnostic, enabled by default) for every device with settings: reads the stored configuration once over the radio and shows what the device reports. It is never repeated and never pressed by the integration itself, a press while the device is read is ignored, and an answer that overlaps a write is dropped because the write is newer.
- **Stored settings as read-only sensors** (diagnostic, enabled by default, named *… (stored)*) for every setting of blinds, D2-01 dimmers and switches. The values are visible on the device page without enabling the number and select entities that change them.
- **Presence detectors (A5-07-03):** alive cycle, PIR off time and ReCom readiness cycle as read-only diagnostic sensors, taken from the gateway's cached device list and the *Read settings* button.
- **ViNET Router (off / on)** and **Repeater mode (off / level 1 / level 2)** as a stored sensor and a select each (select disabled by default) for D2-01-01 and D2-01-11 switches, the repeater mode also for D2-01-03 dimmers, where the mode was only read so far and writing it is untested. Writes are read back and never repeated. The repeater mode is read from the device list too, so it is shown after a restart without a radio request. The modes are labelled *Off (default)*, *Level 1 (single hop / 1-hop)* and *Level 2 (double hop / 2-hop)*.
- **Signal quality** diagnostic sensor for every device, rated from the signal strength: *Good* above -75 dBm, *Sufficient* from -75 to -85 dBm, *Poor* below -85 dBm (a repeater, usually level 1 halfway, is then recommended). The icon follows the rating.
- **Device information:** product ID, software version and hardware version from the gateway's device list, and a diagnostic **Device ID** sensor. The device ID is shown as the serial number of a device.
- **Gateway information:** the gateway device shows its own serial number (`/system/info`, not the EURID). **The serial number of the gateway device changes** from the EURID to `systemInfo.serialNumber`, and is empty if the gateway reports none. New diagnostic sensors show firmware version, app version and API version; EURID, hostname, IPv4 address and IPv6 address exist too but are disabled by default. Versions are read at startup and every 30 minutes together with the device list refresh (a plain gateway read without radio traffic), and written to the log at startup and when one changes. Host name and addresses are resolved at the same times and stay *unknown* where they do not resolve.
- **Radio journal** in the diagnostics download (block `radio_journal` of every device): the last 300 radio requests (state commands, status queries, configuration writes and read-backs, parameter and link table reads) with kind, priority, keys and values sent, outcome, HTTP and gateway status, shortened gateway text, duration and queue wait, and a `feedback` entry when a device reports what a command was waiting for. It is kept in memory only. With debug logging every request also writes one line. Nothing is repeated, and no second state store is added.
- **Diagnostics download:** a `versions` block of the gateway (firmware, app and API version, receive counter, mean signal strength, read time) and a `connection` block (resolved host name and IP). The certificate fingerprint of a WebUI 2026 gateway is redacted, also in free text.

### Changed
- **A confirmed setting is no longer held for 26 hours.** A device list that arrives after a confirmed write is ignored only where it repeats the value from before the write. The written value or any other value is applied and ends the protection at once, so a change made elsewhere shows up. The protection also ends after 26 hours, when the device is removed, and when *Read settings* reports the setting. This replaces the rule of v1.1.0 (#100).
- **A rejected write and a rejected read-back are told apart.** A rejected write says the device probably did not get the value; a rejected read-back says the gateway accepted the value and it may be stored. In both cases Home Assistant keeps the previous value and nothing is repeated. A write that times out or meets an unreachable gateway keeps its own message; a read-back that fails in any other way is reported as not confirmed.
- **Stored switch values** (timer durations, switch-off warning, illumination threshold) are shown without decimals, the switch-off warning signal with one decimal. The unit stays seconds; minutes can be chosen in the entity settings.
- **The fixed Home Assistant reference** for CI moves from 2026.10.3 to 2026.10.4. Between the two only the header size limit of the shared client session changed; the integration is unchanged.
- **Documentation:** the troubleshooting page explains the two write errors; the radio journal, WebUI 2026 and the new entities are documented.

### Known limitations
- A gateway with WebUI 2026 has not been tested: the login endpoint (`POST /auth/login`) and the bearer token header follow the usual pattern and are collected in one place in `api.py`.
- The *Read settings* button of a battery detector is answered only while the detector is ready (the readiness cycle); outside it the gateway reports *no response from device* after up to 45 seconds. Press it once and wait.
- Routing was only seen as *off*. Writing *on* sends the smallest routing time (0.3 s), which is unverified.

### Unchanged
- Existing entities, entity IDs and unique IDs. Signal strength and Last seen stay disabled by default.

## [1.1.1] - 2026-10-30

Bug fixes from @fubu2k, based on the v1.1.0 beta reports.

### Fixed
- **Rocker buttons on other channels:** a button that the gateway reports on a channel other than 0 now fires its event. Such events carry an additional `channel` attribute; events of channel 0 are unchanged. Before, these buttons were dropped (#114).
- **Host name kept:** a host name you entered stays in the entry when the gateway announces itself at the address that name already leads to. Before, it was replaced by the IP address. A name that leads elsewhere or nowhere is still replaced after the EURID check (#113).
- **Blinds running into an end stop:** the estimated opening/closing state now waits 7 s instead of 5 s after the travel time, because a run to fully open or closed is reported about 1.4 s later than a partial run (#116).

### Changed
- **Docs:** *Last run time* is described as the time from the command to the blind's end report. The blind reports by its own timer, so the sensor follows the stored vertical movement time and cannot show whether that time is right (#117). Measured by @haxx-voyager on 24 blinds.

### Unchanged
- Entities, entity IDs and settings. No radio traffic is added.


## [1.1.0] - 2026-10-29

Stable release of v1.1.0b0: **device settings as entities**, read-only wall switch links and a few new diagnostics, built on @fubu2k's work (#89). No code changes since the beta.

### Requirement
- **Home Assistant 2026.10.1 or newer**, as before.

### Before you update
- **This is the first version that writes settings to your devices.** All settings entities are disabled by default. Enable only the ones you need, and **try one device first**.
- **Tested on real hardware during the beta:**
  - **Blinds:** the vertical movement time, written through Home Assistant on all 24 blinds of one installation (black gateway, firmware 1.21.30) and on single blinds in two more, plus the maximum holding time.
  - **Switches:** the LED colour, and the timer durations, the switch-off warning and its signal, the illumination threshold and the operation mode. These were written directly against the gateway's REST API, not through Home Assistant.
  - **Dimmer settings** were not tested on hardware.
- **Local addresses only.** The gateway speaks unencrypted HTTP, so setup, re-authentication and reconfiguration only accept addresses in the local network: private, link-local, VPN ranges, or the networks Home Assistant itself is attached to. An existing entry with a public address keeps working and shows a notice in *Repairs*.

### Added
- **Device settings** as configuration entities, all disabled by default:
  - **Blinds (D2-05-02):** vertical movement time, rotation time, maximum holding time.
  - **Dimmers (D2-01-03):** ramping time, short-press time, long-press time.
  - **Switches (D2-01-01, D2-01-11):** timer durations, switch-off warning and its signal, illumination threshold, plus the selects operation mode, LED colour, LED channel (2-channel) and external input function.

  How a write works:
  - The value is rounded to the setting's step, written once and read back from the device. Nothing is repeated automatically.
  - If the device stored a different value, Home Assistant shows what the device holds and tells you.
  - A confirmed value is kept when a later device list still shows the gateway's older cached copy, until the gateway catches up (at most 26 hours).
  - A setting stays unavailable until the device has reported it.
- **Wall switch links (read only):** the `opus_greennet.get_device_link_tables` action (for administrators), a *Wall switch links* sensor (disabled by default) and the link tables in the diagnostics download. Link tables are never written.
- **Last seen** sensor for every device (disabled by default) and **Uptime** on the gateway device (the time the gateway started).
- **Event stream** details in the diagnostics download.
- **Examples and use cases** in the documentation.

### Changed
- **Leaner event stream:** the gateway is asked for one document per line. If it answers that request with an error, the integration retries once in the previous format and keeps using it. A stream that ends in the middle of a document now reconnects.
- **Gateway actions** are pinned to fixed versions in CI; Home Assistant 2026.10.3 is the test reference.

### Fixed
- The `set_device_configuration` action and the settings entities reject values that are not valid JSON (for example `NaN`) before anything is sent.

### Known limitations
- Writing the value a setting already has can show "state change could not be verified". The device keeps its value.
- *Last run time* of a blind is the time from the command to the blind's end report, not the physical travel. It is about 1.4 s longer than the stored travel time on partial runs, and a few seconds longer at the end stops, where the motor keeps running.

### Unchanged
- Existing entities, entity IDs and unique IDs. Signal strength stays disabled by default.

Thanks @fubu2k for #89, and @haxx-voyager and @drldm for testing the beta on their installations (#93).

## [1.1.0b0] - 2026-10-25

First beta of 1.1: **device settings as entities**, read-only wall switch links and a few new diagnostics, built on @fubu2k's work (#89). v1.0.1 stays the stable release.

### Requirement
- **Home Assistant 2026.10.1 or newer**, as before.

### Before you update
- **This is the first version that writes settings to your devices.** All settings entities are disabled by default. Enable only the ones you need, and **try one device first**: only the blind vertical movement time, the maximum holding time and the switch LED colour were written on real hardware so far.
- **Local addresses only.** The gateway speaks unencrypted HTTP, so setup, re-authentication and reconfiguration only accept addresses in the local network: private, link-local, VPN ranges, or the networks Home Assistant itself is attached to. An existing entry with a public address keeps working and shows a notice in *Repairs*.

### Added
- **Device settings** as configuration entities, all disabled by default:
  - **Blinds (D2-05-02):** vertical movement time, rotation time, maximum holding time.
  - **Dimmers (D2-01-03):** ramping time, short-press time, long-press time.
  - **Switches (D2-01-01, D2-01-11):** timer durations, switch-off warning and its signal, illumination threshold, plus the selects operation mode, LED colour, LED channel (2-channel) and external input function.

  How a write works:
  - The value is rounded to the setting's step, written once and read back from the device. Nothing is repeated automatically.
  - If the device stored a different value, Home Assistant shows what the device holds and tells you.
  - A setting stays unavailable until the device has reported it.
- **Wall switch links (read only):** the `opus_greennet.get_device_link_tables` action (for administrators), a *Wall switch links* sensor (disabled by default) and the link tables in the diagnostics download. Link tables are never written.
- **Last seen** sensor for every device (disabled by default) and **Uptime** on the gateway device (the time the gateway started).
- **Event stream** details in the diagnostics download.
- **Examples and use cases** in the documentation.

### Changed
- **Leaner event stream:** the gateway is asked for one document per line. If it answers that request with an error, the integration retries once in the previous format and keeps using it. A stream that ends in the middle of a document now reconnects.
- **Gateway actions** are pinned to fixed versions in CI; Home Assistant 2026.10.3 is the test reference.

### Fixed
- The `set_device_configuration` action and the settings entities reject values that are not valid JSON (for example `NaN`) before anything is sent.

### Unchanged
- Existing entities, entity IDs and unique IDs. Signal strength stays disabled by default.

### Beta testing
- **Please test:**
  - change one setting per device type (blind, dimmer, switch) and check that the device really changed;
  - after a gateway restart, the event stream keeps working, especially on the black gateway (firmware 1.21.31/1.21.34);
  - setting up by host name (for example `opusshgw.local`).
- Report in the beta issue.
- Thanks @fubu2k for #89!

## [1.0.1] - 2026-10-25

Maintenance release with no functional changes.

### Changed
- CI also validates the repository with the HACS action, as preparation for the HACS default store.

### Unchanged
- Integration code, entities, radio traffic and intervals are the same as in 1.0.0.

## [1.0.0] - 2026-10-24

**This is a big one.** 1.0 is the largest change since the integration started, and the step from an experimental bridge setup to a stable integration you can rely on every day.

**1.0 talks to the OPUS gateway directly over its EnOcean over IP REST API.** The MQTT broker and the Mosquitto bridge are no longer needed. State changes reach Home Assistant in about 5–20 ms instead of seconds, also while Apple Home or the OPUS app use the gateway. Devices, entities, entity IDs, names, areas, dashboards and automations are kept.

v0.8.0 stays available as the last MQTT release; you can go back to it at any time.

### Before you update
- **Home Assistant 2026.10.1 or newer**, also for manual installs. HACS checks this; a manual copy does not.
- **Home Assistant must reach the gateway on port 8080** in the local network, the gateway's REST API. It uses the vendor login: user `admin`, password = the gateway's EURID, unless you changed it.
- **D2-01 actuators other than D2-01-00 to -03 and -11:** switch or dimmer and the number of channels now come from the gateway's device profile. Earlier versions treated many of these as multi-channel devices. A device whose type changes gets new entities. Update the automations and dashboards that used the old ones. D2-01-00 to -03 and -11 keep their entities.
- **Check automations that rely on:**
  - **Dimmer fades.** Dimmers now switch to the new brightness at once, like Apple Home. Use `transition` for a fade.
  - **Rocker and button events.** New event entities are hidden by default. Existing ones and the automations using them are unchanged.

### Updating from an MQTT version (0.x)
1. Update in HACS and restart Home Assistant.
2. The entry finds its gateway in the local network by itself (mDNS, a few seconds), checks that it is the same gateway (EURID) and loads with all entities. There is nothing to enter.
3. Only if the gateway isn't found (for example Docker without host networking) or you changed its password, the entry asks for the address or the password. Port, user and password are prefilled.
4. Once everything works, you can remove the OPUS block from your Mosquitto configuration (for example `/share/mosquitto/opus_bridge.conf`).

**Going back:** install v0.8.0 in HACS and restart. This needs the Mosquitto bridge to still be configured. Entities keep their IDs in both directions.

Details: [upgrading guide](https://github.com/kegelmeier/ha-opus-greennet/blob/v1.0.0/docs/upgrading.md).

### What's new
- **Fast and reliable updates:** one persistent event stream from the gateway instead of MQTT messages.
  - Every reconnect brings all devices up to date.
  - A gateway restart is detected and recovered automatically, including nightly gateway reboots.
  - Reconnects wait longer after each failed attempt (1 s up to 60 s).
- **Commands are confirmed by the gateway directly.** A command for a sleeping battery device is shown only once the device reports it.
- **Gateway discovery:** a new gateway appears under *Settings → Devices & services → Discovered*. Address changes are picked up automatically, so a fixed IP address is no longer required.
- **Reconfigure** the address or login from the entry's menu. A different gateway at the address is rejected.
- **HOPPE SecureConnect AutoLock (D2-06-40)** can be locked and unlocked, with a new *Lock bolt* sensor.
- **Blinds:**
  - *Last run time* sensor, which keeps its value after a restart;
  - *Reference run* buttons;
  - *Blockage* switch (disabled by default);
  - action `opus_greennet.set_cover_position` with an optional reference run.
- **More entities:**
  - *Buttons* events for push buttons built into actuators;
  - F6-03 rockers C and D;
  - *Local control* for D2-01 actuators (disabled by default);
  - *Sensor error* for presence detectors;
  - battery level for HOPPE handles.

### Fixed
- Dimmers no longer use the gateway's slow default fade.
- Commands from Apple Home or other controllers no longer reset a device's signal strength to 0 dBm.
- Commands queued during a gateway outage aren't sent after it recovers.
- F6-10-00 handles show *tilted* instead of *unknown*.
- A5-07-01 presence detectors no longer get an illuminance entity that could never report.
- Blind movement times set by another controller are no longer read as seconds instead of milliseconds.
- The address field rejects URLs (`http://…`, ports, paths) with a clear message.

### Security
- The gateway login is only ever sent to the configured gateway: redirects are not followed.
- Every answer from the gateway is size-limited, and malformed data can't crash the integration.
- Diagnostics redact addresses, credentials, identifiers and the HomeKit code.

### Tested
Beta-tested on six installations with up to 65 devices (OPUS-GW firmware 1.21.30 to 1.21.34), over several days and nightly gateway restarts, including the automatic upgrade from v0.8.0 without entering anything:
- switches and two-channel actuators;
- dimmers;
- blinds;
- leak sensors;
- window contacts (D5-00-01);
- temperature and humidity sensors (A5-04);
- RWM smoke detectors;
- SMS presence detectors;
- HOPPE handles;
- OPUS motion detectors;
- stand-alone rocker switches;
- commands from Apple Home.

**Not yet confirmed on hardware:**
- HeatArea climate;
- D2-01 types other than -01, -03 and -11;
- AutoLock control;
- blind tilt.

Please report in a new issue if something doesn't work.

### Thanks
- @fubu2k for the hardening, the D2-01 profile alignment, AutoLock control and the MQTT quality series.
- @drldm, @nlsrchtr, @haxx-voyager and @florian-hartung for testing the betas (#79).

## [1.0.0b5] - 2026-10-23

Sixth beta of 1.0 (REST): fixes from the beta reports and @fubu2k's hardening of the gateway client (#86, #87). v0.8.0 stays the stable release and the MQTT fallback.

### Requirement
- **Home Assistant 2026.10.1 or newer**, also for manual installs. HACS checks this; a manual copy does not.

### Fixed
- **Last run time of blinds survives a restart:** the sensor keeps its last value and attributes after a Home Assistant restart instead of showing `unknown` until the blind moves again (reported in #79). Live readings such as temperature or signal strength are still not restored, so a stale value is never shown as current.
- **Clear error for a URL as the gateway address:** the setup, re-authentication and reconfiguration forms accept only an IP address or host name. `http://…`, a port, a path or credentials in the address field now show "Enter only the gateway's IP address or host name" instead of "Failed to connect".

### Security
- The gateway client no longer follows redirects, so the gateway login can't be sent to another host.
- Gateway answers are limited to 8 MB, like stream documents already were.
- JSON nested too deeply is treated as invalid JSON (the event stream reconnects) instead of causing an unhandled error.

### Unchanged
- Entities, unique IDs, radio traffic and intervals are the same as in 1.0.0b4. Discovery and the automatic move from MQTT work as in 1.0.0b4.
- **Coming from an older version than 1.0.0b2:** the "Check before updating" note of 1.0.0b2 still applies (D2-01 actuator types).

### Beta testing
- **Please test:** restart Home Assistant and check that the *Last run time* sensors of your blinds keep their value. The upgrade from v0.8.0 (or v0.7.x) without re-authentication is still open for testing (see #79).

## [1.0.0b4] - 2026-10-22

Fifth beta of 1.0 (REST): the gateway is discovered in the local network, so the move from MQTT needs no input in most installations. v0.8.0 stays the stable release and the MQTT fallback.

### Requirement
- **Home Assistant 2026.10.1 or newer**, also for manual installs. HACS checks this; a manual copy does not.

### Added
- **Upgrade from MQTT without entering anything:** at the first start an entry of an MQTT version (0.x) searches the local network for its gateway (mDNS, a few seconds), tests each OPUS gateway it finds with the default credentials (user `admin`, password = the EURID the entry already knows) and takes the one that reports the same EURID. All entities are kept. It asks only when no gateway is found (then for the address) or the password was changed (then only for the password).
- **Gateway discovery:** a new gateway appears under *Settings → Devices & services → Discovered* with its address filled in; only the password has to be entered.
- **Address changes are picked up automatically:** when a configured gateway is announced at another IP address, the entry is updated and reloaded after the gateway confirms its EURID. A fixed IP address is no longer required.

### Unchanged
- Discovery only reads `/system/info`; no radio telegrams, intervals or entity changes. Installations without mDNS between Home Assistant and the gateway (for example Docker without host networking) enter the address as before.
- **Coming from an older version than 1.0.0b2:** the "Check before updating" note of 1.0.0b2 still applies (D2-01 actuator types).

### Beta testing
- **Please test:** the upgrade from v0.8.0 (or v0.7.x) to this beta. Does the entry load on its own, without a re-authentication? Is the gateway listed under *Discovered* on a fresh installation?

## [1.0.0b3] - 2026-10-22

Documentation beta of 1.0 (REST). No change to the integration's behaviour compared with 1.0.0b2.

### Requirement
- **Home Assistant 2026.10.1 or newer**, also for manual installs. HACS checks this; a manual copy does not.

### Changed
- **Shorter README** with features, supported devices, compatibility, installation, configuration, troubleshooting, known limitations and a new roadmap. Details moved to `docs/`: [devices and actions](docs/devices.md), [how it works](docs/how-it-works.md), [troubleshooting](docs/troubleshooting.md), [upgrading](docs/upgrading.md) and [development](docs/development.md).

### Added
- **Roadmap** in the README: device settings of switches, dimmers, blinds and the gateway as Home Assistant entities, and adding and removing devices from Home Assistant (under investigation).

### Unchanged
- Code, entities, unique IDs, radio traffic and intervals are the same as in 1.0.0b2. Updating from 1.0.0b2 is optional; the "Check before updating" note of 1.0.0b2 still applies when you come from an older version.

## [1.0.0b2] - 2026-10-21

Third beta of 1.0 (REST) with @fubu2k's hardening, the D2-01 profile alignment and HOPPE AutoLock control (#83, #85). v0.8.0 stays the stable release and the MQTT fallback.

### Requirement
- **Home Assistant 2026.10.1 or newer**, also for manual installs. HACS checks this; a manual copy does not.

### Check before updating
- **D2-01 actuators other than D2-01-00 to -03 and -11:** switch or dimmer and the number of channels now come from the gateway's device profile; without a profile, a corrected table following the EEP specification applies (D2-01-04, -05 and -09 are single-channel dimmers; -06 to -0F single-channel switches; -10 to -12 two-channel switches). Previous versions treated many of these as multi-channel devices. **A device whose type changes gets new entities; update automations and dashboards that used the old ones.** Entities of channels or platforms a device does not have are removed only when the gateway profile confirmed it. D2-01-00 to -03 and -11 keep their entities.

### Changed
- **More robust reconnects:** the pause after the event stream ends starts at 1 second and doubles up to 60 seconds, plus up to 25 % at random. A stream that closes within 10 seconds counts as a failed attempt, so a gateway that drops every connection cannot cause a reconnect loop. Rejected credentials are never retried.
- **Start-up** takes the device states from the stream's first device list (retried later if it does not arrive within 30 seconds) and reads the blind movement times once more right after it. The first telegram after start-up no longer rewrites every entity.
- Device lists update the known devices in place, so a command error can no longer get lost when a list arrives during the command. The 30-minute list keeps a newer signal strength and last-seen time from a telegram.
- Rocker and button event entities are enabled but hidden by default (new entities only; automations work as before).
- HOPPE handle Open, Tilted and Closed binary sensors are diagnostic entities; presence detector illuminance is shown in whole lux; the presence detector *Sensor error* and the F6-05-01 water sensor show *OK* / *dry* until something is reported.

### Added
- **HOPPE SecureConnect AutoLock (D2-06-40) can be locked and unlocked.** The lock entity sets the answer the gateway stores for the handle's next unlock request and shows the stored answer; the gateway's status 202 is accepted. Quick changes settle for 10 seconds and only the last one is sent. New diagnostic *Lock bolt* binary sensor.
- **Blinds (D2-05-00/02):** diagnostic *Blockage* switch (disabled by default, because it stops the blind), diagnostic buttons *Reference run via top/bottom* and a diagnostic *Last run time* sensor. New action `opus_greennet.set_cover_position` with an optional reference run.
- *Buttons* event entity for push buttons built into actuators; F6-03 rockers also report rockers C and D.
- Diagnostic *Local control* binary sensor (disabled by default) for D2-01 actuators, *Sensor error* for A5-07 presence detectors, battery level for HOPPE handles; presence illuminance above the sensor range is shown as 1000 lx.
- Diagnostics: time to the first device list, and per blind the last run duration and where the shown position came from. The HomeKit QR setup payload is redacted.

### Fixed
- HTTP errors and gateway errors are told apart, every streamed document is limited to 8 MB, and errors never contain credentials.
- F6-10-00 handles show a tilted handle as *tilted* instead of *unknown*.
- A5-07-01 presence detectors no longer get an illuminance entity that could never report.
- Movement and rotation times from another controller's D2-05-00 "Set parameters" command (milliseconds) are no longer taken over as seconds.

### Unchanged
- No additional radio traffic, intervals or command repetitions. Blinds keep their last known position while they report `unknown` during a run. Entity IDs, unique IDs and settings of existing entities are unchanged, except for the D2-01 type corrections above.

### Beta testing
- Thanks @fubu2k for #83 and #85!
- **Please test:** HOPPE AutoLock (D2-06-40), D2-01 actuators other than -00 to -03 and -11, blind reference runs and the blockage switch, actuator push buttons.
- **Not yet tested on hardware:** HeatArea climate, RWM smoke detectors, SMS presence detectors, contacts, A5-04 temperature sensors, blind tilt and commands from Apple Home or the OPUS app.
- Please report in the pinned beta issue (#79).

## [1.0.0b1] - 2026-10-20

Second beta of 1.0 (REST). It is based on the final MQTT release v0.8.0, so v0.8.0 is now the stable release and the MQTT fallback, and an update from v0.8.0 works like one from v0.7.0 (enter the gateway address once).

### Requirement
- **Home Assistant 2026.10.1 or newer**, also for manual installs. HACS checks this; a manual copy does not, and on older versions setup fails with `ImportError: cannot import name 'ATTR_BRIGHTNESS'`.

### Fixed
- A command the gateway queues for a sleeping battery device (status 201, SoftSmartAck) no longer shows the commanded value in advance and starts no cover travel estimate; the entity keeps the reported state until the device reports (approach from @fubu2k's v0.8.0 work).

### Changed
- Rocker event entities use the `button` device class, so the frontend shows a button icon by default. Entity IDs, event types and automations are unchanged (from @fubu2k's v0.8.0 work).

### Beta testing
- Confirmed by beta testers on v1.0.0b0: migration with unchanged entity IDs, blinds (D2-05-02), switches and two-channel actuators, OPUS motion detectors, stand-alone rocker switches and recovery after a gateway restart. Thanks @drldm and @nlsrchtr!
- **Not yet tested on hardware:** HeatArea climate, RWM smoke detectors, SMS presence detectors, HOPPE handles, contacts, A5-04 temperature sensors, blind tilt and commands from Apple Home or the OPUS app.
- Please report in the pinned beta issue (#79).

## [1.0.0b0] - 2026-10-19

First beta of 1.0. The integration now talks to the OPUS gateway **directly over its EnOcean over IP REST API**. The MQTT broker bridge is no longer needed. v0.7.0 remains the stable release and the MQTT fallback.

### Breaking change
- **The gateway's address is needed once.** After updating, the existing entry reports *Re-authentication required*: enter the gateway's IP address. Port (`8080`), user (`admin`) and password (the gateway's EURID in capital letters) are prefilled with the vendor defaults. All devices, entities, entity IDs, names, areas, dashboards and automations are kept.
- The Mosquitto bridge configuration for OPUS (e.g. `/share/mosquitto/opus_bridge.conf`) is no longer used and can be removed once everything works.

### Changed
- One persistent event stream delivers every telegram as the gateway handles it (median ~20 ms from gateway to Home Assistant on a 65-device installation, previously seconds). Every reconnect resynchronises all devices; a health check runs every minute and also detects gateway restarts; battery level, signal strength and cover movement times refresh every 30 minutes.
- Commands are acknowledged directly by the gateway's HTTP answer.
- New **Reconfigure** option to change address or credentials; rejected credentials and a different gateway at the configured address start a re-authentication.

### Added
- Icons for HOPPE handle and lock entities and for the service actions (thanks @fubu2k, #74).
- A repair notice when the unique-ID migration had to remove a duplicate beta entity (thanks @fubu2k, #74).

### Fixed
- **Dimmers switch to the new brightness at once** (like Apple Home); a `transition` uses the dimmer's own configured fade. Previously the gateway applied its slow default fade.
- Commands from HomeKit or other controllers no longer reset a device's signal strength to 0 dBm.
- Commands queued before a gateway outage are not sent after it recovers.

### Changed entity defaults (new entities only)
- HOPPE unlock request and mechanics fault are diagnostic entities and disabled by default; SMS supply voltage is a diagnostic entity; battery levels are shown in whole percent (thanks @fubu2k, #74). Existing entities keep their settings.

### Beta testing
- Enable pre-releases for this repository in HACS, install v1.0.0b0, restart Home Assistant and enter the gateway address when asked.
- Tested on hardware: switches, dimmers, two-channel actuators, blinds and leak sensors (OPUS-GW v1.21.31). **Not yet tested on hardware:** HeatArea climate, RWM smoke detectors, SMS presence detectors, HOPPE handles, contacts, A5-04 temperature sensors, motion detectors and stand-alone rocker switches.
- Please report in the pinned beta issue with your gateway firmware, the device EEPs and redacted diagnostics.

## [0.8.0] - 2026-10-20

Final MQTT release: @fubu2k's quality series on top of v0.7.0. It is the stable fallback for anyone who keeps the Mosquitto bridge; new features go into 1.0, which talks to the gateway directly over its REST API.

### Requirement
- **Home Assistant 2026.10.1 or newer**, also for manual installs (unchanged from v0.7.0).

### Added
- Automatic discovery: Home Assistant offers the gateway as soon as its `friendlyId` topic arrives through the MQTT bridge. Manual setup stays available.
- Icons for HOPPE handle and lock entities and for the service actions.
- A repair notice when the unique-ID migration had to remove a duplicate beta entity, naming the removed and the kept entity ID.
- Diagnostics show commands the gateway queued for later delivery (`deferred_commands`) and requests without an answer or with a possibly late answer (`late_answers`).

### Fixed
- A command the gateway only queued (status 201, SoftSmartAck) no longer shows the commanded state before the device received it; covers start no movement estimate for it.
- An older retained device value no longer overwrites a value that was already updated live during the same connection.
- Numeric device names are kept as text.

### Changed entity defaults
- Rocker event entities use the `button` device class.
- HOPPE unlock request and mechanics fault are diagnostic entities, disabled by default for new entities; SMS supply voltage is a diagnostic entity; battery levels are shown in whole percent. Existing entities keep their settings, IDs and names.

### Validation
- 1,568 tests on Home Assistant 2026.10.1, Ruff, strict mypy, coverage gates and the MQTT traffic baseline (no extra device requests at setup, health check or reconnect).
- Thanks to @fubu2k for the quality series.

## [0.7.0] - 2026-10-19

Stable release of the v0.7.0b1 beta: the motion-detector support from #72 and the quality phases 1–5 from #74/#75. This is the last release that connects through MQTT; the upcoming 1.0 talks to the gateway directly over its REST API.

### Breaking requirement
- **Home Assistant 2026.10.1 or newer is required**, including for manual installation. Stay on v0.6.0 or upgrade Home Assistant first.

### Added
- OPUS motion detector (F6-02-01 + A5-04-01): `buttonAI` pressed means motion, `buttonA0` pressed means no motion; temperature, humidity and battery entities. Thanks to @florian-hartung and @nlsrchtr (#72).
- A5-04-01 and A5-04-02 temperature/humidity profiles with profile-specific temperature limits.

### Fixed
- Rocker presses are no longer replayed by later battery, signal-strength or other device updates; repeated real presses still produce separate events.
- Contact readings and profile-specific temperature ranges are preserved by the refactored device model.

### Changed
- Superseded queued background status checks are dropped, idle request locks are released and redundant entity state writes are avoided.
- Optimistic light/switch/cover state after accepted commands is handled in the device model; MQTT payloads are validated per field.
- Strict typing, quality evidence, per-module coverage gates and repeated lifecycle/race checks in CI. No official Home Assistant quality scale rating is claimed. Thanks to @fubu2k (#74, #75).

### Validation
- 1,315 tests on Home Assistant 2026.10.1 and the latest stable release, with Ruff, strict mypy and dependency checks.
- Ran as v0.7.0b1 on an installation with 65 OPUS devices (switches, dimmers, two-channel actuators, blinds, leak sensors).

## [0.7.0b1] - 2026-10-18

Quality and reliability beta incorporating #74 and its included #75 quality evidence, while retaining the motion-detector features from v0.7.0b0 and all v0.6.0 sensor support. v0.6.0 remains stable.

### Breaking requirement
- **Home Assistant 2026.10.1 or newer is required**, including for manual installation. HACS metadata and test dependencies now enforce this minimum. Older installations should stay on stable v0.6.0 or upgrade HA first.
- The higher minimum is deliberate: this release uses probatio and Home Assistant APIs unavailable in HA 2026.9.4.

### Fixed
- Stop replaying a cached rocker press when later battery, signal-strength or other device updates arrive; repeated real presses still produce separate events.
- Preserve contact readings and profile-specific temperature ranges when integrating the refactored device model.

### Changed
- Drop superseded queued background status checks, release idle request locks and avoid redundant entity state writes.
- Centralize optimistic light/switch/cover state updates in the device model and tighten MQTT payload validation.
- Add strict typing, quality evidence, coverage gates and repeated lifecycle/race checks. No official Home Assistant Quality Scale rating is claimed.

### Beta validation
- Combined suite: 1,315 tests on HA 2026.10.1, plus lint, formatting, strict typing and dependency checks.
- No physical-device validation has been performed for this combined quality beta. Please check repeated rocker presses, motion on/off, contacts, leak sensors, HeatArea readings, cover/light/switch commands, restart/reload and MQTT reconnect. Confirm entity IDs and automations remain intact.
- Motion remains unknown after restart until a valid motion telegram arrives.
- Enable prereleases in HACS and install v0.7.0b1. Report quality/regression feedback on #74 and motion-detector feedback on #72, with HA/gateway versions and redacted logs.

## [0.7.0b0] - 2026-10-18

Beta based on stable v0.6.0, adding the motion-detector support proposed in #72. v0.6.0 remains the stable release. The broader quality/refactoring changes in #74/#75 are not included.

### Added
- Motion detection for OPUS devices announcing F6-02-01 plus A5-04-01: buttonAI pressed means motion; buttonA0 pressed means no motion. Releases leave the motion state unchanged.
- Temperature, humidity and battery entities for the motion detector, preserving existing rocker events and entity identities.
- A5-04-01 and A5-04-02 temperature/humidity profile support with profile-specific temperature limits; A5-04-03 and HeatArea limits are preserved.

### Fixed
- Ignore malformed secondary EEP values when identifying motion and temperature/humidity profiles.

### Validation and beta testing
- 942 tests pass on HA 2026.9.4, including 16 contributed motion-detector cases and four additional malformed-profile regressions. Ruff lint and formatting pass.
- The contributor reports two physical motion detectors on OPUS-GW v1.21.34, with motion transitions matching HomeKit and climate/battery readings matching the gateway. This combined beta has not yet been tested on hardware.
- Motion remains unknown after restart until a valid motion telegram arrives; the gateway snapshot does not provide this state.
- Enable prereleases in HACS, install v0.7.0b0, and restart HA. Verify motion on/off, release telegrams, temperature/humidity/battery readings, existing rocker automations, restart/reload and unchanged entity IDs. Also retest contacts, leak sensors and HeatArea devices.
- Please report results on #72 with HA and gateway versions and redacted logs where needed.

Thanks to @florian-hartung for the motion-detector implementation and hardware evidence.

## [0.6.0] - 2026-10-18

Stable release of the sensor and contact support tested in v0.6.0b1, with the retained device-answer fix. Integration behavior is unchanged from that beta.

### Added
- Temperature, humidity and battery sensors for A5-04-03 devices, including Jaeger Direkt sensors (#64, #65). Temperature supports -20 to +60 °C; existing HeatArea limits remain unchanged.
- Contact binary sensors for D5-00-01 window contacts (#70, #71), with English and German entity names. Missing or unrecognized contact values remain unknown.

### Fixed
- Ignore retained device-answer responses that could restore stale sensor readings or overwrite a wet leak alarm (#62, #73). Retained device-stream discovery remains supported. Leak sensors remain unknown until valid telemetry arrives.

### Validation and known limitations
- 922 automated tests pass on Home Assistant 2026.9.4.
- Community validation on October 15 with HA 2026.9.2, OPUS-GW v1.21.31 and about 80 devices confirmed restart and integration reload without OPUS errors, unchanged entity IDs/counts, no duplicates, and restored temperature/humidity and contact readings.
- A physical leak-sensor trigger produced on then off following its telegrams; sensors without gateway state remained unknown after restart and reload.
- That installation still takes about two minutes to restore entity availability after startup/reload. HeatArea compatibility, negative temperatures and explicit broker reconnect remain unverified on hardware.

Thanks to @florian-hartung for the implementation, gateway examples and beta testing.

Hardware feedback: https://github.com/kegelmeier/ha-opus-greennet/issues/62#issuecomment-6201868511

## [0.6.0b1] - 2026-10-13

Second v0.6.0 beta, retaining A5-04-03 temperature/humidity/battery and D5-00-01 contact support from v0.6.0b0.

### Fixed
- Ignore retained MQTT device-answer responses that could restore stale sensor state (#73, follow-up to #62). Regression tests reproduced old dry leak-sensor state overwriting wet state, changing unknown to dry, or surviving a fresh response with no state. Retained device-stream snapshots remain supported.

### Beta feedback and testing
- Community testing on v0.6.0b0 confirmed three A5-04-03 sensors with live readings and stable entity IDs, and ten D5-00-01 contacts with stable IDs and one observed open/close cycle. No startup errors were reported on an installation with about 80 devices.
- Enable pre-releases in HACS, install **v0.6.0b1**, and restart Home Assistant. Check leak-sensor startup state, integration reload, broker reconnect, and continued live updates. Missing readings should remain unknown; a known alarm must not be cleared by an old retained answer.
- Please continue checking contact open/close behavior, temperature/humidity against the gateway, repeated restart/reload, and HeatArea compatibility. Negative temperatures remain unverified on hardware.
- Report results on #62, #64, or #70 with Home Assistant version and gateway firmware. No production broker messages were injected during automated validation.

### Validation
- 922 tests pass on Home Assistant 2026.9.4 for the combined beta. Ruff lint and formatting checks pass.
- The isolated fix passed 865 tests on Home Assistant 2026.9.4 and 2026.10.1, plus GitHub integration metadata checks. All 13 new regression cases fail on the original beta and pass with the fix.

Thanks to @florian-hartung for the hardware feedback and the retained-answer observation.

## [0.6.0b0] - 2026-10-12

Beta for the device-support requests #64 and #70, built on v0.5.0 with its gateway queue and startup fixes. The feature PRs and issues remain open for review and installation feedback.

### Added
- Temperature, humidity and battery-level sensors for A5-04-03 devices (PR #65 / #64), including the reported Jaeger Direkt sensors.
- A5-04-03 temperature readings from -20 to +60 °C, matching the [EnOcean Alliance profile](https://www.enocean-alliance.org/products/eeps/). Existing HeatArea temperature limits remain 0 to 40 °C.
- A Contact binary sensor for D5-00-01 single input contacts (#70), including window contacts. Reported `open` is `on`, reported `closed` is `off`, and missing or unrecognized values are `unknown`. No battery reading is invented when none is supplied.
- English and German names for the new temperature and contact entities. Heartbeat/trigger messages do not create an entity.

### Beta testing
- Enable pre-release versions for this repository in HACS, install **v0.6.0b0**, and restart Home Assistant. Home Assistant 2026.8 or newer is supported.
- For A5-04-03: confirm temperature (°C), humidity (%) and battery (%) entities; compare them with the gateway; check incoming telegrams and battery updates. Negative temperatures have automated coverage but have not been verified on hardware.
- For D5-00-01: open and close each contact and confirm the entity follows the physical state. Check initial state after restart. Missing data must remain unknown, and gateway outages must show unavailable.
- Restart or reload the integration and check for duplicate entities or changed entity IDs. Verify existing HeatArea devices, dashboards and automations still work.
- Post results on [PR #65](https://github.com/kegelmeier/ha-opus-greennet/pull/65), [issue #64](https://github.com/kegelmeier/ha-opus-greennet/issues/64), or [issue #70](https://github.com/kegelmeier/ha-opus-greennet/issues/70). Include Home Assistant version, gateway firmware, device profile and redacted logs if something differs.

### Validation
- 909 tests pass on Home Assistant 2026.8.2 and 2026.10.1. Ruff, dependency compatibility, coverage and integration metadata checks pass.
- The A5-04-03 contributor reports testing three Jaeger Direkt devices on OPUS-GW v1.21.31. The contact implementation uses the gateway snapshots and live values reported in #70. The combined candidate has automated validation; no additional local hardware test was performed.

Thanks to florian-hartung for the A5-04-03 implementation, contact request, gateway examples and device testing.

## [0.5.0] - 2026-10-11

Stable release of the cover movement and discovery improvements from v0.5.0b0, plus the reviewed gateway fixes for larger installations.

### Added
- Estimated opening/closing state for covers with a configured travel time, including partial travel, changed targets, Stop, and movement after restart (#55).
- An integration ZIP attached to GitHub releases for manual installation.

### Fixed
- Bulk cover and switch commands no longer overwhelm the gateway with concurrent awaited requests. Control commands take priority, and each acknowledgement deadline starts after its request is published (#62).
- Large retained MQTT snapshots no longer consume the initial setup probe's deadline. Device discovery loads in the background after the gateway responds (#66).
- Discovery completes when a disconnect interrupts startup, including a quick reconnect inside the snapshot debounce window.
- Repeated binary-sensor discovery avoids duplicate entity submissions while retaining removal and rediscovery support (#57).
- Subscription acknowledgement waits use the subscription's actual QoS. Slow telemetry acknowledgements do not fail the gateway health check.
- Setup logs distinguish request timeouts, MQTT disconnection, rejected requests, and network errors.

### Changed
- Awaited gateway requests are serialized across devices. Background status queries run after queued control commands.
- Telemetry streams subscribe at QoS 0 to reduce retained-replay traffic; answer subscriptions remain at QoS 1. Rapid reconnects avoid repeatedly requesting complete device snapshots.
- Moisture and HeatArea binary sensors use declarative descriptions while retaining their entity identities and state semantics (#58).

### Upgrade notes
- Home Assistant 2026.8 or newer remains supported. Restart Home Assistant after upgrading.
- Large command batches can take longer to finish because requests are sent one at a time. Gateway acknowledgement does not prove that an actuator reached its target; cover movement and fallback positions remain estimates.
- F6-05-01 leak sensors remain unknown until valid liquidDetected telemetry arrives. Missing data is not shown as dry, and invalid updates do not clear a known alarm.
- Gateway outages mark entities unavailable and invalidate integration requests queued before the outage. The reported nightly gateway restart still requires manufacturer investigation.
- AutoLock remains read-only pending manufacturer guidance.

### Validation
- 852 tests pass on Home Assistant 2026.8.2, 2026.9.4, and 2026.10.1, with Ruff and dependency checks.
- Community testing of the contributor's transport implementation covered up to 24 covers in one call and startup with 82 devices and approximately 30,000 retained MQTT messages. The final interrupted-startup correction and preserved unknown leak state have automated regression coverage; no additional local hardware test was performed.

Thanks to fubu2k for the gateway fixes, haxx-voyager and florian-hartung for the large-installation testing, and drldm for the cover observations and validation.

## [0.5.0b0] - 2026-10-03

First beta of v0.5.0, building on the confirmed v0.4.0 stable release.

### Added
- Estimated opening/closing state for covers with a positive configured `verticalMovementTime` (#55). Travel is based on distance, or a full travel after startup when the position is unknown, plus five seconds of grace.
- Cover status checks after estimated travel: with a 25-second full travel, movement lasts up to 30 seconds and status checks run at 40 and 50 seconds. Valid numeric feedback ends the estimate and cancels pending position checks.

### Fixed
- Repeated binary-sensor discovery no longer submits an existing entity ID twice. Newly discovered sensor types remain addable; removal waits for old entities to finish before rediscovery creates replacements (#57).

### Changed
- Moisture and HeatArea binary-sensor definitions share a declarative registry while preserving entity IDs, names, categories and state interpretation (#58).

### Beta testing
- Check full and partial shutter travel, a new target during travel, Stop, and the first movement after restarting Home Assistant. Please include configured travel time and MQTT position reports when reporting timing issues.
- Motion and the final fallback position are estimates, not measurements. A stalled motor or incorrect configured travel time can make them inaccurate. Covers without a configured travel time retain previous behaviour.
- Check existing moisture/HeatArea entities and repeat discovery, reload, and removal/rediscovery. Entity IDs and dashboard assignments should remain unchanged.
- No new HOPPE AutoLock control commands are introduced; status remains read-only.

Thanks to drldm for the shutter observations and proposal, and to fubu2k for the discovery and sensor-definition proposals.

### Validation
- 828 automated tests pass on Home Assistant 2026.9.4. Ruff, dependency compatibility and Hassfest checks pass. Physical shutter timing still needs beta validation.

## [0.4.0] - 2026-10-03

Stable release of the device support and fixes validated in the 0.4.0 beta series. Community feedback confirms smoke-detector states, SMS readings and existing dashboards, HOPPE handle/AutoLock status, and the telemetry fast path. Device removal was also confirmed working.

### Added
- OPUS RWM F6-05-02 smoke-alarm and low-battery sensors. Numeric battery values are not invented when the gateway does not publish them (#42).
- OPUS SMS A5-07-01/A5-07-03 motion, illuminance, supply-voltage, and battery readings (#43).
- HOPPE D2-06-40, F6-10-00, and D2-03-10 handle-position sensors. AutoLock models also expose reported lock status, unlock requests, and mechanics faults (#44).
- Manual removal of stale child devices, with gateway protection and rediscovery support (#46).
- German translations and migration of community-fork illuminance/AutoLock entries while retaining existing Home Assistant entity IDs and settings.

### Fixed
- Accept the gateway payload formats reported by testers alongside the original beta formats, including percentage battery strings and RWM transmit-mode updates.
- Apply battery and signal-strength telemetry immediately, retaining validation and protection against stale buffered values (#45).
- Handle named state updates following indexed snapshots without leaving stale readings.

### Upgrade notes
- AutoLock MQTT control remains disabled pending manufacturer guidance. Handle position is independent of lock status.
- If both fork and beta illuminance/AutoLock entries exist for the same device and gateway, the fork entry is preserved and the duplicate beta entry removed. Update references to the removed beta ID; the integration logs both IDs.
- Unlock-request states are `requested` / `not_requested`; update automations that previously compared boolean text.
- Removal does not unpair a device from the gateway. Devices still reported by the gateway can reappear.

### Validation
- 735 automated tests pass on Home Assistant 2026.9.4, with Ruff, dependency validation, and Hassfest checks. Earlier beta validation also covered 2026.8.2.
- CI now tracks the latest stable Home Assistant release automatically, with weekly and manual runs.

Thanks to fubu2k for the patches and hardware validation. The new proposals in #55, #57, and #58 are reserved for the next beta.

## [0.4.0b1] - 2026-10-01

Beta follow-up incorporating fubu2k's reports and proposed corrections for #42–#46. Enable pre-release versions in HACS, select **v0.4.0b1**, and restart Home Assistant.

### Fixed

- **RWM smoke detectors (#42):** Accept `alarm=on/off` and boolean low-battery feedback alongside the existing beta formats. Preserve explicit transmit-mode keys and fill only missing or empty fallback keys. Invalid readings remain unknown.
- **SMS presence detectors (#43):** Accept `motionDetected` and `illumination` for both A5-07-01 and A5-07-03, including snapshots and live updates. Accept battery percentages such as `85%` while retaining finite 0–100 validation.
- **HOPPE handles (#44):** Accept `handle`, `unlock`, and `mechanics` feedback; show translated unlock-request states. Handle position, lock state, and diagnostics remain independent. AutoLock remains read-only pending manufacturer clarification.
- **Immediate telemetry (#45):** Battery percentages, including JSON-quoted payloads, also work through the fast path without waiting for the multipart debounce.
- Named state updates now work after indexed snapshots instead of leaving entities stuck on stale readings.
- Migrate community-fork illuminance and AutoLock unique IDs while preserving existing Home Assistant entity IDs, custom names, and settings. Guard migration against unrelated device and gateway entries; avoid duplicate AutoLock setup during rediscovery.

### Added

- German translations, including “Beleuchtungsstärke” for illuminance and readable HOPPE states.
- Regression coverage for reported payload variants, HA entity updates, invalid values, migration/reload/duplicate/disabled cases, and translation consistency.

### Upgrade notes

If both the community-fork and beta entities exist for the same device and gateway, the fork entity is preserved and the duplicate beta entry is removed. Update any dashboards or automations referencing the removed beta entity; both IDs are logged. Existing fork references continue using the preserved entity ID. Automations comparing the AutoLock unlock-request sensor with boolean text should use `requested` / `not_requested` instead.

### Validation

**735 tests** pass on Home Assistant **2026.8.2** and **2026.9.4**, with Ruff and Hassfest checks. Manual device removal (#46) remains covered. Physical IQ-DOT/EnOcean validation is still needed; percentage battery strings are supported, but a real payload capture is still needed to confirm the cause of the reported battery issue.

Thanks to **fubu2k** for the detailed reports, working examples, and proposed patches.

## [0.4.0b0] - 2026-09-30

Beta release for community validation of issues #42–#46. Enable pre-release
versions in HACS, select v0.4.0b0, and restart Home Assistant.

### Added

- **OPUS RWM smoke detectors (#42):** F6-05-02 smoke-alarm and low-battery binary sensors, including indexed `transmitModes` snapshots and value-only live updates.
- **OPUS SMS presence sensors (#43):** A5-07-01 and A5-07-03 motion, illuminance, supply-voltage, and battery-level entities.
- **HOPPE window handles (#44):** D2-06-40, F6-10-00, and D2-03-10 handle-position sensors and separate Open, Tilted, and Closed binary sensors for automations such as CCA. D2-06-40 also exposes read-only AutoLock status, unlock requests, and mechanics faults. AutoLock MQTT control remains disabled pending manufacturer clarification; lock/unlock actions send no commands.
- **Manual device removal (#46):** Remove stale child devices from Home Assistant without unpairing them from OPUS or disrupting other devices. The gateway is protected. Devices still reported by the gateway can be rediscovered without duplicate entities; automation references must be updated separately.

### Fixed

- **Immediate telemetry (#45):** Flat battery-level and signal-strength updates bypass the multipart state debounce, validate readings, and preserve buffered structural updates.
- Missing or invalid detector/handle readings remain unknown, and gateway outages mark entities unavailable. Handle position and lock state remain independent.

### Changed

- Added device documentation, English entity names, and removal/reload/rediscovery coverage for the new profiles.
- Automated validation passes **668 tests** on Home Assistant **2026.8.2** and **2026.9.4**, plus Ruff and Hassfest. Physical-device behavior still needs beta validation.

## [0.3.3] - 2026-09-30

Stable release of the fixes validated in the 0.3.3 beta series. Existing entity
IDs, rocker event names, and configured MQTT routes are preserved.

### Fixed

- **Cover Stop (#37):** Send the dedicated stop command with the correct actuator channel and check the final position/tilt where supported.
- **State confirmation (#35):** Keep delayed status checks active through unknown, invalid, or unrelated feedback. Confirm commands only with valid feedback for the requested field and channel, including queued and overlapping commands.
- **Gateway startup and recovery:** Use fresh uptime replies for health checks, tolerate missing system-information responses, and handle retained snapshots and MQTT reconnects reliably. Broker and gateway outages mark entities unavailable and cancel pending requests.
- **Command errors:** Validate gateway acknowledgements and report rejection or timeout instead of silently assuming success.
- **Device state parsing:** Handle complete JSON telegrams, list-form snapshots, indexed updates, and channel context without replaying cached rocker events.
- **Entity state and controls:** Preserve unknown/unavailable readings correctly, keep very low nonzero brightness on, report climate activity accurately, support standard climate actions, and hide tilt controls when rotation is disabled.
- **Diagnostics:** Redact identifiers and credentials from diagnostic data, including error details.

### Changed

- README and the MQTT protocol reference describe command confirmation, Stop encoding, scoped broker routes, and optional bridge-status reporting. Public examples use fictional identifiers.
- Automated validation covers Home Assistant 2026.8.2 and 2026.9.4: **612 tests**, plus Ruff and Hassfest checks.

## [0.3.3b1] - 2026-09-30

Existing entity IDs and configured MQTT routes are preserved.

### Fixed

- **Cover Stop (#37):** Send the dedicated `stop: "true"` command instead of an invalid position value, retain the actuator channel, and request final position/tilt where supported.
- **State confirmation (#35):** Unknown, invalid, unavailable, malformed, or unrelated feedback no longer cancels delayed status checks. Confirmation is isolated to the requested field and channel, including feedback before acknowledgement and queued or overlapping commands.
- **Gateway startup:** Use fresh uptime responses for health when firmware does not answer system-information requests. System information is optional.
- **Device state parsing:** Read list-form snapshots and indexed updates with the correct channel, without replaying cached rocker events.
- **Startup and reconnect:** Probe health before large retained subscriptions, separate subscription waiting from command deadlines, and reduce repetitive retained-data debug logging.
- **Shutter capabilities:** Read actual device configuration so shutters with zero rotation time do not expose tilt controls.

### Changed

- README and the MQTT protocol reference document command confirmation and Stop encoding. MQTT remains the integration transport.
- All **612 tests pass** on both Home Assistant 2026.8.2 and 2026.9.4, with **90.88% coverage** on 2026.9.4. Ruff and Hassfest validation pass.

## [0.3.3b0] - 2026-09-11

Beta release for physical-device testing. Existing entity IDs and rocker event names are preserved.

### Fixed

- Setup now verifies that the selected OPUS gateway responds. Broker and gateway outages mark entities unavailable, pending operations stop on disconnect or reload, and reconnection refreshes discovery and displayed state.
- Commands wait for gateway acknowledgements and report rejection or timeout. Confirmed feedback received while waiting takes precedence over estimated state; accepted commands receive channel-specific status checks.
- Fixed complete JSON telegram handling and separation of rapid flattened button telegrams. Late device metadata can complete discovery.
- Unknown device readings remain unknown, explicitly unavailable measurements clear stale values, and invalid values are ignored. Very low nonzero brightness stays on. Climate activity no longer assumes that an enabled zone is actively heating; standard climate on/off/toggle actions are supported.
- Push entities no longer poll, and reconnecting does not replay rocker events.
- Diagnostics remove gateway/device identifiers, credentials, names, and identifiers embedded in error data.

### Changed

- README includes scoped, directional Mosquitto routes, an optional bridge-status notification topic, command-delivery limitations, and a physical beta-testing checklist.
- All 509 automated tests pass on both Home Assistant 2026.8.2 and 2026.9.1, with 88.27% coverage. Tests exercise real Home Assistant configuration, registry, entity, service, reconnect, and unload paths.

## [0.3.2] - 2026-09-11

### Fixed
- **Duplicate signal-strength entities** (#26): Repeated discovery no longer causes duplicate unique-ID errors at startup, while newly recognized sensor types can still be added.
- **Tilt controls on roller shutters** (#28): Tilt controls are hidden when the bridge reports `rotationTime` as `0` or `noRotation`. Existing discovery and state updates refresh this setting without extra configuration queries; ordinary position controls remain available, and devices without a valid rotation-time value retain their existing EEP behavior.

## [0.3.2b0] - 2026-08-31

### Fixed
- **Duplicate signal-strength entities** (#26): Repeated device discovery no longer submits the same sensor unique ID more than once during startup, eliminating Home Assistant duplicate-entity errors while still allowing newly recognized sensor types to be added when device profiles are completed.

## [0.3.1] - 2026-08-24

### Added
- **F6-05-01 liquid leakage sensors** (#24): OPUS water sensors expose a native Home Assistant moisture binary sensor from the EnOcean `liquidDetected` state. The entity remains unknown until the bridge reports an explicit valid wet or clear value, and malformed updates do not overwrite the last valid state.

## [0.3.1b0] - 2026-08-23

### Added
- **F6-05-01 liquid leakage sensors** (#24): OPUS water sensors now expose a native Home Assistant moisture binary sensor from the EnOcean `liquidDetected` state. The entity remains unknown until the bridge reports an explicit valid wet or clear value, and malformed updates do not overwrite the last valid state.

## [0.3.0] - 2026-08-23

### Added
- **Downloadable diagnostics**: Home Assistant diagnostics now include redacted gateway details, discovered devices, channel state, update sources, and the latest bridge command error.
- **Update latency tracing**: Debug logs identify MQTT receipt, message finalization, dispatcher notification, and entity state-write timing.

### Fixed
- **Multi-channel actuator control** (#20): Commands include the mandatory channel selector, including channel 0, and state reconciliation remains isolated per channel.
- **Home Assistant device-registry compatibility** (#21): Child devices link to the gateway through `via_device_id`, replacing the API scheduled for removal in Home Assistant 2027.8.
- **Gateway command acknowledgements**: Successful OPUS responses with HTTP status 200 or 201 are accepted, while real errors remain visible in diagnostics.
- **Legacy multi-channel entities**: Obsolete aggregate switch and light registry entries are migrated or removed after restart.
- **Discovery and lifecycle reliability**: Device identity, wrapped discovery responses, MQTT failures, subscriptions, timers, and temporary callbacks are handled consistently.

### Changed
- **Minimum Home Assistant version**: Home Assistant 2026.8 or newer is now required.
- **Quality gates**: CI tests Home Assistant 2026.8.2 on Python 3.14 and enforces Ruff, Hassfest, and at least 75% test coverage.

## [0.3.0b1] - 2026-08-17

### Fixed
- **Gateway command acknowledgements**: Successful OPUS `putAnswer` responses with HTTP status 200 or 201 are no longer reported as command failures. Real error responses remain visible in diagnostics, while accepted commands continue waiting for confirmed device state.
- **Legacy multi-channel entities**: Obsolete aggregate switch and light registry entries are migrated to channel 0 when possible or removed when the channel-0 entity already exists, eliminating stale unavailable duplicates after restart.

## [0.3.0b0] - 2026-08-17

### Added
- **Downloadable diagnostics**: Home Assistant diagnostics now include redacted gateway details, discovered devices, channel state, update sources, and the latest bridge command error.
- **Update latency tracing**: Debug logs identify MQTT receipt, message finalization, dispatcher notification, and entity state-write timing.

### Fixed
- **Multi-channel actuator control** (#20): Commands now put the mandatory `channel` selector first and include channel `0` for multi-channel devices. Outbound echoes retain the selector and reconciliation is isolated per channel.
- **Home Assistant device-registry compatibility** (#21): The integration creates a gateway device and links children through `via_device_id`, removing the deprecated `via_device` path scheduled for removal in Home Assistant 2027.8.
- **Command failure reporting**: MQTT publish failures are raised to Home Assistant and asynchronous gateway errors are recorded instead of leaving a false successful state.
- **Stable device discovery**: Devices are keyed by their EnOcean ID, wrapped discovery responses are accepted, and friendly-name changes no longer break entity updates.
- **Lifecycle cleanup**: MQTT subscriptions, debounce timers, delayed reconciliation queries, and temporary callbacks are cleaned up on unload or failed setup.

### Changed
- **Minimum Home Assistant version**: Home Assistant 2026.8 or newer is now required.
- **Power sensor classification**: The D1-4B-07 kW sensor is now correctly named and classified as power consumption; its existing unique ID is preserved.
- **Service actions**: ReCom actions are administrator-only, validate the selected gateway and device, and return response data for read operations.
- **Entity diagnostics**: Signal-strength entities are disabled by default, and volatile diagnostic values moved from entity attributes into downloadable diagnostics.
- **Quality gates**: CI now tests Home Assistant 2026.8.2 on Python 3.14 and enforces Ruff, Hassfest, and at least 75% test coverage.

## [0.2.1] - 2026-06-26

### Added
- **Raw OPUS MQTT debug logging**: Debug logging now includes raw subscribed OPUS MQTT topic and payload details before parsing, with long payloads truncated.
- **Native bridge HomeKit reconciliation**: State commands seen on the OPUS MQTT telegram stream are applied optimistically and followed by delayed status checks after 5 and 20 seconds, so failed commands can be corrected by the bridge's confirmed status response.

### Fixed
- **Faster local-control updates**: Known devices now apply `stream/devices/.../states/...` updates immediately instead of waiting for the discovery debounce.
- **Fragmented telegram handling**: `stream/telegram` processing waits long enough to collect split key/value messages and ignores incomplete function fragments.
- **Multi-channel local-control updates**: Telegram functions with an embedded `channel` field now update the matching Home Assistant channel entity.
- **Pre-discovery telegram merge**: Devices first seen through `stream/telegram` are merged by device ID when full metadata arrives, avoiding temporary entries that could miss later entity updates.
- **Native bridge HomeKit state sync**: Outbound OPUS MQTT command telegrams that carry state functions can now update Home Assistant even when the bridge does not publish an immediate separate confirmed status telegram.
- **Outbound query filtering**: Outbound query/status telegrams are ignored as state updates, preventing status requests from changing entities.

## [0.2.0] - 2026-05-12

### Added
- **Per-button rocker switch events** (#18): event entities for F6-02-xx / F6-03-xx rocker switches now fire one event per (button, action) pair — `buttonA0_pressed`, `buttonA0_released`, `buttonAI_pressed`, …, `multipleButtons_released`. Each event also carries `button` and `action` in its event attributes, so HA automations can react to the specific physical button that was pressed.

### Fixed
- **Rocker events were effectively unusable**: the previous implementation fired a generic `press`/`release` derived from `channel.is_on`, which was never set by F6-* button telegrams. Automations triggered by old event types received no useful button information.

### Changed
- **Breaking**: event types on rocker entities changed from `press` / `release` / `short_press` / `long_press` to per-button names listed above. Any existing automation triggers using the old event types must be updated. The old types never carried button data, so practical impact should be minimal.

## [0.1.10] - 2026-02-14

### Reset
- **Reset to v0.1.6 state**: This release reverts all changes from v0.1.7, v0.1.8, and v0.1.9 to address stability issues. The codebase is now identical to v0.1.6.

## [0.1.6] - 2026-02-14

### Fixed
- **Reduced debounce from 100ms to 20ms**: Optimized MQTT message debouncing for both `stream/device` and `stream/telegram` handlers. State changes now propagate faster while still debouncing rapid successive messages.

## [0.1.5] - 2026-02-14

### Added
- **Optimistic state updates**: Lights, switches, and covers now update their state immediately when turned on/off or moved, before waiting for MQTT confirmation. Added `_attr_assumed_state = True` to all controllable entities for instant UI feedback.

## [0.1.4] - 2026-02-14

### Fixed
- **Device ID to friendly ID lookup in telegram handler**: Fixed `_finalize_telegram` to correctly look up devices using `friendly_id` as the key (matching what's stored in `self.devices`) instead of `device_id` directly. Previously, telegrams arriving before device discovery would create duplicate entries with `friendly_id` as key, while discovered devices used the same key — but the lookup code checked `device_id` and missed matches.

### Changed
- **Reorganized device data storage**: Split raw MQTT data into separate dictionaries (`_device_data`, `_telegram_data`, `_device_stream_data`) to prevent crosstalk between different message sources.

## [0.1.3] - 2026-02-14

### Fixed
- **Dimmable light on/off**: Dimmers now use `dimValue: 100` / `dimValue: 0` instead of `switch: on/off`, matching the OPUS MQTT spec (section 5.3). On/off controls in HA now work correctly for dimmable lights.
- **Faster external state updates**: Fixed `_finalize_telegram` to extract functions from the `from` sub-key of flattened MQTT topics. Previously the handler looked at the top level and found nothing, causing ~10s delays until the next `stream/device` delta arrived.

### Added
- **Test suite**: 155 pytest tests covering device model, coordinator helpers, MQTT finalization, command building, and config flow validation. Runs in <0.5s.
- **`reload_entry` developer service**: Re-runs integration setup/teardown without restarting HA. Useful for testing MQTT reconnection and config lifecycle.

## [0.1.2] - 2025-02-13

### Fixed
- **HA commands now update entity state**: Fixed `stream/device` delta handler to correctly parse `state.functions` array format. Previously it only looked for the `states` flat dict format (used by boot data), so live deltas after commands were silently dropped — entities stayed stale until an external change arrived.

## [0.1.1] - 2025-02-13

### Fixed
- **Faster state updates**: Reduced debounce timer for `stream/device` and `stream/telegram` handlers from 500ms to 100ms, making external state changes reflect near-instantly in Home Assistant

## [0.1.0] - 2025-02-13

### Added
- **Climate platform**: Full HeatArea support for OPUS Valve (D1-4B-05), CosiTherm (D1-4B-06), and Electro Heating (D1-4B-07) with temperature control, HVAC modes, and humidity
- **Sensor platform**: Humidity, feed temperature, energy consumption, and signal strength sensors
- **Binary sensor platform**: Window open, actuator errors (not responding, deactivated, missing temperature), battery low, and circuit-in-use sensors
- **Event platform**: Rocker switch press/release events for F6-02-xx and F6-03-xx switches
- **ReCom API services**: `get_device_configuration`, `set_device_configuration`, `get_device_parameters` exposed as HA service calls
- **Gateway diagnostics**: System info and uptime queries
- **Device profile queries**: Fetch device capability profiles via MQTT
- **Active GET discovery**: Devices now discovered via `get/devices` on startup (no longer relies solely on `stream/devices` boot broadcast)
- **`stream/device` subscription**: Live delta updates for real-time state changes
- **services.yaml**: Service descriptions for the HA Developer Tools UI

### Fixed
- **Multi-channel commands**: Commands now correctly include `channel` key for multi-channel devices
- **Initial state handling**: All known function keys (climate, energy, errors) are now applied on startup, not just switch/dimValue/position/angle

### Changed
- Expanded `KNOWN_STATE_KEYS` to cover all climate, error, and sensor function keys
- `PLATFORMS` list now includes all 7 platforms: light, switch, cover, climate, sensor, binary_sensor, event

## [0.0.10] - 2024-11-29

### Fixed
- **State updates now working**: Fixed multiple issues preventing device state updates from reflecting in Home Assistant:
  - Fixed telegram topic regex to match actual bridge structure (removed incorrect `from/to` segment)
  - Fixed device key mismatch between coordinator and entities (now consistently uses `friendly_id`)
  - Filter out `direction='to'` telegrams (commands) - only process `direction='from'` (status responses)
  - Handle both list and dict formats for telegram functions data
  - Thread safety: Fixed `async_dispatcher_send` being called from wrong thread via proper `@callback` decorator
- **All devices now load correctly**: Auto-discovered devices from telegrams now get properly updated with EEP info during discovery, ensuring all entities are created

## [0.0.9] - 2024-11-24

### Added
- **Initial state on startup**: Devices now load their current state during discovery from `stream/devices` data. Previously, entities would show unknown state until a physical change occurred.

## [0.0.8] - 2024-11-24

### Fixed
- **State updates now work correctly**: Fixed telegram topic regex pattern to match actual bridge structure (`EnOcean/{EAG}/stream/telegram/{DeviceID}/{property}`) instead of expecting a `from/to` direction segment.

## [0.0.7] - 2024-11-24

### Fixed
- **Simplified command functions**: Removed unnecessary `channel` function from commands. Commands now only send the required function (e.g., `{"key": "switch", "value": "on"}`).

## [0.0.6] - 2024-11-24

### Fixed
- **Commands now use correct JSON format**: Fixed command payload to use `{"state": {"functions": [...]}}` format instead of `{"telegram": {...}}`. The bridge's `put` endpoint expects a `state` object, not a `telegram` wrapper.

## [0.0.5] - 2024-11-24

### Fixed
- **State updates now work with flattened MQTT structure**: Rewrote telegram handler to parse flattened MQTT topics (like device discovery) instead of expecting JSON payloads. State changes from physical switches now properly update entity states in Home Assistant.

## [0.0.4] - 2024-11-24

### Fixed
- **Commands now use correct device ID**: Fixed critical bug where commands were sent using the friendly name instead of the actual EnOcean device ID. This prevented lights, switches, and covers from responding to commands.
- **Updated repository URLs**: Fixed documentation and issue tracker URLs in manifest to point to the correct repository (`opus_homeassistant`).

## [0.0.3] - 2024-11-24

### Fixed
- **Icon display in Home Assistant**: Moved `icon.png` and `logo.png` to integration root folder for proper display in the UI.

## [0.0.2] - 2024-11-24

### Fixed
- **Device discovery with flattened MQTT structure**: Rewrote coordinator to handle Opus GreenNet's flattened MQTT topic structure (one topic per property) instead of JSON payloads.

### Changed
- Subscribe to `EnOcean/{EAG}/stream/devices/#` wildcard topic
- Aggregate device properties from individual MQTT messages
- Discovery timer waits for all properties before creating entities

## [0.0.1] - 2024-11-24

### Added
- Initial release
- Auto-discovery of EnOcean devices via MQTT
- Support for lights (dimmable and on/off)
- Support for switches
- Support for covers (blinds/shades) with position and tilt
- UI-based configuration via Config Flow
- Real-time state updates via MQTT push
- Multi-channel device support
