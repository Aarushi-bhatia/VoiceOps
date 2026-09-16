#!/usr/bin/env python
"""Populate the database with demo agents and calls.

    python scripts/seed.py            # agents + a queued batch of calls
    python scripts/seed.py --run      # also drive the worker so there is history
    python scripts/seed.py --reset    # drop and recreate the schema first

Safe to run repeatedly: agents are matched by name.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.enums import CallPriority  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import Agent  # noqa: E402
from app.db.session import get_engine, init_models, session_scope  # noqa: E402
from app.seed_data import DEMO_AGENTS, DEMO_NUMBERS  # noqa: E402
from app.services.calls import CallService  # noqa: E402

PRIORITIES = [
    CallPriority.URGENT,
    CallPriority.HIGH,
    CallPriority.NORMAL,
    CallPriority.NORMAL,
    CallPriority.LOW,
]


async def seed(*, calls: int, run_worker: bool, reset: bool, seed_value: int) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    rng = random.Random(seed_value)

    if reset:
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        print("dropped existing schema")
    await init_models()

    async with session_scope() as session:
        agents: list[Agent] = []
        for spec in DEMO_AGENTS:
            agent = await session.scalar(select(Agent).where(Agent.name == spec["name"]))
            if agent is None:
                agent = Agent(**spec)
                session.add(agent)
                await session.flush()
                print(f"created agent {agent.name}")
            agents.append(agent)

        service = CallService(session)
        created = 0
        for index in range(calls):
            agent = rng.choice(agents)
            number, _note = rng.choice(DEMO_NUMBERS)
            scheduled = None
            if index % 9 == 8:
                scheduled = datetime.now(timezone.utc) + timedelta(minutes=rng.randint(5, 120))
            await service.create_call(
                agent_id=agent.id,
                to_number=number,
                priority=rng.choice(PRIORITIES),
                scheduled_at=scheduled,
                metadata={
                    "customer_name": rng.choice(
                        ["Dana", "Sam", "Priya", "Alex", "Jordan", "Mika", "Chen"]
                    ),
                    "source": "seed",
                },
                idempotency_key=f"seed:{seed_value}:{index}",
            )
            created += 1
        print(f"queued {created} calls across {len(agents)} agents")

    if run_worker:
        from app.worker.call_worker import CallWorker

        worker = CallWorker()
        print("running the worker over the queued batch...")
        for _ in range(max(calls // max(settings.worker_concurrency, 1) + 4, 8)):
            await worker.tick()
            await worker._drain()
        stats = await worker.queue.stats()
        print(
            f"queue now: ready={stats.ready} scheduled={stats.scheduled} "
            f"dead_letter={stats.dead_letter} completed={stats.counters['completed']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=45, help="how many demo calls to queue")
    parser.add_argument(
        "--run", action="store_true", help="drive the worker so the dashboard has history"
    )
    parser.add_argument("--reset", action="store_true", help="drop and recreate the schema first")
    parser.add_argument("--seed", type=int, default=7, help="RNG seed for repeatable data")
    args = parser.parse_args()

    if not get_settings().uses_real_redis and args.run:
        print(
            "note: REDIS_URL is unset, so this uses the in-process queue. "
            "Calls are driven here and then the queue is discarded on exit."
        )
    asyncio.run(
        seed(calls=args.calls, run_worker=args.run, reset=args.reset, seed_value=args.seed)
    )


if __name__ == "__main__":
    main()
