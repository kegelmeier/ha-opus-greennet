# CLAUDE.md — Project Guidelines for AI Assistants

## Project Overview

Home Assistant custom integration for OPUS GreenNet Bridge (EnOcean over IP via the gateway REST API).
Repository: https://github.com/kegelmeier/ha-opus-greennet

## Tech Stack

- Python 3.14 (Home Assistant 2026.10+; fixed CI reference 2026.10.4)
- Home Assistant custom component architecture
- EnOcean over IP REST API of the gateway (event stream, aiohttp via Home Assistant's shared session): plain HTTP with Basic auth for older firmware, HTTPS with login and a pinned certificate fingerprint for WebUI 2026
- HACS compatible

## Project Structure

- `custom_components/opus_greennet/` — all integration code
- `manifest.json` — version (single source of truth) + metadata
- `const.py` — all constants and EEP mappings
- `api.py` — REST client (requests, error mapping, event stream splitting, request priority lock)
- `protocol.py` — pure helpers that read gateway documents (state functions, feedback fields)
- `coordinator.py` — connection lifecycle (event stream, health probe), device state, commands
- `wire.py` — typed gateway data and validation helpers for the JSON boundary
- `enocean_device.py` — device/channel data model
- `entity.py` — shared base entity, repair issues for entities
- `config_flow.py` — user, zeroconf, reauth, reconfigure and options flow
- `discovery.py` — gateway search in the local network
- `diagnostics.py` — config entry and device diagnostics (redacted)
- `write_access.py` — opt-in for writing device settings
- `readings.py`, `binary_sensor_descriptions.py` — read-only profile readings and binary sensor descriptions
- Platform files: `light.py`, `switch.py`, `cover.py`, `climate.py`, `lock.py`, `sensor.py`, `binary_sensor.py`, `event.py`, `number.py`, `select.py`, `button.py`, `time.py`
- `services.yaml` — HA service definitions (rendered in Developer Tools)
- `icons.json` — icon translations
- `brand/` — local brand images
- `strings.json` + `translations/en.json` + `translations/de.json` — UI strings and entity names
- `quality_scale.yaml` — quality scale self-assessment (evidence: `docs/quality/quality-scale-evidence.md`)

## Branch Strategy (MANDATORY for every change)

ALWAYS create a new branch for each feature or fix. NO direct commits to main.

> **Note:** For small, low-risk changes (typos, release note updates, CHANGELOG-only edits), direct commits to `main` are permitted.

### Branch Naming Convention

For FEATURES: `feat/<short-description>`
For FIXES:    `fix/<short-description>`

Examples:
- New platform:             `feat/number-platform`
- Bug fix for covers:       `fix/cover-position-inversion`
- Breaking change:          `feat/new-discovery-protocol`

### Workflow

1. Create branch from main (`feat/` or `fix/`). Direct push to main is OK for minor changes.
2. Make changes
3. Run `pytest -v` — all tests must pass before proceeding
4. Update README.md (features, supported devices, compatibility, roadmap) and the matching page in docs/ (device behaviour, actions, project structure)
5. Push branch → create PR (see PR Guidelines below)
6. Merge to main
7. **Version & release are done separately** (see Versioning & HACS Release below)

## PR Guidelines (MANDATORY)

**PRs never contain version bumps.** No changes to `manifest.json` version, no CHANGELOG entries. Versioning is done manually as a separate step after merging.

### PR Title Format
```
feat: <short description>
fix: <short description>
```
Examples:
- `feat: add number platform for dimmer speed`
- `fix: cover position inversion`

### PR Body Structure
```markdown
## Summary
- Brief description of what changed and why

## Changes
- List of specific changes

## Testing
- [ ] `pytest -v` passes (all tests)
- [ ] Manual testing done (if applicable): describe what was tested
```

## PR Checklist (before pushing the branch)

1. **Tests pass** — Run `pytest -v`. All tests must pass. Add tests for new functionality.
2. **README.md / docs/** — Update README.md for features, supported devices, compatibility or roadmap; docs/devices.md for device behaviour and actions; docs/development.md for project structure or test count.
3. **Translation files** — If new entity types, errors or UI strings are added:
   - `strings.json` — add entity names under `"entity"` block, errors under `"exceptions"`
   - `translations/en.json` and `translations/de.json` — mirror the same entries
4. **services.yaml** — If new HA services are added, add descriptions here (and the service strings)
5. **No version changes** — Do NOT modify `manifest.json` version or CHANGELOG.md in the PR.

## Versioning & HACS Release (manual, after PR is merged)

Versioning is done **manually and separately** from PRs. After one or more PRs are merged:

### Version Rules
- **PATCH (0.0.x → 0.0.x+1)**: Bug fixes only
- **MINOR (0.x.0)**: New features, platforms, device support
- **MAJOR (x.0.0)**: Breaking changes

### Release Steps
1. Bump `"version"` in `manifest.json`
2. Add CHANGELOG.md section at the top with:
   - Version number and date
   - `### Added` / `### Fixed` / `### Changed` / `### Removed` subsections as needed
   - Clear, user-facing descriptions of what changed
3. Commit version bump + changelog directly to main
4. Create a GitHub Release (required for HACS):
   ```bash
   gh release create v<version> --title "v<version>" --notes "<paste CHANGELOG section>"
   ```
5. Verify at: `https://github.com/kegelmeier/ha-opus-greennet/releases`

## Coding Patterns

- All platforms follow the dispatcher signal pattern (see `light.py` as reference)
- Device state is held in `EnOceanChannel` dataclass fields
- Coordinator handles ALL gateway communication; entities never call the API directly
- Runtime data lives in `entry.runtime_data`; exceptions raised to the user carry `translation_domain` and `translation_key`
- Use `KNOWN_STATE_KEYS` in const.py when adding new function keys
- Multi-channel devices require the `channel` key in command functions

## Testing

- Run `pytest -v` before every push — all tests must pass
- CI additionally runs `ruff check`, `ruff format --check`, `mypy --strict`, per-module coverage (`script/check_coverage.py`: above 95 %, `config_flow.py` 100 %) and hassfest; do not lower these gates
- If a change affects a quality scale rule, update `quality_scale.yaml` and `docs/quality/quality-scale-evidence.md`
- Manual testing: trigger physical devices, verify HA entity states update
- Check HA logs at debug level: `custom_components.opus_greennet: debug`
- When adding new functionality, add corresponding tests in `tests/`

## Reference

- Gateway interface contract: `docs/quality/protocol-contract.md`
- Former MQTT reference (payload semantics still apply): `opus_mqtt_services_reference.md`
- EEP profiles: https://www.enocean-alliance.org/eep/
