"""Language-understanding calls the conversation runtime makes.

Each task sends the model a small JSON envelope and asks for JSON back against
an explicit schema. The mock model dispatches on the envelope's ``task`` field;
the Anthropic adapter passes the schema through ``output_config.format`` so the
reply is guaranteed to parse.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.workflow import Intent
from app.voice.base import LanguageModel, LLMMessage, LLMResult

logger = logging.getLogger(__name__)

CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["intent", "confidence", "reasoning"],
    "additionalProperties": False,
}

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "value": {"type": ["string", "number", "boolean", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["value", "confidence"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

_CLASSIFY_SYSTEM = (
    "You classify a customer's spoken reply into exactly one of the supplied "
    "intents. Judge only what the customer said. If none of the intents clearly "
    "applies, return null with a low confidence rather than guessing."
)
_EXTRACT_SYSTEM = (
    "You extract a single field value from a customer's spoken reply. Return the "
    "value exactly as the customer gave it, normalised for the requested type. "
    "If the reply does not contain the value, return null."
)
_SUMMARY_SYSTEM = (
    "You write a two-sentence summary of a customer support call for an agent "
    "reviewing it later. State what the customer wanted and how it ended."
)


class NLU:
    def __init__(
        self,
        llm: LanguageModel,
        *,
        system_prompt: str = "",
        model: str | None = None,
        temperature: float = 0.3,
    ) -> None:
        self.llm = llm
        self.system_prompt = system_prompt
        self.model = model
        self.temperature = temperature

    async def classify(
        self, utterance: str, intents: list[Intent], *, context: dict[str, Any] | None = None
    ) -> tuple[str | None, float, LLMResult]:
        payload = {
            "task": "classify",
            "utterance": utterance,
            "options": [
                {"name": i.name, "description": i.description, "examples": i.examples}
                for i in intents
            ],
            "context": context or {},
        }
        result = await self._call(_CLASSIFY_SYSTEM, payload, CLASSIFY_SCHEMA)
        data = result.structured or {}
        intent = data.get("intent")
        valid = {i.name for i in intents}
        if intent not in valid:
            intent = None
        return intent, float(data.get("confidence") or 0.0), result

    async def extract(
        self,
        utterance: str,
        *,
        field: str,
        field_type: str = "text",
        context: dict[str, Any] | None = None,
    ) -> tuple[Any, float, LLMResult]:
        payload = {
            "task": "extract",
            "utterance": utterance,
            "field": field,
            "field_type": field_type,
            "context": context or {},
        }
        result = await self._call(_EXTRACT_SYSTEM, payload, EXTRACT_SCHEMA)
        data = result.structured or {}
        value = data.get("value")
        if isinstance(value, str) and not value.strip():
            value = None
        return value, float(data.get("confidence") or 0.0), result

    async def summarize(
        self, transcript: list[dict[str, Any]], collected: dict[str, Any]
    ) -> tuple[str, LLMResult]:
        payload = {"task": "summarize", "transcript": transcript, "collected": collected}
        result = await self._call(_SUMMARY_SYSTEM, payload, SUMMARY_SCHEMA, max_tokens=256)
        data = result.structured or {}
        return str(data.get("summary") or ""), result

    async def _call(
        self,
        task_system: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        *,
        max_tokens: int = 256,
    ) -> LLMResult:
        system = task_system if not self.system_prompt else f"{self.system_prompt}\n\n{task_system}"
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=json.dumps(payload, default=str)),
        ]
        return await self.llm.complete(
            messages,
            model=self.model,
            temperature=self.temperature,
            max_tokens=max_tokens,
            json_schema=schema,
        )
