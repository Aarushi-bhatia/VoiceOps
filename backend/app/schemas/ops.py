"""Queue, analytics and health response models."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class QueueStatsRead(BaseModel):
    ready: int
    scheduled: int
    processing: int
    dead_letter: int
    backlog: int
    counters: dict[str, int]


class DeadLetterEntry(BaseModel):
    call_id: str
    agent_id: str
    to_number: str
    priority: str
    attempt: int
    max_attempts: int
    last_error: str | None = None
    enqueued_at: datetime | None = None


class HealthStatus(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    redis: str
    providers: dict[str, str]


class OutcomeCount(BaseModel):
    outcome: str
    count: int


class FailureCount(BaseModel):
    category: str
    count: int
    retryable: bool


class TimeseriesPoint(BaseModel):
    bucket: datetime | date
    total: int = 0
    completed: int = 0
    failed: int = 0
    resolved: int = 0


class LatencyPercentiles(BaseModel):
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    samples: int = 0


class AnalyticsOverview(BaseModel):
    window_days: int
    total_calls: int = 0
    completed: int = 0
    failed: int = 0
    in_flight: int = 0
    connect_rate: float = Field(
        default=0.0, description="Share of calls that reached a conversation"
    )
    resolution_rate: float = Field(
        default=0.0, description="Share of completed calls resolved without a human"
    )
    escalation_rate: float = 0.0
    avg_duration_seconds: float = 0.0
    avg_attempts: float = 0.0
    total_cost_cents: float = 0.0
    outcomes: list[OutcomeCount] = Field(default_factory=list)
    failures: list[FailureCount] = Field(default_factory=list)
    turn_latency: LatencyPercentiles = Field(default_factory=LatencyPercentiles)


class AgentPerformance(BaseModel):
    agent_id: uuid.UUID
    agent_name: str
    total_calls: int = 0
    completed: int = 0
    failed: int = 0
    resolved: int = 0
    escalated: int = 0
    resolution_rate: float = 0.0
    avg_duration_seconds: float = 0.0
    avg_turns: float = 0.0
    total_cost_cents: float = 0.0
