# Quality scale evidence matrix

Rule -> implementation -> tests -> documentation for every rule in
`custom_components/opus_greennet/quality_scale.yaml`.

## Status of this assessment

- Opus GreenNet Bridge is a **custom integration**. Home Assistant assigns
  quality scale tiers only to core integrations. This file and
  `quality_scale.yaml` are a verifiable self-assessment, **not an official
  rating**. `manifest.json` intentionally declares no `quality_scale` key.
- Rule keys, tiers and the file schema are taken from hassfest
  (`script/hassfest/quality_scale.py`, home-assistant/core commit
  `7ff0e7fbdea4e079ae3f99e048497f5e8c7e3360`, 2026-10-14): 54 rules (bronze 20,
  silver 10, gold 21, platinum 3). Rule texts:
  <https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/>.
- Hassfest skips `quality_scale.yaml` for custom integrations. The CI test
  `tests/test_quality_scale.py` therefore applies the official schema, the
  official rule keys, the applicable official validators (`config-flow`,
  `runtime-data`, `strict-typing`, `test-before-setup`, `unique-config-entry`,
  `discovery`) and checks that this table matches the YAML.
- Summary: 50 done, 4 exempt, 0 todo. Open items stay visible as `todo`.
  Exempt rules are self-assessed and would need an official review to be
  accepted, so no tier is claimed.

`QS` = `tests/test_quality_scale.py`, `TR` = `tests/test_translations.py`.

