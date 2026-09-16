"""Queue observability and dead-letter operations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import Calls, Queue
from app.core.enums import CallStatus, EventType
from app.schemas.common import Message
from app.schemas.ops import DeadLetterEntry, QueueStatsRead

router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("/stats", response_model=QueueStatsRead)
async def queue_stats(queue: Queue) -> QueueStatsRead:
    stats = await queue.stats()
    return QueueStatsRead(
        ready=stats.ready,
        scheduled=stats.scheduled,
        processing=stats.processing,
        dead_letter=stats.dead_letter,
        backlog=stats.backlog,
        counters=stats.counters,
    )


@router.get("/dead-letter", response_model=list[DeadLetterEntry])
async def list_dead_letter(
    queue: Queue,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[DeadLetterEntry]:
    jobs = await queue.list_dead_letter(limit=limit, offset=offset)
    return [
        DeadLetterEntry(
            call_id=job.call_id,
            agent_id=job.agent_id,
            to_number=job.to_number,
            priority=str(job.priority),
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            last_error=job.last_error,
            enqueued_at=datetime.fromtimestamp(job.enqueued_at_ms / 1000, tz=UTC),
        )
        for job in jobs
    ]


@router.post("/dead-letter/{call_id}/requeue", response_model=Message)
async def requeue_dead_letter(call_id: uuid.UUID, queue: Queue, service: Calls) -> Message:
    """Put a permanently failed call back on the ready queue for another run."""
    job = await queue.requeue_dead(str(call_id))
    if job is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"call {call_id} is not in the dead letter queue"
        )

    call = await service.get_call(call_id)
    call.status = CallStatus.QUEUED
    call.attempt = job.attempt
    call.failure_category = None
    call.failure_reason = None
    call.next_retry_at = None
    call.ended_at = None
    await service.record_event(
        call, EventType.ENQUEUED, {"requeued_from": "dead_letter"}, publish=True
    )
    return Message(detail=f"call {call_id} requeued")


@router.delete("/dead-letter/{call_id}", response_model=Message)
async def purge_dead_letter(call_id: uuid.UUID, queue: Queue) -> Message:
    """Drop a call from the dead-letter queue without retrying it."""
    if not await queue.purge_dead_letter(str(call_id)):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"call {call_id} is not in the dead letter queue"
        )
    return Message(detail=f"call {call_id} purged")
