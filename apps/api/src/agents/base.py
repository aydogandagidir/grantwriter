"""Base interfaces for the seven AI agents.

Per docs/06-agent-architecture.md §3. Concrete agents subclass `BaseAgent` and
implement `run` (single-shot) and `stream` (incremental output for SSE). Agents
that don't naturally stream (e.g. embedding-based scorers) yield a single
terminal event from `stream`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AgentInput(BaseModel):
    proposal_id: UUID
    tenant_id: UUID | None = None
    programme_id: str | None = None
    language: str | None = None
    brief: dict[str, Any] = Field(default_factory=dict)
    call: dict[str, Any] = Field(default_factory=dict)
    previous_outputs: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    agent_id: str
    status: str
    output: dict[str, Any] = Field(default_factory=dict)
    citations_extracted: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0
    cost_usd: float = 0.0
    tokens_used: dict[str, int] = Field(default_factory=dict)


class BaseAgent(ABC):
    agent_id: str
    name: str
    description: str
    version: str
    requires_rag: bool = False
    estimated_duration_seconds: int = 30

    @abstractmethod
    async def run(self, agent_input: AgentInput) -> AgentOutput: ...

    @abstractmethod
    def stream(self, agent_input: AgentInput) -> AsyncIterator[str]: ...
