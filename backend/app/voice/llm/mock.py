"""Deterministic language model used when no LLM credentials are configured.

It understands the same small task envelope the real adapter receives from
:mod:`app.agents.nlu` - ``classify``, ``extract``, ``reply`` and ``summarize``
- and answers with rule-based heuristics. That keeps the whole conversation
runtime exercisable (and testable) without a network call.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Sequence
from typing import Any

from app.voice.base import LLMMessage, LLMResult

_STOPWORDS = {
    "a",
    "an",
    "the",
    "i",
    "you",
    "my",
    "me",
    "is",
    "it",
    "to",
    "of",
    "and",
    "for",
    "on",
    "in",
    "that",
    "this",
    "with",
    "was",
    "were",
    "am",
    "be",
    "do",
    "did",
    "please",
    "would",
    "like",
    "want",
    "need",
    "can",
    "could",
    "just",
    "about",
    "have",
    "has",
    "had",
    "im",
    "its",
    "get",
    "got",
}

_AFFIRMATIVE = {
    "yes",
    "yeah",
    "yep",
    "sure",
    "correct",
    "right",
    "ok",
    "okay",
    "please",
    "affirmative",
}
_NEGATIVE = {"no", "nope", "nah", "negative", "wrong", "incorrect", "dont", "not"}

_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "phone": re.compile(r"\+?\d[\d\s\-().]{7,}\d"),
    "order_id": re.compile(r"\b[A-Z]{2,5}-?\d{3,10}\b", re.IGNORECASE),
    "number": re.compile(r"\b\d+(?:\.\d+)?\b"),
    "date": re.compile(
        r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
        r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b",
        re.IGNORECASE,
    ),
    "zip": re.compile(r"\b\d{5}(?:-\d{4})?\b"),
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9']+", text.lower()) if t not in _STOPWORDS}


class MockLanguageModel:
    name = "mock"

    def __init__(self, latency_scale: float = 1.0) -> None:
        self.latency_scale = latency_scale

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 512,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        started = time.perf_counter()
        await asyncio.sleep(0.12 * self.latency_scale)

        payload = _last_user_payload(messages)
        task = payload.get("task", "reply")
        handler = {
            "classify": _classify,
            "extract": _extract,
            "reply": _reply,
            "summarize": _summarize,
        }.get(task, _reply)
        structured = handler(payload)

        text = json.dumps(structured)
        return LLMResult(
            text=text,
            model=model or "mock-llm",
            input_tokens=sum(len(m.content) for m in messages) // 4,
            output_tokens=len(text) // 4,
            latency_ms=(time.perf_counter() - started) * 1000,
            provider=self.name,
            structured=structured,
        )


def _last_user_payload(messages: Sequence[LLMMessage]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.role == "user":
            try:
                parsed = json.loads(message.content)
            except (json.JSONDecodeError, TypeError):
                return {"task": "reply", "utterance": message.content}
            return (
                parsed
                if isinstance(parsed, dict)
                else {"task": "reply", "utterance": message.content}
            )
    return {}


def _classify(payload: dict[str, Any]) -> dict[str, Any]:
    """Pick the option whose *distinctive* vocabulary best matches the utterance.

    Only words unique to one option count. A word shared by several options
    carries no signal about which was meant: in a delivery follow-up every
    branch mentions "delivery", so scoring on it made "did it arrive?" match
    the yes branch purely because the customer also said the word.
    """
    utterance = _tokens(str(payload.get("utterance", "")))
    options: list[dict[str, Any]] = payload.get("options") or []

    vocabularies: list[tuple[str, set[str]]] = []
    for option in options:
        vocabulary = _tokens(
            " ".join(
                [
                    str(option.get("name", "")).replace("_", " "),
                    str(option.get("description", "")),
                    " ".join(option.get("examples", []) or []),
                ]
            )
        )
        if vocabulary:
            vocabularies.append((str(option.get("name")), vocabulary))

    # A word appearing in more than one option describes the topic, not the choice.
    seen: dict[str, int] = {}
    for _, vocabulary in vocabularies:
        for word in vocabulary:
            seen[word] = seen.get(word, 0) + 1

    best_name, best_score, best_coverage = None, 0.0, 0.0
    for name, vocabulary in vocabularies:
        distinctive = {w for w in vocabulary if seen[w] == 1}
        overlap = len(utterance & distinctive)
        if not overlap:
            continue
        # Rank by how much of the option's distinctive vocabulary was hit,
        # normalised so verbose options do not always win.
        score = overlap / (len(distinctive) ** 0.5)
        if score > best_score:
            # Confidence comes from coverage of the *utterance*, not of the
            # option. One word matching out of a seven-word sentence is a weak
            # signal however large the option's vocabulary is, and reporting it
            # as strong is what let "a delivery that hasn't shown up yet" match
            # a "yes" branch on the single word "delivery".
            best_name = name
            best_score = score
            best_coverage = overlap / max(len(utterance), 1)

    if best_name is None:
        return {"intent": None, "confidence": 0.0, "reasoning": "no option matched"}
    confidence = round(min(0.2 + 0.78 * best_coverage, 0.98), 3)
    return {
        "intent": best_name,
        "confidence": confidence,
        "reasoning": f"matched {best_name} on {best_coverage:.0%} of the reply",
    }


def _extract(payload: dict[str, Any]) -> dict[str, Any]:
    utterance = str(payload.get("utterance", "")).strip()
    field_type = str(payload.get("field_type") or payload.get("field") or "text").lower()
    lowered = utterance.lower()

    if field_type in {"boolean", "confirmation", "yes_no"}:
        words = set(re.findall(r"[a-z']+", lowered))
        if words & _AFFIRMATIVE:
            return {"value": True, "confidence": 0.95}
        if words & _NEGATIVE:
            return {"value": False, "confidence": 0.95}
        return {"value": None, "confidence": 0.0}

    for key, pattern in _PATTERNS.items():
        if key in field_type:
            match = pattern.search(utterance)
            if match:
                return {"value": match.group(0).strip(), "confidence": 0.93}
            return {"value": None, "confidence": 0.0}

    # Free text: anything that is not a pure filler response counts.
    if not utterance or _tokens(utterance) <= {"sorry", "what", "repeat", "pardon"}:
        return {"value": None, "confidence": 0.0}
    return {"value": utterance, "confidence": 0.7}


def _reply(payload: dict[str, Any]) -> dict[str, Any]:
    instruction = str(payload.get("instruction") or "").strip()
    utterance = str(payload.get("utterance") or "").strip()
    if instruction:
        return {"text": instruction}
    if utterance:
        return {"text": "Thanks, I've noted that. Let me take a look for you."}
    return {"text": "Sorry, I didn't catch that. Could you say it once more?"}


def _summarize(payload: dict[str, Any]) -> dict[str, Any]:
    transcript: list[dict[str, Any]] = payload.get("transcript") or []
    customer_lines = [t.get("text", "") for t in transcript if t.get("role") == "customer"]
    collected = payload.get("collected") or {}
    headline = customer_lines[0] if customer_lines else "No customer speech captured."
    details = ", ".join(f"{k}={v}" for k, v in collected.items())
    summary = f"Customer said: {headline}"
    if details:
        summary += f" Captured: {details}."
    return {"summary": summary, "turns": len(transcript)}
