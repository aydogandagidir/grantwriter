"""Programme registry.

Adding a new programme = new module under ``src/programs/<id>/`` + one
line here. Per docs/07 §1, agent code MUST resolve programme behaviour
through this registry — no ``if programme_id == ...`` branches.
"""

from __future__ import annotations

from src.programs.base import (
    BaseProgramModule,
    BriefField,
    BriefSchema,
    BriefSection,
    CallMetadata,
    ValidationIssue,
)
from src.programs.horizon_eu_ria import HorizonEURIAModule
from src.programs.tubitak_1501 import TUBITAK1501Module

REGISTRY: dict[str, BaseProgramModule] = {
    "tubitak_1501": TUBITAK1501Module(),
    "horizon_eu_ria": HorizonEURIAModule(),
}


def get_module(programme_id: str) -> BaseProgramModule:
    """Look up a programme module by id; raise on unknown."""

    if programme_id not in REGISTRY:
        raise KeyError(f"Unknown programme_id: {programme_id!r}")
    return REGISTRY[programme_id]


__all__ = [
    "BaseProgramModule",
    "BriefField",
    "BriefSchema",
    "BriefSection",
    "CallMetadata",
    "HorizonEURIAModule",
    "REGISTRY",
    "TUBITAK1501Module",
    "ValidationIssue",
    "get_module",
]
