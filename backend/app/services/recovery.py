"""Rebuild the queue from the database on startup.

The database is the source of truth for what still needs to happen. Redis holds
only the working set, so after a Redis restart (or when running on the
in-process stub, which starts empty every time) the queue has to be rebuilt or
those calls would sit in the database forever.

Enqueuing is keyed by call id, so running this on several replicas at once is
safe - the second write lands on the same sorted-set member.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CallPriority, CallStatus
from app.db.models import Call
from app.queue.call_queue import CallQueue, QueuedCall

logger = logging.getLogger(__name__)

# Waiting to run: re-enqueue exactly as they were.
PENDING = (CallStatus.QUEUED, CallStatus.SCHEDULED, CallStatus.RETRYING)
# Claimed by a worker that is no longer around: the conversation is lost, so
# these go back in the queue for a fresh attempt.
INTERRUPTED = (CallStatus.DIALING, CallStatus.IN_PROGRESS)


async def requeue_pending_calls(session: AsyncSession, queue: CallQueue) -> int:
    calls = (
        await session.scalars(
            select(Call).where(Call.status.in_([*PENDING, *INTERRUPTED])).order_by(Call.created_at)
        )
    ).all()
    if not calls:
        return 0

    now = datetime.now(UTC)
    restored = 0
    for call in calls:
        if CallStatus(call.status) in INTERRUPTED:
            call.status = CallStatus.RETRYING
            call.failure_reason = "worker went away mid-call; requeued on restart"

        scheduled = call.scheduled_at
        due_ms = (
            int(scheduled.timestamp() * 1000) if scheduled is not None and scheduled > now else None
        )
        await queue.enqueue(
            QueuedCall(
                call_id=str(call.id),
                agent_id=str(call.agent_id),
                to_number=call.to_number,
                priority=CallPriority(call.priority),
                attempt=call.attempt,
                max_attempts=call.max_attempts,
                scheduled_at_ms=due_ms,
            )
        )
        restored += 1

    logger.info("restored pending calls into the queue", extra={"count": restored})
    return restored
