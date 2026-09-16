"""Standalone worker process entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine, init_models
from app.queue.redis_client import close_redis
from app.worker.call_worker import CallWorker

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.voiceops_env != "local")

    if not settings.uses_real_redis:
        logger.error(
            "REDIS_URL is not set. The in-process queue stub cannot be shared "
            "between processes, so a standalone worker would see an empty queue. "
            "Set REDIS_URL, or run the API alone - it embeds a worker in that mode."
        )
        raise SystemExit(1)

    await init_models()

    # Redis holds only the working set; the database says what still has to run.
    from app.db.session import session_scope
    from app.queue.call_queue import get_queue
    from app.services.recovery import requeue_pending_calls

    async with session_scope() as session:
        await requeue_pending_calls(session, get_queue())

    worker = CallWorker(settings=settings)

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    task = asyncio.create_task(worker.run_forever())
    await stopping.wait()
    logger.info("shutdown requested, draining in-flight calls")
    await worker.stop(drain=True)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await close_redis()
    await dispose_engine()


def run() -> None:  # pragma: no cover - console entry point
    asyncio.run(main())


if __name__ == "__main__":  # pragma: no cover
    run()
