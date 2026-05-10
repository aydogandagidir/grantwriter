"""Endpoint tests for POST /generate, GET /jobs/{id}, and SSE stream guard.

The SSE stream itself is exercised more deeply in the integration tests
once Redis is wired into CI; here we just confirm the endpoint exists
and returns 503 cleanly when Redis is not configured.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from src.api.routes import jobs as jobs_module
from src.api.routes import proposals as proposals_module
from src.core.auth import get_current_user_id
from src.core.db import get_db


def _allowed_quota_row() -> dict[str, Any]:
    """Default consume_quota return — tenant has headroom."""

    return {
        "monthly_proposals_used": 1,
        "monthly_proposal_limit": 3,
        "billing_period_start": date(2026, 5, 1),
        "plan": "starter",
    }


@pytest.fixture
def overridden_app(app: FastAPI) -> AsyncIterator[FastAPI]:
    """Default DB stub: tenant exists, quota has headroom.

    Individual tests override ``fake_conn.fetchval`` / ``fake_conn.fetchrow``
    to exercise the missing-proposal (404) and quota-exhausted (402)
    branches.
    """

    fake_user_id = uuid.uuid4()
    fake_conn = AsyncMock()
    # fetchval is the proposal-lookup → tenant_id resolver in /generate.
    fake_conn.fetchval.return_value = uuid.uuid4()
    # fetchrow drives consume_quota; the default row keeps existing tests
    # passing through the happy path.
    fake_conn.fetchrow.return_value = _allowed_quota_row()

    async def _user_override() -> uuid.UUID:
        return fake_user_id

    async def _db_override() -> AsyncIterator[Any]:
        yield fake_conn

    app.dependency_overrides[get_current_user_id] = _user_override
    app.dependency_overrides[get_db] = _db_override
    # Stash the conn on the app so individual tests can re-program return
    # values without re-building the override stack.
    app.state._test_fake_conn = fake_conn
    yield app
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_db, None)


# ── POST /generate ─────────────────────────────────────────────────────


async def test_generate_enqueues_task_and_returns_job_info(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST returns 202 with job_id, status_url, stream_url."""

    fake_async_result = MagicMock()
    fake_async_result.id = "fake-celery-task-id"

    fake_delay = MagicMock(return_value=fake_async_result)
    monkeypatch.setattr(
        proposals_module.generate_draft_task, "delay", fake_delay
    )

    proposal_id = uuid.uuid4()
    response = await client.post(f"/api/v1/proposals/{proposal_id}/generate")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == "fake-celery-task-id"
    assert body["proposal_id"] == str(proposal_id)
    assert body["status_url"] == "/api/v1/jobs/fake-celery-task-id"
    assert body["stream_url"] == f"/api/v1/proposals/{proposal_id}/stream"
    assert body["estimated_duration_seconds"] >= 1

    fake_delay.assert_called_once_with(str(proposal_id))


async def test_generate_returns_404_when_proposal_missing(
    overridden_app: FastAPI,
    client: AsyncClient,
) -> None:
    """The proposal-id → tenant-id lookup returns NULL → 404 before quota."""

    overridden_app.state._test_fake_conn.fetchval.return_value = None

    response = await client.post(f"/api/v1/proposals/{uuid.uuid4()}/generate")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


async def test_generate_returns_402_when_quota_exhausted(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """consume_quota returns None (denied) and peek_quota reports the
    snapshot. Route surfaces 402 + ``X-Plan-*`` headers + structured
    detail body. The Celery task must NOT be enqueued."""

    fake_conn = overridden_app.state._test_fake_conn
    # First fetchrow = consume (denied), second fetchrow = peek (snapshot).
    fake_conn.fetchrow.side_effect = [
        None,
        {
            "used_this_month": 3,
            "monthly_proposal_limit": 3,
            "current_period_start": date(2026, 5, 1),
            "plan": "starter",
        },
    ]

    fake_delay = MagicMock()
    monkeypatch.setattr(
        proposals_module.generate_draft_task, "delay", fake_delay
    )

    response = await client.post(f"/api/v1/proposals/{uuid.uuid4()}/generate")

    assert response.status_code == 402
    body = response.json()
    assert body["detail"]["error"] == "monthly_quota_exceeded"
    assert body["detail"]["plan"] == "starter"
    assert body["detail"]["limit"] == 3
    assert body["detail"]["used"] == 3
    assert body["detail"]["period_start"] == "2026-05-01"

    assert response.headers["X-Plan-Limit"] == "3"
    assert response.headers["X-Plan-Used"] == "3"
    assert response.headers["X-Plan-Remaining"] == "0"

    fake_delay.assert_not_called()


# ── GET /jobs/{id} ─────────────────────────────────────────────────────


def _patch_async_result(
    monkeypatch: pytest.MonkeyPatch, *, state: str, result: Any = None
) -> None:
    fake = MagicMock()
    fake.state = state
    fake.result = result

    def _factory(_job_id: str) -> Any:
        return fake

    monkeypatch.setattr(jobs_module.celery_app, "AsyncResult", _factory)


async def test_jobs_returns_completed_with_result_payload(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_async_result(
        monkeypatch,
        state="SUCCESS",
        result={"status": "draft_complete", "agent_outputs": {}},
    )
    response = await client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"] == {"status": "draft_complete", "agent_outputs": {}}
    assert body["error"] is None


async def test_jobs_returns_running_for_started_state(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_async_result(monkeypatch, state="STARTED")
    response = await client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


async def test_jobs_returns_queued_for_pending_state(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_async_result(monkeypatch, state="PENDING")
    response = await client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


async def test_jobs_returns_failed_with_error_message(
    overridden_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_async_result(
        monkeypatch, state="FAILURE", result=RuntimeError("LLM provider down")
    )
    response = await client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "LLM provider down" in (body["error"] or "")


# ── GET /stream ────────────────────────────────────────────────────────


async def test_stream_returns_503_when_redis_not_configured(
    overridden_app: FastAPI,
    client: AsyncClient,
) -> None:
    """In test mode REDIS_URL is unset → endpoint should fail cleanly."""

    response = await client.get(
        f"/api/v1/proposals/{uuid.uuid4()}/stream",
    )
    assert response.status_code == 503
    assert "redis" in response.json()["detail"].lower()
