# SPDX-License-Identifier: MIT
"""Explicitly opt-in experimental readings; these field identities are guesses.

Raw frames were observed from one main unit. The mapping, units, integer
scaling and plausibility ranges below have not been independently calibrated.
Unmapped fields stay unknown. No desired/confirmed or mechanical state is
inferred, and this pure helper implements no freshness or availability policy.
"""

from __future__ import annotations

from .framing import Frame, encode_frame


_HYPOTHESES = {
    0xAC: ('pm25', 'µg/m³', 0, 999, False),
    0xA2: ('indoor_temperature', '°C', -40, 85, True),
    0xA8: ('indoor_humidity', '%', 0, 100, False),
}


def decode_experimental(frame: Frame) -> dict[str, object] | None:
    """Return name/value/unit/provisional/source_type, or None if unsupported.

    Calling this function is the opt-in: Decoder never invokes it. A manual
    Frame value is revalidated against its raw bytes, so invalid CRC or field
    disagreement cannot yield a reading. A valid measured zero stays zero.
    """
    if not isinstance(frame, Frame):
        raise TypeError('expected a Frame')
    try:
        if frame.raw != encode_frame(frame.kind, frame.payload):
            return None
    except (TypeError, ValueError):
        return None
    hypothesis = _HYPOTHESES.get(frame.kind)
    if hypothesis is None or len(frame.payload) != 2:
        return None
    name, unit, low, high, signed = hypothesis
    value = int.from_bytes(frame.payload, 'big', signed=signed)
    if not low <= value <= high:
        return None
    return {'name': name, 'value': value, 'unit': unit,
            'provisional': True, 'source_type': frame.kind}
