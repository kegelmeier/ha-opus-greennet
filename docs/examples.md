# Examples and use cases

Entity IDs, device names and EURIDs below are fictional. Replace them with the
ones of your installation (**Settings → Devices & services → Opus GreenNet
Bridge**). How each device type behaves is described in
[Devices](devices.md); how updates and acknowledgements work in
[How it works](how-it-works.md).

- [Blueprints](#blueprints)
- [Examples](#examples)
- [Use cases](#use-cases)

## Blueprints

Four blueprints cover common automations. Import one with the button, then
create an automation from it under **Settings → Automations & scenes →
Blueprints**. The pickers list only the matching Opus GreenNet entities, so no
entity IDs have to be typed. A blueprint is a copy: later changes in this
repository reach you through **Re-import blueprint** in Home Assistant, not
through the Marketplace.

| Blueprint | Import |
|-----------|--------|
| Rocker button controls a light | [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fkegelmeier%2Fha-opus-greennet%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fopus_greennet%2Fbutton_to_light.yaml) |
| Window handle opened or tilted, send a notification | [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fkegelmeier%2Fha-opus-greennet%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fopus_greennet%2Fwindow_handle_notification.yaml) |
| Window handle opened or tilted, blinds move up | [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fkegelmeier%2Fha-opus-greennet%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fopus_greennet%2Fwindow_handle_blind_up.yaml) |
| Water leak, warn | [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fkegelmeier%2Fha-opus-greennet%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fopus_greennet%2Fwater_leak_warning.yaml) |

- **Rocker button controls a light.** Toggles, switches on or off lights when
  the chosen button is pressed. Every press counts, also the same button twice
  in a row.
- **Window handle, notification.** Runs your notification actions when the
  handle is tilted or open, optionally only after it stayed there for a while.
  Unknown and unavailable positions do nothing.
- **Window handle, blinds move up.** Opens the chosen blinds when the handle is
  tilted or open, for example to keep the way free at a balcony door. Blinds
  that are already completely open get no command.
- **Water leak, warn.** Runs your warning actions when a leak sensor reports
  water and can repeat them every few minutes until the sensor reports dry. A
  gateway outage (*unavailable*) does not count as dry.

The notification and warning inputs take any actions, for example the notify
action of your phone, a siren or lights. The files are in
[`blueprints/automation/opus_greennet`](../blueprints/automation/opus_greennet).
To use them without the import button, copy them to
`<config>/blueprints/automation/opus_greennet/`.

## Examples

### Run an automation from a rocker switch

Rocker switches and the buttons of OPUS actuators are event entities. Their
event types are `<button>_pressed` and `<button>_released`, for example
`buttonA0_pressed`; the attributes `button` and `action` carry the same
information. Every press is its own event, also when the same button is
pressed twice in a row. Event entities are enabled but hidden by default, so
they do not show up in generated dashboards.

```yaml
automation:
  - alias: Hallway rocker, top button, toggle the hall light
    triggers:
      - trigger: state
        entity_id: event.hallway_rocker
    conditions:
      - condition: template
        value_template: >-
          {{ trigger.to_state.attributes.event_type == 'buttonA0_pressed' }}
    actions:
      - action: light.toggle
        target:
          entity_id: light.hall
```

### Move a blind to a position, with and without a reference run

`position` uses the Home Assistant scale (100 = open, 0 = closed). A direct
move needs no reference run. `first_up` or `first_down` drives to the end
position first and recalibrates the blind's position tracking; it takes longer than a direct move.

```yaml
# Direct move
action: opus_greennet.set_cover_position
data:
  device_id: AABB1122   # EURID of the blind
  position: 30

# Recalibrate first: drive to the top end position, then to 30 %
action: opus_greennet.set_cover_position
data:
  device_id: AABB1122
  position: 30
  repositioning: first_up
```

Add `channel` for a multi-channel actuator and `config_entry_id` when more than
one gateway is set up. The action is acknowledged by the gateway, not by the
blind: see [Commands and acknowledgements](how-it-works.md#commands-and-acknowledgements).

### Notify when a window handle is left tilted

The *Handle position* sensor of a HOPPE window handle has the states `open`,
`tilted` and `closed`. A missing or invalid position stays `unknown`, so the
automation does not fire on gaps.

```yaml
automation:
  - alias: Living room window tilted for 30 minutes
    triggers:
      - trigger: state
        entity_id: sensor.living_room_window_handle_position
        to: tilted
        for: "00:30:00"
    actions:
      - action: notify.notify
        data:
          message: The living room window has been tilted for 30 minutes.
```

### Alert on a water leak

The leak sensor reports only when its state changes. Until it has reported
water the binary sensor shows *off* (dry), and it shows *unavailable* during a
gateway outage, so a loss of the gateway does not look like a dry floor.

```yaml
automation:
  - alias: Water leak in the basement
    triggers:
      - trigger: state
        entity_id: binary_sensor.basement_water_sensor_water_leak
        to: "on"
    actions:
      - action: notify.notify
        data:
          title: Water leak
          message: The basement water sensor reports a leak.
```

### Show the last run time of a blind

*Last run time* is a sensor with the attributes `direction`, `full_travel`
(true when the run covered the whole height) and the start and end position.
Its value is the time from the command to the blind's end report, not the
mechanical travel time, and can be longer than the configured
`verticalMovementTime`. On the blinds measured so far it follows the blind's
own timer, which is set by the stored `verticalMovementTime`, so it does not
reveal a stored time that is wrong. Compare runs of the same kind
(`full_travel: true`) with each other, for example to notice a blind whose
reports change:

```yaml
type: entities
entities:
  - entity: sensor.bedroom_blind_last_run_time
  - type: attribute
    entity: sensor.bedroom_blind_last_run_time
    attribute: full_travel
```

### Measure the real travel time of a blind

The stored *Vertical movement time* should equal the time the blind really
needs (see the calibration tip in [Troubleshooting](troubleshooting.md)). The
sensors cannot show it, so measure it by hand: the script starts a full run,
you press a stop button as soon as the blind has physically stopped, and the
elapsed time goes to the logbook. After 90 s without a press the blind
is stopped. Your reaction time is part of the result, so measure each direction
twice and enter the typical value in *Vertical movement time*. Thanks to the
user who calibrated 24 blinds this way.

Create an `input_button.blind_measure_stop` helper first, then:

```yaml
script:
  blind_measure_travel_time:
    alias: Measure blind travel time
    mode: restart
    fields:
      cover:
        selector:
          entity:
            domain: cover
      direction:
        selector:
          select:
            options: [open, close]
    sequence:
      - action: "cover.{{ direction }}_cover"
        target:
          entity_id: "{{ cover }}"
      - variables:
          started: "{{ now().timestamp() }}"
      - wait_for_trigger:
          - trigger: state
            entity_id: input_button.blind_measure_stop
        timeout: 90
      - if: "{{ not wait.trigger }}"
        then:
          - action: cover.stop_cover
            target:
              entity_id: "{{ cover }}"
          - stop: No stop pressed within 90 s
      - action: logbook.log
        data:
          name: Blind measurement
          entity_id: "{{ cover }}"
          message: >-
            {{ direction }}: {{ (now().timestamp() - started) | round(1) }} s
```

Start the blind at the opposite end, then run the script from a dashboard
tile, and put a tile for `input_button.blind_measure_stop` next to it:

```yaml
type: tile
entity: cover.bedroom_blind
name: Measure closing
tap_action:
  action: perform-action
  perform_action: script.blind_measure_travel_time
  data:
    cover: cover.bedroom_blind
    direction: close
```

### Read the configuration stored on a device

The read actions return their data as the action response (Admin only; see
[Actions](devices.md#actions)). In a script:

```yaml
action: opus_greennet.get_device_configuration
data:
  device_id: AABB1122
response_variable: stored
```

ReCom requests travel over the radio and can take several seconds (timeout
45 s). `set_device_configuration` writes to the device and is never retried
automatically.

### See which wall switches control a device

```yaml
action: opus_greennet.get_device_link_tables
data:
  device_id: AABB1122
response_variable: links
```

`links.linkTables.inbound` lists the wall switches the device listens to,
`outbound` the devices it reports to; the gateway itself appears as `gateway`.
This action only reads. Link tables are changed with the OPUS app.

## Use cases

- **Local, cloud-free control of an OPUS installation.** Home Assistant talks to
  the gateway in your network over its REST API; no broker and no cloud
  account. State changes arrive as pushed telegrams, typically within a few
  hundredths of a second, including local switch operation and commands from
  other controllers such as HomeKit.
- **Shutter and blind automation.** Position, tilt and the estimated movement
  appear as a cover. Sun protection or wind protection automations use
  `cover.set_cover_position`, `opus_greennet.set_cover_position` for reference
  runs, and the *Blockage* switch (disabled by default because it stops the
  blind).
- **Window supervision and heating.** HOPPE handles report open, tilted and
  closed. The diagnostic *Open* and *Tilted* binary sensors can be assigned to
  the window-contact inputs of a heating or Cover Control Automation setup.
- **Physical buttons as automation triggers.** Wall rockers and the rocker
  buttons of actuators trigger scenes and other automations in Home Assistant.
- **Safety and presence.** Water leak sensors, smoke alarms and presence
  detectors become binary sensors for notifications. During a gateway outage
  they show *unavailable*, so a lost gateway does not look like "no alarm".
- **Diagnosing a radio installation.** Signal strength (disabled by default) and
  signal quality,
  battery level and the redacted diagnostics download show devices with weak signal
  or low battery. See
  [Troubleshooting](troubleshooting.md).
- **Several gateways.** Each gateway is its own entry. Actions take a
  `config_entry_id` when more than one gateway is loaded.