## Bronze

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `action-setup` | done | `__init__.py`: `async_setup` -> `_register_services`; handlers validate entry and device | `test_service_actions.py::test_actions_are_registered_before_any_entry_loads`, `::test_unknown_and_unloaded_entries_are_validation_errors`; QS `test_actions_are_set_up_in_async_setup` | docs/devices.md Actions |
| `appropriate-polling` | exempt | Push integration (`iot_class: local_push`, REST event stream); `entity.py` `_attr_should_poll = False`; 60 s gateway health probe and 30 min device list refresh only | QS `test_every_entity_class_has_entity_name_and_no_polling`; TR `test_every_created_entity_has_a_translated_name` (`should_poll is False`) | docs/how-it-works.md Updates and availability |
| `brands` | done | `brand/icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png` (local brand images, HA 2026.3+) | QS `test_brands` | [Brand images](https://developers.home-assistant.io/docs/core/integration/brand_images/) |
| `common-modules` | done | `coordinator.py` (runtime coordinator), `entity.py` (`OpusGreenNetEntity`) | QS `test_common_modules` | docs/development.md Project structure |
| `config-flow` | done | `config_flow.py`, `manifest.json` `config_flow: true`, `data_description` in `strings.json` | QS `test_config_flow_validator`; TR `test_config_flow_errors_and_aborts_are_translated` | README Configuration |
| `config-flow-test-coverage` | done | `config_flow.py` at 100 % (CI gate) | `test_config_flow.py` (user, reauth and reconfigure steps, every error and a recovering submit, unique ID asserted); `script/check_coverage.py` | `phases/phase-07.md` |
| `dependency-transparency` | done | `manifest.json` `requirements: []` | QS `test_no_requirements_and_injected_websession` | – |
| `docs-actions` | done | `services.yaml`, service strings | TR `test_services_yaml_matches_service_strings` | docs/devices.md Actions |
| `docs-conditions` | exempt | No conditions provided | – | – |
| `docs-high-level-description` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README introduction, Features |
| `docs-installation-instructions` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Compatibility, Installation; docs/upgrading.md |
| `docs-removal-instructions` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Removing the integration |
| `docs-triggers` | exempt | No triggers provided (no `device_trigger.py`, no trigger platform) | – | – |
| `entity-event-setup` | done | `entity.py` `async_added_to_hass` with `async_on_remove(async_dispatcher_connect(...))`, inherited by all entity classes | `test_event_lifecycle.py`, `test_binary_sensor_discovery.py::test_rediscovery_waits_for_live_binary_removal`, `test_ha_lifecycle.py::test_setup_creates_and_unloads_entry` | – |
| `entity-unique-id` | done | Unique IDs from EAG ID, device ID, channel and key (`entity.py`, platforms) | `test_beta_entity_migration.py`, `test_binary_sensor_descriptions.py::test_description_inventory_matches_stable`, `test_entities.py::test_platform_setup_adds_expected_entities` | `protocol-contract.md` §8 |
| `has-entity-name` | done | `_attr_has_entity_name = True` on every entity class | QS `test_every_entity_class_has_entity_name_and_no_polling`; TR `test_every_created_entity_has_a_translated_name` | – |
| `runtime-data` | done | `entry.runtime_data = OpusGreenNetRuntimeData(...)` | QS `test_runtime_data_validator`; `test_init.py::test_setup_creates_gateway_registry_device` | – |
| `test-before-configure` | done | `config_flow.py` reads `/system/info` and the gateway EURID (`async_read_gateway_id`) before creating the entry | `test_config_flow.py::test_user_flow_wrong_password`, `::test_user_flow_unreachable_gateway`, `::test_user_flow_device_without_eurid` | README Configuration |
| `test-before-setup` | done | `async_setup_entry` raises `ConfigEntryNotReady` (unreachable gateway) or `ConfigEntryAuthFailed` (credentials, identity, missing address) | QS `test_test_before_setup_validator`; `test_ha_lifecycle.py::test_setup_retries_for_an_unusable_gateway`; `test_init.py::test_failed_setup_states_in_home_assistant` | docs/troubleshooting.md Recovery |
| `unique-config-entry` | done | `async_set_unique_id(eag_id)` + `_abort_if_unique_id_configured(updates=...)` | QS `test_unique_config_entry_validator`; `test_config_flow.py::test_adding_known_gateway_updates_its_connection` | – |

## Silver

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `action-exceptions` | done | `ServiceValidationError` for invalid input, translated `HomeAssistantError` (`request_error`) for failed requests and entity commands | `test_service_actions.py::test_unknown_device_is_a_validation_error_without_request`, `::test_rejected_read_surfaces_translated_error`; `test_coordinator_commands.py::test_rejected_or_malformed_ack_fails_action`, `::test_offline_commands_are_never_sent` | README Troubleshooting, docs/troubleshooting.md |
| `config-entry-unloading` | done | `async_unload_entry` unloads platforms, then stops the coordinator (event stream, timers, queued requests) | `test_init.py::test_unload_stops_coordinator_after_platforms`, `::test_failed_platform_unload_keeps_coordinator_running`; `test_coordinator_stream.py::test_unload_stops_stream_and_timers`; `test_coordinator_commands.py::test_unload_cancels_queued_request` | – |
| `docs-configuration-parameters` | done | One option, write access to device settings, with a warning and a confirmation in the options flow | `test_write_access.py::test_the_options_dialog_enables_and_disables_write_access` | README Configuration (Write access to device settings) |
| `docs-installation-parameters` | done | `data_description` for host, port, username and password | TR `test_config_flow_errors_and_aborts_are_translated` | README Configuration |
| `entity-unavailable` | done | `OpusGreenNetEntity.available` follows `coordinator.available` | `test_ha_lifecycle.py::test_entity_service_and_gateway_outage`; `test_coordinator_stream.py::test_gateway_outage_marks_entities_unavailable_until_it_returns` | docs/how-it-works.md Updates and availability |
| `integration-owner` | done | `manifest.json` `codeowners` | QS `test_integration_owner` | – |
| `log-when-unavailable` | done | `_set_gateway_available` logs once at info on each change | `test_coordinator_edge_cases.py::test_unavailability_is_logged_once_and_recovery_once` | – |
| `parallel-updates` | done | `PARALLEL_UPDATES = 0` in every platform; the coordinator serialises radio requests | QS `test_parallel_updates_in_every_platform`; `test_coordinator_commands.py::test_global_send_queue_keeps_one_request_in_flight` | – |
| `reauthentication-flow` | done | `config_flow.py` `async_step_reauth` / `reauth_confirm`; `ConfigEntryAuthFailed` in setup; `entry.async_start_reauth` on HTTP 401 at runtime | `test_config_flow.py::test_legacy_mqtt_entry_asks_for_the_gateway_address`, `::test_reauth_rejects_a_different_gateway`; `test_coordinator_stream.py::test_stream_auth_failure_starts_reauth_once` | docs/upgrading.md |
| `test-coverage` | done | CI: `fail_under = 97`, `script/check_coverage.py` (> 95 % per module) | `tests/test_coverage_gate.py` | `phases/phase-07.md` |

## Gold

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `devices` | done | Gateway device in `__init__.py`, EnOcean devices via `device_info` with `via_device_id` (`entity.py`) | `test_init.py::test_setup_creates_gateway_registry_device`; `test_binary_sensor_discovery.py::test_gateway_discovery_and_tracking_are_isolated` | – |
| `diagnostics` | done | `diagnostics.py` (config entry and device diagnostics, `TO_REDACT`, identifier redaction in text) | `test_diagnostics.py`, `test_diagnostics_edge_cases.py`; QS `test_stale_devices_and_diagnostics_entry_points` | README Troubleshooting, docs/troubleshooting.md |
| `discovery` | done | `manifest.json` `zeroconf` (`_enocean._tcp.local.`, `opusshgw*`); `config_flow.py` `async_step_zeroconf`; `discovery.py` search for entries of the former MQTT setup | `test_discovery.py`; QS `test_discovery_status_matches_the_code` | README Configuration, docs/upgrading.md |
| `discovery-update-info` | done | `config_flow.py` `_async_adopt_address`: a configured gateway announced at another address is updated after its EURID is confirmed | `test_discovery.py::test_discovery_updates_a_changed_address`, `::test_discovery_completes_a_legacy_entry_and_ends_its_reauth` | README Configuration |
| `docs-data-update` | done | – | QS `test_documentation_rules_point_to_existing_sections` | docs/how-it-works.md Updates and availability |
| `docs-examples` | done | – | QS `test_documentation_rules_point_to_existing_sections` | docs/examples.md Examples |
| `docs-known-limitations` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Known limitations, docs/how-it-works.md |
| `docs-supported-devices` | done | `const.py` `EEP_MAPPINGS` | TR `test_every_created_entity_has_a_translated_name` (all EEPs) | README Supported devices |
| `docs-supported-functions` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Features, Supported devices; docs/devices.md Actions |
| `docs-troubleshooting` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Troubleshooting, docs/troubleshooting.md |
| `docs-use-cases` | done | – | QS `test_documentation_rules_point_to_existing_sections` | docs/examples.md Use cases |
| `dynamic-devices` | done | `SIGNAL_DEVICE_DISCOVERED` dispatcher listeners in every platform; new devices from stream resyncs, the periodic device list and late lookups | `test_coordinator_devices.py::test_device_list_creates_devices_and_dispatches_discovery`; `test_coordinator_stream.py::test_unknown_device_is_looked_up_once`, `::test_periodic_device_list_refresh_updates_metadata_only`; `test_entities.py::test_sensor_setup_adds_new_types_after_profile_discovery` | docs/devices.md Removing or replacing a device |
| `entity-category` | done | `EntityCategory.DIAGNOSTIC` for signal strength and actuator status binary sensors | `test_binary_sensor_descriptions.py::test_description_inventory_matches_stable`; `test_entities.py::test_sensor_values_and_metadata` | – |
| `entity-device-class` | done | Device classes on sensors, binary sensors and covers | `test_entities.py::test_sensor_values_and_metadata`, `::test_moisture_sensor_values_and_metadata`; `test_binary_sensor_descriptions.py` | – |
| `entity-disabled-by-default` | done | Signal strength sensor; D2-06-40 unlock request and mechanics fault (diagnostic, new registry entries only) | `test_entities.py::test_sensor_values_and_metadata`; `test_handle_diagnostics.py`; `test_sensor_metadata.py` | – |
| `entity-translations` | done | `translation_key` on every named entity; `strings.json`, `translations/en.json`, `translations/de.json` | TR (all tests) | – |
| `exception-translations` | done | `translation_domain`/`translation_key` on raised Home Assistant errors | TR `test_raised_exception_keys_are_translated`; `test_service_actions.py::test_rejected_read_surfaces_translated_error` | – |
| `icon-translations` | done | `icons.json` (handle position, handle closed, unlock request, mechanics fault, service icons) | `test_icon_translations.py` | – |
| `reconfiguration-flow` | done | `config_flow.py` `async_step_reconfigure` (address and credentials, identity check) | `test_config_flow.py::test_reconfigure_changes_the_connection`, `::test_reconfigure_rejects_a_different_gateway`; QS `test_reauth_and_reconfigure_flows_verify_the_gateway_identity` | README Configuration |
| `repair-issues` | done | `entity.py` `async_create_duplicate_beta_entity_issue` (persistent, fixable confirm flow) and `async_set_non_local_address_issue` (notice for a non-local gateway address); both removed with the entry (`async_remove_entry`) | `test_repair_issues.py`, `test_local_gateway_address.py` | – |
| `stale-devices` | done | `async_remove_config_entry_device`; no automatic removal (gateway cannot confirm a device is gone) | `test_device_removal.py`; QS `test_stale_devices_and_diagnostics_entry_points` | docs/devices.md Removing or replacing a device |

## Platinum

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `async-dependency` | exempt | No Python requirements; aiohttp ships with Home Assistant | QS `test_no_requirements_and_injected_websession` | – |
| `inject-websession` | done | `OpusGatewayClient` receives `async_get_clientsession(hass)` in `__init__.py` and `config_flow.py` | QS `test_no_requirements_and_injected_websession` | – |
| `strict-typing` | done | `[tool.mypy] strict = true` for all modules; CI step `python -m mypy`; typed `OpusGreenNetConfigEntry` | QS `test_strict_typing_requires_typed_config_entry_everywhere`, `test_strict_typing_gate_is_configured_and_enforced_in_ci` | `phases/phase-05.md` |
