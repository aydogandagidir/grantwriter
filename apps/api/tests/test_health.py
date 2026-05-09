"""Smoke tests for the public /health endpoint."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


async def test_health_returns_version(client: AsyncClient) -> None:
    response = await client.get("/health")

    body = response.json()
    assert "version" in body
    assert isinstance(body["version"], str)
    assert body["version"]
