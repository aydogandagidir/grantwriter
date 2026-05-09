"""LLM provider abstraction and shared types.

This is the only place in the codebase that defines what an "LLM call"
looks like. Concrete providers (Claude, OpenAI) implement
:class:`LLMProvider`; the router orchestrates routing, retry, fallback,
and cost tracking across them. Business logic must never call SDKs
directly — it goes through :class:`~src.llm.router.LLMRouter`.
"""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

ProviderName = Literal["claude", "openai"]
TaskName = Literal[
    "call_analyst",
    "excellence_writer",
    "impact_writer",
    "implementation_writer",
    "compliance_reviewer",
    "hallucination_hunter",
    "rerank",
]
Role = Literal["user", "assistant"]


class LLMMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class LLMRequest(BaseModel):
    """Inputs to a single LLM call. Provider-agnostic."""

    model_config = ConfigDict(frozen=True)

    task: TaskName
    tenant_id: UUID
    proposal_id: UUID | None = None
    user_id: UUID | None = None
    system: str
    messages: list[LLMMessage]
    cache_system: bool = False
    """Hint to the provider to cache the system prompt block. Claude only;
    OpenAI ignores it (its caching is opaque, server-side)."""
    temperature: float = 0.0
    max_tokens: int = 4096


class LLMUsage(BaseModel):
    """Token accounting from a completed call."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    """Cache READ hits — billed at the cheap rate."""
    cache_creation_tokens: int = 0
    """Cache WRITE — billed at a slight premium, Claude only."""

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cached_tokens

    @property
    def has_cache_hit(self) -> bool:
        return self.cached_tokens > 0


class LLMResponse(BaseModel):
    """Outputs of a single LLM call."""

    model_config = ConfigDict(frozen=True)

    text: str
    model: str
    provider: ProviderName
    usage: LLMUsage
    finish_reason: str = "stop"
    cost_usd: float = 0.0
    used_byok: bool = False


# ── Pricing ──────────────────────────────────────────────────────────────


class ModelPricing(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_per_mtok: float
    output_per_mtok: float
    cached_read_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0


# Source: docs/06-agent-architecture.md §7.3 + Anthropic / OpenAI public
# pricing pages (snapshot 2026-05). Keep in sync; cost dashboards depend on it.
PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-7": ModelPricing(
        input_per_mtok=15.00,
        output_per_mtok=75.00,
        cached_read_per_mtok=1.50,
        cache_write_per_mtok=18.75,
    ),
    "claude-sonnet-4-6": ModelPricing(
        input_per_mtok=3.00,
        output_per_mtok=15.00,
        cached_read_per_mtok=0.30,
        cache_write_per_mtok=3.75,
    ),
    "gpt-4o": ModelPricing(
        input_per_mtok=2.50,
        output_per_mtok=10.00,
    ),
    "gpt-4o-mini": ModelPricing(
        input_per_mtok=0.15,
        output_per_mtok=0.60,
    ),
}


def calculate_cost(model: str, usage: LLMUsage) -> float:
    """USD cost for a call, computed from token counts and the pricing table.

    Returns 0.0 for unknown models — better silent than crash; the router
    logs a warning so the omission is visible without breaking the call.
    """

    pricing = PRICING.get(model)
    if pricing is None:
        return 0.0
    return (
        (usage.input_tokens / 1_000_000) * pricing.input_per_mtok
        + (usage.output_tokens / 1_000_000) * pricing.output_per_mtok
        + (usage.cached_tokens / 1_000_000) * pricing.cached_read_per_mtok
        + (usage.cache_creation_tokens / 1_000_000) * pricing.cache_write_per_mtok
    )


# ── Errors ───────────────────────────────────────────────────────────────


class LLMError(Exception):
    """Base for any LLM-call failure."""


class LLMRetryableError(LLMError):
    """Transient: rate-limit, timeout, 5xx — caller should retry / fall back."""


class LLMUnrecoverableError(LLMError):
    """Permanent: 400 (bad request), 401 (bad key), 403, content-policy refusal."""


# ── Provider interface ───────────────────────────────────────────────────


class LLMProvider(ABC):
    """Concrete providers (Claude, OpenAI) implement this."""

    name: ProviderName

    @abstractmethod
    async def complete(self, request: LLMRequest, *, model: str, api_key: str) -> LLMResponse:
        """One-shot completion. Must raise :class:`LLMRetryableError` for
        transient failures and :class:`LLMUnrecoverableError` for permanent
        ones — the router uses that distinction to decide on fallback."""

    @abstractmethod
    def stream(self, request: LLMRequest, *, model: str, api_key: str) -> AsyncIterator[str]:
        """Yield text deltas as they arrive. Implementations must aggregate
        usage internally and expose it via a ``last_response`` attribute or
        equivalent (provider-specific)."""


# ── Retry helper ─────────────────────────────────────────────────────────

T = TypeVar("T")


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    cap_delay: float = 8.0,
) -> T:
    """Run ``operation`` with exponential backoff on :class:`LLMRetryableError`.

    Backoff: ``base_delay * 2**attempt`` with a small uniform jitter, capped
    at ``cap_delay``. ``LLMUnrecoverableError`` (and any other exception) is
    re-raised immediately so the router can fall back to the secondary
    provider without burning the retry budget.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_exc: LLMRetryableError | None = None
    for attempt in range(max_attempts):
        try:
            return await operation()
        except LLMRetryableError as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                raise
            delay = min(base_delay * (2**attempt), cap_delay) + random.uniform(0, 0.25)
            await asyncio.sleep(delay)
    # Unreachable in practice; mypy needs the explicit raise.
    assert last_exc is not None
    raise last_exc


