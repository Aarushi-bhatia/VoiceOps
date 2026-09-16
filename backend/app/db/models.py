"""Persistent domain model: agents, calls, conversation turns and call events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    CallOutcome,
    CallPriority,
    CallStatus,
    EventType,
    FailureCategory,
    TurnRole,
)
from app.db.base import (
    Base,
    JSONType,
    TimestampType,
    created_at_col,
    enum_column,
    updated_at_col,
    uuid_pk,
)


class Agent(Base):
    """A configurable voice agent: persona, voice, and conversation workflow."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Persona / model configuration
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    llm_model: Mapped[str] = mapped_column(String(80), nullable=False, default="mock-llm")
    temperature: Mapped[float] = mapped_column(default=0.3, nullable=False)

    # Voice configuration
    voice_id: Mapped[str] = mapped_column(String(80), nullable=False, default="default")
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en-US")

    # Conversation guardrails
    max_turns: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    max_call_seconds: Mapped[int] = mapped_column(Integer, default=600, nullable=False)

    # The workflow graph, validated by app.agents.workflow.Workflow.
    workflow: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    calls: Mapped[list[Call]] = relationship(back_populates="agent", lazy="noload")


class Call(Base):
    """One outbound (or inbound) support call, including its queue state."""

    __tablename__ = "calls"
    __table_args__ = (
        Index("ix_calls_status_scheduled_at", "status", "scheduled_at"),
        Index("ix_calls_agent_created", "agent_id", "created_at"),
        UniqueConstraint("idempotency_key", name="uq_calls_idempotency_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Telephony addressing
    to_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    from_number: Mapped[str | None] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16), default="outbound", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)

    # Queue state
    status: Mapped[CallStatus] = enum_column(
        CallStatus, default=CallStatus.QUEUED, nullable=False, index=True
    )
    priority: Mapped[CallPriority] = enum_column(
        CallPriority, default=CallPriority.NORMAL, nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(TimestampType, index=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(TimestampType)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    # Lifecycle timestamps
    queued_at: Mapped[datetime | None] = mapped_column(TimestampType)
    started_at: Mapped[datetime | None] = mapped_column(TimestampType)
    ended_at: Mapped[datetime | None] = mapped_column(TimestampType)
    duration_seconds: Mapped[float | None] = mapped_column()

    # Results
    outcome: Mapped[CallOutcome | None] = enum_column(CallOutcome, index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    collected_data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    failure_category: Mapped[FailureCategory | None] = enum_column(FailureCategory, index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    recording_url: Mapped[str | None] = mapped_column(String(512))
    cost_cents: Mapped[float] = mapped_column(default=0.0, nullable=False)

    # Caller-supplied context made available to the agent (order id, name, ...)
    call_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    agent: Mapped[Agent] = relationship(back_populates="calls", lazy="joined")
    turns: Mapped[list[CallTurn]] = relationship(
        back_populates="call",
        lazy="noload",
        cascade="all, delete-orphan",
        order_by="CallTurn.index",
    )
    events: Mapped[list[CallEvent]] = relationship(
        back_populates="call",
        lazy="noload",
        cascade="all, delete-orphan",
        order_by="CallEvent.created_at",
    )


class CallTurn(Base):
    """A single utterance in a conversation, with per-stage latency."""

    __tablename__ = "call_turns"
    __table_args__ = (UniqueConstraint("call_id", "index", name="uq_call_turns_call_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[TurnRole] = enum_column(TurnRole, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(64))

    stt_ms: Mapped[float | None] = mapped_column()
    llm_ms: Mapped[float | None] = mapped_column()
    tts_ms: Mapped[float | None] = mapped_column()
    latency_ms: Mapped[float | None] = mapped_column()
    confidence: Mapped[float | None] = mapped_column()

    created_at: Mapped[datetime] = created_at_col()

    call: Mapped[Call] = relationship(back_populates="turns", lazy="noload")


class CallEvent(Base):
    """Append-only audit trail for everything that happens to a call."""

    __tablename__ = "call_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[EventType] = enum_column(EventType, nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    call: Mapped[Call] = relationship(back_populates="events", lazy="noload")
