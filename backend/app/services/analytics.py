"""Aggregations behind the analytics dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CallOutcome, CallStatus, FailureCategory
from app.db.models import Agent, Call, CallTurn
from app.schemas.ops import (
    AgentPerformance,
    AnalyticsOverview,
    FailureCount,
    LatencyPercentiles,
    OutcomeCount,
    TimeseriesPoint,
)

IN_FLIGHT = (
    CallStatus.QUEUED,
    CallStatus.SCHEDULED,
    CallStatus.DIALING,
    CallStatus.IN_PROGRESS,
    CallStatus.RETRYING,
)


def _flag(condition):
    return case((condition, 1), else_=0)


def _window_start(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def overview(session: AsyncSession, *, days: int = 7) -> AnalyticsOverview:
    since = _window_start(days)
    totals = (
        await session.execute(
            select(
                func.count(Call.id),
                func.sum(_flag(Call.status == CallStatus.COMPLETED)),
                func.sum(_flag(Call.status == CallStatus.FAILED)),
                func.sum(_flag(Call.status.in_(IN_FLIGHT))),
                func.sum(_flag(Call.outcome == CallOutcome.RESOLVED)),
                func.sum(_flag(Call.outcome == CallOutcome.ESCALATED)),
                func.avg(Call.duration_seconds),
                func.avg(Call.attempt),
                func.sum(Call.cost_cents),
            ).where(Call.created_at >= since)
        )
    ).one()

    total = int(totals[0] or 0)
    completed = int(totals[1] or 0)
    failed = int(totals[2] or 0)
    in_flight = int(totals[3] or 0)
    resolved = int(totals[4] or 0)
    escalated = int(totals[5] or 0)
    finished = completed + failed

    outcome_rows = await session.execute(
        select(Call.outcome, func.count(Call.id))
        .where(Call.created_at >= since, Call.outcome.is_not(None))
        .group_by(Call.outcome)
        .order_by(func.count(Call.id).desc())
    )
    failure_rows = await session.execute(
        select(Call.failure_category, func.count(Call.id))
        .where(Call.created_at >= since, Call.failure_category.is_not(None))
        .group_by(Call.failure_category)
        .order_by(func.count(Call.id).desc())
    )

    return AnalyticsOverview(
        window_days=days,
        total_calls=total,
        completed=completed,
        failed=failed,
        in_flight=in_flight,
        connect_rate=round(completed / finished, 4) if finished else 0.0,
        resolution_rate=round(resolved / completed, 4) if completed else 0.0,
        escalation_rate=round(escalated / completed, 4) if completed else 0.0,
        avg_duration_seconds=round(float(totals[6] or 0.0), 2),
        avg_attempts=round(float(totals[7] or 0.0), 2),
        total_cost_cents=round(float(totals[8] or 0.0), 2),
        outcomes=[OutcomeCount(outcome=str(o), count=int(c)) for o, c in outcome_rows],
        failures=[
            FailureCount(
                category=str(cat),
                count=int(count),
                retryable=FailureCategory(cat).is_transient,
            )
            for cat, count in failure_rows
        ],
        turn_latency=await turn_latency(session, since=since),
    )


async def turn_latency(
    session: AsyncSession, *, since: datetime | None = None
) -> LatencyPercentiles:
    """End-to-end response latency per customer turn (STT + LLM).

    Percentiles are computed in Python: the sample is bounded and this keeps the
    query portable between SQLite and Postgres.
    """
    query = select(CallTurn.latency_ms).where(CallTurn.latency_ms.is_not(None))
    if since is not None:
        query = query.where(CallTurn.created_at >= since)
    values = sorted(float(v) for v in (await session.scalars(query.limit(50_000))).all())
    if not values:
        return LatencyPercentiles()

    def pct(fraction: float) -> float:
        index = min(int(len(values) * fraction), len(values) - 1)
        return round(values[index], 2)

    return LatencyPercentiles(
        p50_ms=pct(0.50), p90_ms=pct(0.90), p95_ms=pct(0.95), p99_ms=pct(0.99), samples=len(values)
    )


async def timeseries(
    session: AsyncSession, *, days: int = 14, bucket: str = "day"
) -> list[TimeseriesPoint]:
    since = _window_start(days)
    # date_trunc is Postgres-only; strftime is SQLite-only. Bucketing in Python
    # over a bounded window keeps one code path for both.
    rows = await session.execute(
        select(Call.created_at, Call.status, Call.outcome).where(Call.created_at >= since)
    )
    buckets: dict[datetime, TimeseriesPoint] = {}
    for created_at, status, outcome in rows:
        key = _truncate(created_at, bucket)
        point = buckets.setdefault(key, TimeseriesPoint(bucket=key))
        point.total += 1
        if status == CallStatus.COMPLETED:
            point.completed += 1
        elif status == CallStatus.FAILED:
            point.failed += 1
        if outcome == CallOutcome.RESOLVED:
            point.resolved += 1
    return [buckets[k] for k in sorted(buckets)]


def _truncate(value: datetime, bucket: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if bucket == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


async def agent_performance(session: AsyncSession, *, days: int = 30) -> list[AgentPerformance]:
    since = _window_start(days)
    turn_counts = (
        select(CallTurn.call_id, func.count(CallTurn.id).label("turns"))
        .group_by(CallTurn.call_id)
        .subquery()
    )
    rows = await session.execute(
        select(
            Agent.id,
            Agent.name,
            func.count(Call.id),
            func.sum(_flag(Call.status == CallStatus.COMPLETED)),
            func.sum(_flag(Call.status == CallStatus.FAILED)),
            func.sum(_flag(Call.outcome == CallOutcome.RESOLVED)),
            func.sum(_flag(Call.outcome == CallOutcome.ESCALATED)),
            func.avg(Call.duration_seconds),
            func.sum(Call.cost_cents),
            func.avg(turn_counts.c.turns),
        )
        .select_from(Agent)
        .join(Call, (Call.agent_id == Agent.id) & (Call.created_at >= since), isouter=True)
        .join(turn_counts, turn_counts.c.call_id == Call.id, isouter=True)
        .group_by(Agent.id, Agent.name)
        .order_by(func.count(Call.id).desc())
    )

    out: list[AgentPerformance] = []
    for (
        agent_id,
        name,
        total,
        completed,
        failed,
        resolved,
        escalated,
        avg_duration,
        cost,
        avg_turns,
    ) in rows:
        completed = int(completed or 0)
        resolved = int(resolved or 0)
        out.append(
            AgentPerformance(
                agent_id=agent_id,
                agent_name=name,
                total_calls=int(total or 0),
                completed=completed,
                failed=int(failed or 0),
                resolved=resolved,
                escalated=int(escalated or 0),
                resolution_rate=round(resolved / completed, 4) if completed else 0.0,
                avg_duration_seconds=round(float(avg_duration or 0.0), 2),
                avg_turns=round(float(avg_turns or 0.0), 2),
                total_cost_cents=round(float(cost or 0.0), 2),
            )
        )
    return out
