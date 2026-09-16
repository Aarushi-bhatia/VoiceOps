"""Drives one call through an agent's workflow.

The runtime owns the STT -> LLM -> TTS turn loop and the node transitions. It
knows nothing about queues or persistence: it takes an open
:class:`~app.voice.base.CallSession` and returns a
:class:`ConversationResult` the worker writes to the database.

Failure split, which matters for retries:

* Errors raised while *placing* the call (no answer, busy, invalid number)
  happen in ``telephony.dial`` - outside this runtime - and are retried.
* A line that drops *after* the customer answered ends the conversation with
  :attr:`CallOutcome.CUSTOMER_HUNG_UP`. Redialling would restart the
  conversation from scratch, which is worse for the customer than leaving it
  for a human to pick up, so this counts as a completed call.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.nlu import NLU
from app.agents.workflow import (
    BranchNode,
    CollectNode,
    HangupNode,
    Node,
    SayNode,
    TransferNode,
    Workflow,
    render,
)
from app.core.enums import CallOutcome, TurnRole
from app.voice.base import CallSession, TelephonyError, VoiceStack

logger = logging.getLogger(__name__)

EventHook = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class AgentConfig:
    """Everything the runtime needs from an Agent row."""

    name: str
    workflow: Workflow
    system_prompt: str = ""
    llm_model: str | None = None
    temperature: float = 0.3
    voice_id: str = "default"
    language: str = "en-US"
    max_turns: int = 24
    max_call_seconds: int = 600


@dataclass
class TurnRecord:
    index: int
    role: TurnRole
    text: str
    node_id: str | None = None
    stt_ms: float | None = None
    llm_ms: float | None = None
    tts_ms: float | None = None
    latency_ms: float | None = None
    confidence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"role": str(self.role), "text": self.text, "node_id": self.node_id}


@dataclass
class ConversationResult:
    outcome: CallOutcome
    turns: list[TurnRecord] = field(default_factory=list)
    collected: dict[str, Any] = field(default_factory=dict)
    node_path: list[str] = field(default_factory=list)
    summary: str = ""
    transferred_to: str | None = None
    ended_reason: str = "completed"
    duration_seconds: float = 0.0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def transcript(self) -> list[dict[str, Any]]:
        return [t.as_dict() for t in self.turns]


class ConversationRuntime:
    def __init__(
        self,
        stack: VoiceStack,
        config: AgentConfig,
        *,
        on_event: EventHook | None = None,
    ) -> None:
        self.stack = stack
        self.config = config
        self.nlu = NLU(
            stack.llm,
            system_prompt=config.system_prompt,
            model=config.llm_model,
            temperature=config.temperature,
        )
        self._on_event = on_event

    async def run(
        self, session: CallSession, *, context: dict[str, Any] | None = None
    ) -> ConversationResult:
        state = _RunState(context=dict(context or {}))
        started = time.perf_counter()
        workflow = self.config.workflow
        node: Node | None = workflow.start

        try:
            while node is not None:
                guard = self._guard(state, started)
                if guard is not None:
                    state.outcome, state.ended_reason = guard
                    await self._say_safely(session, state, _GUARD_MESSAGE, node.id)
                    break

                state.node_path.append(node.id)
                await self._emit("node_entered", {"node_id": node.id, "type": str(node.type)})
                node = await self._step(session, state, node)
            else:
                # Left the graph without reaching a transfer or hangup node.
                if state.outcome is CallOutcome.UNRESOLVED and state.ended_reason == "completed":
                    state.ended_reason = "workflow ran out of nodes"
        except TelephonyError as exc:
            # The customer is gone; keep whatever the conversation captured.
            state.outcome = CallOutcome.CUSTOMER_HUNG_UP
            state.ended_reason = str(exc)
            logger.info(
                "call ended early", extra={"reason": str(exc), "node_path": state.node_path}
            )

        result = ConversationResult(
            outcome=state.outcome,
            turns=state.turns,
            collected=state.collected,
            node_path=state.node_path,
            transferred_to=state.transferred_to,
            ended_reason=state.ended_reason,
            duration_seconds=time.perf_counter() - started,
            llm_calls=state.llm_calls,
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
        )
        result.summary = await self._summarize(state)
        await self._hangup_safely(session)
        return result

    # ------------------------------- nodes -------------------------------

    async def _step(self, session: CallSession, state: _RunState, node: Node) -> Node | None:
        workflow = self.config.workflow

        if isinstance(node, SayNode):
            await self._speak(session, state, node.prompt, node.id)
            return workflow.node(node.next) if node.next else None

        if isinstance(node, CollectNode):
            return await self._collect(session, state, node)

        if isinstance(node, BranchNode):
            return await self._branch(session, state, node)

        if isinstance(node, TransferNode):
            if node.prompt:
                await self._speak(session, state, node.prompt, node.id)
            await session.transfer(node.destination)
            state.outcome = node.outcome
            state.transferred_to = node.destination
            state.ended_reason = f"transferred to {node.destination}"
            await self._emit("transferred", {"destination": node.destination})
            return None

        if isinstance(node, HangupNode):
            if node.prompt:
                await self._speak(session, state, node.prompt, node.id)
            state.outcome = node.outcome
            state.ended_reason = "agent ended the call"
            return None

        raise ValueError(f"unsupported node type: {node.type}")

    async def _collect(
        self, session: CallSession, state: _RunState, node: CollectNode
    ) -> Node | None:
        workflow = self.config.workflow
        for attempt in range(node.max_attempts):
            prompt = node.prompt if attempt == 0 else (node.reprompt or node.prompt)
            await self._speak(session, state, prompt, node.id)
            utterance = await self._hear(session, state, node.id)
            if not utterance:
                continue

            value, confidence, llm = await self.nlu.extract(
                utterance,
                field=node.field,
                field_type=node.field_type,
                context=state.render_context(),
            )
            state.record_llm(llm)
            state.attach_llm_latency(llm.latency_ms)
            if value is not None:
                state.collected[node.field] = value
                await self._emit(
                    "field_collected",
                    {"field": node.field, "value": value, "confidence": confidence},
                )
                return workflow.node(node.next) if node.next else None

        logger.info("collect node gave up", extra={"node_id": node.id, "field": node.field})
        target = node.on_failure or node.next
        return workflow.node(target) if target else None

    async def _branch(
        self, session: CallSession, state: _RunState, node: BranchNode
    ) -> Node | None:
        workflow = self.config.workflow
        for attempt in range(node.max_attempts):
            prompt = node.prompt if attempt == 0 else (node.reprompt or node.prompt)
            if prompt:
                await self._speak(session, state, prompt, node.id)
            utterance = await self._hear(session, state, node.id)
            if not utterance:
                continue

            intent, confidence, llm = await self.nlu.classify(
                utterance, node.intents, context=state.render_context()
            )
            state.record_llm(llm)
            state.attach_llm_latency(llm.latency_ms)

            if intent is not None and confidence >= node.min_confidence:
                await self._emit("intent_matched", {"intent": intent, "confidence": confidence})
                target = next(i.next for i in node.intents if i.name == intent)
                return workflow.node(target)

            await self._emit(
                "intent_unmatched",
                {"node_id": node.id, "utterance": utterance, "confidence": confidence},
            )

        return workflow.node(node.default)

    # ------------------------------- turns -------------------------------

    async def _speak(
        self, session: CallSession, state: _RunState, template: str, node_id: str
    ) -> None:
        text = render(template, state.render_context())
        synthesis = await self.stack.tts.synthesize(
            text, voice_id=self.config.voice_id, language=self.config.language
        )
        await session.play(synthesis.audio)
        state.add_turn(
            TurnRecord(
                index=len(state.turns),
                role=TurnRole.AGENT,
                text=text,
                node_id=node_id,
                tts_ms=synthesis.latency_ms,
                latency_ms=synthesis.latency_ms,
            )
        )
        await self._emit("turn", {"role": "agent", "text": text, "node_id": node_id})

    async def _hear(self, session: CallSession, state: _RunState, node_id: str) -> str:
        audio = await session.listen(timeout_seconds=8.0)
        transcription = await self.stack.stt.transcribe(audio, language=self.config.language)
        text = transcription.text.strip()
        state.add_turn(
            TurnRecord(
                index=len(state.turns),
                role=TurnRole.CUSTOMER,
                text=text,
                node_id=node_id,
                stt_ms=transcription.latency_ms,
                latency_ms=transcription.latency_ms,
                confidence=transcription.confidence,
            )
        )
        await self._emit("turn", {"role": "customer", "text": text, "node_id": node_id})
        return text

    # ------------------------------- misc --------------------------------

    def _guard(self, state: _RunState, started: float) -> tuple[CallOutcome, str] | None:
        if len(state.turns) >= self.config.max_turns:
            return CallOutcome.UNRESOLVED, f"hit the {self.config.max_turns}-turn limit"
        if time.perf_counter() - started >= self.config.max_call_seconds:
            return CallOutcome.UNRESOLVED, f"hit the {self.config.max_call_seconds}s call limit"
        return None

    async def _summarize(self, state: _RunState) -> str:
        if not state.turns:
            return "No conversation took place."
        try:
            summary, llm = await self.nlu.summarize(
                [t.as_dict() for t in state.turns], state.collected
            )
            state.record_llm(llm)
            return summary
        except Exception as exc:  # a failed summary must not fail the call
            logger.warning("summary generation failed", extra={"error": str(exc)})
            return ""

    async def _say_safely(
        self, session: CallSession, state: _RunState, text: str, node_id: str
    ) -> None:
        try:
            await self._speak(session, state, text, node_id)
        except TelephonyError:
            pass

    async def _hangup_safely(self, session: CallSession) -> None:
        try:
            if session.is_open:
                await session.hangup()
        except Exception as exc:  # noqa: BLE001 - hangup is best effort
            logger.debug("hangup failed", extra={"error": str(exc)})

    async def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            await self._on_event(name, payload)


_GUARD_MESSAGE = "I'm sorry, I'm not able to finish this here. Let me get someone to call you back."


@dataclass
class _RunState:
    context: dict[str, Any]
    turns: list[TurnRecord] = field(default_factory=list)
    collected: dict[str, Any] = field(default_factory=dict)
    node_path: list[str] = field(default_factory=list)
    outcome: CallOutcome = CallOutcome.UNRESOLVED
    ended_reason: str = "completed"
    transferred_to: str | None = None
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def render_context(self) -> dict[str, Any]:
        return {**self.context, **self.collected}

    def add_turn(self, turn: TurnRecord) -> None:
        self.turns.append(turn)

    def record_llm(self, llm: Any) -> None:
        self.llm_calls += 1
        self.input_tokens += getattr(llm, "input_tokens", 0)
        self.output_tokens += getattr(llm, "output_tokens", 0)

    def attach_llm_latency(self, latency_ms: float) -> None:
        """Attribute an LLM call to the customer turn that triggered it."""
        for turn in reversed(self.turns):
            if turn.role is TurnRole.CUSTOMER:
                turn.llm_ms = latency_ms
                turn.latency_ms = (turn.stt_ms or 0.0) + latency_ms
                return
