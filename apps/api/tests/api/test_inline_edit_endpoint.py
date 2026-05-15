"""Endpoint tests for ``POST /proposals/{id}/inline-edit`` (Faz 4).

The editor's slash-command surface hits this endpoint with a selection
plus a small window of surrounding context; the server pipes that into
``LLMRouter.complete(task='inline_rewrite')`` and returns the
replacement text.

We mock the router so the route layer is the only thing under test —
prompt assembly, validation (selection too short / too long), 404 on
missing proposal, response shape, and that the user_id + tenant_id +
proposal_id propagate onto the LLMRequest for cost accounting.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from src.core.auth import get_current_user_id
from src.core.db import get_db
from src.core.llm_dep import get_llm_router
from src.llm.base import LLMRequest, LLMResponse, LLMUsage


def _proposal_row() -> dict[str, Any]:
    return {
        "tenant_id": uuid4(),
        "language": "en",
        "programme_id": "horizon_eu_ria",
    }


def _llm_response(text: str = "Rewritten text.") -> LLMResponse:
    return LLMResponse(
        text=text,
        model="claude-sonnet-4-6",
        provider="claude",
        usage=LLMUsage(input_tokens=120, output_tokens=80),
        cost_usd=0.0021,
    )


@pytest.fixture
def overridden_app(app: FastAPI) -> AsyncIterator[FastAPI]:
    """Override JWT + DB + LLM router deps for endpoint isolation."""

    fake_user_id = uuid.uuid4()
    fake_conn = AsyncMock()
    fake_conn.fetchrow = AsyncMock(return_value=_proposal_row())

    fake_router = AsyncMock()
    fake_router.complete = AsyncMock(return_value=_llm_response())

    async def _user_override() -> uuid.UUID:
        return fake_user_id

    async def _db_override() -> AsyncIterator[Any]:
        yield fake_conn

    async def _router_override() -> Any:
        return fake_router

    app.dependency_overrides[get_current_user_id] = _user_override
    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_llm_router] = _router_override
    app.state.test_conn = fake_conn  # type: ignore[attr-defined]
    app.state.test_router = fake_router  # type: ignore[attr-defined]
    yield app
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_llm_router, None)


# ── happy path ─────────────────────────────────────────────────────────


async def test_inline_edit_returns_replacement_text(
    overridden_app: FastAPI, client: AsyncClient
) -> None:
    overridden_app.state.test_router.complete = AsyncMock(  # type: ignore[attr-defined]
        return_value=_llm_response(text="A clearer version of the selection.")
    )

    response = await client.post(
        f"/api/v1/proposals/{uuid.uuid4()}/inline-edit",
        json={
            "command": "rewrite",
            "section": "excellence",
            "selection_text": "The project will do X and then do Y.",
            "context_before": "Section preamble. ",
            "context_after": " Trailing sentences.",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["replacement_text"] == "A clearer version of the selection."
    assert body["command"] == "rewrite"
    assert body["model"] == "claude-sonnet-4-6"
    assert body["tokens_used"] == 200
    assert body["cost_usd"] == 0.0021


async def test_inline_edit_strips_model_meta_prefix(
    overridden_app: FastAPI, client: AsyncClient
) -> None:
    """Some Sonnet replies start with 'Here's the rewrite:' despite the
    system prompt; the endpoint trims that single leading meta line."""

    overridden_app.state.test_router.complete = AsyncMock(  # type: ignore[attr-defined]
        return_value=_llm_response(
            text="Here's a shorter version:\nThe project executes X then Y."
        )
    )

    response = await client.post(
        f"/api/v1/proposals/{uuid.uuid4()}/inline-edit",
        json={
            "command": "shorter",
            "section": "impact",
            "selection_text": "The project will do X and then do Y.",
        },
    )

    assert response.status_code == 200
    assert (
        response.json()["replacement_text"] == "The project executes X then Y."
    )


async def test_inline_edit_routes_through_inline_rewrite_task(
    overridden_app: FastAPI, client: AsyncClient
) -> None:
    """The LLMRequest the route builds must have ``task='inline_rewrite'``
    and carry tenant_id / proposal_id / user_id so cost accounting wires
    up correctly."""

    fake_router = overridden_app.state.test_router  # type: ignore[attr-defined]
    proposal_id = uuid.uuid4()

    response = await client.post(
        f"/api/v1/proposals/{proposal_id}/inline-edit",
        json={
            "command": "longer",
            "section": "implementation",
            "selection_text": "WP1 starts in month 1.",
        },
    )

    assert response.status_code == 200
    assert fake_router.complete.await_count == 1
    request: LLMRequest = fake_router.complete.await_args.args[0]
    assert request.task == "inline_rewrite"
    assert request.proposal_id == proposal_id
    assert request.tenant_id is not None
    assert request.user_id is not None
    # System prompt should be the "longer" variant, not "shorter" or "rewrite".
    assert "Expand" in request.system or "expand" in request.system
    user_msg = request.messages[0].content
    assert "<section>implementation</section>" in user_msg
    assert "WP1 starts in month 1." in user_msg


async def test_inline_edit_clamps_long_context_windows(
    overridden_app: FastAPI, client: AsyncClient
) -> None:
    """A 5 000-char context_before should be clamped to the last 500."""

    fake_router = overridden_app.state.test_router  # type: ignore[attr-defined]
    big_context = "x" * 5000

    response = await client.post(
        f"/api/v1/proposals/{uuid.uuid4()}/inline-edit",
        json={
            "command": "rewrite",
            "section": "excellence",
            "selection_text": "Selection.",
            "context_before": big_context,
            "context_after": big_context,
        },
    )

    assert response.status_code == 200
    request: LLMRequest = fake_router.complete.await_args.args[0]
    # Each context window is capped at 500 chars. We check by locating
    # the run of consecutive x's inside each XML wrapper rather than
    # counting bare x's in the whole user message — "conte**x**t_before"
    # in the tag name otherwise inflates the count by a few.
    user_msg = request.messages[0].content
    import re

    before_match = re.search(r"<context_before>\n(x+)\n</context_before>", user_msg)
    after_match = re.search(r"<context_after>\n(x+)\n</context_after>", user_msg)
    assert before_match is not None, "context_before block missing"
    assert after_match is not None, "context_after block missing"
    assert len(before_match.group(1)) == 500, (
        f"context_before should clamp to 500 chars, got {len(before_match.group(1))}"
    )
    assert len(after_match.group(1)) == 500, (
        f"context_after should clamp to 500 chars, got {len(after_match.group(1))}"
    )


# ── validation errors ──────────────────────────────────────────────────


async def test_inline_edit_rejects_empty_selection(
    overridden_app: FastAPI, client: AsyncClient
) -> None:
    response = await client.post(
        f"/api/v1/proposals/{uuid.uuid4()}/inline-edit",
        json={
            "command": "rewrite",
            "section": "excellence",
            "selection_text": "   \n\n   ",
        },
    )

    assert response.status_code == 400
    assert "non-empty" in response.json()["detail"]


async def test_inline_edit_rejects_too_long_selection(
    overridden_app: FastAPI, client: AsyncClient
) -> None:
    """A 4 001-char selection is the section-regenerate flow's job."""

    response = await client.post(
        f"/api/v1/proposals/{uuid.uuid4()}/inline-edit",
        json={
            "command": "rewrite",
            "section": "excellence",
            "selection_text": "a" * 4001,
        },
    )

    assert response.status_code == 400
    assert "too long" in response.json()["detail"]


