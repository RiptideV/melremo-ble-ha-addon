"""BLE session management for MELRemo controllers."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from bleak import BleakClient

from .models import UnitConfig, UnitState
from .protocol import (
    FAN_NAME_TO_REQUEST_VALUE,
    HVAC_MODE_TO_UNITMODE,
    MELREMO_NOTIFY_CHAR,
    MELREMO_WRITE_CHAR,
    Status,
    build_static_login_frames,
    build_static_operation_frame,
    build_static_status_request_frame,
    chunk_frame,
    is_valid_melremo_frame,
    parse_status_frame,
)

_LOGGER = logging.getLogger(__name__)

OPERATION_SUCCESS_FRAME = bytes.fromhex("09 00 0b 05 00 01 01 00 00 1b 00")
OPERATION_SUCCESS_BODY = OPERATION_SUCCESS_FRAME[3:-2]


def is_operation_success_frame(frame: bytes) -> bool:
    """Return true for operation-success responses with any sequence number.

    Confirmed success example for request sequence 3:
      09 00 0b 05 00 01 01 00 00 1b 00

    Byte 2 contains response direction + sequence, so it varies with the
    request sequence. The checksum varies accordingly.
    """
    return (
        len(frame) == len(OPERATION_SUCCESS_FRAME)
        and is_valid_melremo_frame(frame)
        and frame[:2] == OPERATION_SUCCESS_FRAME[:2]
        and (frame[2] & 0x08) == 0x08
        and (frame[2] & 0xF0) == 0
        and frame[3:-2] == OPERATION_SUCCESS_BODY
    )


class MelremoBleError(RuntimeError):
    """Raised when a MELRemo BLE operation fails."""


class MelremoBleClient:
    """Short-lived BLE client for one configured MELRemo unit.

    The proven flow is conservative: connect, subscribe, send the 3-frame login
    prelude, perform one operation/status sequence, then disconnect.
    """

    def __init__(
        self,
        unit: UnitConfig,
        *,
        command_timeout: int = 20,
        semaphore: Optional[asyncio.Semaphore] = None,
        chunk_size: int = 20,
        chunk_delay: float = 0.05,
        prelude_delay: float = 0.15,
    ) -> None:
        self.unit = unit
        self.command_timeout = command_timeout
        self.semaphore = semaphore or asyncio.Semaphore(1)
        self.chunk_size = chunk_size
        self.chunk_delay = chunk_delay
        self.prelude_delay = prelude_delay

    async def poll_status(self) -> Status:
        async with self.semaphore:
            return await asyncio.wait_for(self._poll_status(), timeout=self.command_timeout)

    async def apply(
        self,
        state: UnitState,
        *,
        power: Optional[bool] = None,
        target_temp: Optional[float] = None,
        fan: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> Status:
        async with self.semaphore:
            return await asyncio.wait_for(
                self._apply(state, power=power, target_temp=target_temp, fan=fan, mode=mode),
                timeout=self.command_timeout,
            )

    async def _connect(self) -> tuple[BleakClient, asyncio.Queue[bytes]]:
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        client = BleakClient(self.unit.address, timeout=self.command_timeout)
        await client.connect()
        if not client.is_connected:
            raise MelremoBleError(f"failed to connect to {self.unit.id} at {self.unit.address}")

        def on_notify(_sender: object, data: bytearray) -> None:
            queue.put_nowait(bytes(data))

        await client.start_notify(MELREMO_NOTIFY_CHAR, on_notify)
        return client, queue

    async def _poll_status(self) -> Status:
        client, queue = await self._connect()
        try:
            await self._login(client, queue)
            return await self._request_status(client, queue, seq_no=3)
        finally:
            await self._safe_disconnect(client)

    async def _apply(
        self,
        state: UnitState,
        *,
        power: Optional[bool],
        target_temp: Optional[float],
        fan: Optional[str],
        mode: Optional[str],
    ) -> Status:
        client, queue = await self._connect()
        try:
            await self._login(client, queue)
            context = await self._request_status_or_none(client, queue, seq_no=3)

            current_power = context.power if context else state.power
            current_mode = context.mode_value if context else state.mode_value
            current_temp = context.target_temp if context and context.target_temp is not None else state.target_temp
            current_fan = context.fan_value if context else state.fan_value

            requested_power = power
            requested_unitmode: Optional[int] = None
            if mode is not None:
                if mode == "off":
                    requested_power = False
                else:
                    if mode not in HVAC_MODE_TO_UNITMODE:
                        raise MelremoBleError(f"unsupported HVAC mode: {mode}")
                    requested_power = True
                    requested_unitmode = HVAC_MODE_TO_UNITMODE[mode]

            fan_speed: Optional[int] = None
            if fan is not None:
                fan_name = "medium" if fan == "middle" else fan
                fan_name = "quiet" if fan_name == "silent" else fan_name
                if fan_name not in FAN_NAME_TO_REQUEST_VALUE:
                    raise MelremoBleError(f"unsupported fan mode: {fan}")
                fan_speed = FAN_NAME_TO_REQUEST_VALUE[fan_name]

            operation = build_static_operation_frame(
                seq_no=4,
                power=requested_power,
                temp_c=target_temp,
                temp_slot=self._temp_slot_for_mode(context.mode if context else state.mode),
                fan_speed=fan_speed,
                unitmode=requested_unitmode,
                current_power=current_power,
                current_mode=current_mode,
                current_temp_c=current_temp,
                current_fan=current_fan,
                current_vane=state.vane,
            )
            await self._write_frame(client, operation)
            await self._wait_for_operation_success(queue, timeout=3.0)
            return await self._request_status(client, queue, seq_no=5)
        finally:
            await self._safe_disconnect(client)

    @staticmethod
    def _temp_slot_for_mode(mode: str) -> str:
        if mode == "heat":
            return "heat"
        if mode == "auto":
            return "auto"
        return "cool"

    async def _login(self, client: BleakClient, queue: asyncio.Queue[bytes]) -> None:
        for frame in build_static_login_frames(self.unit.pin, seq_no=0):
            await self._write_frame(client, frame)
            if self.prelude_delay:
                await asyncio.sleep(self.prelude_delay)
        self._drain_queue(queue)

    async def _request_status_or_none(self, client: BleakClient, queue: asyncio.Queue[bytes], *, seq_no: int) -> Optional[Status]:
        try:
            return await self._request_status(client, queue, seq_no=seq_no)
        except Exception as err:  # noqa: BLE status fallback is intentional
            _LOGGER.warning("status pre-read failed for %s: %s", self.unit.id, err)
            return None

    async def _request_status(self, client: BleakClient, queue: asyncio.Queue[bytes], *, seq_no: int) -> Status:
        self._drain_queue(queue)
        await self._write_frame(client, build_static_status_request_frame(seq_no=seq_no))
        return await self._wait_for_status(queue, timeout=3.0)

    async def _write_frame(self, client: BleakClient, frame: bytes) -> None:
        # Do not log raw write frames: login frames contain the controller PIN
        # encoded as reversed BCD nibbles.
        _LOGGER.debug("%s write frame len=%d", self.unit.id, len(frame))
        for chunk in chunk_frame(frame, self.chunk_size):
            await client.write_gatt_char(MELREMO_WRITE_CHAR, chunk, response=False)
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)

    async def _wait_for_operation_success(self, queue: asyncio.Queue[bytes], timeout: float) -> None:
        frames_seen = 0
        async for frame in self._iter_notification_frames(queue, timeout):
            frames_seen += 1
            if is_operation_success_frame(frame):
                _LOGGER.debug("%s operation success response received", self.unit.id)
                return
            _LOGGER.debug("%s ignoring non-success operation response len=%d", self.unit.id, len(frame))
        raise MelremoBleError(
            f"operation success response not received from {self.unit.id}; received {frames_seen} frame(s)"
        )

    async def _wait_for_status(self, queue: asyncio.Queue[bytes], timeout: float) -> Status:
        frames_seen = 0
        async for frame in self._iter_notification_frames(queue, timeout):
            frames_seen += 1
            status = parse_status_frame(frame)
            if status is not None:
                return status
            if is_operation_success_frame(frame):
                _LOGGER.debug("%s ignoring operation success while waiting for status", self.unit.id)
            else:
                _LOGGER.debug("%s ignoring non-status response len=%d", self.unit.id, len(frame))
        raise MelremoBleError(
            f"no decodable status response from {self.unit.id}; received {frames_seen} frame(s)"
        )

    async def _iter_notification_frames(self, queue: asyncio.Queue[bytes], timeout: float):
        buf = bytearray()
        expected: Optional[int] = None
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                data = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not data:
                continue
            if not buf:
                if len(data) < 2:
                    continue
                expected = int.from_bytes(data[:2], "little") + 2
            buf.extend(data)
            while expected is not None and len(buf) >= expected:
                frame = bytes(buf[:expected])
                if is_valid_melremo_frame(frame):
                    yield frame
                else:
                    _LOGGER.warning("%s invalid notification frame len=%d", self.unit.id, len(frame))
                extra = buf[expected:]
                buf = bytearray(extra)
                expected = int.from_bytes(buf[:2], "little") + 2 if len(buf) >= 2 else None
        if buf:
            _LOGGER.debug("%s leftover partial notification len=%d", self.unit.id, len(buf))

    @staticmethod
    def _drain_queue(queue: asyncio.Queue[bytes]) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    @staticmethod
    async def _safe_disconnect(client: BleakClient) -> None:
        try:
            if client.is_connected:
                try:
                    await client.stop_notify(MELREMO_NOTIFY_CHAR)
                except Exception:  # noqa: best-effort cleanup
                    pass
                await client.disconnect()
        except Exception:  # noqa: best-effort cleanup
            pass
