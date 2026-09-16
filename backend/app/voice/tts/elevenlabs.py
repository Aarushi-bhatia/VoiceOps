"""ElevenLabs text-to-speech, requesting telephony-grade 8 kHz mu-law audio."""

from __future__ import annotations

import time

import httpx

from app.core.enums import FailureCategory
from app.voice.base import AudioChunk, SynthesisResult, VoiceProviderError

API_URL = "https://api.elevenlabs.io/v1/text-to-speech"


class ElevenLabsTextToSpeech:
    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = "eleven_flash_v2_5",
        output_format: str = "ulaw_8000",
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not api_key:
            raise ValueError("ElevenLabs requires ELEVENLABS_API_KEY")
        self.api_key = api_key
        self.model_id = model_id
        self.output_format = output_format
        self.timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def synthesize(
        self, text: str, *, voice_id: str = "default", language: str = "en-US"
    ) -> SynthesisResult:
        started = time.perf_counter()
        try:
            response = await self._client.post(
                f"{API_URL}/{voice_id}",
                params={"output_format": self.output_format},
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": self.model_id,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
            )
            response.raise_for_status()
            audio_bytes = response.content
        except httpx.TimeoutException as exc:
            raise VoiceProviderError(
                f"elevenlabs timed out after {self.timeout}s",
                category=FailureCategory.TIMEOUT,
                provider=self.name,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise VoiceProviderError(
                f"elevenlabs returned {exc.response.status_code}",
                category=_category_for_status(exc.response.status_code),
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise VoiceProviderError(
                f"elevenlabs request failed: {exc}",
                category=FailureCategory.NETWORK,
                provider=self.name,
            ) from exc

        # mu-law at 8 kHz is one byte per sample.
        duration_ms = (len(audio_bytes) / 8000) * 1000 if self.output_format == "ulaw_8000" else 0.0
        return SynthesisResult(
            audio=AudioChunk(
                data=audio_bytes,
                duration_ms=duration_ms,
                sample_rate=8000,
                encoding="mulaw" if self.output_format == "ulaw_8000" else "pcm_s16le",
                text_hint=text,
            ),
            characters=len(text),
            latency_ms=(time.perf_counter() - started) * 1000,
            provider=self.name,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _category_for_status(status: int) -> FailureCategory:
    if status == 429:
        return FailureCategory.RATE_LIMITED
    if status >= 500:
        return FailureCategory.PROVIDER_ERROR
    return FailureCategory.AGENT_CONFIG
