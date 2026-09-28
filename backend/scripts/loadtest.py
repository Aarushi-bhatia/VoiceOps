#!/usr/bin/env python
"""Measure how many calls the platform actually gets through.

Queues a batch of calls, runs workers over them, and reports throughput,
latency and queue behaviour. Runs against the mock voice stack, so it measures
*our* machinery - the queue, the worker pool, the conversation engine and the
database - rather than a phone carrier's round-trip time.

    python scripts/loadtest.py --calls 500 --concurrency 16
    REDIS_URL=redis://localhost:6379/13 python scripts/loadtest.py --calls 2000
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.enums import CallPriority, CallStatus  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import Agent, Call, CallTurn  # noqa: E402
from app.db.session import get_engine, init_models, session_scope  # noqa: E402
from app.seed_data import DEMO_AGENTS  # noqa: E402
from app.services.calls import CallService  # noqa: E402
from app.worker.call_worker import CallWorker  # noqa: E402

# Always answers, so throughput is measured on calls that actually converse
# rather than on dial failures.
NUMBER = "+15551110001111"


async def run(calls: int, concurrency: int, reset: bool) -> None:
    timeout_seconds = max(600.0, calls * 1.5)
    settings = get_settings()
    settings.worker_concurrency = concurrency
    configure_logging("WARNING", json_output=False)

    # Measure the platform, not the calling policy. The carrier rate limit and
    # the calling-hours window are deliberate throttles - leaving them on would
    # measure how long the policy says to wait, and outside the window every
    # call defers and the run never finishes.
    settings.carrier_calls_per_second = 0.0
    settings.calling_hours_start = 0
    settings.calling_hours_end = 0
    settings.max_queue_depth = 0
    settings.max_call_age_seconds = 0

    if reset:
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await init_models()

    async with session_scope() as session:
        agent = await session.scalar(select(Agent).where(Agent.name == DEMO_AGENTS[0]["name"]))
        if agent is None:
            agent = Agent(**DEMO_AGENTS[0])
            session.add(agent)
            await session.flush()
        agent_id = agent.id

    # ---- enqueue ----
    started = time.perf_counter()
    async with session_scope() as session:
        service = CallService(session)
        for index in range(calls):
            await service.create_call(
                agent_id=agent_id,
                to_number=NUMBER,
                priority=CallPriority.NORMAL,
                idempotency_key=f"load:{started}:{index}",
            )
    enqueue_seconds = time.perf_counter() - started

    # ---- drain ----
    worker = CallWorker(settings=settings)
    drain_started = time.perf_counter()
    ticks = 0
    while True:
        await worker.tick()
        ticks += 1
        stats = await worker.queue.stats()
        if stats.ready == 0 and stats.processing == 0 and stats.scheduled == 0:
            break
        # Scale the guard with the batch size: a 10k run legitimately takes
        # longer than a fixed ten-minute ceiling.
        if time.perf_counter() - drain_started > timeout_seconds:
            print(f"  ! gave up after {timeout_seconds:.0f}s")
            break
        await asyncio.sleep(0.005)
    await worker._drain()
    drain_seconds = time.perf_counter() - drain_started

    # ---- measure ----
    async with session_scope() as session:
        completed = await session.scalar(
            select(func.count()).select_from(Call).where(Call.status == CallStatus.COMPLETED)
        )
        durations = list(
            (
                await session.scalars(
                    select(Call.duration_seconds).where(Call.duration_seconds.is_not(None))
                )
            ).all()
        )
        turn_count = await session.scalar(select(func.count()).select_from(CallTurn))
        latencies = sorted(
            float(v)
            for v in (
                await session.scalars(
                    select(CallTurn.latency_ms).where(CallTurn.latency_ms.is_not(None))
                )
            ).all()
        )

    def pct(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        return values[min(int(len(values) * fraction), len(values) - 1)]

    backend = "redis" if settings.uses_real_redis else "in-process"
    print()
    print("  admission gates      disabled (measuring the platform, not the policy)")
    print(f"  queue backend        {backend}")
    print(f"  worker concurrency   {concurrency}")
    print(
        f"  calls queued         {calls} in {enqueue_seconds:.1f}s "
        f"({calls / max(enqueue_seconds, 1e-9):,.0f}/s)"
    )
    print(f"  calls completed      {completed} in {drain_seconds:.1f}s")
    print(
        f"  throughput           {completed / max(drain_seconds, 1e-9):.1f} calls/sec "
        f"({completed / max(drain_seconds, 1e-9) * 60:,.0f}/min, "
        f"{completed / max(drain_seconds, 1e-9) * 3600:,.0f}/hour)"
    )
    print(f"  scheduling passes    {ticks}")
    print(f"  conversation turns   {turn_count} ({turn_count / max(completed, 1):.1f} per call)")
    if durations:
        print(
            f"  call duration        mean {statistics.mean(durations):.2f}s, "
            f"p95 {pct(sorted(durations), 0.95):.2f}s"
        )
    if latencies:
        print(
            f"  turn latency         p50 {pct(latencies, 0.5):.0f}ms, "
            f"p95 {pct(latencies, 0.95):.0f}ms, p99 {pct(latencies, 0.99):.0f}ms"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--reset", action="store_true", default=True)
    args = parser.parse_args()
    asyncio.run(run(args.calls, args.concurrency, args.reset))


if __name__ == "__main__":
    main()
