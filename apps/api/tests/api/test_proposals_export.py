"""HTTP endpoint test for POST /api/v1/proposals/{id}/export.

Mocks :meth:`generate_proposal_docx_task.delay` so the test does not
need a Celery worker or a Redis broker. Mocks the JWT dependency so
the test does not need a real signing secret.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from httpx import AsyncClient
from src.api.routes.proposals import enqueue_export
from src.core.auth import get_current_user_id


async def test_export_endpoint_enqueues_task_and_returns_job_id(
    app: object, client: AsyncClient
) -> None:
    fake_user = uuid.uuid4()
    fake_task_id = "celery-task-id-abc123"

    # Bypass the bearer-token dependency for this test.
    app.dependency_overrides[get_current_user_id] = lambda: fake_user  # type: ignore[attr-defined]

    proposal_id = uuid.uuid4()
    proposal_payload = {
        "tenant_id": str(uuid.uuid4()),
        "programme_id": "tubitak_1501",
        "title": "Smoke",
        "draft": {
            "excellence_md": "## B1\n\n.",
            "impact_md": "## C1\n\n.",
            "implementation_md": "## D1\n\n.",
        },
        "budget": {"by_category": {}},
    }

    class _FakeAsyncResult:
        id = fake_task_id

    with patch(
        "src.api.routes.proposals.generate_proposal_docx_task.delay",
        return_value=_FakeAsyncResult(),
    ) as mock_delay:
        response = await client.post(
            f"/api/v1/proposals/{proposal_id}/export", json=proposal_payload
        )

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == fake_task_id
    assert body["status"] == "queued"
    assert body["proposal_id"] == str(proposal_id)

    # The task got the expanded proposal dict (with id injected).
    mock_delay.assert_called_once()
    sent_payload = mock_delay.call_args.args[0]
    assert sent_payload["id"] == str(proposal_id)
    assert sent_payload["programme_id"] == "tubitak_1501"

    # Cleanup — don't leak the override into other tests in the session.
    app.dependency_overrides.pop(get_current_user_id, None)  # type: ignore[attr-defined]


async def test_export_endpoint_requires_auth(client: AsyncClient) -> None:
    """No bearer token → 401 from the bearer scheme."""

    response = await client.post(f"/api/v1/proposals/{uuid.uuid4()}/export", json={})
    assert response.status_code in (401, 403)


# Silence the unused-import warning for the helper above.
_ = enqueue_export
