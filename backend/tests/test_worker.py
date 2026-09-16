"""End-to-end worker behaviour: queue -> call -> database, including retries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.enums import CallOutcome, CallStatus, FailureCategory
from app.db.models import Call, CallEvent, CallTurn
from app.db.session import session_scope
from app.queue.retry import RetryPolicy
from app.services.calls import CallService
from app.worker.call_worker import CallWorker

pytestmark = pytest.mark.usefixtures("settings", "queue", "stack")


async def drain(worker: CallWorker, *, ticks: int = 5) -> None:
    """Run scheduling passes until every started call has finished."""
    for _ in range(ticks):
        await worker.tick()
        await worker._drain()


async def enqueue(db, agent, number: str, **kwargs) -> Call:
    service = CallService(db)
    call = await service.create_call(agent_id=agent.id, to_number=number, **kwargs)
    await db.commit()
    return call


async def reload(call_id) -> Call:
    async with session_scope() as session:
        return await session.get(Call, call_id)


async def test_worker_runs_a_call_and_records_the_transcript(db, agent, queue):
    call = await enqueue(db, agent, "+15551111")
    worker = CallWorker()
    await drain(worker)

    stored = await reload(call.id)
    assert stored.status is CallStatus.COMPLETED
    assert stored.outcome in set(CallOutcome)
    assert stored.started_at is not None and stored.ended_at is not None
    assert stored.duration_seconds is not None
    assert stored.attempt == 1
    assert stored.cost_cents > 0

    async with session_scope() as session:
        turns = (await session.scalars(select(CallTurn).where(CallTurn.call_id == call.id))).all()
        events = (
            await session.scalars(select(CallEvent).where(CallEvent.call_id == call.id))
        ).all()
    assert len(turns) >= 2
    types = {str(e.type) for e in events}
    assert {"enqueued", "dialing", "answered", "completed"} <= types

    stats = await queue.stats()
    assert stats.processing == 0 and stats.ready == 0
    assert stats.counters["completed"] == 1


async def test_transient_failure_schedules_a_retry(db, agent, queue):
    # +...9999 never answers, which is a transient failure.
    call = await enqueue(db, agent, "+15559999")
    worker = CallWorker()
    await drain(worker, ticks=1)

    stored = await reload(call.id)
    assert stored.status is CallStatus.RETRYING
    assert stored.attempt == 1
    assert stored.failure_category is FailureCategory.NO_ANSWER
    assert stored.next_retry_at is not None

    stats = await queue.stats()
    assert stats.scheduled == 1, "the retry waits for its backoff window"
    assert stats.counters["retried"] == 1


async def test_permanent_failure_goes_straight_to_the_dead_letter_queue(db, agent, queue):
    call = await enqueue(db, agent, "+15550000")  # invalid number
    worker = CallWorker()
    await drain(worker, ticks=1)

    stored = await reload(call.id)
    assert stored.status is CallStatus.FAILED
    assert stored.failure_category is FailureCategory.INVALID_NUMBER
    assert stored.attempt == 1, "a permanent failure is not retried"

    stats = await queue.stats()
    assert stats.dead_letter == 1
    entries = await queue.list_dead_letter()
    assert entries[0].call_id == str(call.id)


async def test_retries_are_exhausted_then_dead_lettered(db, agent, queue):
    call = await enqueue(db, agent, "+15559999", max_attempts=3)
    worker = CallWorker()
    # Collapse the backoff so the test does not have to wait it out.
    worker.retry_policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.0, jitter_ratio=0.0)

    for _ in range(3):
        await worker.tick()
        await worker._drain()
        # Make whatever was scheduled immediately due.
        for call_id in await queue.redis.zrange(queue.keys.scheduled, 0, -1):
            await queue.redis.zadd(queue.keys.scheduled, {call_id: 0})

    stored = await reload(call.id)
    assert stored.status is CallStatus.FAILED
    assert stored.attempt == 3
    assert (await queue.stats()).dead_letter == 1


async def test_cancelled_calls_are_dropped_without_dialling(db, agent, queue):
    call = await enqueue(db, agent, "+15551111")
    service = CallService(db, queue)
    await service.cancel_call(call.id)
    await db.commit()

    worker = CallWorker()
    await drain(worker, ticks=1)

    stored = await reload(call.id)
    assert stored.status is CallStatus.CANCELED
    assert stored.started_at is None, "a cancelled call is never dialled"


async def test_expired_lease_is_reclaimed_and_retried(db, agent, queue):
    await enqueue(db, agent, "+15551111")
    worker = CallWorker()
    # Claim with an already-expired lease, the way a crashed worker leaves it.
    claimed = await queue.claim(1, lease_seconds=-1)
    assert claimed

    await worker.tick()
    stats = await queue.stats()
    assert stats.counters["reclaimed"] == 1
    assert stats.scheduled + stats.ready >= 1, "the reclaimed call is queued again"


async def test_worker_respects_its_concurrency_limit(db, agent, settings, queue):
    for index in range(6):
        await enqueue(db, agent, f"+1555100{index}")
    settings.worker_concurrency = 2
    worker = CallWorker(settings=settings)

    started = await worker.tick()
    assert started == 2
    assert worker.capacity == 0
    await worker._drain()
    assert worker.capacity == 2


async def test_scheduled_calls_are_not_run_before_they_are_due(db, agent, queue):
    call = await enqueue(
        db,
        agent,
        "+15551111",
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )
    worker = CallWorker()
    assert await worker.tick() == 0

    stored = await reload(call.id)
    assert stored.status is CallStatus.SCHEDULED


async def test_worker_run_forever_stops_cleanly(db, agent):
    worker = CallWorker()
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.05)
    await worker.stop()
    await asyncio.wait_for(task, timeout=5)


async def test_pending_calls_are_restored_after_a_queue_loss(db, agent, queue):
    """The database is the source of truth if Redis is emptied or replaced."""
    from app.queue.redis_client import InMemoryRedis
    from app.services.recovery import requeue_pending_calls

    for number in ("+15551111", "+15552222"):
        await enqueue(db, agent, number)
    assert (await queue.stats()).ready == 2

    # Simulate Redis coming back empty.
    queue._redis = InMemoryRedis()
    assert (await queue.stats()).ready == 0

    async with session_scope() as session:
        restored = await requeue_pending_calls(session, queue)
    assert restored == 2
    assert (await queue.stats()).ready == 2


async def test_interrupted_calls_are_requeued_as_retrying(db, agent, queue):
    from app.services.recovery import requeue_pending_calls

    call = await enqueue(db, agent, "+15551111")
    async with session_scope() as session:
        stored = await session.get(Call, call.id)
        stored.status = CallStatus.IN_PROGRESS

    async with session_scope() as session:
        assert await requeue_pending_calls(session, queue) == 1

    stored = await reload(call.id)
    assert stored.status is CallStatus.RETRYING
    assert "mid-call" in stored.failure_reason


async def test_timestamps_come_back_timezone_aware(db, agent):
    """SQLite has no timezone support; the column type normalises for both backends."""
    when = datetime.now(UTC) + timedelta(hours=2)
    call = await enqueue(db, agent, "+15551111", scheduled_at=when)

    stored = await reload(call.id)
    assert stored.created_at.tzinfo is not None
    assert stored.scheduled_at.tzinfo is not None
    # The comparison that used to raise on SQLite.
    assert stored.scheduled_at > datetime.now(UTC)
