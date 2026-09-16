"""Conversation runtime: node transitions, retries within a node, and drop-outs."""

from __future__ import annotations

import pytest

from app.agents.runtime import AgentConfig, ConversationRuntime
from app.agents.workflow import DEFAULT_WORKFLOW, Workflow
from app.core.enums import CallOutcome, TurnRole
from app.voice.base import AudioChunk, DialRequest, TelephonyError, VoiceStack
from app.voice.llm.mock import MockLanguageModel
from app.voice.stt.mock import MockSpeechToText
from app.voice.telephony.mock import MockTelephonyProvider
from app.voice.tts.mock import MockTextToSpeech


def build_stack() -> VoiceStack:
    return VoiceStack(
        stt=MockSpeechToText(0.0),
        tts=MockTextToSpeech(0.0),
        llm=MockLanguageModel(0.0),
        telephony=MockTelephonyProvider(latency_scale=0.0),
    )


def config(workflow: dict, **kwargs) -> AgentConfig:
    return AgentConfig(name="Ava", workflow=Workflow.model_validate(workflow), **kwargs)


class ScriptedSession:
    """A call session that replays a fixed list of customer utterances."""

    def __init__(self, replies: list[str | Exception]) -> None:
        self.call_id = "scripted"
        self.external_id = "scripted-1"
        self.replies = list(replies)
        self.spoken: list[str] = []
        self.transferred_to: str | None = None
        self.hung_up = False
        self._open = True

    @property
    def is_open(self) -> bool:
        return self._open

    async def play(self, audio: AudioChunk) -> None:
        self.spoken.append(audio.text_hint or "")

    async def listen(self, *, timeout_seconds: float = 8.0) -> AudioChunk:
        if not self.replies:
            return AudioChunk(b"", text_hint=None)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            self._open = False
            raise reply
        return AudioChunk(b"x" if reply else b"", text_hint=reply or None)

    async def transfer(self, destination: str) -> None:
        self.transferred_to = destination
        self._open = False

    async def hangup(self, reason: str = "completed") -> None:
        self.hung_up = True
        self._open = False


async def test_happy_path_reaches_the_resolved_hangup():
    session = ScriptedSession(
        ["I need a refund, the item arrived damaged", "It's ORD-12345", "Yes, that's correct"]
    )
    result = await ConversationRuntime(build_stack(), config(DEFAULT_WORKFLOW)).run(session)

    assert result.outcome is CallOutcome.RESOLVED
    assert result.collected == {"order_id": "ORD-12345"}
    assert result.node_path == ["greeting", "collect_order", "confirm", "resolve", "goodbye"]
    assert session.hung_up
    assert "ORD-12345" in session.spoken[-2], "collected values render into later prompts"


async def test_unrecognised_reply_is_re_asked_before_falling_back():
    session = ScriptedSession(["mumble mumble", "sorry what", "third try"])
    result = await ConversationRuntime(build_stack(), config(DEFAULT_WORKFLOW)).run(session)

    greeting_prompts = [s for s in session.spoken if "help you with" in s]
    assert len(greeting_prompts) == 2, "branch retries once with the reprompt"
    assert result.outcome is CallOutcome.ESCALATED
    assert session.transferred_to == "support-queue"


async def test_collect_retries_then_routes_to_on_failure():
    workflow = {
        "start_node": "ask",
        "nodes": [
            {
                "id": "ask",
                "type": "collect",
                "prompt": "What's your order number?",
                "reprompt": "Sorry, the order number again?",
                "field": "order_id",
                "field_type": "order_id",
                "max_attempts": 2,
                "next": "done",
                "on_failure": "human",
            },
            {"id": "done", "type": "hangup", "outcome": "resolved"},
            {"id": "human", "type": "transfer", "destination": "support", "outcome": "escalated"},
        ],
    }
    session = ScriptedSession(["um I don't have it", "no idea sorry"])
    result = await ConversationRuntime(build_stack(), config(workflow)).run(session)

    assert result.node_path == ["ask", "human"]
    assert result.outcome is CallOutcome.ESCALATED
    assert session.spoken.count("Sorry, the order number again?") == 1


