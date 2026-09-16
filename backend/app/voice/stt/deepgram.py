"""Deepgram speech-to-text (pre-recorded endpoint, one utterance per request)."""

from __future__ import annotations

import time

import httpx

from app.core.enums import FailureCategory
from app.voice.base import AudioChunk, TranscriptionResult, VoiceProviderError

API_URL = "https://api.deepgram.com/v1/listen"


class DeepgramSpeechToText:
    name = "deepgram"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "nova-3",
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("Deepgram requires DEEPGRAM_API_KEY")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def transcribe(
        self, audio: AudioChunk, *, language: str = "en-US"
    ) -> TranscriptionResult:
        if audio.is_silence:
            return TranscriptionResult(text="", confidence=0.0, provider=self.name)

        started = time.perf_counter()
        try:
            response = await self._client.post(
                API_URL,
                params={
                    "model": self.model,
                    "language": language,
                    "smart_format": "true",
                    "punctuate": "true",
                },
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": _content_type(audio),
                },
                content=audio.data,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise VoiceProviderError(
                f"deepgram timed out after {self.timeout}s",
                category=FailureCategory.TIMEOUT,
                provider=self.name,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise VoiceProviderError(
                f"deepgram returned {exc.response.status_code}: {exc.response.text[:200]}",
                category=_category_for_status(exc.response.status_code),
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise VoiceProviderError(
                f"deepgram request failed: {exc}",
                category=FailureCategory.NETWORK,
                provider=self.name,
            ) from exc

        alternative = _first_alternative(body)
        return TranscriptionResult(
            text=alternative.get("transcript", "").strip(),
            confidence=float(alternative.get("confidence", 0.0)),
            is_final=True,
            latency_ms=(time.perf_counter() - started) * 1000,
            provider=self.name,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _content_type(audio: AudioChunk) -> str:
    """Map the chunk's wire format onto a Deepgram content type."""
    base = {
        "pcm_s16le": "audio/l16",
        "mulaw": "audio/x-mulaw",
        "alaw": "audio/x-alaw",
    }.get(audio.encoding, "audio/l16")
    return f"{base};rate={audio.sample_rate}"


def _first_alternative(body: dict) -> dict:
    channels = (body.get("results") or {}).get("channels") or []
    if not channels:
        return {}
    alternatives = channels[0].get("alternatives") or []
    return alternatives[0] if alternatives else {}


def _category_for_status(status: int) -> FailureCategory:
    if status == 429:
        return FailureCategory.RATE_LIMITED
    if status >= 500:
        return FailureCategory.PROVIDER_ERROR
    return FailureCategory.AGENT_CONFIG  # 4xx: bad key or bad request - do not retry
