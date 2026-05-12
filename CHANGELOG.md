# Changelog

All notable changes to this project will be documented in this file.

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
- **Commands now use correct device ID**: Fixed critical bug where commands were sent using the friendly name (e.g., `KG_Vorrat-1K-1`) instead of the actual EnOcean device ID (e.g., `01A02F6C`). This prevented lights, switches, and covers from responding to commands.
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
