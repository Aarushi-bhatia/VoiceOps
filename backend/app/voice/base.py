"""Provider-agnostic contracts for the voice pipeline.

The runtime only ever talks to these protocols, so a mock stack (no
credentials, deterministic) and a real stack (Deepgram / Anthropic /
ElevenLabs / Twilio) are interchangeable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.enums import FailureCategory


class VoiceProviderError(RuntimeError):
    """Raised by any provider; carries the retry classification."""

    def __init__(
        self,
        message: str,
        *,
        category: FailureCategory = FailureCategory.PROVIDER_ERROR,
        provider: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.category = category
        self.provider = provider

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.provider}/{self.category}] {super().__str__()}"


class TelephonyError(VoiceProviderError):
    """The call itself could not be placed or was dropped."""


@dataclass
class AudioChunk:
    """A slice of call audio.

    ``text_hint`` is populated only by the mock stack, which passes the intended
    words alongside synthetic bytes so mock STT is deterministic. Real providers
    leave it ``None`` and every consumer must work without it.
    """

    data: bytes
    duration_ms: float = 0.0
    sample_rate: int = 8000
    # Wire format of ``data``; STT adapters map this onto a content type.
    encoding: str = "pcm_s16le"
    text_hint: str | None = None

    @property
    def is_silence(self) -> bool:
        return not self.data


@dataclass
class TranscriptionResult:
    text: str
    confidence: float = 1.0
    is_final: bool = True
    latency_ms: float = 0.0
    provider: str = "mock"


@dataclass
class SynthesisResult:
    audio: AudioChunk
    characters: int = 0
    latency_ms: float = 0.0
    provider: str = "mock"


@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResult:
    text: str
    model: str = "mock-llm"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    provider: str = "mock"
    structured: dict[str, Any] | None = None


@dataclass
class DialRequest:
    call_id: str
    to_number: str
    from_number: str | None = None
    timeout_seconds: float = 30.0
    # Which delivery attempt this is (0-based). Providers may use it, and the
    # mock stack uses it to model numbers that only answer on a later try.
    attempt: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CallSession(Protocol):
    """A live, answered call."""

    call_id: str
    external_id: str

    @property
    def is_open(self) -> bool: ...

    async def play(self, audio: AudioChunk) -> None:
        """Play agent audio to the caller."""

    async def listen(self, *, timeout_seconds: float = 8.0) -> AudioChunk:
        """Wait for the caller to speak. Returns empty audio on silence."""

    async def transfer(self, destination: str) -> None:
        """Hand the call to a human queue or number."""

    async def hangup(self, reason: str = "completed") -> None: ...


@runtime_checkable
class SpeechToText(Protocol):
    name: str

    async def transcribe(
        self, audio: AudioChunk, *, language: str = "en-US"
    ) -> TranscriptionResult: ...


@runtime_checkable
class TextToSpeech(Protocol):
    name: str

    async def synthesize(
        self, text: str, *, voice_id: str = "default", language: str = "en-US"
    ) -> SynthesisResult: ...


@runtime_checkable
class LanguageModel(Protocol):
    name: str

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 512,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResult: ...


@runtime_checkable
class TelephonyProvider(Protocol):
    name: str

    async def dial(self, request: DialRequest) -> CallSession:
        """Place the call and return once it is answered.

        Raises :class:`TelephonyError` for no-answer, busy, invalid number, etc.
        """


@dataclass
class VoiceStack:
    """The four providers a call needs, bundled."""

    stt: SpeechToText
    tts: TextToSpeech
    llm: LanguageModel
    telephony: TelephonyProvider

    def describe(self) -> dict[str, str]:
        return {
            "stt": self.stt.name,
            "tts": self.tts.name,
            "llm": self.llm.name,
            "telephony": self.telephony.name,
        }
