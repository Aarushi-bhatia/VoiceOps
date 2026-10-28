"""Builds the configured :class:`~app.voice.base.VoiceStack`.

Every provider defaults to ``mock``, so a fresh checkout runs the full pipeline
with no credentials. Naming a real provider without its credentials is a
configuration error and fails loudly at startup rather than mid-call.
"""

from __future__ import annotations

import logging

from app.core.config import Settings, get_settings
from app.voice.base import (
    LanguageModel,
    SpeechToText,
    TelephonyProvider,
    TextToSpeech,
    VoiceStack,
)
from app.voice.llm.mock import MockLanguageModel
from app.voice.stt.mock import MockSpeechToText
from app.voice.telephony import providers as telephony_providers
from app.voice.telephony.guard import GuardedTelephony
from app.voice.tts.mock import MockTextToSpeech

logger = logging.getLogger(__name__)


def build_stt(settings: Settings) -> SpeechToText:
    if settings.stt_provider == "mock":
        return MockSpeechToText(latency_scale=settings.mock_latency_scale)
    if settings.stt_provider == "deepgram":
        from app.voice.stt.deepgram import DeepgramSpeechToText

        return DeepgramSpeechToText(settings.deepgram_api_key or "")
    raise ValueError(f"unknown STT_PROVIDER: {settings.stt_provider}")


def build_tts(settings: Settings) -> TextToSpeech:
    if settings.tts_provider == "mock":
        return MockTextToSpeech(latency_scale=settings.mock_latency_scale)
    if settings.tts_provider == "elevenlabs":
        from app.voice.tts.elevenlabs import ElevenLabsTextToSpeech

        return ElevenLabsTextToSpeech(settings.elevenlabs_api_key or "")
    raise ValueError(f"unknown TTS_PROVIDER: {settings.tts_provider}")


def build_llm(settings: Settings) -> LanguageModel:
    if settings.llm_provider == "mock":
        return MockLanguageModel(latency_scale=settings.mock_latency_scale)
    if settings.llm_provider == "anthropic":
        from app.voice.llm.anthropic import AnthropicLanguageModel

        return AnthropicLanguageModel(settings.anthropic_api_key, model=settings.anthropic_model)
    if settings.llm_provider == "gemini":
        from app.voice.llm.gemini import GeminiLanguageModel

        return GeminiLanguageModel(
            settings.gemini_api_key,
            model=settings.gemini_model,
            thinking_budget=settings.gemini_thinking_budget,
            max_attempts=settings.gemini_max_attempts,
        )
    raise ValueError(f"unknown LLM_PROVIDER: {settings.llm_provider}")


def build_telephony(settings: Settings) -> TelephonyProvider:
    provider = telephony_providers.build(settings)
    if settings.telephony_provider == "mock":
        return provider
    return GuardedTelephony(
        provider,
        allowed_numbers=settings.telephony_allowed_number_set,
        max_call_seconds=settings.telephony_max_call_seconds,
    )


_stack: VoiceStack | None = None


def get_voice_stack(settings: Settings | None = None) -> VoiceStack:
    global _stack
    if _stack is None:
        settings = settings or get_settings()
        _stack = VoiceStack(
            stt=build_stt(settings),
            tts=build_tts(settings),
            llm=build_llm(settings),
            telephony=build_telephony(settings),
        )
        logger.info("voice stack ready", extra=_stack.describe())
    return _stack


def set_voice_stack(stack: VoiceStack | None) -> None:
    """Override the process-wide stack (tests, simulation)."""
    global _stack
    _stack = stack
