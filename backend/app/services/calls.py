"""Call lifecycle: creating, cancelling, retrying and recording calls.

Everything that changes a call's queue state goes through here so the database
row and the Redis queue never disagree.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CallOutcome, CallPriority, CallStatus, EventType, FailureCategory
from app.db.models import Agent, Call, CallEvent, CallTurn
from app.queue.call_queue import CallQueue, QueuedCall, get_queue

logger = logging.getLogger(__name__)


class CallNotFoundError(LookupError):
    pass


class AgentNotFoundError(LookupError):
    pass


class CallService:
    def __init__(self, session: AsyncSession, queue: CallQueue | None = None) -> None:
        self.session = session
        self.queue = queue or get_queue()

    # ------------------------------ creating ------------------------------

    async def create_call(
        self,
        *,
        agent_id: uuid.UUID,
        to_number: str,
        from_number: str | None = None,
        priority: CallPriority = CallPriority.NORMAL,
        scheduled_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        max_attempts: int | None = None,
        idempotency_key: str | None = None,
    ) -> Call:
        if idempotency_key:
            existing = await self.session.scalar(
                select(Call).where(Call.idempotency_key == idempotency_key)
            )
            if existing is not None:
                logger.info(
                    "idempotent create returned the existing call",
                    extra={"call_id": str(existing.id), "idempotency_key": idempotency_key},
                )
                return existing

        agent = await self.session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFoundError(f"no agent {agent_id}")
        if not agent.is_active:
            raise ValueError(f"agent '{agent.name}' is not active")

        now = _utcnow()
        is_future = scheduled_at is not None and scheduled_at > now
        call = Call(
            agent_id=agent_id,
            to_number=to_number,
            from_number=from_number,
            priority=priority,
            status=CallStatus.SCHEDULED if is_future else CallStatus.QUEUED,
            scheduled_at=scheduled_at,
            queued_at=now,
            max_attempts=max_attempts or self.queue.settings.retry_max_attempts,
            call_metadata=metadata or {},
            idempotency_key=idempotency_key,
        )
        self.session.add(call)
        await self.session.flush()

        await self.queue.enqueue(self._job_for(call))
        await self.record_event(
            call,
            EventType.ENQUEUED,
            {"priority": str(priority), "scheduled_at": scheduled_at},
            publish=True,
        )
        return call

    def _job_for(self, call: Call, attempt: int | None = None) -> QueuedCall:
        return QueuedCall(
            call_id=str(call.id),
            agent_id=str(call.agent_id),
            to_number=call.to_number,
            priority=CallPriority(call.priority),
            attempt=call.attempt if attempt is None else attempt,
            max_attempts=call.max_attempts,
            scheduled_at_ms=(
                int(call.scheduled_at.timestamp() * 1000) if call.scheduled_at else None
            ),
        )

    # ------------------------------ control -------------------------------

    async def cancel_call(self, call_id: uuid.UUID) -> Call:
        call = await self.get_call(call_id)
        if CallStatus(call.status).is_terminal:
            raise ValueError(f"call is already {call.status}")
        call.status = CallStatus.CANCELED
        call.ended_at = _utcnow()
        call.failure_category = FailureCategory.CANCELED
        call.failure_reason = "canceled by operator"
        await self.queue.cancel(str(call.id))
        await self.record_event(call, EventType.CANCELED, {}, publish=True)
        return call

    async def retry_call(self, call_id: uuid.UUID, *, reset_attempts: bool = False) -> Call:
        """Put a finished or failed call back on the queue immediately."""
        call = await self.get_call(call_id)
        if call.status in {CallStatus.QUEUED, CallStatus.DIALING, CallStatus.IN_PROGRESS}:
            raise ValueError(f"call is already {call.status}")

        if reset_attempts:
            call.attempt = 0
        call.status = CallStatus.QUEUED
        call.scheduled_at = None
        call.next_retry_at = None
        call.ended_at = None
        call.failure_category = None
        call.failure_reason = None
        call.queued_at = _utcnow()

        # The call may still be sitting in the dead-letter queue.
        await self.queue.purge_dead_letter(str(call.id))
        await self.queue.enqueue(self._job_for(call))
        await self.record_event(call, EventType.ENQUEUED, {"manual_retry": True}, publish=True)
        return call

    # ------------------------------ recording -----------------------------

    async def mark_dialing(self, call: Call, attempt: int) -> None:
        call.status = CallStatus.DIALING
        call.attempt = attempt
        call.started_at = _utcnow()
        await self.record_event(
            call, EventType.DIALING, {"attempt": attempt, "to": call.to_number}, publish=True
        )

    async def mark_answered(self, call: Call, external_id: str) -> None:
        call.status = CallStatus.IN_PROGRESS
        call.external_id = external_id
        await self.record_event(
            call, EventType.ANSWERED, {"external_id": external_id}, publish=True
        )

    async def record_result(self, call: Call, result: Any) -> None:
        """Persist a :class:`~app.agents.runtime.ConversationResult`."""
        ended = _utcnow()
        call.status = CallStatus.COMPLETED
        call.ended_at = ended
        call.outcome = CallOutcome(result.outcome)
        call.summary = result.summary
        call.collected_data = dict(result.collected)
        call.duration_seconds = result.duration_seconds
        call.cost_cents = estimate_cost_cents(result)

        for turn in result.turns:
            self.session.add(
                CallTurn(
                    call_id=call.id,
                    index=turn.index,
                    role=turn.role,
                    text=turn.text,
                    node_id=turn.node_id,
                    stt_ms=turn.stt_ms,
                    llm_ms=turn.llm_ms,
                    tts_ms=turn.tts_ms,
                    latency_ms=turn.latency_ms,
                    confidence=turn.confidence,
                )
            )

        await self.record_event(
            call,
            EventType.COMPLETED,
            {
                "outcome": str(result.outcome),
                "node_path": result.node_path,
                "turns": len(result.turns),
                "ended_reason": result.ended_reason,
                "duration_seconds": round(result.duration_seconds, 2),
            },
            publish=True,
        )

    async def record_failure(
        self,
        call: Call,
        *,
        category: FailureCategory,
        reason: str,
        attempt: int,
        retry_at: datetime | None,
        dead_lettered: bool,
    ) -> None:
        call.attempt = attempt
        call.failure_category = category
        call.failure_reason = reason
        call.next_retry_at = retry_at

        if dead_lettered:
            call.status = CallStatus.FAILED
            call.ended_at = _utcnow()
            if category is FailureCategory.NO_ANSWER:
                call.outcome = CallOutcome.NO_ANSWER
            await self.record_event(
                call,
                EventType.DEAD_LETTERED,
                {"category": str(category), "reason": reason, "attempt": attempt},
                publish=True,
            )
        else:
            call.status = CallStatus.RETRYING
            call.scheduled_at = retry_at
            await self.record_event(
                call,
                EventType.RETRY_SCHEDULED,
                {
                    "category": str(category),
                    "reason": reason,
                    "attempt": attempt,
                    "retry_at": retry_at,
                },
                publish=True,
            )

    async def record_event(
        self,
        call: Call,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
        *,
        publish: bool = False,
    ) -> CallEvent:
        event = CallEvent(call_id=call.id, type=event_type, payload=_jsonable(payload or {}))
        self.session.add(event)
        if publish:
            await self.queue.publish_event(
                {
                    "type": str(event_type),
                    "call_id": str(call.id),
                    "agent_id": str(call.agent_id),
                    "status": str(call.status),
                    "payload": _jsonable(payload or {}),
                }
            )
        return event

    # ------------------------------ reading -------------------------------

    async def get_call(self, call_id: uuid.UUID) -> Call:
        call = await self.session.get(Call, call_id)
        if call is None:
            raise CallNotFoundError(f"no call {call_id}")
        return call


# Rough per-call cost model, used for dashboard reporting. Tune per contract.
STT_CENTS_PER_MINUTE = 0.43
TTS_CENTS_PER_1K_CHARS = 3.0
TELEPHONY_CENTS_PER_MINUTE = 1.3
LLM_CENTS_PER_1K_INPUT = 0.5
LLM_CENTS_PER_1K_OUTPUT = 2.5


def estimate_cost_cents(result: Any) -> float:
    minutes = max(result.duration_seconds, 0.0) / 60
    agent_chars = sum(len(t.text) for t in result.turns if str(t.role) == "agent")
    return round(
        minutes * (STT_CENTS_PER_MINUTE + TELEPHONY_CENTS_PER_MINUTE)
        + (agent_chars / 1000) * TTS_CENTS_PER_1K_CHARS
        + (result.input_tokens / 1000) * LLM_CENTS_PER_1K_INPUT
        + (result.output_tokens / 1000) * LLM_CENTS_PER_1K_OUTPUT,
        4,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key, value in payload.items()
    }
