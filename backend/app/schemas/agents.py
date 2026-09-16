"""Agent request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.workflow import Workflow


class AgentBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    system_prompt: str = ""
    llm_model: str = "mock-llm"
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)
    voice_id: str = "default"
    language: str = "en-US"
    max_turns: int = Field(default=24, ge=2, le=200)
    max_call_seconds: int = Field(default=600, ge=30, le=7200)
    is_active: bool = True


class AgentCreate(AgentBase):
    workflow: dict[str, Any]

    @field_validator("workflow")
    @classmethod
    def _validate_workflow(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Round-tripping through Workflow rejects dangling edges, unreachable
        # nodes and missing terminals before anything is persisted.
        return Workflow.model_validate(value).model_dump(mode="json")


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    system_prompt: str | None = None
    llm_model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    voice_id: str | None = None
    language: str | None = None
    max_turns: int | None = Field(default=None, ge=2, le=200)
    max_call_seconds: int | None = Field(default=None, ge=30, le=7200)
    is_active: bool | None = None
    workflow: dict[str, Any] | None = None

    @field_validator("workflow")
    @classmethod
    def _validate_workflow(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return Workflow.model_validate(value).model_dump(mode="json")


class AgentRead(AgentBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class AgentStats(BaseModel):
    """Per-agent rollup shown next to each agent in the dashboard."""

    agent_id: uuid.UUID
    agent_name: str
    total_calls: int = 0
    completed: int = 0
    failed: int = 0
    resolved: int = 0
    escalated: int = 0
    resolution_rate: float = 0.0
    avg_duration_seconds: float = 0.0


class SimulationTurn(BaseModel):
    role: str
    text: str
    node_id: str | None = None
    latency_ms: float | None = None


class SimulationResult(BaseModel):
    """Output of a dry-run conversation against the mock voice stack."""

    outcome: str
    ended_reason: str
    node_path: list[str]
    collected: dict[str, Any]
    summary: str
    turns: list[SimulationTurn]
    duration_seconds: float


class SimulationRequest(BaseModel):
    to_number: str = "+15551234567"
    context: dict[str, Any] = Field(
        default_factory=dict, description="Values available to prompt placeholders"
    )
    seed: int | None = Field(
        default=None, description="Fixes the simulated customer so a run is repeatable"
    )
