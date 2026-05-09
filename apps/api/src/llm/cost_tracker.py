"""Per-call usage logging.

Every successful LLM round-trip writes one row to ``tenant_usage_log``
(schema: docs/03 §2.5). Stripe metered billing reads from this table; the
row is the source of truth for per-tenant cost. Failures are logged but
never written — partial / estimated charges create reconciliation pain.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from src.llm.base import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


async def log_call(
    conn: asyncpg.Connection,
    *,
    request: LLMRequest,
    response: LLMResponse,
    extra_metadata: dict[str, Any] | None = None,
) -> UUID:
    """Insert a row in ``tenant_usage_log`` for a completed LLM call.

    Returns the new row's id. Errors propagate — the router decides whether
    a logging failure should be fatal (currently: log + swallow).
    """

    metadata: dict[str, Any] = {
        "task": request.task,
        "provider": response.provider,
        "model": response.model,
        "finish_reason": response.finish_reason,
        "cache_creation_tokens": response.usage.cache_creation_tokens,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    new_id = await conn.fetchval(
        """
        insert into tenant_usage_log (
          tenant_id, user_id, proposal_id,
          event_type, resource,
          input_tokens, output_tokens, cached_tokens,
          cost_usd, used_byok, metadata
        ) values (
          $1, $2, $3,
          'llm_call', $4,
          $5, $6, $7,
          $8, $9, $10::jsonb
        ) returning id
        """,
        request.tenant_id,
        request.user_id,
        request.proposal_id,
        response.model,
        response.usage.input_tokens,
        response.usage.output_tokens,
        response.usage.cached_tokens,
        response.cost_usd,
        response.used_byok,
        json.dumps(metadata),
    )
    logger.info(
        "llm_call_logged",
        extra={
            "tenant_id": str(request.tenant_id),
            "task": request.task,
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cached_tokens": response.usage.cached_tokens,
            "cost_usd": response.cost_usd,
            "used_byok": response.used_byok,
        },
    )
    return UUID(str(new_id))


__all__ = ["log_call"]
