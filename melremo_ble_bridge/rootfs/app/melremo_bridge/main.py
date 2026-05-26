"""MELRemo BLE Bridge add-on entrypoint."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .ble import MelremoBleClient
from .config import ConfigError, load_config
from .models import AppConfig, Command, UnitConfig, UnitState
from .mqtt import MqttBridge

_LOGGER = logging.getLogger(__name__)


LOG_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}


class UnitWorker:
    def __init__(
        self,
        unit: UnitConfig,
        config: AppConfig,
        queue: asyncio.Queue[Command],
        mqtt: MqttBridge,
        ble_semaphore: asyncio.Semaphore,
        stop_event: asyncio.Event,
    ) -> None:
        self.unit = unit
        self.config = config
        self.queue = queue
        self.mqtt = mqtt
        self.stop_event = stop_event
        self.state = UnitState.from_config(unit)
        self.ble = MelremoBleClient(
            unit,
            command_timeout=config.command_timeout,
            semaphore=ble_semaphore,
        )

    async def run(self) -> None:
        _LOGGER.info("starting worker for %s (%s)", self.unit.id, self.unit.address)
        self.mqtt.publish_state(self.unit, self.state)
        next_poll = 0.0
        loop = asyncio.get_running_loop()
        while not self.stop_event.is_set():
            now = loop.time()
            if now >= next_poll:
                await self.poll_once()
                next_poll = loop.time() + self.config.poll_interval

            timeout = max(0.0, next_poll - loop.time())
            try:
                command = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            try:
                await self.handle_command(command)
            finally:
                self.queue.task_done()

    async def poll_once(self) -> None:
        try:
            status = await self.ble.poll_status()
            self.state.update_from_status(status)
            _LOGGER.info(
                "%s status: power=%s mode=%s temp=%s fan=%s",
                self.unit.id,
                self.state.power,
                self.state.hvac_mode,
                self.state.target_temp,
                self.state.fan,
            )
        except Exception as err:  # noqa: broad worker boundary
            self.state.available = False
            self.state.last_error = str(err)
            _LOGGER.warning("%s status poll failed: %s", self.unit.id, err)
        self.mqtt.publish_state(self.unit, self.state)

    async def handle_command(self, command: Command) -> None:
        _LOGGER.info("%s handling command %s=%r", self.unit.id, command.type, command.value)
        try:
            if command.type == "power":
                status = await self.ble.apply(self.state, power=bool(command.value))
            elif command.type == "temperature":
                status = await self.ble.apply(self.state, target_temp=float(command.value))
            elif command.type == "fan":
                status = await self.ble.apply(self.state, fan=str(command.value))
            elif command.type == "status":
                status = await self.ble.poll_status()
            else:
                raise ValueError(f"unknown command type: {command.type}")
            self.state.update_from_status(status)
            _LOGGER.info(
                "%s updated: power=%s mode=%s temp=%s fan=%s",
                self.unit.id,
                self.state.power,
                self.state.hvac_mode,
                self.state.target_temp,
                self.state.fan,
            )
        except Exception as err:  # noqa: broad worker boundary
            self.state.available = False
            self.state.last_error = str(err)
            _LOGGER.warning("%s command failed: %s", self.unit.id, err)
        self.mqtt.publish_state(self.unit, self.state)


async def async_main() -> None:
    config = load_config()
    logging.getLogger().setLevel(LOG_LEVELS.get(config.log_level, logging.INFO))
    _LOGGER.info("loaded %d MELRemo unit(s)", len(config.units))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    command_queues: dict[str, asyncio.Queue[Command]] = {unit.id: asyncio.Queue() for unit in config.units}
    mqtt = MqttBridge(config, command_queues, loop)
    await mqtt.start()
    await mqtt.publish_discovery(config.units)

    ble_semaphore = asyncio.Semaphore(config.global_ble_concurrency)
    workers = [
        UnitWorker(unit, config, command_queues[unit.id], mqtt, ble_semaphore, stop_event)
        for unit in config.units
    ]
    tasks = [asyncio.create_task(worker.run(), name=f"unit:{worker.unit.id}") for worker in workers]

    try:
        await stop_event.wait()
    finally:
        _LOGGER.info("stopping MELRemo BLE Bridge")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await mqtt.stop()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )


def main() -> None:
    setup_logging()
    try:
        asyncio.run(async_main())
    except ConfigError as err:
        _LOGGER.error("configuration error: %s", err)
        raise SystemExit(1) from err
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
