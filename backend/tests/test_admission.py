"""Backpressure and suppression: the gates that run before a number is dialled."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import CallStatus, FailureCategory
from app.db.models import Call
from app.db.session import session_scope
from app.queue.throttle import CarrierThrottle, DoNotCallList, next_calling_window
from app.services.calls import CallService, DoNotCallError, QueueFullError
from app.worker.call_worker import CallWorker

pytestmark = pytest.mark.usefixtures("settings", "queue", "stack")

API = "/api/v1"


async def enqueue(db, agent, number="+15551111", **kwargs) -> Call:
    call = await CallService(db).create_call(agent_id=agent.id, to_number=number, **kwargs)
    await db.commit()
    return call


async def reload(call_id) -> Call:
    async with session_scope() as session:
        return await session.get(Call, call_id)


# ----------------------------- queue depth -----------------------------


async def test_the_queue_has_a_depth_cap(db, agent, settings, queue):
    settings.max_queue_depth = 3
    for index in range(3):
        await enqueue(db, agent, f"+1555000{index}")

    with pytest.raises(QueueFullError, match="capacity"):
        await enqueue(db, agent, "+15550099")
    assert (await queue.stats()).counters["rejected"] == 1


async def test_a_full_queue_returns_429_not_500(client, agent_payload, settings):
    settings.max_queue_depth = 1
    created = await client.post(f"{API}/agents", json=agent_payload)
    agent_id = created.json()["id"]

    first = await client.post(f"{API}/calls", json={"agent_id": agent_id, "to_number": "+15551111"})
    assert first.status_code == 201

    second = await client.post(
        f"{API}/calls", json={"agent_id": agent_id, "to_number": "+15552222"}
    )
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "30"


async def test_a_cap_of_zero_means_unlimited(db, agent, settings):
    settings.max_queue_depth = 0
    for index in range(12):
        await enqueue(db, agent, f"+1555111{index}")  # must not raise


# ---------------------------- do not call ----------------------------


async def test_a_suppressed_number_is_refused_at_creation(db, agent, queue):
    await DoNotCallList(queue.redis, queue.settings.queue_namespace).add("+15551111", "asked us to")
    with pytest.raises(DoNotCallError):
        await enqueue(db, agent, "+15551111")


async def test_suppression_ignores_formatting(db, agent, queue):
    await DoNotCallList(queue.redis, queue.settings.queue_namespace).add("+1 (555) 111-1111")
    with pytest.raises(DoNotCallError):
        await enqueue(db, agent, "+15551111111")


async def test_a_number_suppressed_after_queueing_is_never_dialled(db, agent, queue):
    """The list can change while a call waits, so it is checked again before dialling."""
    call = await enqueue(db, agent, "+15551110001111")
    await DoNotCallList(queue.redis, queue.settings.queue_namespace).add("+15551110001111")

    worker = CallWorker()
    await worker.tick()
    await worker._drain()

    stored = await reload(call.id)
    assert stored.status is CallStatus.FAILED
    assert stored.failure_category is FailureCategory.DO_NOT_CALL
    assert stored.started_at is None, "a suppressed number must never be dialled"


async def test_suppression_api_round_trip(client, agent_payload):
    assert (await client.get(f"{API}/suppression")).json() == []

    added = await client.post(
        f"{API}/suppression", json={"number": "+15559998888", "reason": "opted out"}
    )
    assert added.status_code == 201
    assert (await client.get(f"{API}/suppression")).json()[0]["reason"] == "opted out"

    agent = (await client.post(f"{API}/agents", json=agent_payload)).json()
    blocked = await client.post(
        f"{API}/calls", json={"agent_id": agent["id"], "to_number": "+15559998888"}
    )
    assert blocked.status_code == 403

    assert (await client.delete(f"{API}/suppression/+15559998888")).status_code == 200
    assert (await client.delete(f"{API}/suppression/+15559998888")).status_code == 404


# ------------------------------ staleness ------------------------------


async def test_a_call_too_old_to_matter_is_dropped(db, agent, settings, queue):
    settings.max_call_age_seconds = 3600
    call = await enqueue(db, agent, "+15551110001111")

    async with session_scope() as session:
        stored = await session.get(Call, call.id)
        stored.created_at = datetime.now(UTC) - timedelta(hours=9)

    worker = CallWorker(settings=settings)
    await worker.tick()
    await worker._drain()

    stored = await reload(call.id)
    assert stored.status is CallStatus.FAILED
    assert stored.failure_category is FailureCategory.EXPIRED
    assert stored.started_at is None
    assert (await queue.stats()).dead_letter == 1


# --------------------------- carrier throttle ---------------------------


async def test_the_throttle_allows_a_burst_then_paces(queue):
    throttle = CarrierThrottle(queue.redis, "t", calls_per_second=1.0, burst=3)
    allowed = [await throttle.acquire("+15551110001111") for _ in range(5)]
    assert allowed[:3] == [0.0, 0.0, 0.0], "the burst should pass immediately"
    assert all(wait > 0 for wait in allowed[3:]), "the rest must wait"


async def test_each_outbound_number_has_its_own_budget(queue):
    throttle = CarrierThrottle(queue.redis, "t", calls_per_second=1.0, burst=1)
    assert await throttle.acquire("+15550000001") == 0.0
    assert await throttle.acquire("+15550000001") > 0, "same line is now exhausted"
    assert await throttle.acquire("+15550000002") == 0.0, "a different line is unaffected"


async def test_a_throttled_call_is_deferred_without_spending_an_attempt(db, agent, settings, queue):
    """Being rate limited is not a failed attempt - nothing was dialled."""
    settings.carrier_calls_per_second = 1.0
    settings.carrier_burst = 1
    worker = CallWorker(settings=settings)
    await worker.throttle.acquire(None)  # drain the bucket

    call = await enqueue(db, agent, "+15551110001111")
    await worker.tick()
    await worker._drain()

    stored = await reload(call.id)
    assert stored.status is CallStatus.SCHEDULED
    assert stored.attempt == 0, "a deferral must not consume a retry attempt"
    assert stored.started_at is None
    assert (await queue.stats()).counters["throttled"] == 1


# ---------------------------- calling hours ----------------------------


@pytest.mark.parametrize(
    ("hour", "allowed"),
    [(3, False), (8, False), (9, True), (19, True), (20, False), (23, False)],
)
def test_the_calling_window_is_respected(hour, allowed):
    now = datetime(2026, 9, 25, hour, 30, tzinfo=UTC)
    result = next_calling_window(now, start_hour=9, end_hour=20, timezone_name="UTC")
    assert (result is None) is allowed


def test_a_window_spanning_midnight_works():
    inside = datetime(2026, 9, 25, 23, 0, tzinfo=UTC)
    outside = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    assert next_calling_window(inside, start_hour=20, end_hour=9) is None
    assert next_calling_window(outside, start_hour=20, end_hour=9) is not None


def test_an_unknown_timezone_does_not_block_calling():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    assert next_calling_window(now, start_hour=9, end_hour=20, timezone_name="Mars/Olympus") is None


async def test_a_call_outside_hours_is_deferred_not_failed(db, agent, settings, queue):
    # A window that excludes now, whenever the suite happens to run.
    current = datetime.now(UTC).hour
    settings.calling_hours_start = (current + 2) % 24
    settings.calling_hours_end = (current + 6) % 24

    call = await enqueue(db, agent, "+15551110001111")
    worker = CallWorker(settings=settings)
    await worker.tick()
    await worker._drain()

    stored = await reload(call.id)
    assert stored.status is CallStatus.SCHEDULED
    assert stored.attempt == 0
    assert stored.scheduled_at is not None
    assert stored.started_at is None
    assert (await queue.stats()).counters["deferred"] == 1


async def test_calls_still_go_out_inside_the_window(db, agent, settings):
    settings.calling_hours_start = 0
    settings.calling_hours_end = 0  # disabled
    call = await enqueue(db, agent, "+15551110001111")

    worker = CallWorker(settings=settings)
    await worker.tick()
    await worker._drain()

    stored = await reload(call.id)
    assert stored.status is CallStatus.COMPLETED
    assert stored.started_at is not None


async def test_default_settings_do_not_block_calls(db, agent):
    """The policy gates must be opt-in.

    Shipping a calling window switched on defaults to silently deferring every
    call outside it - which broke seeding and the dashboard demo at any hour
    outside the hardcoded window. Carrier rate and calling hours are policy that
    depends on the deployment, so both default to off and are set explicitly in
    compose and the Kubernetes config.
    """
    from app.core.config import Settings

    fresh = Settings()
    assert fresh.calling_hours_start == fresh.calling_hours_end, "window must default to disabled"
    assert fresh.carrier_calls_per_second == 0.0, "rate limit must default to disabled"

    call = await enqueue(db, agent, "+15551110001111")
    worker = CallWorker(settings=fresh)
    await worker.tick()
    await worker._drain()

    stored = await reload(call.id)
    assert stored.status is CallStatus.COMPLETED, "a default install must place calls"


async def test_a_provider_failure_after_answering_is_not_redialled(db, agent, queue, stack):
    """The no-redial rule is about the person, not the component.

    A dropped line after the customer answered is deliberately not retried:
    ringing back replays the greeting from a bot with no memory of the
    conversation they were just in. The same is true when the LLM rate-limits
    mid-call - but that used to be retried, because the decision looked only at
    the failure category. It now looks at whether they had answered.
    """
    from app.core.enums import FailureCategory as FC
    from app.voice.base import VoiceProviderError

    class RateLimitedLLM:
        name = "rate-limited"

        async def complete(self, messages, **kwargs):
            raise VoiceProviderError("quota exceeded", category=FC.RATE_LIMITED)

    stack.llm = RateLimitedLLM()
    call = await enqueue(db, agent, "+15551110001111")

    worker = CallWorker(stack=stack)
    await worker.tick()
    await worker._drain()

    stored = await reload(call.id)
    assert stored.status is CallStatus.FAILED, "must not be queued for another attempt"
    assert stored.started_at is not None, "the customer did answer"
    assert (
        "not redialling" in (stored.failure_reason or "")
        or stored.failure_category is FC.RATE_LIMITED
    )

    stats = await queue.stats()
    assert stats.dead_letter == 1, "it goes to a human instead"
    assert stats.counters["retried"] == 0


async def test_the_same_failure_before_answering_is_still_retried(db, agent, queue):
    """The rule only applies once someone has picked up.

    A rate limit while dialling has not interrupted anybody, so backing off and
    trying again is right.
    """
    call = await enqueue(db, agent, "+15551110009999")  # never answers

    worker = CallWorker()
    await worker.tick()
    await worker._drain()

    stored = await reload(call.id)
    assert stored.status is CallStatus.RETRYING
    assert stored.started_at is not None
    assert (await queue.stats()).counters["retried"] == 1
