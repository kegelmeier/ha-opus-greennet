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
- Summary: 40 done, 9 exempt, 5 todo. All open items stay visible as `todo`.
  Because bronze, silver and gold each contain open or exempt items that an
  official review would have to accept, no tier is claimed.

`QS` = `tests/test_quality_scale.py`, `TR` = `tests/test_translations.py`.

## Bronze

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `action-setup` | done | `__init__.py`: `async_setup` -> `_register_services`; handlers validate entry and device | `test_service_actions.py::test_actions_are_registered_before_any_entry_loads`, `::test_unknown_and_unloaded_entries_are_validation_errors`; QS `test_actions_are_set_up_in_async_setup` | README Services |
| `appropriate-polling` | exempt | Push integration (`iot_class: local_push`); `entity.py` `_attr_should_poll = False`; 60 s gateway health probe only | QS `test_every_entity_class_has_entity_name_and_no_polling`; TR `test_every_created_entity_has_a_translated_name` (`should_poll is False`) | README Availability and command behavior |
| `brands` | done | `brand/icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png` (local brand images, HA 2026.3+) | QS `test_brands` | [Brand images](https://developers.home-assistant.io/docs/core/integration/brand_images/) |
| `common-modules` | done | `coordinator.py` (runtime coordinator), `entity.py` (`OpusGreenNetEntity`) | QS `test_common_modules` | README Project Structure |
| `config-flow` | done | `config_flow.py`, `manifest.json` `config_flow: true`, `data_description` in `strings.json` | QS `test_config_flow_validator`; TR `test_config_flow_errors_and_aborts_are_translated` | README Configuration |
| `config-flow-test-coverage` | done | `config_flow.py` at 100 % (CI gate) | `test_config_flow_recovery.py` (every error followed by a recovering submit, unique ID asserted), `test_config_flow.py`, `test_ha_lifecycle.py`; `script/check_coverage.py` | `phases/phase-07.md` |
| `dependency-transparency` | done | `manifest.json` `requirements: []` | QS `test_no_requirements_for_dependency_rules` | – |
| `docs-actions` | done | `services.yaml`, service strings | TR `test_services_yaml_matches_service_strings` | README Services |
| `docs-conditions` | exempt | No conditions provided | – | – |
| `docs-high-level-description` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README introduction, Features |
| `docs-installation-instructions` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Prerequisites, MQTT Bridge Setup, Installation |
| `docs-removal-instructions` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Removing the integration |
| `docs-triggers` | exempt | No triggers provided (no `device_trigger.py`, no trigger platform) | – | – |
| `entity-event-setup` | done | `entity.py` `async_added_to_hass` with `async_on_remove(async_dispatcher_connect(...))`, inherited by all entity classes | `test_event_lifecycle.py`, `test_binary_sensor_discovery.py::test_rediscovery_waits_for_live_binary_removal`, `test_ha_lifecycle.py::test_config_flow_creates_and_unloads_entry` | – |
| `entity-unique-id` | done | Unique IDs from EAG ID, device ID, channel and key (`entity.py`, platforms) | `test_beta_entity_migration.py`, `test_binary_sensor_descriptions.py::test_description_inventory_matches_stable`, `test_entities.py::test_platform_setup_adds_expected_entities` | `protocol-contract.md` §8 |
| `has-entity-name` | done | `_attr_has_entity_name = True` on every entity class | QS `test_every_entity_class_has_entity_name_and_no_polling`; TR `test_every_created_entity_has_a_translated_name` | – |
| `runtime-data` | done | `entry.runtime_data = OpusGreenNetRuntimeData(...)` | QS `test_runtime_data_validator`; `test_init.py::test_setup_creates_gateway_registry_device` | – |
| `test-before-configure` | done | `config_flow.py` probes the gateway uptime endpoint (`async_probe_gateway`) | `test_config_flow_recovery.py::test_recovers_from_unresponsive_gateway`, `::test_recovers_from_disconnected_broker` | README Configuration |
| `test-before-setup` | done | `async_setup_entry` raises `ConfigEntryNotReady` | QS `test_test_before_setup_validator`; `test_ha_lifecycle.py::test_setup_retries_and_cleans_subscriptions_for_unreachable_gateway` | README Troubleshooting, Recovery |
| `unique-config-entry` | done | `async_set_unique_id(eag_id)` + `_abort_if_unique_id_configured()` | QS `test_unique_config_entry_validator`; `test_config_flow_recovery.py::test_second_entry_for_same_gateway_is_aborted` | – |

## Silver

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `action-exceptions` | done | `ServiceValidationError` for invalid input, translated `HomeAssistantError` (`request_error`) for failed requests and entity commands | `test_service_actions.py::test_unknown_device_is_a_validation_error_without_publish`, `::test_rejected_read_surfaces_translated_error`; `test_coordinator_transport.py::test_rejected_or_malformed_ack_fails_action`, `::test_offline_commands_are_never_published` | README Troubleshooting |
| `config-entry-unloading` | done | `async_unload_entry` unloads platforms, then stops the coordinator | `test_init.py::test_unload_stops_coordinator_after_platforms`, `::test_failed_platform_unload_keeps_coordinator_running`; `test_coordinator_transport.py::test_unload_cancels_request_and_subscription` | – |
| `docs-configuration-parameters` | exempt | No options flow | – | README Known limitations |
| `docs-installation-parameters` | done | `data_description` for the EAG identifier | TR `test_config_flow_errors_and_aborts_are_translated` | README Configuration |
| `entity-unavailable` | done | `OpusGreenNetEntity.available` follows `coordinator.available` | `test_ha_lifecycle.py::test_entity_service_and_broker_reconnect`; `test_coordinator_transport.py::test_mqtt_disconnect_still_marks_gateway_unavailable` | README Availability and command behavior |
| `integration-owner` | done | `manifest.json` `codeowners` | QS `test_integration_owner` | – |
| `log-when-unavailable` | done | `_set_gateway_available` logs once at info on each change | `test_coordinator_edge_cases.py::test_unavailability_is_logged_once_and_recovery_once` | – |
| `parallel-updates` | done | `PARALLEL_UPDATES = 0` in every platform; transport serialises requests | QS `test_parallel_updates_in_every_platform`; `test_coordinator_transport.py::test_global_send_queue_keeps_one_request_in_flight` | – |
| `reauthentication-flow` | exempt | No credentials in the config entry | – | README MQTT Bridge Setup |
| `test-coverage` | done | CI: `fail_under = 97`, `script/check_coverage.py` (> 95 % per module) | `tests/test_coverage_gate.py` | `phases/phase-07.md` |

## Gold

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `devices` | done | Gateway device in `__init__.py`, EnOcean devices via `device_info` with `via_device_id` (`entity.py`) | `test_init.py::test_setup_creates_gateway_registry_device`; `test_binary_sensor_discovery.py::test_gateway_discovery_and_tracking_are_isolated` | – |
| `diagnostics` | done | `diagnostics.py` (config entry and device diagnostics, `TO_REDACT`, identifier redaction in text) | `test_diagnostics.py`, `test_diagnostics_edge_cases.py`; QS `test_stale_devices_and_diagnostics_entry_points` | README Troubleshooting |
| `discovery` | todo | Not implemented (no manifest `mqtt` key, no `async_step_mqtt`) | QS `test_discovery_todo_matches_the_code` | README Known limitations |
| `discovery-update-info` | exempt | No network address stored; communication only through the HA broker | – | – |
| `docs-data-update` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Availability and command behavior |
| `docs-examples` | todo | – | – | – |
| `docs-known-limitations` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Known limitations |
| `docs-supported-devices` | done | `const.py` `EEP_MAPPINGS` | TR `test_every_created_entity_has_a_translated_name` (all EEPs) | README Supported Device Types |
| `docs-supported-functions` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Features, Supported Device Types, Services |
| `docs-troubleshooting` | done | – | QS `test_documentation_rules_point_to_existing_sections` | README Troubleshooting |
| `docs-use-cases` | todo | – | – | – |
| `dynamic-devices` | done | `SIGNAL_DEVICE_DISCOVERED` dispatcher listeners in every platform | `test_coordinator_parsing.py::test_live_device_discovery_runs_after_initial_timer_completed`; `test_entities.py::test_sensor_setup_adds_new_types_after_profile_discovery`; `test_platform_edge_cases.py::test_lock_setup_adds_already_discovered_handles` | README Removing or replacing a device |
| `entity-category` | done | `EntityCategory.DIAGNOSTIC` for signal strength and actuator status binary sensors | `test_binary_sensor_descriptions.py::test_description_inventory_matches_stable`; `test_entities.py::test_sensor_values_and_metadata` | – |
| `entity-device-class` | done | Device classes on sensors, binary sensors and covers | `test_entities.py::test_sensor_values_and_metadata`, `::test_moisture_sensor_values_and_metadata`; `test_binary_sensor_descriptions.py` | – |
| `entity-disabled-by-default` | done | Signal strength sensor `entity_registry_enabled_default = False` | `test_entities.py::test_sensor_values_and_metadata` | – |
| `entity-translations` | done | `translation_key` on every named entity; `strings.json`, `translations/en.json`, `translations/de.json` | TR (all tests) | – |
| `exception-translations` | done | `translation_domain`/`translation_key` on raised Home Assistant errors | TR `test_raised_exception_keys_are_translated`; `test_service_actions.py::test_rejected_read_surfaces_translated_error` | – |
| `icon-translations` | todo | No `icons.json` | – | – |
| `reconfiguration-flow` | exempt | The only field (EAG identifier) is the config entry unique ID | QS `test_reconfiguration_exemption_matches_the_code` | README Known limitations |
| `repair-issues` | todo | No repair issues yet; candidate in `quality_scale.yaml` comment | – | `phases/phase-07.md` |
| `stale-devices` | done | `async_remove_config_entry_device`; no automatic removal (gateway cannot confirm a device is gone) | `test_device_removal.py`; QS `test_stale_devices_and_diagnostics_entry_points` | README Removing or replacing a device |

## Platinum

| Rule | Status | Implementation | Tests | Documentation |
|------|--------|----------------|-------|---------------|
| `async-dependency` | exempt | No Python requirements | QS `test_no_requirements_for_dependency_rules` | – |
| `inject-websession` | exempt | No HTTP client imports | QS `test_no_requirements_for_dependency_rules` | – |
| `strict-typing` | done | `[tool.mypy] strict = true` for all modules; CI step `python -m mypy`; typed `OpusGreenNetConfigEntry` | QS `test_strict_typing_requires_typed_config_entry_everywhere`, `test_strict_typing_gate_is_configured_and_enforced_in_ci` | `phases/phase-05.md` |
