"""Deterministic speech-to-text used when no STT credentials are configured.

Mock audio produced by :mod:`app.voice.tts.mock` and the mock telephony
provider carries the spoken words in ``AudioChunk.text_hint``; this provider
simply reads them back, with a simulated recognition latency and a confidence
that degrades for longer utterances the way a real recogniser's does.
"""

from __future__ import annotations

import asyncio
import hashlib
import time

from app.voice.base import AudioChunk, TranscriptionResult


class MockSpeechToText:
    name = "mock"

    def __init__(self, latency_scale: float = 1.0) -> None:
        self.latency_scale = latency_scale

    async def transcribe(
        self, audio: AudioChunk, *, language: str = "en-US"
    ) -> TranscriptionResult:
        started = time.perf_counter()
        text = (audio.text_hint or "").strip()
        # Roughly proportional to utterance length, as streaming STT would be.
        await asyncio.sleep(min(0.04 + len(text) * 0.002, 0.4) * self.latency_scale)
        return TranscriptionResult(
            text=text,
            confidence=0.0 if not text else _confidence_for(text),
            is_final=True,
            latency_ms=(time.perf_counter() - started) * 1000,
            provider=self.name,
        )


def _confidence_for(text: str) -> float:
    """Stable pseudo-confidence in [0.82, 0.99] derived from the text itself."""
    digest = hashlib.sha256(text.encode()).digest()[0]
    return round(0.82 + (digest / 255) * 0.17, 3)
