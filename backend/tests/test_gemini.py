"""Gemini adapter, exercised against a stubbed HTTP client (no network, no key)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.agents.nlu import CLASSIFY_SCHEMA, EXTRACT_SCHEMA
from app.core.enums import FailureCategory
from app.voice.base import LLMMessage, VoiceProviderError
from app.voice.llm.gemini import GeminiLanguageModel, to_gemini_schema


def stub(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def reply(payload: dict, *, usage: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
            "usageMetadata": usage or {"promptTokenCount": 120, "candidatesTokenCount": 18},
        },
    )


# ------------------------------- schema -------------------------------


def test_types_are_upper_cased_and_additional_properties_dropped():
    schema = to_gemini_schema(CLASSIFY_SCHEMA)
    assert schema["type"] == "OBJECT"
    assert "additionalProperties" not in schema
    assert schema["properties"]["confidence"]["type"] == "NUMBER"
    assert schema["required"] == ["intent", "confidence", "reasoning"]


def test_a_union_of_types_collapses_to_one_nullable_type():
    """Gemini cannot express `["string", "null"]`; it wants a type plus nullable."""
    assert to_gemini_schema(CLASSIFY_SCHEMA)["properties"]["intent"] == {
        "type": "STRING",
        "nullable": True,
    }
    assert to_gemini_schema(EXTRACT_SCHEMA)["properties"]["value"]["nullable"] is True


def test_nested_arrays_and_objects_are_translated():
    schema = to_gemini_schema(
        {"type": "object", "properties": {"tags": {"type": "array", "items": {"type": "string"}}}}
    )
    assert schema["properties"]["tags"] == {"type": "ARRAY", "items": {"type": "STRING"}}


# ------------------------------- requests -------------------------------


async def test_request_shape_matches_the_gemini_api():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return reply({"intent": "refund", "confidence": 0.9, "reasoning": "ok"})

    llm = GeminiLanguageModel("secret", model="gemini-2.0-flash", client=stub(handler))
    result = await llm.complete(
        [LLMMessage("system", "You classify intents."), LLMMessage("user", '{"task":"classify"}')],
        json_schema=CLASSIFY_SCHEMA,
        max_tokens=256,
    )

    assert seen["url"].endswith("/models/gemini-2.0-flash:generateContent")
    assert seen["key"] == "secret"
    # System prompt goes in its own field, not as a message.
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "You classify intents."
    assert seen["body"]["contents"][0]["role"] == "user"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == 256
    assert result.structured == {"intent": "refund", "confidence": 0.9, "reasoning": "ok"}
    assert result.provider == "gemini"
    assert (result.input_tokens, result.output_tokens) == (120, 18)


async def test_assistant_turns_are_renamed_to_model():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["roles"] = [c["role"] for c in json.loads(request.content)["contents"]]
        return reply({"summary": "done"})

    llm = GeminiLanguageModel("k", client=stub(handler))
    await llm.complete(
        [LLMMessage("user", "hello"), LLMMessage("assistant", "hi"), LLMMessage("user", "again")]
    )
    assert seen["roles"] == ["user", "model", "user"]


async def test_a_blocked_or_empty_candidate_does_not_crash():
    llm = GeminiLanguageModel("k", client=stub(lambda r: httpx.Response(200, json={})))
    result = await llm.complete([LLMMessage("user", "x")], json_schema=CLASSIFY_SCHEMA)
    assert result.text == ""
    assert result.structured is None


async def test_non_json_response_is_logged_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"candidates": [{"content": {"parts": [{"text": "sorry"}]}}]}
        return httpx.Response(200, json=body)

    llm = GeminiLanguageModel("k", client=stub(handler))
    result = await llm.complete([LLMMessage("user", "x")], json_schema=CLASSIFY_SCHEMA)
    assert result.text == "sorry"
    assert result.structured is None


async def test_no_user_message_is_a_configuration_error():
    llm = GeminiLanguageModel("k", client=stub(lambda r: reply({})))
    with pytest.raises(VoiceProviderError) as exc:
        await llm.complete([LLMMessage("system", "only a system prompt")])
    assert exc.value.category is FailureCategory.AGENT_CONFIG


def test_a_missing_key_fails_fast():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiLanguageModel("")


# ---------------------- failure classification ----------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, FailureCategory.RATE_LIMITED),
        (503, FailureCategory.PROVIDER_ERROR),
        (500, FailureCategory.PROVIDER_ERROR),
        (400, FailureCategory.AGENT_CONFIG),
        (403, FailureCategory.AGENT_CONFIG),
    ],
)
async def test_http_errors_map_onto_retry_categories(status, expected):
    """Rate limits and 5xx are worth retrying; a bad key or bad request is not."""
    llm = GeminiLanguageModel(
        "k", max_attempts=1, client=stub(lambda r: httpx.Response(status, text="nope"))
    )
    with pytest.raises(VoiceProviderError) as exc:
        await llm.complete([LLMMessage("user", "x")])
    assert exc.value.category is expected
    assert exc.value.category.is_transient is expected.is_transient


async def test_a_timeout_is_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    llm = GeminiLanguageModel("k", max_attempts=1, client=stub(handler))
    with pytest.raises(VoiceProviderError) as exc:
        await llm.complete([LLMMessage("user", "x")])
    assert exc.value.category is FailureCategory.TIMEOUT
    assert exc.value.category.is_transient


async def test_a_connection_error_is_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    llm = GeminiLanguageModel("k", max_attempts=1, client=stub(handler))
    with pytest.raises(VoiceProviderError) as exc:
        await llm.complete([LLMMessage("user", "x")])
    assert exc.value.category is FailureCategory.NETWORK


# ------------------------- retrying the provider -------------------------


async def test_a_transient_failure_is_retried_rather_than_failing_the_call(monkeypatch):
    """Overload and rate limits are normal on a shared endpoint.

    Letting them escape abandons a call with a customer on the line, so the
    adapter absorbs them instead of surfacing them as a failed call.
    """
    slept: list[float] = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.voice.llm.gemini.asyncio.sleep", no_sleep)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, json={"error": {"code": 503, "message": "high demand"}})
        return reply({"intent": "refund", "confidence": 0.9, "reasoning": "ok"})

    llm = GeminiLanguageModel("k", client=stub(handler))
    result = await llm.complete([LLMMessage("user", "x")], json_schema=CLASSIFY_SCHEMA)

    assert attempts["n"] == 3
    assert result.structured["intent"] == "refund"
    assert slept == [1.0, 2.0], "backoff should grow between attempts"


async def test_googles_own_retry_hint_is_honoured(monkeypatch):
    slept: list[float] = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.voice.llm.gemini.asyncio.sleep", no_sleep)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "code": 429,
                        "message": "quota",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                "retryDelay": "7s",
                            }
                        ],
                    }
                },
            )
        return reply({"value": "ORD-1", "confidence": 1.0})

    llm = GeminiLanguageModel("k", client=stub(handler))
    await llm.complete([LLMMessage("user", "x")], json_schema=EXTRACT_SCHEMA)
    assert slept == [7.0], "Google said how long to wait; use that, not the computed backoff"


async def test_a_permanent_failure_is_not_retried(monkeypatch):
    async def no_sleep(seconds):
        raise AssertionError("a 400 must not be retried")

    monkeypatch.setattr("app.voice.llm.gemini.asyncio.sleep", no_sleep)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, text="bad request")

    llm = GeminiLanguageModel("k", client=stub(handler))
    with pytest.raises(VoiceProviderError):
        await llm.complete([LLMMessage("user", "x")])
    assert attempts["n"] == 1


async def test_a_waiting_caller_caps_how_long_we_stall(monkeypatch):
    """Google can ask for a 48-second wait; a caller will not hold that long."""
    slept: list[float] = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.voice.llm.gemini.asyncio.sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "details": [
                        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "300s"}
                    ]
                }
            },
        )

    llm = GeminiLanguageModel("k", max_attempts=2, client=stub(handler))
    with pytest.raises(VoiceProviderError):
        await llm.complete([LLMMessage("user", "x")])
    assert slept == [20.0], "capped at MAX_RETRY_WAIT_SECONDS"
