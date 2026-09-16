"""Dry-run a workflow against a simulated customer.

Used by the dashboard's "test agent" panel: it exercises the real runtime and
the real workflow, but always through the mock voice stack, so it costs nothing
and places no call regardless of which providers are configured for production.
"""

from __future__ import annotations

from app.agents.runtime import ConversationRuntime
from app.core.config import Settings, get_settings
from app.db.models import Agent
from app.schemas.agents import SimulationRequest, SimulationResult, SimulationTurn
from app.voice.base import DialRequest, TelephonyError, VoiceStack
from app.voice.llm.mock import MockLanguageModel
from app.voice.stt.mock import MockSpeechToText
from app.voice.telephony.mock import MockTelephonyProvider
from app.voice.tts.mock import MockTextToSpeech
from app.worker.call_worker import build_agent_config


def build_mock_stack(seed: int) -> VoiceStack:
    # latency_scale=0: a simulation should return as fast as the UI can render.
    return VoiceStack(
        stt=MockSpeechToText(latency_scale=0.0),
        tts=MockTextToSpeech(latency_scale=0.0),
        llm=MockLanguageModel(latency_scale=0.0),
        telephony=MockTelephonyProvider(seed=seed, latency_scale=0.0),
    )


async def simulate_agent(
    agent: Agent, request: SimulationRequest, settings: Settings | None = None
) -> SimulationResult:
    settings = settings or get_settings()
    stack = build_mock_stack(request.seed if request.seed is not None else settings.mock_seed)
    config = build_agent_config(agent)

    simulation_id = f"sim-{agent.id}-{request.seed or 0}"
    try:
        session = await stack.telephony.dial(
            DialRequest(
                call_id=simulation_id,
                # A simulation should always connect; the reserved fixture
                # suffixes are for exercising the retry path via real calls.
                to_number=request.to_number,
                metadata=request.context,
            )
        )
    except TelephonyError as exc:
        return SimulationResult(
            outcome="no_answer",
            ended_reason=str(exc),
            node_path=[],
            collected={},
            summary="The simulated customer did not answer.",
            turns=[],
            duration_seconds=0.0,
        )

    result = await ConversationRuntime(stack, config).run(
        session, context={"to_number": request.to_number, **request.context}
    )
    return SimulationResult(
        outcome=str(result.outcome),
        ended_reason=result.ended_reason,
        node_path=result.node_path,
        collected=result.collected,
        summary=result.summary,
        duration_seconds=round(result.duration_seconds, 3),
        turns=[
            SimulationTurn(
                role=str(t.role),
                text=t.text,
                node_id=t.node_id,
                latency_ms=round(t.latency_ms, 2) if t.latency_ms is not None else None,
            )
            for t in result.turns
        ],
    )
