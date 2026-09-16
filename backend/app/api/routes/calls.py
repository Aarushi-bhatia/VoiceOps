"""Creating, listing, inspecting and controlling calls."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import Calls, DbSession, Queue
from app.core.enums import CallOutcome, CallPriority, CallStatus
from app.db.models import Call
from app.schemas.calls import BulkCallCreate, CallCreate, CallDetail, CallRead
from app.schemas.common import Page
from app.services.calls import AgentNotFoundError, CallNotFoundError

router = APIRouter(prefix="/calls", tags=["calls"])


@router.post("", response_model=CallRead, status_code=status.HTTP_201_CREATED)
async def create_call(payload: CallCreate, service: Calls) -> Call:
    try:
        return await service.create_call(
            agent_id=payload.agent_id,
            to_number=payload.to_number,
            from_number=payload.from_number,
            priority=payload.priority,
            scheduled_at=payload.scheduled_at,
            metadata=payload.metadata,
            max_attempts=payload.max_attempts,
            idempotency_key=payload.idempotency_key,
        )
    except AgentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/bulk", response_model=list[CallRead], status_code=status.HTTP_201_CREATED)
async def create_calls_bulk(payload: BulkCallCreate, service: Calls) -> list[Call]:
    """Queue a batch of calls for one agent (a campaign)."""
    created: list[Call] = []
    try:
        for recipient in payload.recipients:
            created.append(
                await service.create_call(
                    agent_id=payload.agent_id,
                    to_number=recipient.to_number,
                    priority=payload.priority,
                    scheduled_at=payload.scheduled_at,
                    metadata=recipient.metadata,
                    max_attempts=payload.max_attempts,
                    idempotency_key=recipient.idempotency_key,
                )
            )
    except AgentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return created


@router.get("", response_model=Page[CallRead])
async def list_calls(
    session: DbSession,
    status_in: list[CallStatus] | None = Query(default=None, alias="status"),
    outcome_in: list[CallOutcome] | None = Query(default=None, alias="outcome"),
    agent_id: uuid.UUID | None = None,
    priority: CallPriority | None = None,
    to_number: str | None = Query(default=None, max_length=32),
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    failed_only: bool = Query(default=False, description="Only calls that failed or are retrying"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[CallRead]:
    filters = []
    if status_in:
        filters.append(Call.status.in_(status_in))
    if outcome_in:
        filters.append(Call.outcome.in_(outcome_in))
    if agent_id:
        filters.append(Call.agent_id == agent_id)
    if priority:
        filters.append(Call.priority == priority)
    if to_number:
        filters.append(Call.to_number.ilike(f"%{to_number}%"))
    if created_after:
        filters.append(Call.created_at >= created_after)
    if created_before:
        filters.append(Call.created_at <= created_before)
    if failed_only:
        filters.append(Call.status.in_([CallStatus.FAILED, CallStatus.RETRYING]))

    total = await session.scalar(select(func.count()).select_from(Call).where(*filters)) or 0
    rows = await session.scalars(
        select(Call).where(*filters).order_by(Call.created_at.desc()).limit(limit).offset(offset)
    )
    return Page[CallRead](
        items=[CallRead.model_validate(c) for c in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(call_id: uuid.UUID, session: DbSession, queue: Queue) -> CallDetail:
    call = await session.scalar(
        select(Call)
        .where(Call.id == call_id)
        .options(selectinload(Call.turns), selectinload(Call.events))
    )
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no call {call_id}")

    detail = CallDetail.model_validate(call)
    if not CallStatus(call.status).is_terminal:
        detail.queue_position = await queue.position(str(call.id))
    return detail


@router.post("/{call_id}/cancel", response_model=CallRead)
async def cancel_call(call_id: uuid.UUID, service: Calls) -> Call:
    try:
        return await service.cancel_call(call_id)
    except CallNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{call_id}/retry", response_model=CallRead)
async def retry_call(
    call_id: uuid.UUID,
    service: Calls,
    reset_attempts: bool = Query(
        default=True, description="Start the backoff schedule over from attempt 0"
    ),
) -> Call:
    try:
        return await service.retry_call(call_id, reset_attempts=reset_attempts)
    except CallNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
