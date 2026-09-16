"""Queue semantics: priority ordering, leases, scheduling and dead-lettering."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import CallPriority
from app.queue.call_queue import CallQueue, QueuedCall
from app.queue.redis_client import now_ms

pytestmark = pytest.mark.usefixtures("settings")


def make_job(number: str, priority: CallPriority = CallPriority.NORMAL, **kwargs) -> QueuedCall:
    return QueuedCall(
        call_id=str(uuid.uuid4()), agent_id="agent", to_number=number, priority=priority, **kwargs
    )


async def test_claims_highest_priority_first(queue: CallQueue):
    normal = make_job("+1normal", CallPriority.NORMAL)
    urgent = make_job("+1urgent", CallPriority.URGENT)
    low = make_job("+1low", CallPriority.LOW)
    high = make_job("+1high", CallPriority.HIGH)
    for job in (normal, urgent, low, high):
        await queue.enqueue(job)

    claimed = await queue.claim(4)
    assert [j.to_number for j in claimed] == ["+1urgent", "+1high", "+1normal", "+1low"]


async def test_same_priority_is_first_in_first_out(queue: CallQueue):
    jobs = [make_job(f"+1{i}") for i in range(4)]
    for job in jobs:
        await queue.enqueue(job)
        # The ready score is priority band + enqueue time in ms, so separate
        # the enqueues enough that the ordering is not a millisecond tie.
        await asyncio.sleep(0.002)

    claimed = await queue.claim(4)
    assert [j.to_number for j in claimed] == [j.to_number for j in jobs]


async def test_claimed_calls_are_leased_and_not_handed_out_twice(queue: CallQueue):
    await queue.enqueue(make_job("+1one"))
    assert len(await queue.claim(5)) == 1
    assert await queue.claim(5) == []

    stats = await queue.stats()
    assert stats.processing == 1
    assert stats.ready == 0


async def test_heartbeat_extends_a_lease_and_fails_after_release(queue: CallQueue):
    job = make_job("+1hb")
    await queue.enqueue(job)
    await queue.claim(1)

    assert await queue.heartbeat(job.call_id) is True
    await queue.release(job.call_id)
    assert await queue.heartbeat(job.call_id) is False


async def test_expired_leases_are_reclaimed(queue: CallQueue):
    job = make_job("+1stall")
    await queue.enqueue(job)
    # A lease already in the past is exactly what a crashed worker leaves behind.
    await queue.claim(1, lease_seconds=-1)

    reclaimed = await queue.reclaim_stalled()
    assert [j.call_id for j in reclaimed] == [job.call_id]
    assert (await queue.stats()).processing == 0


async def test_scheduled_calls_wait_until_they_are_due(queue: CallQueue):
    future = datetime.now(UTC) + timedelta(hours=1)
    job = make_job("+1later", scheduled_at_ms=int(future.timestamp() * 1000))

    assert await queue.enqueue(job) == "scheduled"
    assert await queue.claim(5) == []
    assert (await queue.stats()).scheduled == 1

    # Nothing is due yet.
    assert await queue.promote_due() == []

    past = datetime.now(UTC) - timedelta(seconds=1)
    job.scheduled_at_ms = int(past.timestamp() * 1000)
    await queue.enqueue(job)
    assert [j.call_id for j in await queue.claim(5)] == [job.call_id]


async def test_promote_due_moves_scheduled_calls_into_ready(queue: CallQueue):
    future = datetime.now(UTC) + timedelta(hours=2)
    job = make_job("+1due", CallPriority.HIGH, scheduled_at_ms=int(future.timestamp() * 1000))
    await queue.enqueue(job)
    assert (await queue.stats()).scheduled == 1

    # Simulate time passing by moving the due score into the past.
    await queue.redis.zadd(queue.keys.scheduled, {job.call_id: now_ms() - 1000})

    assert await queue.promote_due() == [job.call_id]
    stats = await queue.stats()
    assert (stats.scheduled, stats.ready) == (0, 1)
    assert [j.call_id for j in await queue.claim(1)] == [job.call_id]


async def test_dead_letter_then_requeue_round_trip(queue: CallQueue):
    job = make_job("+1dead")
    await queue.enqueue(job)
    await queue.claim(1)
    job.attempt = 4

    await queue.dead_letter(job, "invalid_number: not a real number")
    stats = await queue.stats()
    assert stats.dead_letter == 1
    assert stats.processing == 0

    entries = await queue.list_dead_letter()
    assert entries[0].call_id == job.call_id
    assert entries[0].last_error is not None

    requeued = await queue.requeue_dead(job.call_id)
    assert requeued is not None
    assert requeued.attempt == 0, "a manual requeue restarts the attempt count"
    assert (await queue.stats()).dead_letter == 0
    assert len(await queue.claim(1)) == 1


async def test_purge_dead_letter(queue: CallQueue):
    job = make_job("+1purge")
    await queue.enqueue(job)
    await queue.dead_letter(job, "gone")
    assert await queue.purge_dead_letter(job.call_id) is True
    assert await queue.purge_dead_letter(job.call_id) is False


async def test_schedule_retry_increments_attempt_and_parks_the_call(queue: CallQueue):
    job = make_job("+1retry")
    await queue.enqueue(job)
    await queue.claim(1)

    retry_at = datetime.now(UTC) + timedelta(minutes=5)
    assert await queue.schedule_retry(job, retry_at) == "scheduled"
    assert job.attempt == 1
    assert await queue.claim(5) == []
    assert (await queue.stats()).counters["retried"] == 1


async def test_position_reports_place_in_line(queue: CallQueue):
    first = make_job("+1a", CallPriority.URGENT)
    second = make_job("+1b", CallPriority.NORMAL)
    await queue.enqueue(first)
    await queue.enqueue(second)

    assert await queue.position(first.call_id) == 0
    assert await queue.position(second.call_id) == 1
    await queue.claim(2)
    assert await queue.position(first.call_id) is None


async def test_cancel_removes_a_waiting_call(queue: CallQueue):
    job = make_job("+1cancel")
    await queue.enqueue(job)
    await queue.cancel(job.call_id)
    assert await queue.claim(5) == []
    assert (await queue.stats()).counters["canceled"] == 1


async def test_scheduling_a_retry_releases_the_lease(queue: CallQueue):
    """A retried call must not stay in `processing`.

    The worker that failed the call still holds its lease. If enqueueing the
    retry left that lease behind, the reaper would reclaim the call once it
    expired and the customer would be phoned twice.
    """
    job = make_job("+1leak")
    await queue.enqueue(job)
    await queue.claim(1)
    assert (await queue.stats()).processing == 1

    await queue.schedule_retry(job, datetime.now(UTC) + timedelta(minutes=5))

    stats = await queue.stats()
    assert stats.processing == 0, "the lease must be released when the retry is queued"
    assert stats.scheduled == 1


async def test_a_retried_call_is_not_also_reclaimed(queue: CallQueue):
    job = make_job("+1double")
    await queue.enqueue(job)
    # An expired lease is what the reaper looks for.
    await queue.claim(1, lease_seconds=-1)
    await queue.schedule_retry(job, datetime.now(UTC) + timedelta(minutes=5))

    assert await queue.reclaim_stalled() == [], "a rescheduled call must not be reclaimed too"
    assert (await queue.stats()).counters["reclaimed"] == 0


async def test_requeueing_releases_a_dead_workers_lease(queue: CallQueue):
    """Recovery and manual retry paths go through enqueue, so they release too."""
    job = make_job("+1recover")
    await queue.enqueue(job)
    await queue.claim(1, lease_seconds=-1)

    await queue.enqueue(job)

    stats = await queue.stats()
    assert (stats.processing, stats.ready) == (0, 1)
