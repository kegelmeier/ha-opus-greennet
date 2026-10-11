# Changelog

All notable changes to this project will be documented in this file.

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
