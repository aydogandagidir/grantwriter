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
    if not path.is_file():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


__all__ = ["load_prompt"]
