"""Versioned-prompt loader.

Layout:
    src/agents/prompts/<programme>/<agent>/<version>.md

``programme="_shared"`` for cross-programme prompts (e.g. Call Analyst,
Compliance Reviewer). Per-programme overrides go under
``programme="horizon_eu"``, ``"tubitak_1501"``, etc.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(*, programme: str, agent: str, version: str = "v1") -> str:
    """Read and return a prompt template as a string.

    Raises :class:`FileNotFoundError` with the resolved path on miss —
    silent template-not-found bugs ate the better part of an afternoon
    on a sister project; explicit failure is the correct trade.
    """

    path = _PROMPTS_DIR / programme / agent / f"{version}.md"
    return load_prompt_from_path(str(path))


def load_prompt_from_path(path: str) -> str:
    """Read a prompt file by absolute path.

    Used by writer agents that resolve the prompt location through the
    programme module (``module.get_prompt_path``) — the programme owns
    the layout, the agent just reads the result.
    """

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Prompt not found: {p}")
    return p.read_text(encoding="utf-8")


__all__ = ["load_prompt", "load_prompt_from_path"]
