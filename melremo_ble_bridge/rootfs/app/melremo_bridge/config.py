"""Add-on option loading."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .models import AppConfig, MqttConfig, UnitConfig, UnitDefaults
from .protocol import FAN_NAME_TO_REQUEST_VALUE

OPTIONS_PATH = Path(os.environ.get("MELREMO_OPTIONS", "/data/options.json"))


class ConfigError(ValueError):
    """Raised for invalid add-on options."""


def _require_unit_id(value: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        raise ConfigError(f"invalid unit id: {value!r}")
    return value


def _defaults_from_dict(data: dict[str, Any] | None) -> UnitDefaults:
    data = data or {}
    fan = str(data.get("fan", "auto"))
    if fan == "medium":
        fan = "middle"
    if fan not in FAN_NAME_TO_REQUEST_VALUE:
        raise ConfigError(f"unsupported fan default: {fan}")
    return UnitDefaults(
        power=bool(data.get("power", False)),
        mode=str(data.get("mode", "cool")),
        target_temp=float(data.get("target_temp", 25.0)),
        fan=fan,
        vane=int(data.get("vane", 3)),
    )


def load_options(path: Path = OPTIONS_PATH) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"options file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_config(options: dict[str, Any]) -> AppConfig:
    mqtt_data = options.get("mqtt", {}) or {}
    mqtt = MqttConfig(
        discovery_prefix=str(mqtt_data.get("discovery_prefix", "homeassistant")).strip("/"),
        base_topic=str(mqtt_data.get("base_topic", "melremo")).strip("/"),
        host=str(mqtt_data.get("host", "") or ""),
        port=int(mqtt_data.get("port", 1883) or 1883),
        username=str(mqtt_data.get("username", "") or ""),
        password=str(mqtt_data.get("password", "") or ""),
    )

    units: list[UnitConfig] = []
    seen_ids: set[str] = set()
    for item in options.get("units", []) or []:
        unit_id = _require_unit_id(item.get("id", ""))
        if unit_id in seen_ids:
            raise ConfigError(f"duplicate unit id: {unit_id}")
        seen_ids.add(unit_id)
        units.append(
            UnitConfig(
                id=unit_id,
                name=str(item.get("name") or unit_id),
                address=str(item.get("address") or "").strip(),
                pin=str(item.get("pin") or ""),
                defaults=_defaults_from_dict(item.get("defaults")),
            )
        )
    if not units:
        raise ConfigError("configure at least one MELRemo unit")

    return AppConfig(
        mqtt=mqtt,
        poll_interval=int(options.get("poll_interval", 60)),
        command_timeout=int(options.get("command_timeout", 20)),
        global_ble_concurrency=int(options.get("global_ble_concurrency", 1)),
        publish_raw_diagnostics=bool(options.get("publish_raw_diagnostics", False)),
        log_level=str(options.get("log_level", "info")),
        units=units,
    )


def load_config(path: Path = OPTIONS_PATH) -> AppConfig:
    return parse_config(load_options(path))
