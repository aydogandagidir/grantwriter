"""Shared fixtures and helpers for LLM router tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest
from src.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    ProviderName,
)
from src.llm.router import LLMRouter, PlatformKeys


class FakeProvider(LLMProvider):
    """Test double for :class:`LLMProvider`.

    Each call invokes the next ``script_step`` in order. A step is either a
    ready :class:`LLMResponse` (returned) or an :class:`Exception` (raised),
    or a callable that builds either at call time so tests can capture the
    request before producing a response.
    """

    def __init__(
        self,
        name: ProviderName,
        script: list[
            LLMResponse | Exception | Callable[[LLMRequest, str, str], LLMResponse | Exception]
        ],
    ) -> None:
        self.name = name
        self._script = list(script)
        self.calls: list[tuple[LLMRequest, str, str]] = []

    async def complete(self, request: LLMRequest, *, model: str, api_key: str) -> LLMResponse:
        self.calls.append((request, model, api_key))
        if not self._script:
            raise AssertionError(f"FakeProvider({self.name}) ran out of script entries")
        step = self._script.pop(0)
        if callable(step):
            step = step(request, model, api_key)
        if isinstance(step, Exception):
            raise step
        return step

    async def stream(self, request: LLMRequest, *, model: str, api_key: str) -> AsyncIterator[str]:
        # Stream tests aren't part of S1.D3.T1; minimal stub.
        response = await self.complete(request, model=model, api_key=api_key)
        for chunk in response.text.split(" "):
            yield chunk + " "


def make_response(
    *,
    text: str = "ok",
    model: str,
    provider: ProviderName,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cached_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cost_usd: float = 0.001,
) -> LLMResponse:
    return LLMResponse(
        text=text,
        model=model,
        provider=provider,
        usage=LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_creation_tokens=cache_creation_tokens,
        ),
        cost_usd=cost_usd,
    )


def make_request(
    *,
    task: str = "call_analyst",
    tenant_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    cache_system: bool = False,
    temperature: float = 0.0,
) -> LLMRequest:
    return LLMRequest(
        task=task,  # type: ignore[arg-type]
        tenant_id=tenant_id or uuid.uuid4(),
        proposal_id=proposal_id,
        user_id=user_id,
        system="You are a helpful test assistant.",
        messages=[{"role": "user", "content": "say hi"}],  # type: ignore[list-item]
        cache_system=cache_system,
        temperature=temperature,
        max_tokens=128,
    )


def build_router(
    *,
    providers: dict[ProviderName, LLMProvider],
    db_pool: asyncpg.Pool | None = None,
    platform_anthropic: str | None = "platform-anthropic-key",
    platform_openai: str | None = "platform-openai-key",
    master_key: str | None = None,
    max_retries: int = 3,
) -> LLMRouter:
    return LLMRouter(
        providers=providers,
        platform_keys=PlatformKeys(anthropic=platform_anthropic, openai=platform_openai),
        master_encryption_key=master_key,
        db_pool=db_pool,
        max_retries=max_retries,
    )


# ── Optional DB fixtures (skip when TEST_DATABASE_URL is unset) ──────────


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-bound LLM tests")
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


@asynccontextmanager
async def transient_tenant(
    pool: asyncpg.Pool, *, name: str = "LLMRouter Test"
) -> AsyncIterator[uuid.UUID]:
    """Create a tenant for the duration of the with-block; delete on exit."""

    tenant_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            name,
            f"t-{tenant_id}",
        )
    try:
        yield tenant_id
    finally:
        async with pool.acquire() as conn:
            await conn.execute("delete from tenant_llm_config where tenant_id = $1", tenant_id)
            await conn.execute("delete from tenant_usage_log where tenant_id = $1", tenant_id)
            await conn.execute("delete from tenants where id = $1", tenant_id)


__all__ = [
    "FakeProvider",
    "build_router",
    "make_request",
    "make_response",
    "transient_tenant",
]
