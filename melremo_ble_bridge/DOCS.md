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
  - id: bedroom
    name: Bedroom AC
    address: AA:BB:CC:DD:EE:FF
    pin: "5678"
```

### Required settings

At least one unit is required. For each unit, these fields are required:

| Option | Description |
|---|---|
| `units[].id` | Stable identifier used in MQTT topics and unique IDs. Use letters, numbers, `_`, or `-`. |
| `units[].name` | Friendly Home Assistant climate/device name. |
| `units[].address` | BLE MAC address on Home Assistant/Linux, discovered with `bluetoothctl`. During development on macOS this may be a CoreBluetooth UUID. |
| `units[].pin` | Four-digit/four-nibble MELRemo PIN for that controller. Quote it, e.g. `"1234"`. |

Minimal configuration:

```yaml
units:
  - id: office
    name: Office AC
    address: AA:BB:CC:DD:EE:FF
    pin: "1234"
```

### Optional settings

All other settings are optional and have defaults.

| Option | Default | Description |
|---|---:|---|
| `mqtt.discovery_prefix` | `homeassistant` | MQTT Discovery prefix. |
| `mqtt.base_topic` | `melremo` | Base topic for commands and state. |
| `mqtt.host` | empty | Optional explicit broker host. Leave blank to use Supervisor MQTT service details. |
| `mqtt.port` | `1883` | Optional explicit broker port. |
| `mqtt.username` | empty | Optional explicit broker username. |
| `mqtt.password` | empty | Optional explicit broker password. |
| `poll_interval` | `60` | Seconds between status polls. |
| `command_timeout` | `20` | BLE command timeout in seconds. |
| `global_ble_concurrency` | `1` | Maximum simultaneous BLE sessions. Use `1` for best reliability. |
| `publish_raw_diagnostics` | `false` | Publish retained raw status frames to MQTT diagnostics. Disabled by default to reduce data exposure. |
| `log_level` | `info` | Add-on log level. |

Internal fallback state before the first successful status poll:

| Value | Default |
|---|---:|
| power | `false` |
| mode | `cool` |
| target temperature | `25.0` |
| fan | `auto` |
| vane | `3` |

Note: `units[].defaults` is intentionally not exposed in the Supervisor add-on schema. Home Assistant Supervisor treats nested keys inside list items as required, which prevents saving a minimal unit config. The add-on runtime still uses the internal fallback values above until it polls the real controller status.

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

Supported HVAC modes match the core user-facing modes:

- `off`
- `auto`
- `heat`
- `cool`
- `dry`
- `fan_only` / Fan

Mode commands use the MELRemo operation-byte mapping:

```text
off      -> power=false, keep current unitmode
fan_only -> power=true,  unitmode=0  byte 0x01
cool     -> power=true,  unitmode=1  byte 0x09
heat     -> power=true,  unitmode=2  byte 0x11
dry      -> power=true,  unitmode=6  byte 0x31
auto     -> power=true,  unitmode=15 byte 0x79
```

Other supported commands:

- target temperature in Celsius, 16.0–31.0°C range enforced, 0.5°C step expected
- fan modes:
  - `auto`
  - `high`
  - `medium`
  - `low`
  - `quiet`

## Known limitations

- Vane/swing is intentionally not exposed yet.
- Current room temperature is decoded from the status frame and published as `current_temperature`.
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
