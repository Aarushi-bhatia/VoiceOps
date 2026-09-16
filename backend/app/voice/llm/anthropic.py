"""Anthropic Messages API adapter.

Notes that shape this implementation:

* ``temperature`` is rejected by Claude Opus 5 and the other current models -
  response shape is controlled with ``output_config`` instead. The
  :class:`~app.voice.base.LanguageModel` protocol still accepts a temperature
  so mock/other providers can honour it; here it is deliberately unused.
* Latency matters on a live phone call, so requests run at ``effort: "low"``
  with thinking left at its default rather than disabled - low effort is the
  supported way to trade depth for speed.
* Every NLU call wants JSON back, so the caller's ``json_schema`` is passed via
  ``output_config.format``, which guarantees the first text block parses.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from typing import Any

from app.core.enums import FailureCategory
from app.voice.base import LLMMessage, LLMResult, VoiceProviderError

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"


class AnthropicLanguageModel:
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = "low",
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        if client is not None:
            self._client = client
            return
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise VoiceProviderError(
                "the anthropic package is not installed; install voiceops[llm]",
                category=FailureCategory.AGENT_CONFIG,
                provider=self.name,
            ) from exc
        # A bare client also resolves an `ant auth login` profile, so an unset
        # ANTHROPIC_API_KEY is not necessarily an error.
        self._client = (
            anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()
        )

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.3,  # noqa: ARG002 - rejected by current models
        max_tokens: int = 512,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        system_prompt = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in {"user", "assistant"}
        ]
        if not turns:
            raise VoiceProviderError(
                "at least one user message is required",
                category=FailureCategory.AGENT_CONFIG,
                provider=self.name,
            )

        output_config: dict[str, Any] = {"effort": self.effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}

        request: dict[str, Any] = {
            "model": model or self.model,
            # Conversational turns are one or two sentences of JSON; a small cap
            # keeps time-to-first-word low on a live call.
            "max_tokens": max_tokens,
            "messages": turns,
            "output_config": output_config,
        }
        if system_prompt:
            request["system"] = system_prompt

        started = time.perf_counter()
        try:
            response = await self._client.messages.create(**request)
        except Exception as exc:  # mapped to retry categories below
            raise _to_provider_error(exc, self.name) from exc

        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            raise VoiceProviderError(
                f"model declined the request ({getattr(details, 'category', 'unknown')})",
                category=FailureCategory.AGENT_CONFIG,
                provider=self.name,
            )

        text = next((b.text for b in response.content if b.type == "text"), "")
        structured: dict[str, Any] | None = None
        if json_schema is not None:
            try:
                parsed = json.loads(text)
                structured = parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                logger.warning(
                    "model returned non-JSON despite a schema",
                    extra={"request_id": getattr(response, "_request_id", None)},
                )

        usage = response.usage
        return LLMResult(
            text=text,
            model=response.model,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            latency_ms=(time.perf_counter() - started) * 1000,
            provider=self.name,
            structured=structured,
        )


def _to_provider_error(exc: Exception, provider: str) -> VoiceProviderError:
    """Map SDK exceptions onto the retry classification."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return VoiceProviderError(str(exc), category=FailureCategory.UNKNOWN, provider=provider)

    if isinstance(exc, anthropic.RateLimitError):
        return VoiceProviderError(
            str(exc), category=FailureCategory.RATE_LIMITED, provider=provider
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return VoiceProviderError(str(exc), category=FailureCategory.TIMEOUT, provider=provider)
    if isinstance(exc, anthropic.APIConnectionError):
        return VoiceProviderError(str(exc), category=FailureCategory.NETWORK, provider=provider)
    if isinstance(exc, anthropic.APIStatusError):
        # 5xx is worth another attempt; 4xx means the request itself is wrong.
        category = (
            FailureCategory.PROVIDER_ERROR
            if exc.status_code >= 500
            else FailureCategory.AGENT_CONFIG
        )
        return VoiceProviderError(str(exc), category=category, provider=provider)
    return VoiceProviderError(str(exc), category=FailureCategory.UNKNOWN, provider=provider)
