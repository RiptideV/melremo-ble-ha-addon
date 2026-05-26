# MELRemo BLE Home Assistant Add-on Repository

This repository contains the `MELRemo BLE Bridge` add-on.

The add-on exposes Mitsubishi MELRemo BLE controllers as Home Assistant MQTT climate entities.

## Finding the BLE MAC address

On the Home Assistant host or another Linux machine with Bluetooth:

```bash
bluetoothctl scan on
bluetoothctl devices
bluetoothctl info AA:BB:CC:DD:EE:FF
```

Look for a MELRemo/Mitsubishi controller name such as `M/R_OFFICE`, then copy its BLE MAC address into `units[].address`.

The configured `units[].name` is the friendly Home Assistant climate/device name. It does **not** replace `units[].address`; BLE control should use the MAC address because friendly names may be duplicated, absent, or change over time.

Minimal add-on configuration:

```yaml
units:
  - id: office
    name: Office AC
    address: AA:BB:CC:DD:EE:FF
    pin: "0000"
```

All other settings are optional. Per-unit `defaults` are not exposed in the Supervisor schema; the add-on uses internal fallback values until it polls the controller. See [`melremo_ble_bridge/DOCS.md`](melremo_ble_bridge/DOCS.md) for full configuration and usage.
