"""FastAPI application factory and entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import agents, analytics, calls, events, health, queue
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine, init_models
from app.queue.redis_client import close_redis
from app.voice.registry import get_voice_stack

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

DESCRIPTION = """
VoiceOps automates customer-support calls with a configurable voice agent:
speech-to-text, an LLM policy over a workflow graph, and text-to-speech, driven
by a Redis-backed priority queue with scheduling, leases and exponential-backoff
retries.

Providers default to a deterministic mock stack, so every endpoint here works
without any third-party credentials.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.voiceops_env != "local")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await init_models()
        get_voice_stack(settings)
        await _restore_queue()
        worker_task: asyncio.Task[None] | None = None

        if app.state.embedded_worker:
            from app.worker.call_worker import CallWorker

            worker = CallWorker(settings=settings)
            app.state.worker = worker
            worker_task = asyncio.create_task(worker.run_forever(), name="embedded-worker")
            logger.info("embedded worker started inside the API process")

        try:
            yield
        finally:
            if worker_task is not None:
                await app.state.worker.stop(drain=False)
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker_task
            await close_redis()
            await dispose_engine()

    app = FastAPI(
        title="VoiceOps",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/docs",
    )
    # Without a real Redis the queue lives in this process, so the worker has to
    # run here too or nothing would ever consume the queue.
    app.state.embedded_worker = not settings.uses_real_redis

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(events.router)
    for module in (agents, calls, queue, analytics):
        app.include_router(module.router, prefix=API_PREFIX)

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": "voiceops", "docs": "/docs", "health": "/health"}

    return app


async def _restore_queue() -> None:
    """Rebuild the working queue from the database (see app.services.recovery)."""
    from app.db.session import session_scope
    from app.queue.call_queue import get_queue
    from app.services.recovery import requeue_pending_calls

    try:
        async with session_scope() as session:
            await requeue_pending_calls(session, get_queue())
    except Exception:
        # A recovery failure must not stop the API from serving.
        logger.exception("could not restore pending calls into the queue")


app = create_app()


def run() -> None:  # pragma: no cover - console entry point
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        reload=settings.voiceops_env == "local",
    )
