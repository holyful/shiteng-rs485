# SPDX-License-Identifier: MIT
"""Transport-independent, evidence-scoped STX-W/Shiteng RS485 protocol core."""

from .commands import Fan, Mode, encode_control, encode_humidity, heartbeat, make_control, patch_control
from .framing import Decoder, Frame, MAX_FRAME_BYTES, crc16, encode_frame
from .telemetry import decode_experimental

__all__ = [
    'Decoder', 'Frame', 'MAX_FRAME_BYTES', 'crc16', 'encode_frame',
    'Fan', 'Mode', 'make_control', 'patch_control', 'encode_control',
    'encode_humidity', 'heartbeat', 'decode_experimental',
]
