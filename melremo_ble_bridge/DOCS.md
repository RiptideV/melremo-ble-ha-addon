# MELRemo BLE Bridge

Expose Mitsubishi MELRemo BLE controllers as Home Assistant MQTT climate entities.

This add-on communicates directly with MELRemo controllers over BLE using the reverse-engineered MELRemo/Gemini frame protocol and publishes Home Assistant MQTT Discovery payloads.

## Requirements

- Home Assistant OS or Supervised install with Bluetooth available to the host.
- MQTT integration and broker. The Mosquitto broker add-on is recommended.
- One configured MELRemo unit per AC/controller, each with its own BLE address and PIN.

## Finding the BLE MAC address

The add-on should be configured with the controller BLE address in `units[].address`.

On the Home Assistant host, SSH add-on terminal, or another Linux machine with Bluetooth, scan with `bluetoothctl`:

```bash
bluetoothctl scan on
```

Wait for nearby devices to appear, then list discovered devices:

```bash
bluetoothctl devices
```

You may see a controller advertised with a friendly name such as:

```text
Device AA:BB:CC:DD:EE:FF M/R_OFFICE
```

Inspect it if needed:

```bash
bluetoothctl info AA:BB:CC:DD:EE:FF
```

Use the MAC address, for example `AA:BB:CC:DD:EE:FF`, as `units[].address`.

### Can I use the friendly AC name instead?

Use `units[].name` for the friendly Home Assistant climate/device name, for example `Office AC`.

Do not rely on the BLE friendly name for connection. BLE names can be missing, duplicated, localized, or changed, and Home Assistant/Linux BLE APIs connect most reliably by MAC address. The add-on currently requires `units[].address` for each unit.

During development on macOS, BLE APIs may show a CoreBluetooth UUID instead of a MAC address. Home Assistant OS/Supervised on Linux normally needs the BLE MAC address.

## Configuration

Example with two units:

```yaml
mqtt:
  discovery_prefix: homeassistant
  base_topic: melremo
  # Usually leave these blank; the add-on reads the MQTT service details
  # from the Supervisor when services: mqtt:need is available.
  host: ""
  port: 1883
  username: ""
  password: ""
poll_interval: 60
command_timeout: 20
global_ble_concurrency: 1
publish_raw_diagnostics: false
log_level: info
units:
  - id: office
    name: Office AC
    address: 00:11:22:33:44:55
    pin: "1234"
    defaults:
      power: false
      mode: cool
      target_temp: 25.0
      fan: auto
      vane: 3
  - id: bedroom
    name: Bedroom AC
    address: AA:BB:CC:DD:EE:FF
    pin: "5678"
    defaults:
      power: false
      mode: cool
      target_temp: 24.0
      fan: middle
      vane: 3
```

### Options

| Option | Description |
|---|---|
| `mqtt.discovery_prefix` | MQTT Discovery prefix, usually `homeassistant`. |
| `mqtt.base_topic` | Base topic for commands and state, default `melremo`. |
| `mqtt.host` / `port` / `username` / `password` | Optional explicit broker settings. Leave blank to use Supervisor MQTT service details. |
| `poll_interval` | Seconds between status polls. |
| `command_timeout` | BLE command timeout in seconds. |
| `global_ble_concurrency` | Maximum simultaneous BLE sessions. Use `1` for best reliability. |
| `publish_raw_diagnostics` | Publish retained raw status frames to MQTT diagnostics. Disabled by default to reduce data exposure. |
| `units[].id` | Stable identifier used in MQTT topics and unique IDs. |
| `units[].name` | Friendly climate entity/device name. |
| `units[].address` | BLE MAC address on Home Assistant/Linux, discovered with `bluetoothctl`. During development on macOS this may be a CoreBluetooth UUID. |
| `units[].pin` | Four-digit/four-nibble MELRemo PIN for that controller. |
| `units[].defaults` | Fallback state used before the first successful status poll. |

## MQTT entities

For each unit, the add-on publishes one MQTT climate discovery payload:

```text
homeassistant/climate/melremo_<unit_id>/config
```

State and command topics use:

```text
melremo/<unit_id>/climate/mode/set
melremo/<unit_id>/climate/mode/state
melremo/<unit_id>/climate/target_temperature/set
melremo/<unit_id>/climate/target_temperature/state
melremo/<unit_id>/climate/fan_mode/set
melremo/<unit_id>/climate/fan_mode/state
melremo/<unit_id>/climate/current_temperature/state
melremo/<unit_id>/availability
```

## Supported commands

Initial version supports:

- HVAC mode `off`
- HVAC mode `cool` (implemented as power on while preserving current mode value)
- target temperature in Celsius, 16.0–31.0°C range enforced, 0.5°C step expected
- fan modes:
  - `auto`
  - `silent`
  - `low`
  - `middle`
  - `high`
  - `high-power`
  - `rapid`

## Known limitations

- Only `off` and `cool` are exposed as Home Assistant HVAC modes until additional MELRemo mode mappings are captured and verified.
- Current room temperature is not separately decoded yet; the add-on publishes the decoded target/set temperature as `current_temperature` for now.
- BLE access inside Home Assistant containers can depend on host Bluetooth/BlueZ and DBus permissions. This add-on uses `host_dbus: true` and disables AppArmor initially for compatibility. Disabling AppArmor increases container privileges; replace it with a tighter profile when BLE permissions are fully characterized.
- Long-lived authenticated sessions are not assumed. The add-on reconnects and sends the login prelude for each status/command sequence.

## Troubleshooting

1. Set `log_level: debug`.
2. Confirm the MQTT broker is running and the MQTT integration is enabled in Home Assistant.
3. Confirm the BLE address is reachable from the Home Assistant host.
4. Confirm the PIN is correct for each controller.
5. Start with one unit and `global_ble_concurrency: 1`.

Diagnostic topics:

```text
melremo/<unit_id>/diagnostic/last_error
melremo/<unit_id>/diagnostic/raw_status   # only when publish_raw_diagnostics=true
```

## Security

- The PIN is stored in Home Assistant add-on options and may be included in backups. Treat Home Assistant backups as sensitive.
- Leave explicit MQTT credentials blank when possible so the add-on uses the Supervisor MQTT service details.
- Anyone with publish access to `melremo/<unit_id>/climate/+/set` can control that AC. Use broker ACLs if your MQTT broker is shared.
- Debug logs intentionally do not print raw BLE write frames because login frames encode the PIN.
- Raw status diagnostics are disabled by default. If enabled, retained MQTT messages expose detailed AC state.
