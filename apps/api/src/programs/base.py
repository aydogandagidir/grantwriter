"""``BaseProgramModule`` — plugin interface for grant programmes.

Per docs/07 §1: programme-specific code is isolated under
``src/programs/<programme_id>/``. The core knows nothing about which
programme is which — it dispatches by id via the registry.

S1.D5.T1 only ships the TÜBİTAK 1501 implementation; remaining programmes
land in S2.D6+.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

ProgrammeLanguage = Literal["tr", "en", "both"]
Severity = Literal["blocker", "warning", "info"]
FieldType = Literal["text", "textarea", "number", "select", "multiselect", "date", "currency"]


class BriefField(BaseModel):
    """One field on the user-facing brief form."""

    model_config = ConfigDict(frozen=True)

    key: str
    label_tr: str
    label_en: str
    type: FieldType
    required: bool = True
    max_length: int | None = None
    options: list[dict[str, Any]] | None = None
    help_text_tr: str | None = None
    help_text_en: str | None = None
    placeholder_tr: str | None = None
    placeholder_en: str | None = None


class BriefSection(BaseModel):
    title_tr: str
    title_en: str
    fields: list[BriefField]


class BriefSchema(BaseModel):
    sections: list[BriefSection]


class CallMetadata(BaseModel):
    """Output of the Call Analyst agent, normalised across programmes."""

    eligibility: dict[str, Any] = Field(default_factory=dict)
    scope_summary: str = ""
    expected_outcomes: list[str] = Field(default_factory=list)
    expected_impacts: list[str] = Field(default_factory=list)
    evaluation_criteria: list[dict[str, Any]] = Field(default_factory=list)
    page_limit: int | None = None
    language: Literal["tr", "en"] | None = None
    key_terms_to_use: list[str] = Field(default_factory=list)
    deadlines: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    severity: Severity
    section: str | None = None
    code: str
    message_tr: str
    message_en: str
    suggestion: str | None = None


class BaseProgramModule(ABC):
    """One concrete subclass per supported grant programme."""

    program_id: ClassVar[str]
    name_tr: ClassVar[str]
    name_en: ClassVar[str]
    funder: ClassVar[str]
    language: ClassVar[ProgrammeLanguage]

    sections: ClassVar[list[str]]
    """Canonical section ids — usually ``["excellence", "impact", "implementation"]``."""

    subsection_map: ClassVar[dict[str, list[str]]]
    """E.g. ``{"excellence": ["1.1_objectives", "1.2_methodology", ...]}``."""

    @abstractmethod
    def parse_call(self, call_text: str, call_metadata: dict[str, Any]) -> CallMetadata:
        """Programme-specific call-document interpretation. Default impl
        often just wraps the Call Analyst output; programmes that need
        custom extraction override this."""

    @abstractmethod
    def get_brief_schema(self) -> BriefSchema:
        """Program-specific brief form schema for the frontend."""

    @abstractmethod
    def get_template_path(self) -> str:
        """DOCX template file path on disk (relative or absolute)."""

    @abstractmethod
    def get_prompt_path(self, agent_id: str) -> str:
        """Prompt file path for the given agent."""

    @abstractmethod
    def validate_draft(
        self, draft: dict[str, Any], metadata: CallMetadata
    ) -> list[ValidationIssue]:
        """Programme-specific draft validation rules."""

    @abstractmethod
    def export_docx(self, proposal: dict[str, Any]) -> bytes:
        """Render ``proposal["draft"]`` + budget into a DOCX and return bytes."""

    @abstractmethod
    def export_xlsx_budget(self, proposal: dict[str, Any]) -> bytes | None:
        """Optional Excel budget export — None for programmes that don't need one."""


__all__ = [
    "BaseProgramModule",
    "BriefField",
    "BriefSchema",
    "BriefSection",
    "CallMetadata",
    "FieldType",
    "ProgrammeLanguage",
    "Severity",
    "ValidationIssue",
]
