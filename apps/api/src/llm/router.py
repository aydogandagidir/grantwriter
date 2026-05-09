"""LLMRouter — the only public entry point for LLM calls in this codebase.

Resolves task → (provider, model), pulls per-tenant BYOK keys when present,
falls back to platform keys otherwise, retries transient failures, falls
back to the secondary provider on persistent ones, and logs every
successful call to ``tenant_usage_log``.

Business logic NEVER imports anthropic or openai directly. Always go through
``LLMRouter.complete(request)``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg

from src.llm import cost_tracker, key_vault
from src.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMRetryableError,
    LLMUnrecoverableError,
    ProviderName,
    route_for,
    with_retries,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlatformKeys:
    """Bluedev-managed fallback keys for tenants without BYOK."""

    anthropic: str | None = None
    openai: str | None = None


class LLMRouter:
    """Per-process orchestrator.

    Construct once with concrete providers (or test fakes) and reuse across
    requests. The router is stateless — concurrency-safe.
    """

    def __init__(
        self,
        *,
        providers: dict[ProviderName, LLMProvider],
        platform_keys: PlatformKeys,
        master_encryption_key: str | None,
        db_pool: asyncpg.Pool | None = None,
        max_retries: int = 3,
    ) -> None:
        self._providers = providers
        self._platform_keys = platform_keys
        self._master_key = master_encryption_key
        self._db_pool = db_pool
        self._max_retries = max_retries

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run a request to completion, with retry + fallback + cost logging.

        Returns the :class:`LLMResponse` from whichever provider succeeded.
        Raises :class:`LLMUnrecoverableError` if both primary and fallback
        return permanent failures.
        """

        route = route_for(request.task)
        request_with_route = request.model_copy(
            update={
                "temperature": request.temperature or route.temperature,
                "max_tokens": request.max_tokens or route.max_tokens,
            }
        )

        # Try primary, fall back on retryable-after-retries OR permanent failure.
        try:
            response = await self._call_provider(
                request_with_route,
                provider_name=route.primary_provider,
                model=route.primary_model,
            )
            await self._log(request_with_route, response)
            return response
        except LLMRetryableError as exc:
            logger.warning(
                "primary_exhausted_falling_back",
                extra={
                    "task": request.task,
                    "primary_provider": route.primary_provider,
                    "primary_model": route.primary_model,
                    "fallback_provider": route.fallback_provider,
                    "fallback_model": route.fallback_model,
                    "reason": "retryable_after_retries",
                    "exc_type": type(exc).__name__,
                },
            )
        except LLMUnrecoverableError as exc:
            logger.warning(
                "primary_failed_falling_back",
                extra={
                    "task": request.task,
                    "primary_provider": route.primary_provider,
                    "fallback_provider": route.fallback_provider,
                    "reason": "unrecoverable",
                    "exc_type": type(exc).__name__,
                },
            )

        response = await self._call_provider(
            request_with_route,
            provider_name=route.fallback_provider,
            model=route.fallback_model,
        )
        await self._log(request_with_route, response)
        return response

    async def _call_provider(
        self,
        request: LLMRequest,
        *,
        provider_name: ProviderName,
        model: str,
    ) -> LLMResponse:
        provider = self._providers[provider_name]
        api_key, used_byok = await self._resolve_api_key(provider_name, request)
        if api_key is None:
            raise LLMUnrecoverableError(
                f"No API key available for provider={provider_name} "
                f"(tenant has no BYOK and platform key is unset)"
            )

        async def attempt() -> LLMResponse:
            return await provider.complete(request, model=model, api_key=api_key)

        response = await with_retries(attempt, max_attempts=self._max_retries)
        return response.model_copy(update={"used_byok": used_byok})

    async def _resolve_api_key(
        self, provider_name: ProviderName, request: LLMRequest
    ) -> tuple[str | None, bool]:
        """Return ``(api_key, used_byok)``. BYOK keys never reach the logger."""

        kind: key_vault.KeyKind = "anthropic" if provider_name == "claude" else "openai"

        if self._db_pool is not None and self._master_key:
            async with self._db_pool.acquire() as conn:
                if not await key_vault.prefer_managed_keys(conn, tenant_id=request.tenant_id):
                    byok = await key_vault.get_byok_key(
                        conn,
                        tenant_id=request.tenant_id,
                        kind=kind,
                        master_key=self._master_key,
                    )
                    if byok:
                        return byok, True

        platform = (
            self._platform_keys.anthropic
            if provider_name == "claude"
            else self._platform_keys.openai
        )
        return platform, False

    async def _log(self, request: LLMRequest, response: LLMResponse) -> None:
        """Best-effort logging — failures here must not fail the LLM call."""

        if self._db_pool is None:
            return
        try:
            async with self._db_pool.acquire() as conn:
                await cost_tracker.log_call(conn, request=request, response=response)
        except Exception:
            logger.exception(
                "cost_tracker_failed",
                extra={
                    "task": request.task,
                    "provider": response.provider,
                    "model": response.model,
                },
            )


# Convenience factory — used by application code; tests construct the
# router directly with FakeProvider instances.
def build_default_router(
    *,
    db_pool: asyncpg.Pool | None,
    platform_anthropic_key: str | None,
    platform_openai_key: str | None,
    master_encryption_key: str | None,
) -> LLMRouter:
    """Wire up the production providers and return a ready-to-use router."""

    # Imports are deferred so unit tests that exercise pure routing logic
    # don't pay the cost of loading the SDKs.
    from src.llm.claude_provider import ClaudeProvider
    from src.llm.openai_provider import OpenAIProvider

    return LLMRouter(
        providers={
            "claude": ClaudeProvider(),
            "openai": OpenAIProvider(),
        },
        platform_keys=PlatformKeys(
            anthropic=platform_anthropic_key,
            openai=platform_openai_key,
        ),
        master_encryption_key=master_encryption_key,
        db_pool=db_pool,
    )


__all__ = ["LLMRouter", "PlatformKeys", "build_default_router"]
