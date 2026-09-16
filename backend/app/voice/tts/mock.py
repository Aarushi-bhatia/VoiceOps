"""Deterministic text-to-speech used when no TTS credentials are configured.

Produces silent PCM sized to a realistic speaking rate and carries the spoken
words in ``text_hint`` so the mock telephony/STT pair stays end-to-end
deterministic.
"""

from __future__ import annotations

import asyncio
import time

from app.voice.base import AudioChunk, SynthesisResult

# Average conversational speech, used to size the synthetic audio buffer.
WORDS_PER_MINUTE = 155
SAMPLE_RATE = 8000
BYTES_PER_SAMPLE = 2


class MockTextToSpeech:
    name = "mock"

    def __init__(self, latency_scale: float = 1.0) -> None:
        self.latency_scale = latency_scale

    async def synthesize(
        self, text: str, *, voice_id: str = "default", language: str = "en-US"
    ) -> SynthesisResult:
        started = time.perf_counter()
        await asyncio.sleep(min(0.05 + len(text) * 0.0015, 0.5) * self.latency_scale)
        duration_ms = speech_duration_ms(text)
        samples = int(SAMPLE_RATE * duration_ms / 1000)
        audio = AudioChunk(
            data=b"\x00" * (samples * BYTES_PER_SAMPLE),
            duration_ms=duration_ms,
            sample_rate=SAMPLE_RATE,
            text_hint=text,
        )
        return SynthesisResult(
            audio=audio,
            characters=len(text),
            latency_ms=(time.perf_counter() - started) * 1000,
            provider=self.name,
        )


def speech_duration_ms(text: str) -> float:
    words = max(len(text.split()), 1)
    return (words / WORDS_PER_MINUTE) * 60_000