# ── Task → model routing table ───────────────────────────────────────────


class RouteEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    primary_provider: ProviderName
    primary_model: str
    fallback_provider: ProviderName
    fallback_model: str
    temperature: float = 0.0
    max_tokens: int = 4096


# Source: docs/06-agent-architecture.md §7.2.
TASK_ROUTES: dict[TaskName, RouteEntry] = {
    "call_analyst": RouteEntry(
        primary_provider="claude",
        primary_model="claude-opus-4-7",
        fallback_provider="openai",
        fallback_model="gpt-4o",
        temperature=0.0,  # deterministic parsing
        max_tokens=4096,
    ),
    "excellence_writer": RouteEntry(
        primary_provider="claude",
        primary_model="claude-opus-4-7",
        fallback_provider="openai",
        fallback_model="gpt-4o",
        temperature=0.3,  # slightly creative
        max_tokens=8192,
    ),
    "impact_writer": RouteEntry(
        primary_provider="claude",
        primary_model="claude-opus-4-7",
        fallback_provider="openai",
        fallback_model="gpt-4o",
        temperature=0.3,
        max_tokens=8192,
    ),
    "implementation_writer": RouteEntry(
        primary_provider="claude",
        primary_model="claude-opus-4-7",
        fallback_provider="openai",
        fallback_model="gpt-4o",
        temperature=0.3,
        max_tokens=8192,
    ),
    "compliance_reviewer": RouteEntry(
        primary_provider="claude",
        primary_model="claude-sonnet-4-6",
        fallback_provider="openai",
        fallback_model="gpt-4o-mini",
        temperature=0.0,
        max_tokens=2048,
    ),
    "hallucination_hunter": RouteEntry(
        primary_provider="claude",
        primary_model="claude-sonnet-4-6",
        fallback_provider="openai",
        fallback_model="gpt-4o-mini",
        temperature=0.0,
        max_tokens=2048,
    ),
    "rerank": RouteEntry(
        # RAG candidate re-ranking — Sonnet-class is plenty for ordering 20
        # short chunks; deterministic temperature so order is reproducible.
        primary_provider="claude",
        primary_model="claude-sonnet-4-6",
        fallback_provider="openai",
        fallback_model="gpt-4o-mini",
        temperature=0.0,
        max_tokens=512,
    ),
}


def route_for(task: TaskName) -> RouteEntry:
    return TASK_ROUTES[task]


__all__ = [
    "LLMError",
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMRetryableError",
    "LLMUnrecoverableError",
    "LLMUsage",
    "ModelPricing",
    "PRICING",
    "ProviderName",
    "RouteEntry",
    "TASK_ROUTES",
    "TaskName",
    "calculate_cost",
    "route_for",
    "with_retries",
]
