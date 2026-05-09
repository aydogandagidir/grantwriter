"""Base agent interface.

Every agent in the orchestrator inherits from :class:`BaseAgent`. The shape
of :class:`AgentInput` and :class:`AgentOutput` is fixed by docs/06 §3 and
must NOT change without updating every agent and the orchestrator.

The carrier fields (``brief``, ``call``, ``previous_outputs``, ``output``)
are typed ``dict[str, Any]`` deliberately: each programme module supplies
its own program-specific schema, and each agent validates the slice it
cares about against its own internal Pydantic model. Locking down the
carrier here would block plug-and-play of new programmes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AgentStatus = Literal["completed", "failed", "skipped"]


class AgentInput(BaseModel):
    """Inputs shared by every agent."""

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID
    tenant_id: UUID
    programme_id: str
    language: Literal["tr", "en"]
    brief: dict[str, Any] = Field(default_factory=dict)
    call: dict[str, Any] = Field(default_factory=dict)
    previous_outputs: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    """Outputs returned by every agent."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    status: AgentStatus
    output: dict[str, Any] = Field(default_factory=dict)
    citations_extracted: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0
    cost_usd: float = 0.0
    tokens_used: dict[str, int] = Field(default_factory=dict)
    """Keys: ``input``, ``output``, ``cached`` — provider-agnostic."""


class BaseAgent(ABC):
    """Abstract base for the seven concrete agents."""

    agent_id: str
    name: str
    description: str
    version: str
    requires_rag: bool
    estimated_duration_seconds: int

    @abstractmethod
    async def run(self, input: AgentInput) -> AgentOutput:
        """One-shot execution. On any failure, return ``AgentOutput`` with
        ``status="failed"`` and a ``metadata["error"]`` message — the
        orchestrator handles retry / compensation. Don't raise from here."""

    @abstractmethod
    def stream(self, input: AgentInput) -> AsyncIterator[str]:
        """Yield text deltas as they become available. For structured-JSON
        agents (Call Analyst, Compliance) the stream may yield the entire
        output as a single chunk; for writer agents (Excellence, Impact,
        Implementation) it yields token deltas in real time."""


__all__ = ["AgentInput", "AgentOutput", "AgentStatus", "BaseAgent"]
