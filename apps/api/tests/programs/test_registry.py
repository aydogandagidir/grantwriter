"""Plugin-system parity test — every registered programme must implement
the BaseProgramModule contract end-to-end.

Per docs/07 §8. The plug-and-play promise (add a new programme by
adding a directory + one registry line) is only credible if every
existing programme also conforms. This file is the smoke test that
catches drift early — if a future programme forgets a prompt file or
a brief field, this fails before the orchestrator does at runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.programs import REGISTRY
from src.programs.base import BaseProgramModule

_EXPECTED_PROGRAMMES = {
    "tubitak_1501",
    "tubitak_1507",
    "kosgeb_arge",
    "horizon_eu_ria",
    "cascade_funding",
}

_WRITER_AGENTS = ("excellence_writer", "impact_writer", "implementation_writer")


def test_registry_contains_all_five_programmes() -> None:
    assert set(REGISTRY) == _EXPECTED_PROGRAMMES


@pytest.mark.parametrize("programme_id", sorted(_EXPECTED_PROGRAMMES))
def test_programme_implements_base_interface(programme_id: str) -> None:
    module = REGISTRY[programme_id]
    assert isinstance(module, BaseProgramModule)
    assert module.program_id == programme_id
    assert module.name_tr
    assert module.name_en
    assert module.funder
    assert module.language in ("tr", "en", "both")
    assert module.sections, f"{programme_id}: empty sections"
    assert module.subsection_map, f"{programme_id}: empty subsection_map"


@pytest.mark.parametrize("programme_id", sorted(_EXPECTED_PROGRAMMES))
def test_programme_brief_schema_renders(programme_id: str) -> None:
    schema = REGISTRY[programme_id].get_brief_schema()
    assert len(schema.sections) > 0, f"{programme_id}: brief has no sections"
    field_keys = {f.key for s in schema.sections for f in s.fields}
    assert "title" in field_keys, f"{programme_id}: brief missing 'title' field"


@pytest.mark.parametrize("programme_id", sorted(_EXPECTED_PROGRAMMES))
def test_programme_template_path_exists(programme_id: str) -> None:
    template_path = Path(REGISTRY[programme_id].get_template_path())
    assert template_path.is_file(), f"{programme_id}: template missing at {template_path}"


@pytest.mark.parametrize("programme_id", sorted(_EXPECTED_PROGRAMMES))
def test_programme_writer_prompts_resolve(programme_id: str) -> None:
    module = REGISTRY[programme_id]
    for agent_id in _WRITER_AGENTS:
        prompt_path = Path(module.get_prompt_path(agent_id))
        assert prompt_path.is_file(), (
            f"{programme_id}: prompt missing for {agent_id} at {prompt_path}"
        )


@pytest.mark.parametrize("programme_id", sorted(_EXPECTED_PROGRAMMES))
def test_programme_validate_draft_accepts_empty(programme_id: str) -> None:
    """An empty draft should not crash validate_draft — it returns
    issues (programme-specific blockers) but doesn't raise."""

    module = REGISTRY[programme_id]
    metadata = module.parse_call("", {})
    issues = module.validate_draft({}, metadata)
    # Validation may or may not find issues on an empty draft, but
    # the call itself must succeed and return a list.
    assert isinstance(issues, list)


@pytest.mark.parametrize("programme_id", sorted(_EXPECTED_PROGRAMMES))
def test_programme_export_docx_handles_minimal_proposal(programme_id: str) -> None:
    """Every programme should produce a non-empty DOCX from a minimal
    proposal dict — the export path is part of the public contract."""

    module = REGISTRY[programme_id]
    blob = module.export_docx(
        {
            "title": "Smoke Test Project",
            "draft": {
                "excellence_md": "## section\n\nbody.",
                "impact_md": "## section\n\nbody.",
                "implementation_md": "## section\n\nbody.",
            },
        }
    )
    assert isinstance(blob, bytes)
    assert len(blob) > 1000  # non-trivial DOCX
