# SPDX-License-Identifier: MIT
"""Observed command payloads for one STX-W installation, not all Shiteng units.

This module builds bytes only. It performs no writes, polling or retries and
does not interpret a returned frame as successful physical execution.
"""

from __future__ import annotations

from enum import IntEnum

from .framing import BytesLike, _bytes, _integer, encode_frame


class Fan(IntEnum):
    LOW = 1
    MID = 2
    HIGH = 3


class Mode(IntEnum):
    FRESH = 0
    INNER_VENT = 1
    INNER_DEHUMIDIFY = 2
    FRESH_DEHUMIDIFY = 3


def _boolean(value: bool, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f'{name} must be an explicit boolean')
    return value


def patch_control(payload: BytesLike, *, power: bool | None = None,
                  auto: bool | None = None, fan: Fan | int | None = None,
                  mode: Mode | int | None = None) -> bytes:
    """Patch observed bits of a four-byte payload, preserving all other bits.

    None means unchanged. Mode changes also update byte 1 bit 2 to match the
    observed settled fresh-air states. That bit's physical meaning is unknown;
    this is not an implementation of the original multi-stage transitions.
    Fan zero/stop is unverified and intentionally unsupported.
    """
    raw = _bytes(payload)
    if len(raw) != 4:
        raise ValueError('control payload must contain exactly four bytes')
    result = bytearray(raw)
    if power is not None:
        result[0] = (result[0] & ~0x80) | (0x80 if _boolean(power, 'power') else 0)
    if auto is not None:
        result[0] = (result[0] & ~0x40) | (0x40 if _boolean(auto, 'auto') else 0)
    if fan is not None:
        result[1] = (result[1] & ~0x60) | (_integer(fan, 1, 3, 'fan') << 5)
    if mode is not None:
        mode = _integer(mode, 0, 3, 'mode')
        result[0] = (result[0] & ~0x03) | mode
        result[1] = (result[1] & ~0x04) | (0x04 if mode in (0, 3) else 0)
    return bytes(result)


def make_control(*, power: bool, auto: bool, fan: Fan | int, mode: Mode | int) -> bytes:
    """Build a PAYLOAD from explicit settings; fixed tail 14 00 is opaque.

    Only the documented captured cases were tested. Bit-field combinations
    beyond them are inferred; no automatic fan-reset rule is implemented.
    """
    _boolean(power, 'power')
    _boolean(auto, 'auto')
    _integer(fan, 1, 3, 'fan')
    _integer(mode, 0, 3, 'mode')
    return patch_control(b'\x00\x00\x14\x00', power=power, auto=auto, fan=fan, mode=mode)


def encode_control(*, power: bool, auto: bool, fan: Fan | int, mode: Mode | int) -> bytes:
    """Build a complete type-81 frame; this function does not send it."""
    return encode_frame(0x81, make_control(power=power, auto=auto, fan=fan, mode=mode))


def encode_humidity(value: int) -> bytes:
    """Type C4 setpoint. Only 55/60 were labeled in original-panel captures.

    The accepted 30..90 interval is a conservative application/UI policy, not
    an established protocol range, persistence guarantee or appliance ACK.
    """
    value = _integer(value, 30, 90, 'humidity')
    return encode_frame(0xc4, value.to_bytes(2, 'big'))


def heartbeat() -> bytes:
    """Observed periodic type-20/payload-00 frame; no timer or I/O is started."""
    return encode_frame(0x20, b'\x00')
