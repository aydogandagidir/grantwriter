"""TÜBİTAK 1507 (KOBİ AR-GE Başlangıç) programme module.

The simplified, KOBİ-only sister of TÜBİTAK 1501. Per docs/07 §5: same
AGY100 form, but tighter parameters — 12-24 month duration, 1.5M TL
budget cap, KOBİ-only eligibility. Inherits the rule engine and DOCX
export from :class:`~src.programs._tubitak_base.TUBITAKBaseModule` and
overrides only the class-level parameters + paths.

Prompt sharing: 1507 uses 1501's writer prompts unchanged. The form
sections (B1-B4, C1-C3, D1-D4) are identical, and the writers'
production behaviour matches what 1507 evaluators expect.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from src.programs._tubitak_base import TUBITAKBaseModule

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "agy100_1507_2026.docx"
# Reuse 1501's prompts — TÜBİTAK forms are identical across the
# industrial-R&D programmes; rolling separate prompts would just drift.
_PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "agents"
    / "prompts"
    / "tubitak_1501"
)


class TUBITAK1507Module(TUBITAKBaseModule):
    program_id: ClassVar[str] = "tubitak_1507"
    name_tr: ClassVar[str] = "TÜBİTAK 1507 KOBİ AR-GE Başlangıç"
    name_en: ClassVar[str] = "TÜBİTAK 1507 Early-Stage SME R&D"

    # Parameter overrides — see docs/07 §5.
    duration_min_months: ClassVar[int] = 12
    duration_max_months: ClassVar[int] = 24
    requires_kobi: ClassVar[bool] = True
    budget_max_tl: ClassVar[int | None] = 1_500_000

    def get_template_path(self) -> str:
        return str(_TEMPLATE_PATH)

    def get_prompt_path(self, agent_id: str) -> str:
        return str(_PROMPTS_DIR / agent_id / "v1.md")


__all__ = ["TUBITAK1507Module"]
