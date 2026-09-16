"""Read-only analytics for the CX operations dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import DbSession
from app.schemas.ops import AgentPerformance, AnalyticsOverview, TimeseriesPoint
from app.services import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=AnalyticsOverview)
async def overview(session: DbSession, days: int = Query(default=7, ge=1, le=365)):
    return await analytics.overview(session, days=days)


@router.get("/timeseries", response_model=list[TimeseriesPoint])
async def timeseries(
    session: DbSession,
    days: int = Query(default=14, ge=1, le=365),
    bucket: str = Query(default="day", pattern="^(day|hour)$"),
):
    return await analytics.timeseries(session, days=days, bucket=bucket)


@router.get("/agents", response_model=list[AgentPerformance])
async def agents(session: DbSession, days: int = Query(default=30, ge=1, le=365)):
    return await analytics.agent_performance(session, days=days)
