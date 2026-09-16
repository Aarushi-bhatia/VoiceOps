"""Liveness and readiness."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import AppSettings, DbSession, Queue
from app.schemas.ops import HealthStatus
from app.voice.registry import get_voice_stack

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthStatus)
async def health(settings: AppSettings) -> HealthStatus:
    """Liveness: the process is up. Does not touch dependencies."""
    return HealthStatus(
        status="ok",
        version=VERSION,
        environment=settings.voiceops_env,
        database="unchecked",
        redis="unchecked",
        providers=get_voice_stack(settings).describe(),
    )


@router.get("/health/ready", response_model=HealthStatus)
async def ready(
    session: DbSession, queue: Queue, settings: AppSettings, response: Response
) -> HealthStatus:
    """Readiness: verifies the database and queue are actually reachable."""
    database = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        database = f"error: {exc}"
        logger.warning("database readiness check failed", extra={"error": str(exc)})

    redis = "ok"
    try:
        await queue.redis.ping()
    except Exception as exc:
        redis = f"error: {exc}"
        logger.warning("redis readiness check failed", extra={"error": str(exc)})

    healthy = database == "ok" and redis == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthStatus(
        status="ok" if healthy else "degraded",
        version=VERSION,
        environment=settings.voiceops_env,
        database=database,
        redis=redis,
        providers=get_voice_stack(settings).describe(),
    )
