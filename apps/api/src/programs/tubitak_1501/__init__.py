"""TÜBİTAK 1501 (Sanayi AR-GE Projeleri) programme module.

Inherits :class:`~src.programs._tubitak_base.TUBITAKBaseModule` for the
AGY100 form layout, brief schema, validation rule engine, and DOCX
export. This subclass only provides 1501's identity (program_id, name)
and the path to its template + prompts. The 1501-specific parameters
(18-36 month duration, no budget cap, KOBİ optional) match the base
defaults so no overrides are needed for the rule engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from src.programs._tubitak_base import TUBITAKBaseModule

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "agy100_2026.docx"
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "agents" / "prompts" / "tubitak_1501"


class TUBITAK1501Module(TUBITAKBaseModule):
    program_id: ClassVar[str] = "tubitak_1501"
    name_tr: ClassVar[str] = "TÜBİTAK 1501 Sanayi AR-GE"
    name_en: ClassVar[str] = "TÜBİTAK 1501 Industrial R&D"

    # Defaults from base apply: 18-36 mo, no KOBİ requirement, no
    # budget cap, B2 ≥ 800 words. 1501 accepts large firms; KOBİ
    # status only changes the support rate, not eligibility.

    def get_template_path(self) -> str:
        return str(_TEMPLATE_PATH)

    def get_prompt_path(self, agent_id: str) -> str:
        return str(_PROMPTS_DIR / agent_id / "v1.md")


__all__ = ["TUBITAK1501Module"]
