"""Agent configuration CRUD plus a dry-run simulator."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AppSettings, DbSession
from app.core.enums import CallOutcome, CallStatus
from app.db.models import Agent, Call
from app.schemas.agents import (
    AgentCreate,
    AgentRead,
    AgentStats,
    AgentUpdate,
    SimulationRequest,
    SimulationResult,
)
from app.schemas.common import Page
from app.services.simulation import simulate_agent

router = APIRouter(prefix="/agents", tags=["agents"])


def _flag(condition):
    """1 when the condition holds, 0 otherwise - portable COUNT(FILTER)."""
    return case((condition, 1), else_=0)


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(payload: AgentCreate, session: DbSession) -> Agent:
    agent = Agent(**payload.model_dump())
    session.add(agent)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"an agent named '{payload.name}' already exists"
        ) from exc
    return agent


@router.get("", response_model=Page[AgentRead])
async def list_agents(
    session: DbSession,
    is_active: bool | None = None,
    search: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AgentRead]:
    filters = []
    if is_active is not None:
        filters.append(Agent.is_active == is_active)
    if search:
        filters.append(Agent.name.ilike(f"%{search}%"))

    total = await session.scalar(select(func.count()).select_from(Agent).where(*filters)) or 0
    rows = await session.scalars(
        select(Agent).where(*filters).order_by(Agent.created_at.desc()).limit(limit).offset(offset)
    )
    return Page[AgentRead](
        items=[AgentRead.model_validate(a) for a in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(agent_id: uuid.UUID, session: DbSession) -> Agent:
    return await _load(agent_id, session)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(agent_id: uuid.UUID, payload: AgentUpdate, session: DbSession) -> Agent:
    agent = await _load(agent_id, session)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(agent, key, value)
    if "workflow" in changes:
        # Bump the version so call records can be traced to a config revision.
        agent.version += 1
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "that agent name is taken") from exc
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: uuid.UUID, session: DbSession) -> None:
    agent = await _load(agent_id, session)
    in_flight = await session.scalar(
        select(func.count())
        .select_from(Call)
        .where(
            Call.agent_id == agent_id,
            Call.status.in_(
                [
                    CallStatus.QUEUED,
                    CallStatus.SCHEDULED,
                    CallStatus.DIALING,
                    CallStatus.IN_PROGRESS,
                    CallStatus.RETRYING,
                ]
            ),
        )
    )
    if in_flight:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{in_flight} call(s) are still queued for this agent; deactivate it instead",
        )
    await session.delete(agent)


@router.get("/{agent_id}/stats", response_model=AgentStats)
async def agent_stats(agent_id: uuid.UUID, session: DbSession) -> AgentStats:
    agent = await _load(agent_id, session)
    row = (
        await session.execute(
            select(
                func.count(Call.id).label("total"),
                func.sum(_flag(Call.status == CallStatus.COMPLETED)).label("completed"),
                func.sum(_flag(Call.status == CallStatus.FAILED)).label("failed"),
                func.sum(_flag(Call.outcome == CallOutcome.RESOLVED)).label("resolved"),
                func.sum(_flag(Call.outcome == CallOutcome.ESCALATED)).label("escalated"),
                func.avg(Call.duration_seconds).label("avg_duration"),
            ).where(Call.agent_id == agent_id)
        )
    ).one()

    completed = int(row.completed or 0)
    resolved = int(row.resolved or 0)
    return AgentStats(
        agent_id=agent.id,
        agent_name=agent.name,
        total_calls=int(row.total or 0),
        completed=completed,
        failed=int(row.failed or 0),
        resolved=resolved,
        escalated=int(row.escalated or 0),
        resolution_rate=round(resolved / completed, 4) if completed else 0.0,
        avg_duration_seconds=round(float(row.avg_duration or 0.0), 2),
    )


@router.post("/{agent_id}/simulate", response_model=SimulationResult)
async def simulate(
    agent_id: uuid.UUID,
    payload: SimulationRequest,
    session: DbSession,
    settings: AppSettings,
) -> SimulationResult:
    """Run the workflow against a simulated customer - no phone call, no cost."""
    agent = await _load(agent_id, session)
    return await simulate_agent(agent, payload, settings)


async def _load(agent_id: uuid.UUID, session: DbSession) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no agent {agent_id}")
    return agent
