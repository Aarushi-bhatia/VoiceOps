"""Deepgram adapter: the request it builds for raw call audio."""

from __future__ import annotations

import httpx

from app.voice.base import AudioChunk
from app.voice.stt.deepgram import DeepgramSpeechToText


async def test_raw_call_audio_is_described_by_encoding_and_rate():
    """Headerless mu-law has nothing for Deepgram to sniff.

    Sent with only a Content-Type, the real API rejects it as "corrupt or
    unsupported data", so the encoding and sample rate must be query params.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        alternative = {"transcript": "my order number is ORD12345", "confidence": 0.98}
        body = {"results": {"channels": [{"alternatives": [alternative]}]}}
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    stt = DeepgramSpeechToText("test-key", client=client)
    audio = AudioChunk(data=b"\x10\x20" * 400, sample_rate=8000, encoding="mulaw")

    result = await stt.transcribe(audio)

    params = seen[0].url.params
    assert params["encoding"] == "mulaw"
    assert params["sample_rate"] == "8000"
    assert params["channels"] == "1"
    assert seen[0].content == audio.data
    assert result.text == "my order number is ORD12345"
