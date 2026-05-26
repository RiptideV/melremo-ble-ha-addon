"""Configuration and runtime models for the MELRemo BLE Bridge."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .protocol import FAN_NAME_TO_REQUEST_VALUE, FAN_REQUEST_VALUE_TO_NAME, Status, validate_pin


@dataclass
class UnitDefaults:
    power: bool = False
    mode: str = "cool"
    target_temp: float = 25.0
    fan: str = "auto"
    vane: int = 3

    @property
    def fan_value(self) -> int:
        return FAN_NAME_TO_REQUEST_VALUE.get(self.fan, 4)


@dataclass
class UnitConfig:
    id: str
    name: str
    address: str
    pin: str
    defaults: UnitDefaults = field(default_factory=UnitDefaults)

    def __post_init__(self) -> None:
        self.pin = validate_pin(self.pin)


@dataclass
class MqttConfig:
    discovery_prefix: str = "homeassistant"
    base_topic: str = "melremo"
    host: str = ""
    port: int = 1883
    username: str = ""
    password: str = ""


@dataclass
class AppConfig:
    mqtt: MqttConfig
    poll_interval: int
    command_timeout: int
    global_ble_concurrency: int
    publish_raw_diagnostics: bool
    log_level: str
    units: list[UnitConfig]


@dataclass
class UnitState:
    power: bool
    mode: str
    mode_value: int
    target_temp: float
    current_temperature: Optional[float]
    fan: str
    fan_value: int
    vane: int
    available: bool = False
    last_error: str = ""
    raw_status: str = ""

    @classmethod
    def from_config(cls, unit: UnitConfig) -> "UnitState":
        return cls(
            power=unit.defaults.power,
            mode=unit.defaults.mode,
            mode_value=1,
            target_temp=unit.defaults.target_temp,
            current_temperature=None,
            fan=unit.defaults.fan,
            fan_value=unit.defaults.fan_value,
            vane=unit.defaults.vane,
        )

    def update_from_status(self, status: Status) -> None:
        self.power = status.power
        self.mode = status.mode
        self.mode_value = status.mode_value
        if status.target_temp is not None:
            self.target_temp = status.target_temp
        self.current_temperature = status.room_temperature
        self.fan_value = status.fan_value
        self.fan = FAN_REQUEST_VALUE_TO_NAME.get(status.fan_value, status.fan)
        self.available = True
        self.last_error = ""
        self.raw_status = status.raw

    @property
    def hvac_mode(self) -> str:
        return self.mode if self.power else "off"

    def attributes(self, *, include_raw: bool = False) -> dict[str, object]:
        attrs: dict[str, object] = {
            "mode_value": self.mode_value,
            "fan_value": self.fan_value,
            "vane": self.vane,
            "last_error": self.last_error,
        }
        if include_raw:
            attrs["raw_status"] = self.raw_status
        return attrs


@dataclass
class Command:
    type: str
    value: object = None
