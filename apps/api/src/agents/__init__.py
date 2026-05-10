"""Agent registry.

Adding a new agent: implement :class:`~src.agents.base.BaseAgent`, then
add an entry here. The orchestrator (S2+) reads ``AGENTS`` to dispatch
by id without circular imports.
"""

from __future__ import annotations

from src.agents.base import AgentInput, AgentOutput, AgentStatus, BaseAgent
from src.agents.call_analyst import CallAnalyst
from src.agents.compliance_reviewer import ComplianceReviewer
from src.agents.excellence_writer import ExcellenceWriter
from src.agents.hallucination_hunter import HallucinationHunter
from src.agents.impact_writer import ImpactWriter
from src.agents.implementation_writer import ImplementationWriter

AGENTS: dict[str, type[BaseAgent]] = {
    "call_analyst": CallAnalyst,
    "excellence_writer": ExcellenceWriter,
    "impact_writer": ImpactWriter,
    "implementation_writer": ImplementationWriter,
    "compliance_reviewer": ComplianceReviewer,
    "hallucination_hunter": HallucinationHunter,
}

__all__ = [
    "AGENTS",
    "AgentInput",
    "AgentOutput",
    "AgentStatus",
    "BaseAgent",
    "CallAnalyst",
    "ComplianceReviewer",
    "ExcellenceWriter",
    "HallucinationHunter",
    "ImpactWriter",
    "ImplementationWriter",
]
