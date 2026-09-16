"""Call request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import CallOutcome, CallPriority, CallStatus, FailureCategory


class CallCreate(BaseModel):
    agent_id: uuid.UUID
    to_number: str = Field(min_length=3, max_length=32)
    from_number: str | None = Field(default=None, max_length=32)
    priority: CallPriority = CallPriority.NORMAL
    scheduled_at: datetime | None = Field(
        default=None, description="Hold the call until this time (UTC)"
    )
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Context for the agent's prompts"
    )
    idempotency_key: str | None = Field(default=None, max_length=128)


class BulkCallCreate(BaseModel):
    """Queue a campaign in one request."""

    agent_id: uuid.UUID
    priority: CallPriority = CallPriority.NORMAL
    scheduled_at: datetime | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    recipients: list[CallRecipient] = Field(min_length=1, max_length=1000)


class CallRecipient(BaseModel):
    to_number: str = Field(min_length=3, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class CallRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    to_number: str
    from_number: str | None
    direction: str
    status: CallStatus
    priority: CallPriority
    attempt: int
    max_attempts: int
    outcome: CallOutcome | None
    failure_category: FailureCategory | None
    failure_reason: str | None
    summary: str | None
    collected_data: dict[str, Any]
    call_metadata: dict[str, Any]
    scheduled_at: datetime | None
    next_retry_at: datetime | None
    queued_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    cost_cents: float
    external_id: str | None
    created_at: datetime
    updated_at: datetime


class TurnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    role: str
    text: str
    node_id: str | None
    stt_ms: float | None
    llm_ms: float | None
    tts_ms: float | None
    latency_ms: float | None
    confidence: float | None
    created_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: str
    payload: dict[str, Any]
    created_at: datetime


class CallDetail(CallRead):
    turns: list[TurnRead] = Field(default_factory=list)
    events: list[EventRead] = Field(default_factory=list)
    queue_position: int | None = None


BulkCallCreate.model_rebuild()
