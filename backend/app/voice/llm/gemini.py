"""Google Gemini adapter for the language-model interface.

Uses the REST API over httpx rather than the Google SDK, matching how the
Deepgram and ElevenLabs adapters are written and adding no new dependency.

Gemini's structured-output schema is OpenAPI-flavoured and stricter than plain
JSON Schema: type names are upper-case, ``additionalProperties`` is rejected,
and a field cannot declare a union of types. :func:`to_gemini_schema`
translates the schemas in :mod:`app.agents.nlu` into that dialect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.enums import FailureCategory
from app.voice.base import LLMMessage, LLMResult, VoiceProviderError

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.8-flash"
# A caller is waiting, so never wait longer than this between attempts.
MAX_RETRY_WAIT_SECONDS = 20.0

_TYPE_MAP = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


class GeminiLanguageModel:
    name = "gemini"

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        thinking_budget: int = 0,
        max_attempts: int = 4,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini requires GEMINI_API_KEY")
        self.api_key = api_key
        self.model = model
        self.thinking_budget = thinking_budget
        self.max_attempts = max(max_attempts, 1)
        self.timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 512,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        system_prompt = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            # Gemini calls the assistant role "model".
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages
            if m.role in {"user", "assistant"}
        ]
        if not contents:
            raise VoiceProviderError(
                "at least one user message is required",
                category=FailureCategory.AGENT_CONFIG,
                provider=self.name,
            )

        generation: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if self.thinking_budget >= 0:
            # Thinking tokens come out of maxOutputTokens, so on a small budget
            # they consume it and the JSON comes back truncated. They also cost
            # latency, which matters more than depth on a live call - the model
            # is classifying a reply, not solving anything. 0 disables.
            generation["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        if json_schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = to_gemini_schema(json_schema)

        payload: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        target = self._resolve_model(model)
        started = time.perf_counter()
        body = await self._post_with_retry(target, payload)

        finish_reason = _finish_reason(body)
        if finish_reason == "MAX_TOKENS":
            # Silently returning half a JSON document would surface much later
            # as a mysterious routing failure.
            raise VoiceProviderError(
                f"response hit the {max_tokens}-token limit before finishing; "
                "raise max_tokens or lower the thinking budget",
                category=FailureCategory.AGENT_CONFIG,
                provider=self.name,
            )

        text = _first_text(body)
        structured: dict[str, Any] | None = None
        if json_schema is not None and text:
            try:
                parsed = json.loads(text)
                structured = parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                logger.warning(
                    "gemini returned non-JSON despite a response schema",
                    extra={"finish_reason": finish_reason, "preview": text[:120]},
                )

        usage = body.get("usageMetadata") or {}
        return LLMResult(
            text=text,
            model=target,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
            latency_ms=(time.perf_counter() - started) * 1000,
            provider=self.name,
            structured=structured,
        )

    async def _post_with_retry(self, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        """One request, retried while the failure is the provider's own.

        Rate limits and overload are normal on a shared endpoint and usually
        clear in seconds. Letting them escape would abandon a call that has a
        customer on the line, so they are absorbed here rather than surfacing
        as a failed call. Google states how long to wait on a 429; that hint is
        preferred over the computed backoff when it is shorter than the budget.
        """
        last: VoiceProviderError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._client.post(
                    f"{API_ROOT}/models/{target}:generateContent",
                    headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException:
                last = VoiceProviderError(
                    f"gemini timed out after {self.timeout}s",
                    category=FailureCategory.TIMEOUT,
                    provider=self.name,
                )
                delay = None
            except httpx.HTTPStatusError as exc:
                last = VoiceProviderError(
                    f"gemini returned {exc.response.status_code}: {exc.response.text[:200]}",
                    category=_category_for_status(exc.response.status_code),
                    provider=self.name,
                )
                delay = _retry_after_seconds(exc.response)
            except httpx.HTTPError as exc:
                last = VoiceProviderError(
                    f"gemini request failed: {exc}",
                    category=FailureCategory.NETWORK,
                    provider=self.name,
                )
                delay = None

            if not last.category.is_transient or attempt == self.max_attempts:
                raise last

            wait = delay if delay is not None else min(2.0 ** (attempt - 1), 8.0)
            wait = min(wait, MAX_RETRY_WAIT_SECONDS)
            logger.info(
                "retrying gemini after a transient failure",
                extra={
                    "attempt": attempt,
                    "of": self.max_attempts,
                    "category": str(last.category),
                    "wait_seconds": round(wait, 1),
                },
            )
            await asyncio.sleep(wait)

        raise last  # pragma: no cover - the loop always raises or returns

    def _resolve_model(self, requested: str | None) -> str:
        """Honour a per-agent model override only if this provider serves it.

        An agent's ``llm_model`` is provider-specific and set when the agent is
        configured, which may have been against a different provider entirely -
        the seeded agents carry "mock-llm". Passing that through produced a 404
        for ``models/mock-llm``. An override that is not a Gemini model is
        ignored rather than obeyed into a guaranteed failure.
        """
        if not requested or requested == self.model:
            return self.model
        if requested.startswith("gemini"):
            return requested
        logger.info(
            "ignoring a model override that is not a Gemini model",
            extra={"requested": requested, "using": self.model},
        )
        return self.model

    async def aclose(self) -> None:
        await self._client.aclose()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Google returns a RetryInfo hint on a rate limit; honour it."""
    try:
        details = (response.json().get("error") or {}).get("details") or []
    except ValueError:
        return None
    for detail in details:
        if detail.get("@type", "").endswith("RetryInfo"):
            raw = str(detail.get("retryDelay", "")).rstrip("s")
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def _finish_reason(body: dict[str, Any]) -> str | None:
    candidates = body.get("candidates") or []
    return candidates[0].get("finishReason") if candidates else None


def _first_text(body: dict[str, Any]) -> str:
    """Pull the reply text out, tolerating a blocked or empty candidate."""
    candidates = body.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts).strip()


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate JSON Schema into Gemini's OpenAPI-flavoured dialect.

    Upper-cases type names, drops ``additionalProperties``, and collapses a
    union of types - which Gemini cannot express - to its first non-null member
    plus ``nullable``.
    """
    raw_type = schema.get("type", "string")
    nullable = False
    if isinstance(raw_type, list):
        nullable = "null" in raw_type
        concrete = [t for t in raw_type if t != "null"]
        raw_type = concrete[0] if concrete else "string"

    out: dict[str, Any] = {"type": _TYPE_MAP.get(raw_type, "STRING")}
    if nullable:
        out["nullable"] = True
    if "description" in schema:
        out["description"] = schema["description"]

    if raw_type == "object":
        properties = schema.get("properties") or {}
        out["properties"] = {k: to_gemini_schema(v) for k, v in properties.items()}
        required = [r for r in schema.get("required", []) if r in properties]
        if required:
            out["required"] = required
    elif raw_type == "array" and "items" in schema:
        out["items"] = to_gemini_schema(schema["items"])

    return out


def _category_for_status(status: int) -> FailureCategory:
    if status == 429:
        return FailureCategory.RATE_LIMITED
    if status >= 500:
        return FailureCategory.PROVIDER_ERROR
    # 4xx means a bad key or a malformed request; retrying changes nothing.
    return FailureCategory.AGENT_CONFIG
