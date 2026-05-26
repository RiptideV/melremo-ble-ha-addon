# Changelog

## 0.1.2

- Assume command response as a successful operation response.
- Poll status immediately after the operation success response is received.
- Return as soon as a valid status frame is decoded instead of waiting out the full status timeout.
- Decode room/current temperature from the status frame instead of mirroring the setpoint.
- Use the device name as the primary climate entity name to avoid duplicated names like `Office AC Office AC`.

## 0.1.1

- Removed `units[].defaults` from the Supervisor add-on schema/default options so minimal unit configs can be saved.
- Runtime still uses internal fallback defaults until the first successful controller status poll.

## 0.1.0

- Initial MELRemo BLE Bridge add-on skeleton.
- Supports multiple configured AC units, each with its own BLE address and PIN.
- Publishes MQTT Discovery climate entities.
- Implements status polling plus power, target temperature, and fan mode commands.
- Hardened logging so raw BLE write frames containing PIN-derived login bytes are not printed.
- Added `publish_raw_diagnostics`, disabled by default.
- Added MQTT command-side temperature bounds checking.
- Removed build dependencies from the final add-on image after Python package installation.
