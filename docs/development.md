# Development

- [Project structure](#project-structure)
- [Running the checks](#running-the-checks)
- [Continuous integration](#continuous-integration)
- [Beta testing](#beta-testing)
- [Contributing](#contributing)

## Project structure

```
custom_components/opus_greennet/
├── __init__.py           # Integration setup and action registration
├── manifest.json         # Integration metadata and version
├── config_flow.py        # Setup, discovery, re-authentication and reconfiguration
├── discovery.py          # mDNS search, gateway identification by EURID, detection of WebUI 2026 gateways
├── api.py                # REST client: requests, error mapping, event stream splitting, gateway generations (HTTP/basic, HTTPS/token)
├── protocol.py           # Pure helpers that read gateway documents
├── coordinator.py        # Event stream, health check, device state and commands
├── const.py              # Constants and EEP mappings
├── wire.py               # Typed JSON boundary helpers
├── enocean_device.py     # Device and channel data model
├── entity.py             # Shared entity and device-registry behaviour
├── diagnostics.py        # Redacted diagnostics
├── light.py, switch.py, cover.py, climate.py, lock.py
├── sensor.py, binary_sensor.py, binary_sensor_descriptions.py
├── event.py              # Rocker switches and actuator buttons
├── button.py             # Reference-run buttons for D2-05 blinds
├── number.py             # Time and threshold settings (blinds, dimmers, switches)
├── select.py             # Selectable settings of D2-01 switches
├── services.yaml         # Action definitions
├── strings.json          # Translation source
└── translations/         # en, de
tests/
├── conftest.py           # Shared fixtures (real HA core, fake gateway)
├── fake_gateway.py       # In-process OPUS gateway serving the REST API over HTTP
├── ha_helpers.py         # Config flow and waiting helpers
├── fixtures/             # Anonymized parameter catalogs and configurations read from a gateway (one device per actuator type)
└── test_*.py             # One module per area (api, coordinator, platforms, flows, …)
script/
├── check_coverage.py     # Per-module coverage gate used by CI
└── check_icons.py        # Checks icons.json against the frontend's icon catalogue
docs/
└── quality/              # Quality scale evidence and gateway protocol contract
```

## Running the checks

Use Python 3.14.

```bash
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
python -m mypy
pytest -v --cov --cov-report=json:coverage.json
python script/check_coverage.py coverage.json
```

The suite has 2,656 tests. They run against real Home Assistant core and an
in-process gateway that serves the REST API and its event stream over real HTTP
(`tests/fake_gateway.py`).

## Continuous integration

- CI tests the latest stable Home Assistant release from PyPI (recorded in the
  workflow summary) and a pinned reference version
  (`requirements_test_reference.txt`, currently 2026.10.4) with hassfest built
  from that release; the latest hassfest image runs as an extra signal. If a new release needs a newer Python, CI fails explicitly instead
  of silently testing an older release.
- It runs on pull requests, pushes to main, every Monday at 06:17 UTC and on
  manual dispatch.
- Gates: Ruff, dependency compatibility, strict mypy, hassfest, HACS validation, icon check,
  total coverage of at least 97 %, more than 95 % per module and 100 % for the
  config flow. Lifecycle and race suites run three more times.
- `quality_scale.yaml` is validated against the official rule keys by
  `tests/test_quality_scale.py`. It is a self-assessment, not an official
  rating; see the [evidence matrix](quality/quality-scale-evidence.md).

## Beta testing

Enable pre-release versions for this repository in HACS, select the beta and
restart Home Assistant. Existing entity IDs and rocker event names are kept.

Please check physical controls, low brightness, both channels of 2-channel
actuators, cover position and tilt, climate activity and rapid rocker presses.
Also power-cycle the gateway and reload the integration during a pending
request. Report your gateway firmware, the device EEP and what you observed,
with redacted diagnostics, in the pinned beta issue or a new issue.

## Contributing

- Open pull requests against `main`, one topic per pull request.
- Pull requests contain no version bump and no CHANGELOG entry; both are part
  of the release.
- Add tests for every behaviour change and keep all CI gates green.
- Reference: [EnOcean over IP](https://www.enocean-alliance.org/ip/),
  [EnOcean Equipment Profiles](https://www.enocean-alliance.org/eep/),
  [Home Assistant developer docs](https://developers.home-assistant.io/).
