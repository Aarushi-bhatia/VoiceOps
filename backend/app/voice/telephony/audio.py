"""mu-law helpers and a simple energy gate.

Python 3.13 removed :mod:`audioop`, so the G.711 mu-law decode table is built
here rather than imported.
"""

from __future__ import annotations

# Twilio Media Streams deliver 8 kHz mu-law in 20 ms frames.
FRAME_BYTES = 160
FRAME_MS = 20


def _build_ulaw_table() -> list[int]:
    table: list[int] = []
    for byte in range(256):
        value = ~byte & 0xFF
        sign = value & 0x80
        exponent = (value >> 4) & 0x07
        mantissa = value & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        table.append(-sample if sign else sample)
    return table


ULAW_DECODE_TABLE = _build_ulaw_table()

# mu-law silence encodes to 0xFF/0x7F; anything under this average amplitude is
# treated as background noise rather than speech.
SILENCE_THRESHOLD = 500


def ulaw_to_linear(payload: bytes) -> list[int]:
    return [ULAW_DECODE_TABLE[b] for b in payload]


def average_amplitude(payload: bytes) -> float:
    if not payload:
        return 0.0
    return sum(abs(s) for s in ulaw_to_linear(payload)) / len(payload)


def is_silence(payload: bytes, threshold: float = SILENCE_THRESHOLD) -> bool:
    return average_amplitude(payload) < threshold


def chunk_frames(payload: bytes, size: int = FRAME_BYTES):
    """Split a payload into fixed-size frames, padding the tail with silence."""
    for offset in range(0, len(payload), size):
        frame = payload[offset : offset + size]
        if len(frame) < size:
            frame = frame + b"\xff" * (size - len(frame))
        yield frame
