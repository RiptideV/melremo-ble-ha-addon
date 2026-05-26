"""MQTT Discovery, command subscriptions, and state publishing."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

import aiohttp
import paho.mqtt.client as mqtt

from .models import AppConfig, Command, UnitConfig, UnitState

_LOGGER = logging.getLogger(__name__)


class MqttError(RuntimeError):
    """Raised when MQTT setup fails."""


class MqttBridge:
    def __init__(self, config: AppConfig, command_queues: dict[str, asyncio.Queue[Command]], loop: asyncio.AbstractEventLoop) -> None:
        self.config = config
        self.command_queues = command_queues
        self.loop = loop
        self.client = mqtt.Client(client_id="melremo-ble-bridge")
        self.connected = asyncio.Event()
        self._host = ""
        self._port = 1883

    async def start(self) -> None:
        broker = await self._resolve_broker()
        self._host = broker["host"]
        self._port = int(broker.get("port", 1883))
        username = broker.get("username") or ""
        password = broker.get("password") or ""
        if username or password:
            self.client.username_pw_set(username, password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        _LOGGER.info("connecting to MQTT broker %s:%s", self._host, self._port)
        await asyncio.to_thread(self.client.connect, self._host, self._port, 60)
        self.client.loop_start()
        await asyncio.wait_for(self.connected.wait(), timeout=15)

    async def stop(self) -> None:
        try:
            self.client.loop_stop()
            await asyncio.to_thread(self.client.disconnect)
        except Exception:  # noqa: best-effort shutdown
            pass

    async def publish_discovery(self, units: list[UnitConfig]) -> None:
        for unit in units:
            topic = f"{self.config.mqtt.discovery_prefix}/climate/melremo_{unit.id}/config"
            payload = self._discovery_payload(unit)
            self.publish_json(topic, payload, retain=True)

    def publish_state(self, unit: UnitConfig, state: UnitState) -> None:
        base = self.unit_topic(unit.id)
        self.publish(f"{base}/availability", "online" if state.available else "offline", retain=True)
        self.publish(f"{base}/climate/mode/state", state.hvac_mode, retain=True)
        self.publish(f"{base}/climate/target_temperature/state", f"{state.target_temp:.1f}", retain=True)
        self.publish(f"{base}/climate/fan_mode/state", state.fan, retain=True)
        if state.current_temperature is not None:
            self.publish(f"{base}/climate/current_temperature/state", f"{state.current_temperature:.1f}", retain=True)
        self.publish_json(
            f"{base}/climate/attributes",
            state.attributes(include_raw=self.config.publish_raw_diagnostics),
            retain=True,
        )
        if state.last_error:
            self.publish(f"{base}/diagnostic/last_error", state.last_error, retain=True)
        if self.config.publish_raw_diagnostics and state.raw_status:
            self.publish(f"{base}/diagnostic/raw_status", state.raw_status, retain=True)

    def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        _LOGGER.debug("mqtt publish %s: %s", topic, payload)
        self.client.publish(topic, payload, qos=0, retain=retain)

    def publish_json(self, topic: str, payload: dict[str, object], *, retain: bool = False) -> None:
        self.publish(topic, json.dumps(payload, separators=(",", ":")), retain=retain)

    def unit_topic(self, unit_id: str) -> str:
        return f"{self.config.mqtt.base_topic}/{unit_id}"

    def _discovery_payload(self, unit: UnitConfig) -> dict[str, object]:
        base = self.unit_topic(unit.id)
        return {
            "name": unit.name,
            "unique_id": f"melremo_{unit.id}_climate",
            "object_id": f"melremo_{unit.id}",
            "availability_topic": f"{base}/availability",
            "mode_command_topic": f"{base}/climate/mode/set",
            "mode_state_topic": f"{base}/climate/mode/state",
            "temperature_command_topic": f"{base}/climate/target_temperature/set",
            "temperature_state_topic": f"{base}/climate/target_temperature/state",
            "fan_mode_command_topic": f"{base}/climate/fan_mode/set",
            "fan_mode_state_topic": f"{base}/climate/fan_mode/state",
            "current_temperature_topic": f"{base}/climate/current_temperature/state",
            "json_attributes_topic": f"{base}/climate/attributes",
            "modes": ["off", "cool"],
            "fan_modes": ["auto", "silent", "low", "middle", "high", "high-power", "rapid"],
            "min_temp": 16,
            "max_temp": 31,
            "temp_step": 0.5,
            "temperature_unit": "C",
            "device": {
                "identifiers": [f"melremo_{unit.id}"],
                "name": unit.name,
                "manufacturer": "Mitsubishi Electric",
                "model": "MELRemo BLE Controller",
            },
        }

    def _on_connect(self, _client: mqtt.Client, _userdata: object, _flags: dict[str, object], rc: int) -> None:
        if rc != 0:
            _LOGGER.error("MQTT connection failed rc=%s", rc)
            return
        _LOGGER.info("MQTT connected")
        for unit_id in self.command_queues:
            base = self.unit_topic(unit_id)
            self.client.subscribe(f"{base}/climate/mode/set")
            self.client.subscribe(f"{base}/climate/target_temperature/set")
            self.client.subscribe(f"{base}/climate/fan_mode/set")
        self.loop.call_soon_threadsafe(self.connected.set)

    def _on_disconnect(self, _client: mqtt.Client, _userdata: object, rc: int) -> None:
        if rc:
            _LOGGER.warning("MQTT disconnected unexpectedly rc=%s", rc)
        else:
            _LOGGER.info("MQTT disconnected")
        self.loop.call_soon_threadsafe(self.connected.clear)

    def _on_message(self, _client: mqtt.Client, _userdata: object, message: mqtt.MQTTMessage) -> None:
        topic = message.topic
        payload = message.payload.decode("utf-8", errors="replace").strip()
        _LOGGER.info("MQTT command %s: %s", topic, payload)
        try:
            command = self._command_from_message(topic, payload)
        except Exception as err:
            _LOGGER.warning("ignoring invalid MQTT command on %s: %s", topic, err)
            return
        if command is None:
            return
        unit_id, cmd = command
        queue = self.command_queues.get(unit_id)
        if queue is None:
            _LOGGER.warning("command for unknown unit %s", unit_id)
            return
        self.loop.call_soon_threadsafe(queue.put_nowait, cmd)

    def _command_from_message(self, topic: str, payload: str) -> Optional[tuple[str, Command]]:
        prefix = f"{self.config.mqtt.base_topic}/"
        if not topic.startswith(prefix):
            return None
        remainder = topic[len(prefix) :]
        unit_id, _, suffix = remainder.partition("/")
        if suffix == "climate/mode/set":
            value = payload.lower()
            if value == "off":
                return unit_id, Command("power", False)
            if value == "cool":
                return unit_id, Command("power", True)
            raise MqttError(f"unsupported HVAC mode {payload!r}; currently supports off/cool")
        if suffix == "climate/target_temperature/set":
            temp = float(payload)
            if not 16.0 <= temp <= 31.0:
                raise MqttError(f"target temperature {temp} outside supported range 16.0..31.0C")
            return unit_id, Command("temperature", temp)
        if suffix == "climate/fan_mode/set":
            return unit_id, Command("fan", payload.lower())
        return None

    async def _resolve_broker(self) -> dict[str, object]:
        if self.config.mqtt.host:
            return {
                "host": self.config.mqtt.host,
                "port": self.config.mqtt.port,
                "username": self.config.mqtt.username,
                "password": self.config.mqtt.password,
            }

        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise MqttError("no MQTT host configured and SUPERVISOR_TOKEN is unavailable")

        url = "http://supervisor/services/mqtt"
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                if resp.status >= 400 or data.get("result") != "ok":
                    raise MqttError(f"Supervisor MQTT service lookup failed: HTTP {resp.status}")
                broker = data.get("data") or {}
                if not broker.get("host"):
                    raise MqttError("Supervisor MQTT service response did not include host")
                return broker
