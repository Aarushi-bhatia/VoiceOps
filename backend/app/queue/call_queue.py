"""The Redis-backed call queue.

Structures (all prefixed with the configured namespace):

======================  =====  =====================================================
key                     type   contents
======================  =====  =====================================================
``q:ready``             zset   claimable calls, scored priority-then-FIFO
``q:scheduled``         zset   calls parked until ``scheduled_at`` (score = due ms)
``q:processing``        zset   leased calls (score = lease expiry ms)
``q:jobs``              hash   call_id -> job payload JSON
``q:meta``              hash   call_id -> priority rank
``q:dlq`` / ``q:dlq:jobs``  zset/hash  permanently failed calls, newest last
``stats:<name>``        int    monotonic counters surfaced by the dashboard
``events``              chan   pub/sub channel for live dashboard updates
======================  =====  =====================================================
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings, get_settings
from app.core.enums import CallPriority
from app.queue import scripts
from app.queue.redis_client import RedisBackend, get_redis, now_ms

logger = logging.getLogger(__name__)


@dataclass
class QueuedCall:
    """The unit of work handed to a worker. Kept small - the DB holds the rest."""

    call_id: str
    agent_id: str
    to_number: str
    priority: CallPriority = CallPriority.NORMAL
    attempt: int = 0
    max_attempts: int = 4
    enqueued_at_ms: int = field(default_factory=now_ms)
    scheduled_at_ms: int | None = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    last_error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str) -> QueuedCall:
        data = json.loads(raw)
        data["priority"] = CallPriority(data.get("priority", CallPriority.NORMAL))
        return cls(**data)


@dataclass(frozen=True)
class QueueStats:
    ready: int
    scheduled: int
    processing: int
    dead_letter: int
    counters: dict[str, int]

    @property
    def backlog(self) -> int:
        return self.ready + self.scheduled


class QueueKeys:
    def __init__(self, namespace: str) -> None:
        self.ready = f"{namespace}:q:ready"
        self.scheduled = f"{namespace}:q:scheduled"
        self.processing = f"{namespace}:q:processing"
        self.jobs = f"{namespace}:q:jobs"
        self.meta = f"{namespace}:q:meta"
        self.dlq = f"{namespace}:q:dlq"
        self.dlq_jobs = f"{namespace}:q:dlq:jobs"
        self.events = f"{namespace}:events"
        self.stats_prefix = f"{namespace}:stats:"

    def stat(self, name: str) -> str:
        return f"{self.stats_prefix}{name}"


COUNTERS = (
    "enqueued",
    "claimed",
    "completed",
    "failed",
    "retried",
    "dead_lettered",
    "reclaimed",
    "canceled",
)


class CallQueue:
    """Priority-aware, lease-based work queue for outbound calls."""

    def __init__(
        self,
        redis: RedisBackend | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._redis = redis
        self.keys = QueueKeys(self.settings.queue_namespace)

    @property
    def redis(self) -> RedisBackend:
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    # ----------------------------- producing -----------------------------

    async def enqueue(self, job: QueuedCall) -> str:
        """Add (or re-add) a call. Returns ``"ready"`` or ``"scheduled"``."""
        now = now_ms()
        due = job.scheduled_at_ms if job.scheduled_at_ms is not None else now
        state = await self.redis.run_script(
            scripts.ENQUEUE,
            [
                self.keys.ready,
                self.keys.scheduled,
                self.keys.jobs,
                self.keys.meta,
                self.keys.processing,
            ],
            [job.call_id, job.to_json(), job.priority.rank, int(due), now],
        )
        await self.incr_counter("enqueued")
        state = state if isinstance(state, str) else str(state)
        logger.info(
            "call enqueued",
            extra={
                "call_id": job.call_id,
                "state": state,
                "priority": str(job.priority),
                "attempt": job.attempt,
                "trace_id": job.trace_id,
            },
        )
        return state

    async def schedule_retry(self, job: QueuedCall, retry_at: datetime) -> str:
        """Re-enqueue a failed call for a later attempt."""
        job.attempt += 1
        job.scheduled_at_ms = int(retry_at.timestamp() * 1000)
        state = await self.enqueue(job)
        await self.incr_counter("retried")
        return state

    # ----------------------------- consuming -----------------------------

    async def promote_due(self, limit: int = 200) -> list[str]:
        """Move calls whose scheduled time has arrived into the ready queue."""
        promoted = await self.redis.run_script(
            scripts.PROMOTE,
            [self.keys.scheduled, self.keys.ready, self.keys.meta],
            [now_ms(), limit],
        )
        return list(promoted or [])

    async def claim(self, count: int = 1, lease_seconds: float | None = None) -> list[QueuedCall]:
        """Atomically take up to ``count`` calls and lease them to this worker."""
        if count <= 0:
            return []
        lease = lease_seconds or self.settings.queue_lease_seconds
        expiry = now_ms() + int(lease * 1000)
        payloads = await self.redis.run_script(
            scripts.CLAIM, [self.keys.ready, self.keys.processing, self.keys.jobs], [count, expiry]
        )
        jobs = [QueuedCall.from_json(p) for p in (payloads or []) if p]
        if jobs:
            await self.incr_counter("claimed", len(jobs))
        return jobs

    async def heartbeat(self, call_id: str, lease_seconds: float | None = None) -> bool:
        """Extend a lease. ``False`` means the lease was lost (reaped elsewhere)."""
        lease = lease_seconds or self.settings.queue_lease_seconds
        result = await self.redis.run_script(
            scripts.HEARTBEAT, [self.keys.processing], [call_id, now_ms() + int(lease * 1000)]
        )
        return bool(int(result or 0))

    async def release(self, call_id: str, *, counter: str = "completed") -> None:
        """Remove a call from the working set after a terminal outcome."""
        await self.redis.run_script(
            scripts.RELEASE,
            [
                self.keys.ready,
                self.keys.scheduled,
                self.keys.processing,
                self.keys.jobs,
                self.keys.meta,
            ],
            [call_id],
        )
        if counter:
            await self.incr_counter(counter)

    async def reclaim_stalled(self, limit: int = 100) -> list[QueuedCall]:
        """Return calls whose lease expired (worker crashed or hung)."""
        payloads = await self.redis.run_script(
            scripts.RECLAIM, [self.keys.processing, self.keys.jobs], [now_ms(), limit]
        )
        jobs = [QueuedCall.from_json(p) for p in (payloads or []) if p]
        if jobs:
            await self.incr_counter("reclaimed", len(jobs))
            logger.warning(
                "reclaimed stalled calls", extra={"count": len(jobs), "backend": "queue"}
            )
        return jobs

    # --------------------------- dead lettering ---------------------------

    async def dead_letter(self, job: QueuedCall, reason: str) -> None:
        job.last_error = reason
        await self.redis.run_script(
            scripts.DEAD_LETTER,
            [
                self.keys.ready,
                self.keys.scheduled,
                self.keys.processing,
                self.keys.jobs,
                self.keys.meta,
                self.keys.dlq,
                self.keys.dlq_jobs,
            ],
            [job.call_id, job.to_json(), now_ms()],
        )
        await self.incr_counter("dead_lettered")
        logger.error(
            "call dead-lettered",
            extra={"call_id": job.call_id, "reason": reason, "attempt": job.attempt},
        )

    async def requeue_dead(self, call_id: str, *, reset_attempts: bool = True) -> QueuedCall | None:
        raw = await self.redis.hget(self.keys.dlq_jobs, call_id)
        if raw is None:
            return None
        job = QueuedCall.from_json(raw)
        if reset_attempts:
            job.attempt = 0
            job.last_error = None
            job.scheduled_at_ms = None
            await self.redis.hset(self.keys.dlq_jobs, call_id, job.to_json())
        moved = await self.redis.run_script(
            scripts.REQUEUE_DEAD,
            [
                self.keys.dlq,
                self.keys.dlq_jobs,
                self.keys.jobs,
                self.keys.meta,
                self.keys.ready,
            ],
            [call_id, job.priority.rank, now_ms()],
        )
        return job if int(moved or 0) else None

    async def list_dead_letter(self, limit: int = 50, offset: int = 0) -> list[QueuedCall]:
        ids = await self.redis.zrange(self.keys.dlq, offset, offset + limit - 1)
        out: list[QueuedCall] = []
        for call_id in ids:
            raw = await self.redis.hget(self.keys.dlq_jobs, call_id)
            if raw:
                out.append(QueuedCall.from_json(raw))
        return out

    async def purge_dead_letter(self, call_id: str) -> bool:
        removed = await self.redis.zrem(self.keys.dlq, call_id)
        await self.redis.hdel(self.keys.dlq_jobs, call_id)
        return bool(removed)

    # ------------------------------ control -------------------------------

    async def cancel(self, call_id: str) -> None:
        await self.release(call_id, counter="canceled")

    async def position(self, call_id: str) -> int | None:
        """0-based position in the ready queue, or ``None`` if not waiting."""
        score = await self.redis.zscore(self.keys.ready, call_id)
        if score is None:
            return None
        ahead = await self.redis.zrangebyscore(self.keys.ready, float("-inf"), score)
        return max(len(ahead) - 1, 0)

    # ----------------------------- observing ------------------------------

    async def stats(self) -> QueueStats:
        counters: dict[str, int] = {}
        for name in COUNTERS:
            raw = await self.redis.get(self.keys.stat(name))
            counters[name] = int(raw or 0)
        return QueueStats(
            ready=await self.redis.zcard(self.keys.ready),
            scheduled=await self.redis.zcard(self.keys.scheduled),
            processing=await self.redis.zcard(self.keys.processing),
            dead_letter=await self.redis.zcard(self.keys.dlq),
            counters=counters,
        )

    async def publish_event(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", datetime.now(UTC).isoformat())
        await self.redis.publish(self.keys.events, json.dumps(event, default=str))

    def listen_events(self) -> AsyncIterator[str]:
        return self.redis.listen(self.keys.events)

    async def incr_counter(self, counter: str, amount: int = 1) -> None:
        """Bump one of the dashboard counters in :data:`COUNTERS`."""
        await self.redis.incrby(self.keys.stat(counter), amount)


_queue: CallQueue | None = None


def get_queue() -> CallQueue:
    global _queue
    if _queue is None:
        _queue = CallQueue()
    return _queue


def set_queue(queue: CallQueue | None) -> None:
    global _queue
    _queue = queue
