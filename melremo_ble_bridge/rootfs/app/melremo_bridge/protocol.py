"""MELRemo/Gemini BLE protocol helpers.

This module contains the reverse-engineered frame builders and parsers proven
with MELRemo app 4.7.0 captures. Bit fields are LSB-first within each byte.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

MELREMO_SERVICE_UUID = "0277df18-e796-11e6-bf01-fe55135034f3"
MELREMO_READ_CHARS = [
    "799e3b22-e797-11e6-bf01-fe55135034f3",
    "def9382a-e795-11e6-bf01-fe55135034f3",
]
MELREMO_WRITE_CHAR = "e48c1528-e795-11e6-bf01-fe55135034f3"
MELREMO_NOTIFY_CHAR = "ea1ea690-e795-11e6-bf01-fe55135034f3"

FAN_NAME_TO_REQUEST_VALUE = {
    "auto": 4,
    "silent": 0,
    "low": 1,
    "middle": 2,
    "medium": 2,
    "high": 3,
    "high-power": 8,
    "rapid": 9,
}
FAN_REQUEST_VALUE_TO_NAME = {
    0: "silent",
    1: "low",
    2: "middle",
    3: "high",
    4: "auto",
    8: "high-power",
    9: "rapid",
}

# Only cool/off are exposed initially. Other values are kept as raw integers
# until mode-setting/status captures are confirmed.
MODE_VALUE_TO_NAME = {
    1: "cool",
}


class ProtocolError(ValueError):
    """Raised when a MELRemo frame cannot be encoded or decoded."""


@dataclass
class Status:
    raw: str
    seq: int
    direction: int
    power: bool
    power_raw: int
    mode_value: int
    mode: str
    temps: dict[str, Optional[float]]
    fan_value: int
    fan: str
    fan_raw_byte: int

    @property
    def hvac_mode(self) -> str:
        return self.mode if self.power else "off"

    @property
    def target_temp(self) -> Optional[float]:
        return self.temps.get("cool")

    def as_attributes(self, *, include_raw: bool = False) -> dict[str, object]:
        attrs: dict[str, object] = {
            "seq": self.seq,
            "direction": self.direction,
            "power_raw": self.power_raw,
            "mode_value": self.mode_value,
            "fan_value": self.fan_value,
            "fan_raw_byte": self.fan_raw_byte,
            "temps": self.temps,
        }
        if include_raw:
            attrs["raw_status"] = self.raw
        return attrs


class BitWriter:
    """LSB-first bit writer matching captured MELRemo/Gemini frames."""

    def __init__(self) -> None:
        self._bits: list[int] = []

    def write(self, value: int | bool, width: int) -> None:
        if width < 0:
            raise ProtocolError("negative bit width")
        ivalue = int(value)
        if ivalue < 0 or ivalue >= (1 << width):
            raise ProtocolError(f"value {ivalue} does not fit in {width} bits")
        for shift in range(width):
            self._bits.append((ivalue >> shift) & 1)

    def bytes(self) -> bytes:
        out = bytearray((len(self._bits) + 7) // 8)
        for i, bit in enumerate(self._bits):
            if bit:
                out[i // 8] |= 1 << (i % 8)
        return bytes(out)


def validate_pin(pin: str) -> str:
    pin = str(pin).strip()
    if not re.fullmatch(r"[0-9A-Fa-f]{4}", pin):
        raise ProtocolError("MELRemo PIN must be four hex/decimal digits")
    return pin.upper()


def encode_melremo_temp(temp_c: float) -> bytes:
    """Encode q/E set-temperature value; 25.5C becomes 55 02."""
    tenths = int(round(abs(temp_c) * 10))
    if tenths > 9999:
        raise ProtocolError("temperature is out of encodable range")
    low1 = tenths % 10
    low2 = (tenths // 10) % 10
    high1 = (tenths // 100) % 10
    high2 = (tenths // 1000) % 10
    sign = 1 if temp_c < 0 else 0
    bw = BitWriter()
    bw.write(low1, 4)
    bw.write(low2, 4)
    bw.write(high1, 4)
    bw.write(high2, 3)
    bw.write(sign, 1)
    return bw.bytes()


def decode_melremo_temp(data: bytes) -> Optional[float]:
    if len(data) != 2:
        return None
    b0, b1 = data
    low1 = b0 & 0x0F
    low2 = (b0 >> 4) & 0x0F
    high1 = b1 & 0x0F
    high2 = (b1 >> 4) & 0x07
    sign = -1 if (b1 & 0x80) else 1
    digits = [low1, low2, high1, high2]
    if any(d > 9 for d in digits):
        return None
    tenths = low1 + low2 * 10 + high1 * 100 + high2 * 1000
    return sign * tenths / 10.0


def build_operation_payload(
    *,
    power: Optional[bool] = None,
    temp_c: Optional[float] = None,
    temp_slot: str = "cool",
    fan_speed: Optional[int] = None,
    current_power: bool = True,
    current_mode: int = 1,
    current_temp_c: float = 25.0,
    current_fan: int = 3,
    current_vane: int = 3,
    current_louver: int = 0,
    current_vent: int = 0,
    current_hold: bool = False,
    current_right_left: int = 0,
    current_move_eye: int = 0,
    current_inner_clean: bool = False,
) -> bytes:
    """Build Lo/b current-operation setting payload (17 bytes)."""
    temp_slots = ["cool", "heat", "auto", "upper-setback", "lower-setback"]
    temp_flags = {slot: False for slot in temp_slots}
    temp_values = {slot: encode_melremo_temp(current_temp_c) for slot in temp_slots}
    if temp_c is not None:
        if temp_slot == "normal":
            selected = ["cool", "heat", "auto"]
        elif temp_slot == "all":
            selected = temp_slots
        elif temp_slot in temp_flags:
            selected = [temp_slot]
        else:
            raise ProtocolError(f"unsupported temp slot: {temp_slot}")
        encoded = encode_melremo_temp(temp_c)
        for slot in selected:
            temp_flags[slot] = True
            temp_values[slot] = encoded

    effective_power = current_power if power is None else power
    effective_fan = current_fan if fan_speed is None else fan_speed

    bw = BitWriter()
    # Update flags.
    bw.write(power is not None, 1)
    bw.write(False, 1)  # unitmode_flag
    bw.write(0, 6)      # reserved01
    bw.write(temp_flags["cool"], 1)
    bw.write(temp_flags["heat"], 1)
    bw.write(temp_flags["auto"], 1)
    bw.write(temp_flags["upper-setback"], 1)
    bw.write(temp_flags["lower-setback"], 1)
    bw.write(0, 3)      # reserved02
    bw.write(fan_speed is not None, 1)
    bw.write(False, 1)  # vane_flag
    bw.write(False, 1)  # louver_flag
    bw.write(False, 1)  # vent_flag
    bw.write(False, 1)  # hold_flag
    bw.write(False, 1)  # right_left_flag
    bw.write(False, 1)  # move_eye_flag
    bw.write(False, 1)  # inner_clean_flag

    # Values.
    bw.write(1 if effective_power else 0, 3)
    bw.write(current_mode, 5)
    settemps = b"".join(temp_values[slot] for slot in temp_slots)
    for byte in settemps:
        bw.write(byte, 8)
    bw.write(effective_fan, 4)
    bw.write(current_vane, 4)
    bw.write(current_louver, 4)
    bw.write(current_vent, 4)
    bw.write(1 if current_hold else 0, 1)
    bw.write(current_right_left, 3)
    bw.write(current_move_eye, 3)
    bw.write(1 if current_inner_clean else 0, 1)

    payload = bw.bytes()
    if len(payload) != 17:
        raise AssertionError(f"operation payload should be 17 bytes, got {len(payload)}")
    return payload


def wrap_l1_frame(l1_payload: bytes) -> bytes:
    """Wrap an L1 payload with MELRemo length/checksum."""
    length = len(l1_payload) + 2
    frame = length.to_bytes(2, "little") + l1_payload
    checksum = sum(frame) & 0xFFFF
    return frame + checksum.to_bytes(2, "little")


def l1_header(seq_no: int) -> bytes:
    if not 0 <= seq_no <= 7:
        raise ProtocolError("seq_no must be in 0..7")
    return bytes([seq_no & 0x07])


def l2_header(first_snd_flg: int, phase_type: int) -> bytes:
    bw = BitWriter()
    bw.write(first_snd_flg, 1)
    bw.write(phase_type, 2)
    bw.write(0, 5)
    return bw.bytes()


def build_l3_header(ctrl_type: int, func_no: int) -> bytes:
    bw = BitWriter()
    bw.write(ctrl_type, 3)
    bw.write(0, 5)
    bw.write(func_no, 8)
    return bw.bytes()


def build_login_user_info(pin: str, session_type: int = 0) -> bytes:
    """Build Li/b USER login info. PIN nibbles are written reversed."""
    pin = validate_pin(pin)
    license_pw = "0000"
    bw = BitWriter()
    bw.write(session_type, 6)
    bw.write(0, 2)
    bw.write(1, 8)  # USER
    for ch in reversed(pin):
        bw.write(int(ch, 16), 4)
    bw.write(0, 8)  # no license type
    for ch in reversed(license_pw):
        bw.write(int(ch, 16), 4)
    out = bw.bytes()
    if len(out) != 7:
        raise AssertionError(f"login user info should be 7 bytes, got {len(out)}")
    return out


def build_static_control_frame(pin: str, *, seq_no: int = 0, phase_type: int = 0, session_type: int = 0) -> bytes:
    l2 = l2_header(1, phase_type) + build_login_user_info(pin, session_type=session_type)
    return wrap_l1_frame(l1_header(seq_no) + l2)


def build_static_login_frames(pin: str, *, seq_no: int = 0) -> list[bytes]:
    """Build the three official-app login prelude frames."""
    return [
        build_static_control_frame(pin, seq_no=seq_no & 7, phase_type=0, session_type=0),
        build_static_control_frame(pin, seq_no=(seq_no + 1) & 7, phase_type=1, session_type=0),
        build_static_control_frame(pin, seq_no=(seq_no + 2) & 7, phase_type=0, session_type=4),
    ]


def build_static_l3_frame(*, seq_no: int, ctrl_type: int, func_no: int, payload: bytes = b"") -> bytes:
    l3 = build_l3_header(ctrl_type, func_no) + payload
    l2 = l2_header(1, 2) + l3
    return wrap_l1_frame(l1_header(seq_no) + l2)


def build_static_operation_frame(
    *,
    seq_no: int = 0,
    power: Optional[bool] = None,
    temp_c: Optional[float] = None,
    temp_slot: str = "cool",
    fan_speed: Optional[int] = None,
    current_power: bool = True,
    current_mode: int = 1,
    current_temp_c: float = 25.0,
    current_fan: int = 3,
    current_vane: int = 3,
    current_louver: int = 0,
    current_vent: int = 0,
    current_hold: bool = False,
    current_right_left: int = 0,
    current_move_eye: int = 0,
    current_inner_clean: bool = False,
) -> bytes:
    payload = build_operation_payload(
        power=power,
        temp_c=temp_c,
        temp_slot=temp_slot,
        fan_speed=fan_speed,
        current_power=current_power,
        current_mode=current_mode,
        current_temp_c=current_temp_c,
        current_fan=current_fan,
        current_vane=current_vane,
        current_louver=current_louver,
        current_vent=current_vent,
        current_hold=current_hold,
        current_right_left=current_right_left,
        current_move_eye=current_move_eye,
        current_inner_clean=current_inner_clean,
    )
    return build_static_l3_frame(seq_no=seq_no, ctrl_type=1, func_no=1, payload=payload)


def build_static_status_request_frame(*, seq_no: int = 3) -> bytes:
    return build_static_l3_frame(seq_no=seq_no & 7, ctrl_type=2, func_no=0)


def is_valid_melremo_frame(data: bytes) -> bool:
    if len(data) < 4:
        return False
    length = int.from_bytes(data[:2], "little")
    if length != len(data) - 2:
        return False
    got = int.from_bytes(data[-2:], "little")
    calc = sum(data[:-2]) & 0xFFFF
    return got == calc


def parse_status_frame(frame: bytes) -> Optional[Status]:
    """Best-effort parser for captured current-operation status response."""
    if not is_valid_melremo_frame(frame) or len(frame) < 55 or frame[0:2] != b"\x35\x00":
        return None
    temps = {
        "cool": decode_melremo_temp(frame[30:32]),
        "heat": decode_melremo_temp(frame[32:34]),
        "auto": decode_melremo_temp(frame[34:36]),
        "upper_setback": decode_melremo_temp(frame[36:38]),
        "lower_setback": decode_melremo_temp(frame[38:40]),
    }
    power_mode = frame[9]
    mode_value = power_mode >> 3
    fan_vane = frame[40]
    fan_value = fan_vane >> 4
    power_raw = power_mode & 0x07
    return Status(
        raw=frame.hex(" "),
        seq=frame[2] & 0x07,
        direction=(frame[2] >> 3) & 0x01,
        power=power_raw != 0,
        power_raw=power_raw,
        mode_value=mode_value,
        mode=MODE_VALUE_TO_NAME.get(mode_value, f"mode_{mode_value}"),
        temps=temps,
        fan_value=fan_value,
        fan=FAN_REQUEST_VALUE_TO_NAME.get(fan_value, f"unknown_{fan_value}"),
        fan_raw_byte=fan_vane,
    )


def chunk_frame(frame: bytes, chunk_size: int = 20) -> list[bytes]:
    if chunk_size <= 0 or len(frame) <= chunk_size:
        return [frame]
    return [frame[i : i + chunk_size] for i in range(0, len(frame), chunk_size)]