async def test_silence_does_not_consume_the_collected_value():
    workflow = {
        "start_node": "ask",
        "nodes": [
            {
                "id": "ask",
                "type": "collect",
                "prompt": "Order number?",
                "field": "order_id",
                "field_type": "order_id",
                "max_attempts": 2,
                "next": "done",
            },
            {"id": "done", "type": "hangup", "outcome": "resolved"},
        ],
    }
    session = ScriptedSession(["", "ORD-99887"])
    result = await ConversationRuntime(build_stack(), config(workflow)).run(session)
    assert result.collected == {"order_id": "ORD-99887"}
    assert result.outcome is CallOutcome.RESOLVED


async def test_customer_hanging_up_ends_the_call_without_failing_it():
    session = ScriptedSession(
        ["I need a refund for a damaged item", TelephonyError("caller hung up")]
    )
    result = await ConversationRuntime(build_stack(), config(DEFAULT_WORKFLOW)).run(session)

    assert result.outcome is CallOutcome.CUSTOMER_HUNG_UP
    assert "hung up" in result.ended_reason
    assert result.turns, "turns captured before the drop are kept"


async def test_turn_limit_stops_a_looping_workflow():
    workflow = {
        "start_node": "loop",
        "nodes": [
            {
                "id": "loop",
                "type": "branch",
                "prompt": "Anything else?",
                "intents": [{"name": "yes", "description": "yes more", "next": "loop"}],
                "default": "bye",
                "max_attempts": 1,
            },
            {"id": "bye", "type": "hangup"},
        ],
    }
    session = ScriptedSession(["yes more"] * 50)
    result = await ConversationRuntime(build_stack(), config(workflow, max_turns=6)).run(session)

    assert result.outcome is CallOutcome.UNRESOLVED
    assert "turn limit" in result.ended_reason
    assert len(result.turns) <= 8


async def test_transcript_records_both_speakers_with_latencies():
    session = ScriptedSession(["I need a refund for a damaged order", "ORD-55555", "yes"])
    result = await ConversationRuntime(build_stack(), config(DEFAULT_WORKFLOW)).run(session)

    roles = {t.role for t in result.turns}
    assert roles == {TurnRole.AGENT, TurnRole.CUSTOMER}
    agent_turns = [t for t in result.turns if t.role is TurnRole.AGENT]
    customer_turns = [t for t in result.turns if t.role is TurnRole.CUSTOMER]
    assert all(t.tts_ms is not None for t in agent_turns)
    assert all(t.stt_ms is not None for t in customer_turns)
    assert any(t.llm_ms is not None for t in customer_turns)
    assert [t.index for t in result.turns] == list(range(len(result.turns)))


async def test_summary_is_generated_for_a_real_conversation():
    session = ScriptedSession(["I need a refund for a damaged order", "ORD-11111", "yes"])
    result = await ConversationRuntime(build_stack(), config(DEFAULT_WORKFLOW)).run(session)
    assert result.summary
    assert "ORD-11111" in result.summary


async def test_events_are_emitted_for_each_node_and_turn():
    seen: list[tuple[str, dict]] = []

    async def hook(name, payload):
        seen.append((name, payload))

    session = ScriptedSession(["I need a refund for a damaged order", "ORD-22222", "yes"])
    await ConversationRuntime(build_stack(), config(DEFAULT_WORKFLOW), on_event=hook).run(session)

    names = [n for n, _ in seen]
    assert "node_entered" in names
    assert "turn" in names
    assert "intent_matched" in names
    assert "field_collected" in names


@pytest.mark.parametrize(
    ("number", "expected"),
    [("+15550000", "invalid_number"), ("+15559999", "no_answer"), ("+15558888", "busy")],
)
async def test_mock_dial_fixtures_produce_their_failure_categories(number, expected):
    provider = MockTelephonyProvider(latency_scale=0.0)
    with pytest.raises(TelephonyError) as exc:
        await provider.dial(DialRequest(call_id="c", to_number=number))
    assert str(exc.value.category) == expected


async def test_busy_number_answers_once_attempts_advance():
    provider = MockTelephonyProvider(latency_scale=0.0)
    session = await provider.dial(DialRequest(call_id="c", to_number="+15558888", attempt=2))
    assert session.is_open
