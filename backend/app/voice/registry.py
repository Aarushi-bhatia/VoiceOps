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
from app.voice.telephony.mock import MockTelephonyProvider
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
    raise ValueError(f"unknown LLM_PROVIDER: {settings.llm_provider}")


def build_telephony(settings: Settings) -> TelephonyProvider:
    if settings.telephony_provider == "mock":
        return MockTelephonyProvider(
            seed=settings.mock_seed, latency_scale=settings.mock_latency_scale
        )
    if settings.telephony_provider == "twilio":
        from app.voice.telephony.twilio import TwilioTelephonyProvider

        return TwilioTelephonyProvider(
            account_sid=settings.twilio_account_sid or "",
            auth_token=settings.twilio_auth_token or "",
            from_number=settings.twilio_from_number or "",
            public_base_url=settings.twilio_public_base_url or "",
        )
    raise ValueError(f"unknown TELEPHONY_PROVIDER: {settings.telephony_provider}")


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
