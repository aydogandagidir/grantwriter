"""Agent registry.

Adding a new agent: implement :class:`~src.agents.base.BaseAgent`, then
add an entry here. The orchestrator (S2+) reads ``AGENTS`` to dispatch
by id without circular imports.
"""

from __future__ import annotations

from src.agents.base import AgentInput, AgentOutput, AgentStatus, BaseAgent
from src.agents.call_analyst import CallAnalyst
from src.agents.excellence_writer import ExcellenceWriter
from src.agents.impact_writer import ImpactWriter
from src.agents.implementation_writer import ImplementationWriter

AGENTS: dict[str, type[BaseAgent]] = {
    "call_analyst": CallAnalyst,
    "excellence_writer": ExcellenceWriter,
    "impact_writer": ImpactWriter,
    "implementation_writer": ImplementationWriter,
}

__all__ = [
    "AGENTS",
    "AgentInput",
    "AgentOutput",
    "AgentStatus",
    "BaseAgent",
    "CallAnalyst",
    "ExcellenceWriter",
    "ImpactWriter",
    "ImplementationWriter",
]
