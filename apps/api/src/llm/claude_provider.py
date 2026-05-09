"""Anthropic Claude provider.

The only place in the codebase that imports the ``anthropic`` SDK directly.
Wraps :class:`AsyncAnthropic` and exposes the provider-agnostic
:class:`~src.llm.base.LLMProvider` interface.

Implements:
- system-prompt caching via ``cache_control: {"type": "ephemeral"}``
- error mapping (rate-limit / 5xx / timeout → retryable; 400/401/403 →
  unrecoverable)
- usage extraction including ``cache_creation_input_tokens`` and
  ``cache_read_input_tokens`` for per-call cost accounting.

Streaming yields raw text deltas; usage is aggregated and exposed via
:attr:`ClaudeProvider.last_stream_usage` for the router.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from src.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMRetryableError,
    LLMUnrecoverableError,
    LLMUsage,
    ProviderName,
    calculate_cost,
)

logger = logging.getLogger(__name__)

_HTTP_SERVER_ERROR_FLOOR = 500
_HTTP_SERVER_ERROR_CEIL = 600

_RETRYABLE_TYPES: tuple[type[BaseException], ...] = (
    anthropic.RateLimitError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
)
_UNRECOVERABLE_TYPES: tuple[type[BaseException], ...] = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.BadRequestError,
    anthropic.NotFoundError,
)


def _map_anthropic_error(exc: BaseException) -> LLMRetryableError | LLMUnrecoverableError:
    """Translate an Anthropic SDK exception into our retryable/permanent split."""

    if isinstance(exc, _RETRYABLE_TYPES):
        return LLMRetryableError(str(exc))
    if isinstance(exc, _UNRECOVERABLE_TYPES):
        return LLMUnrecoverableError(str(exc))
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", _HTTP_SERVER_ERROR_FLOOR) or _HTTP_SERVER_ERROR_FLOOR
        if _HTTP_SERVER_ERROR_FLOOR <= status < _HTTP_SERVER_ERROR_CEIL:
            return LLMRetryableError(str(exc))
        return LLMUnrecoverableError(str(exc))
    return LLMUnrecoverableError(str(exc))


def _build_system_blocks(system: str, *, cache: bool) -> list[dict[str, Any]] | str:
    """Return a list of system blocks (with ephemeral cache_control if requested)
    or a plain string when caching is disabled."""

    if not cache:
        return system
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _to_anthropic_messages(messages: list[LLMMessage]) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


def _extract_usage(raw: Any) -> LLMUsage:
    """Pull usage fields from an SDK response (Message or final stream object)."""

    return LLMUsage(
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cached_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
    )


def _extract_text(raw: Any) -> str:
    """Concatenate the text blocks of an Anthropic Message response."""

    parts: list[str] = []
    for block in getattr(raw, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts)


class ClaudeProvider(LLMProvider):
    name: ProviderName = "claude"

    def __init__(self, *, default_timeout: float = 60.0) -> None:
        self._default_timeout = default_timeout
        self.last_stream_usage: LLMUsage | None = None

    def _client(self, api_key: str) -> anthropic.AsyncAnthropic:
        # New client per call: avoids stale state and keeps BYOK keys per-call
        # rather than persisted on a long-lived object that might get logged.
        return anthropic.AsyncAnthropic(api_key=api_key, timeout=self._default_timeout)

    async def complete(self, request: LLMRequest, *, model: str, api_key: str) -> LLMResponse:
        client = self._client(api_key)
        try:
            # SDK expects TextBlockParam / MessageParam TypedDicts; our internal
            # Pydantic models project to the same shape but mypy can't see that
            # cleanly without pulling the full SDK typing surface in here.
            raw = await client.messages.create(
                model=model,
                system=_build_system_blocks(request.system, cache=request.cache_system),  # type: ignore[arg-type]
                messages=_to_anthropic_messages(request.messages),  # type: ignore[arg-type]
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        except anthropic.APIError as exc:
            raise _map_anthropic_error(exc) from exc

        usage = _extract_usage(getattr(raw, "usage", None) or raw)
        text = _extract_text(raw)
        cost = calculate_cost(model, usage)
        return LLMResponse(
            text=text,
            model=model,
            provider=self.name,
            usage=usage,
            finish_reason=str(getattr(raw, "stop_reason", "stop") or "stop"),
            cost_usd=cost,
        )

    async def stream(self, request: LLMRequest, *, model: str, api_key: str) -> AsyncIterator[str]:
        self.last_stream_usage = None
        client = self._client(api_key)
        try:
            async with client.messages.stream(
                model=model,
                system=_build_system_blocks(request.system, cache=request.cache_system),  # type: ignore[arg-type]
                messages=_to_anthropic_messages(request.messages),  # type: ignore[arg-type]
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            ) as stream:
                async for delta in stream.text_stream:
                    yield delta
                final = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise _map_anthropic_error(exc) from exc

        self.last_stream_usage = _extract_usage(getattr(final, "usage", None) or final)


__all__ = ["ClaudeProvider"]