async def test_inline_edit_rejects_unknown_command(
    overridden_app: FastAPI, client: AsyncClient
) -> None:
    """Pydantic Literal validation rejects unknown command strings."""

    response = await client.post(
        f"/api/v1/proposals/{uuid.uuid4()}/inline-edit",
        json={
            "command": "make_it_better",
            "section": "excellence",
            "selection_text": "Selection.",
        },
    )

    assert response.status_code == 422  # FastAPI body validation


# ── 404 ────────────────────────────────────────────────────────────────


async def test_inline_edit_404s_when_proposal_missing(
    overridden_app: FastAPI, client: AsyncClient
) -> None:
    overridden_app.state.test_conn.fetchrow = AsyncMock(return_value=None)  # type: ignore[attr-defined]

    response = await client.post(
        f"/api/v1/proposals/{uuid.uuid4()}/inline-edit",
        json={
            "command": "rewrite",
            "section": "excellence",
            "selection_text": "Selection.",
        },
    )

    assert response.status_code == 404


# ── per-command system prompt selection ───────────────────────────────


@pytest.mark.parametrize(
    "command, keyword",
    [
        ("rewrite", "clearly"),
        ("shorter", "30"),
        ("longer", "Expand"),
        ("translate_en", "English"),
        ("translate_tr", "Turkish"),
    ],
)
async def test_inline_edit_system_prompt_varies_per_command(
    overridden_app: FastAPI,
    client: AsyncClient,
    command: str,
    keyword: str,
) -> None:
    fake_router = overridden_app.state.test_router  # type: ignore[attr-defined]

    response = await client.post(
        f"/api/v1/proposals/{uuid.uuid4()}/inline-edit",
        json={
            "command": command,
            "section": "excellence",
            "selection_text": "Some text.",
        },
    )

    assert response.status_code == 200
    request: LLMRequest = fake_router.complete.await_args.args[0]
    assert keyword in request.system, (
        f"system prompt for {command!r} missing keyword {keyword!r}"
    )


# ── compatibility check on the LLMResponse shape ──────────────────────


def test_llm_response_json_round_trip() -> None:
    """Sanity check that LLMResponse serialises cleanly — the route
    relies on ``.text``, ``.model``, ``.usage.total``, ``.cost_usd``.
    """

    resp = _llm_response(text="hello")
    payload = json.loads(resp.model_dump_json())
    assert payload["text"] == "hello"
    assert payload["model"] == "claude-sonnet-4-6"
    assert payload["usage"]["input_tokens"] == 120
