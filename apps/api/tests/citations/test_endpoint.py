"""POST /api/v1/citations/{id}/verify endpoint test.

Mocks both the JWT dependency and the HTTP client used inside the
verifier (via patching ``httpx.AsyncClient`` to a MockTransport-backed
one). Asserts the contract: 200 + VerifyResponse with the underlying
verification result.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import httpx
from httpx import AsyncClient
from src.core.auth import get_current_user_id


async def test_verify_endpoint_returns_result_for_known_doi(
    app: object, client: AsyncClient
) -> None:
    fake_user = uuid.uuid4()
    citation_id = uuid.uuid4()
    app.dependency_overrides[get_current_user_id] = lambda: fake_user  # type: ignore[attr-defined]

    def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "doi.org":
            return httpx.Response(200)
        if request.url.host == "api.crossref.org":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message": {
                        "DOI": "10.1234/foo",
                        "title": ["Edge AI for textile defect detection"],
                        "container-title": ["Pattern Recognition"],
                        "issued": {"date-parts": [[2023]]},
                    },
                },
            )
        return httpx.Response(404)

    # Inject MockTransport into ALL httpx.AsyncClient instances created
    # for the duration of the request — the route builds its own client.
    real_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(transport_handler)
        real_init(self, *args, **kwargs)

    try:
        with patch.object(httpx.AsyncClient, "__init__", patched_init):
            response = await client.post(
                f"/api/v1/citations/{citation_id}/verify",
                json={
                    "raw_text": "[Smith 2023]",
                    "doi": "10.1234/foo",
                    "title": "Edge AI for textile defect detection",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["citation_id"] == str(citation_id)
        assert body["result"]["status"] == "verified"
        assert body["result"]["source"] == "doi_direct"
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)  # type: ignore[attr-defined]


async def test_verify_endpoint_requires_auth(client: AsyncClient) -> None:
    """No bearer token → 401 / 403 from the bearer scheme."""

    response = await client.post(
        f"/api/v1/citations/{uuid.uuid4()}/verify",
        json={"raw_text": "[Smith 2023]"},
    )
    assert response.status_code in (401, 403)
