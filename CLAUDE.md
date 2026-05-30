# CLAUDE.md — Project Guidelines for AI Assistants

## Project Overview

Home Assistant custom integration for OPUS GreenNet Bridge (EnOcean over IP via MQTT).
Repository: https://github.com/kegelmeier/ha-opus-greennet

## Tech Stack

- Python 3.11+
- Home Assistant custom component architecture
- MQTT (EnOcean over IP protocol)
- HACS compatible

## Project Structure

- `custom_components/opus_greennet/` — all integration code
- `manifest.json` — version (single source of truth) + metadata
- `const.py` — all constants, EEP mappings, topic patterns
- `coordinator.py` — MQTT communication, device discovery, command dispatch
- `enocean_device.py` — device/channel data model
- Platform files: `light.py`, `switch.py`, `cover.py`, `climate.py`, `sensor.py`, `binary_sensor.py`, `event.py`
- `services.yaml` — HA service definitions (rendered in Developer Tools)
- `strings.json` + `translations/en.json` — UI strings and entity names

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
4. Update README.md if the change affects supported devices, features, services, or project structure
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
2. **README.md** — Update if the change affects supported devices, features, services, project structure, or test count.
3. **Translation files** — If new entity types or UI strings are added:
   - `strings.json` — add entity names under `"entity"` block
   - `translations/en.json` — mirror the same entries
4. **services.yaml** — If new HA services are added, add descriptions here
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
- Coordinator handles ALL MQTT communication; entities never touch MQTT directly
- Use `KNOWN_STATE_KEYS` in const.py when adding new function keys
- Multi-channel devices require the `channel` key in command functions

## Testing

- Run `pytest -v` before every push — all tests must pass
- Manual testing: trigger physical devices, verify HA entity states update
- Check HA logs at debug level: `custom_components.opus_greennet: debug`
- When adding new functionality, add corresponding tests in `tests/`

## Reference

- MQTT protocol spec: `opus_mqtt_services_reference.md` in repo root
- EEP profiles: https://www.enocean-alliance.org/eep/
