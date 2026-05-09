"""OpenAI provider.

The only place in the codebase that imports the ``openai`` SDK directly.
OpenAI prompt caching is opaque (server-side, automatic for prompts >1024
tokens) — we surface ``cached_tokens`` from the response when available
but ignore the request's ``cache_system`` flag.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import openai

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
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)
_UNRECOVERABLE_TYPES: tuple[type[BaseException], ...] = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.BadRequestError,
    openai.NotFoundError,
)


def _map_openai_error(exc: BaseException) -> LLMRetryableError | LLMUnrecoverableError:
    if isinstance(exc, _RETRYABLE_TYPES):
        return LLMRetryableError(str(exc))
    if isinstance(exc, _UNRECOVERABLE_TYPES):
        return LLMUnrecoverableError(str(exc))
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", _HTTP_SERVER_ERROR_FLOOR) or _HTTP_SERVER_ERROR_FLOOR
        if _HTTP_SERVER_ERROR_FLOOR <= status < _HTTP_SERVER_ERROR_CEIL:
            return LLMRetryableError(str(exc))
        return LLMUnrecoverableError(str(exc))
    return LLMUnrecoverableError(str(exc))


def _to_openai_messages(system: str, messages: list[LLMMessage]) -> list[dict[str, str]]:
    """OpenAI puts the system prompt in the messages array (role=system)."""

    chat: list[dict[str, str]] = [{"role": "system", "content": system}]
    chat.extend({"role": m.role, "content": m.content} for m in messages)
    return chat


def _extract_usage(raw: Any) -> LLMUsage:
    if raw is None:
        return LLMUsage()
    cached = 0
    details = getattr(raw, "prompt_tokens_details", None)
    if details is not None:
        cached = int(getattr(details, "cached_tokens", 0) or 0)
    prompt_tokens = int(getattr(raw, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(raw, "completion_tokens", 0) or 0)
    # OpenAI's `prompt_tokens` includes cached tokens; subtract so the split
    # (input vs cached) matches Claude's accounting.
    return LLMUsage(
        input_tokens=max(0, prompt_tokens - cached),
        output_tokens=completion_tokens,
        cached_tokens=cached,
    )


class OpenAIProvider(LLMProvider):
    name: ProviderName = "openai"

    def __init__(self, *, default_timeout: float = 60.0) -> None:
        self._default_timeout = default_timeout
        self.last_stream_usage: LLMUsage | None = None

    def _client(self, api_key: str) -> openai.AsyncOpenAI:
        return openai.AsyncOpenAI(api_key=api_key, timeout=self._default_timeout)

    async def complete(self, request: LLMRequest, *, model: str, api_key: str) -> LLMResponse:
        client = self._client(api_key)
        try:
            # SDK expects ChatCompletion*MessageParam TypedDicts.
            raw = await client.chat.completions.create(
                model=model,
                messages=_to_openai_messages(request.system, request.messages),  # type: ignore[arg-type]
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        except openai.OpenAIError as exc:
            raise _map_openai_error(exc) from exc

        choice = raw.choices[0]
        usage = _extract_usage(getattr(raw, "usage", None))
        text = choice.message.content or ""
        cost = calculate_cost(model, usage)
        finish = choice.finish_reason if choice.finish_reason else "stop"
        return LLMResponse(
            text=text,
            model=model,
            provider=self.name,
            usage=usage,
            finish_reason=str(finish),
            cost_usd=cost,
        )

    async def stream(self, request: LLMRequest, *, model: str, api_key: str) -> AsyncIterator[str]:
        self.last_stream_usage = None
        client = self._client(api_key)
        try:
            stream = await client.chat.completions.create(  # type: ignore[call-overload]
                model=model,
                messages=_to_openai_messages(request.system, request.messages),
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield delta
                if getattr(chunk, "usage", None) is not None:
                    self.last_stream_usage = _extract_usage(chunk.usage)
        except openai.OpenAIError as exc:
            raise _map_openai_error(exc) from exc


__all__ = ["OpenAIProvider"]
