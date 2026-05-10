"""Endpoint tests for POST /generate, GET /jobs/{id}, and SSE stream guard.

The SSE stream itself is exercised more deeply in the integration tests
once Redis is wired into CI; here we just confirm the endpoint exists
and returns 503 cleanly when Redis is not configured.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from src.api.routes import jobs as jobs_module
from src.api.routes import proposals as proposals_module
from src.core.auth import get_current_user_id
from src.core.db import get_db


@pytest.fixture
def overridden_app(app: FastAPI) -> AsyncIterator[FastAPI]:
    fake_user_id = uuid.uuid4()
    fake_conn = AsyncMock()

    async def _user_override() -> uuid.UUID:
        return fake_user_id

    async def _db_override() -> AsyncIterator[Any]:
        yield fake_conn

    app.dependency_overrides[get_current_user_id] = _user_override
    app.dependency_overrides[get_db] = _db_override
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
