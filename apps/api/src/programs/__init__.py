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
from src.programs.cascade_funding import CascadeFundingModule
from src.programs.horizon_eu_ria import HorizonEURIAModule
from src.programs.kosgeb_arge import KOSGEBARGEModule
from src.programs.tubitak_1501 import TUBITAK1501Module
from src.programs.tubitak_1507 import TUBITAK1507Module

REGISTRY: dict[str, BaseProgramModule] = {
    "tubitak_1501": TUBITAK1501Module(),
    "tubitak_1507": TUBITAK1507Module(),
    "kosgeb_arge": KOSGEBARGEModule(),
    "horizon_eu_ria": HorizonEURIAModule(),
    "cascade_funding": CascadeFundingModule(),
}


def get_module(programme_id: str) -> BaseProgramModule:
    """Look up a programme module by id; raise on unknown."""

    if programme_id not in REGISTRY:
        raise KeyError(f"Unknown programme_id: {programme_id!r}")
    return REGISTRY[programme_id]


__all__ = [
    "REGISTRY",
    "BaseProgramModule",
    "BriefField",
    "BriefSchema",
    "BriefSection",
    "CallMetadata",
    "CascadeFundingModule",
    "HorizonEURIAModule",
    "KOSGEBARGEModule",
    "TUBITAK1501Module",
    "TUBITAK1507Module",
    "ValidationIssue",
    "get_module",
]
