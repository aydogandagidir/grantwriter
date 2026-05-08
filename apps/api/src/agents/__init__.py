"""Agent registry.

Adding a new agent: implement :class:`~src.agents.base.BaseAgent`, then
add an entry here. The orchestrator (S2+) reads ``AGENTS`` to dispatch
by id without circular imports.
"""

from __future__ import annotations

from src.agents.base import AgentInput, AgentOutput, AgentStatus, BaseAgent
from src.agents.call_analyst import CallAnalyst

AGENTS: dict[str, type[BaseAgent]] = {
    "call_analyst": CallAnalyst,
}

__all__ = [
    "AGENTS",
    "AgentInput",
    "AgentOutput",
    "AgentStatus",
    "BaseAgent",
    "CallAnalyst",
]
