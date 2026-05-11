"""Tenant-scoped BYOK / LLM-config HTTP endpoints.

Routes (per docs/09 §5):

- ``GET  /api/v1/tenant/llm-config``       — booleans only, never the keys.
- ``PUT  /api/v1/tenant/llm-config``       — accept plaintext keys, encrypt
  server-side via :mod:`src.llm.key_vault`, write one ``audit_log`` event.
- ``POST /api/v1/tenant/llm-config/test``  — minimum-cost Claude Sonnet
  ping (5 max_tokens) using the stored BYOK key. 200 + ``valid=true`` on
  success, 401 + ``valid=false, reason="invalid_key"`` on bad key.

Auth: bearer JWT (Supabase) on every route; tenant resolved from
``public.users.tenant_id`` of the caller. The DB connection is the
service-role pool from ``get_db`` — RLS on ``tenant_llm_config`` is
deny-by-default for ``authenticated`` (see migration 010), so app-level
tenant scoping is the enforcement boundary on this table.

**Plaintext keys never leave this module.** They are read off the request
body via ``SecretStr`` (so Pydantic refuses to repr/log them), passed once
to ``key_vault.store_byok_key`` (which inlines them into a parameterised
SQL statement), and the local variable goes out of scope at the end of
the handler. The test endpoint decrypts in-memory, hands the plaintext
to :class:`ClaudeProvider` for one call, and then drops the reference.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from src.core.audit import write_audit_event
from src.core.auth import CurrentUserId
from src.core.config import SettingsDep
from src.core.db import get_db
from src.core.rate_limit import (
    LLM_CONFIG_TEST,
    RateLimitDecision,
    attach_rate_limit_headers,
    rate_limit,
)
from src.llm import key_vault
from src.llm.base import (
    LLMMessage,
    LLMRequest,
    LLMRetryableError,
    LLMUnrecoverableError,
)
from src.llm.claude_provider import ClaudeProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tenant/llm-config", tags=["llm-config"])

_TEST_MODEL = "claude-sonnet-4-6"


# ── Response / request models ──────────────────────────────────────────


class LLMConfigStatus(BaseModel):
    """Booleans + non-secret config. Returned by GET and PUT.

    ``monthly_budget_usd`` / ``alert_threshold_usd`` are ``None`` when the
    tenant hasn't set a cap (the column is nullable). Decimals serialize as
    JSON strings under Pydantic v2 — preserves precision; FE parses.
    """

    model_config = ConfigDict(frozen=True)

    anthropic_configured: bool
    openai_configured: bool
    use_managed_keys: bool
    preferred_provider: Literal["claude", "openai", "auto"]
    monthly_budget_usd: Decimal | None = None
    alert_threshold_usd: Decimal | None = None


class LLMConfigUpdate(BaseModel):
    """PUT body. ``SecretStr`` makes Pydantic refuse to repr the key.

    Budget / alert columns mirror the BYOK clear pattern: an explicit
    ``clear_*`` flag NULLs the column, since ``None`` on the field itself
    means "leave alone". Budget = NULL → no cap; budget = 0 → block all
    spend (semantically distinct, both supported).
    """

    model_config = ConfigDict(extra="forbid")

    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    clear_anthropic: bool = False
    clear_openai: bool = False
    use_managed_keys: bool | None = None
    preferred_provider: Literal["claude", "openai", "auto"] | None = None
    monthly_budget_usd: Decimal | None = Field(default=None, ge=0)
    clear_monthly_budget: bool = False
    alert_threshold_usd: Decimal | None = Field(default=None, ge=0)
    clear_alert_threshold: bool = False

    @model_validator(mode="after")
    def _no_set_and_clear_at_once(self) -> LLMConfigUpdate:
        if self.anthropic_api_key is not None and self.clear_anthropic:
            raise ValueError(
                "anthropic_api_key and clear_anthropic are mutually exclusive"
            )
        if self.openai_api_key is not None and self.clear_openai:
            raise ValueError(
                "openai_api_key and clear_openai are mutually exclusive"
            )
        if self.monthly_budget_usd is not None and self.clear_monthly_budget:
            raise ValueError(
                "monthly_budget_usd and clear_monthly_budget are mutually exclusive"
            )
        if self.alert_threshold_usd is not None and self.clear_alert_threshold:
            raise ValueError(
                "alert_threshold_usd and clear_alert_threshold are mutually exclusive"
            )
        return self


class LLMConfigTestResult(BaseModel):
    """POST /test response. ``valid`` is the only field the UI gates on."""

    model_config = ConfigDict(frozen=True)

    valid: bool
    model: str | None = None
    reason: Literal["no_key_set", "invalid_key", "transient_error"] | None = None


# ── Helpers ────────────────────────────────────────────────────────────


async def _resolve_tenant_id(conn: asyncpg.Connection, *, user_id: UUID) -> UUID:
    """Look up the caller's tenant; mirror the SQL of ``auth.tenant_id()``."""

    tenant_id = await conn.fetchval(
        """
        select tenant_id from public.users
         where id = $1 and deleted_at is null
        """,
        user_id,
    )
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user has no active tenant",
        )
    return UUID(str(tenant_id))


async def _read_status(
    conn: asyncpg.Connection, *, tenant_id: UUID
) -> LLMConfigStatus:
    """Project ``tenant_llm_config`` to the public-safe status shape.

    ``column is not null`` is computed in SQL so the encrypted bytes never
    cross the asyncpg boundary, let alone reach a logger.
    """

    row = await conn.fetchrow(
        """
        select
          (anthropic_api_key_encrypted is not null) as anthropic_configured,
          (openai_api_key_encrypted    is not null) as openai_configured,
          coalesce(use_managed_keys, true)          as use_managed_keys,
          coalesce(preferred_provider, 'claude')    as preferred_provider,
          monthly_budget_usd,
          alert_threshold_usd
        from tenant_llm_config
        where tenant_id = $1
        """,
        tenant_id,
    )
    if row is None:
        return LLMConfigStatus(
            anthropic_configured=False,
            openai_configured=False,
            use_managed_keys=True,
            preferred_provider="claude",
        )
    return LLMConfigStatus(
        anthropic_configured=bool(row["anthropic_configured"]),
        openai_configured=bool(row["openai_configured"]),
        use_managed_keys=bool(row["use_managed_keys"]),
        preferred_provider=str(row["preferred_provider"]),  # type: ignore[arg-type]
        monthly_budget_usd=row["monthly_budget_usd"],
        alert_threshold_usd=row["alert_threshold_usd"],
    )


async def _ensure_row_exists(conn: asyncpg.Connection, *, tenant_id: UUID) -> None:
    """Idempotent insert so ``UPDATE … WHERE tenant_id = …`` always hits a row.

    ``key_vault.store_byok_key`` already does its own upsert. The other
    paths (clear, preferred_provider, use_managed_keys) need a row to
    exist. Calling this once at the top of PUT keeps the rest of the
    handler simple.
    """

    await conn.execute(
        """
        insert into tenant_llm_config (tenant_id) values ($1)
        on conflict (tenant_id) do nothing
        """,
        tenant_id,
    )


def _client_metadata(request: Request) -> tuple[str | None, str | None]:
    """Return ``(ip_address, user_agent)`` for the audit row."""

    client = request.client
    ip = client.host if client is not None else None
    ua = request.headers.get("user-agent")
    return ip, ua


# ── Routes ─────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=LLMConfigStatus,
    summary="Read tenant LLM config — booleans only, keys are never returned",
)
async def get_llm_config(
    user_id: CurrentUserId,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
) -> LLMConfigStatus:
    """Report which BYOK keys are set without ever returning the keys."""

    tenant_id = await _resolve_tenant_id(conn, user_id=user_id)
    status_row = await _read_status(conn, tenant_id=tenant_id)
    logger.info(
        "llm_config_read",
        extra={"tenant_id": str(tenant_id), "user_id": str(user_id)},
    )
    return status_row


@router.put(
    "",
    response_model=LLMConfigStatus,
    summary="Update tenant LLM config — encrypts keys server-side, audits the change",
)
async def update_llm_config(
    request: Request,
    user_id: CurrentUserId,
    settings: SettingsDep,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    body: LLMConfigUpdate,
) -> LLMConfigStatus:
    """Apply the requested changes in a single transaction, then audit.

    Returns 503 if ``LLM_MASTER_ENCRYPTION_KEY`` is unset and the body asks
    to store a key (we cannot encrypt without it).
    """

    tenant_id = await _resolve_tenant_id(conn, user_id=user_id)

    needs_master = body.anthropic_api_key is not None or body.openai_api_key is not None
    master_secret = settings.llm_master_encryption_key
    if needs_master and master_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM_MASTER_ENCRYPTION_KEY not configured on server",
        )

    diff: dict[str, str | bool] = {}

    async with conn.transaction():
        await _ensure_row_exists(conn, tenant_id=tenant_id)

        if body.clear_anthropic:
            await key_vault.clear_byok_key(conn, tenant_id=tenant_id, kind="anthropic")
            diff["anthropic"] = "cleared"
        elif body.anthropic_api_key is not None:
            assert master_secret is not None  # guarded above
            await key_vault.store_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="anthropic",
                plaintext_key=body.anthropic_api_key.get_secret_value(),
                master_key=master_secret.get_secret_value(),
            )
            diff["anthropic"] = "set"

        if body.clear_openai:
            await key_vault.clear_byok_key(conn, tenant_id=tenant_id, kind="openai")
            diff["openai"] = "cleared"
        elif body.openai_api_key is not None:
            assert master_secret is not None  # guarded above
            await key_vault.store_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="openai",
                plaintext_key=body.openai_api_key.get_secret_value(),
                master_key=master_secret.get_secret_value(),
            )
            diff["openai"] = "set"

        if body.use_managed_keys is not None:
            await conn.execute(
                """
                update tenant_llm_config
                   set use_managed_keys = $1, updated_at = now()
                 where tenant_id = $2
                """,
                body.use_managed_keys,
                tenant_id,
            )
            diff["use_managed_keys"] = body.use_managed_keys

        if body.preferred_provider is not None:
            await conn.execute(
                """
                update tenant_llm_config
                   set preferred_provider = $1, updated_at = now()
                 where tenant_id = $2
                """,
                body.preferred_provider,
                tenant_id,
            )
            diff["preferred_provider"] = body.preferred_provider

        if body.clear_monthly_budget:
            await conn.execute(
                """
                update tenant_llm_config
                   set monthly_budget_usd = null, updated_at = now()
                 where tenant_id = $1
                """,
                tenant_id,
            )
            diff["monthly_budget"] = "cleared"
        elif body.monthly_budget_usd is not None:
            await conn.execute(
                """
                update tenant_llm_config
                   set monthly_budget_usd = $1, updated_at = now()
                 where tenant_id = $2
                """,
                body.monthly_budget_usd,
                tenant_id,
            )
            # Diff carries the new dollar amount; the audit guard accepts
            # short numeric strings (well under the 36-char limit).
            diff["monthly_budget_usd"] = str(body.monthly_budget_usd)

        if body.clear_alert_threshold:
            await conn.execute(
                """
                update tenant_llm_config
                   set alert_threshold_usd = null, updated_at = now()
                 where tenant_id = $1
                """,
                tenant_id,
            )
            diff["alert_threshold"] = "cleared"
        elif body.alert_threshold_usd is not None:
            await conn.execute(
                """
                update tenant_llm_config
                   set alert_threshold_usd = $1, updated_at = now()
                 where tenant_id = $2
                """,
                body.alert_threshold_usd,
                tenant_id,
            )
            diff["alert_threshold_usd"] = str(body.alert_threshold_usd)

        if diff:
            ip_address, user_agent = _client_metadata(request)
            await write_audit_event(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action="tenant.llm_config_updated",
                resource_type="tenant_llm_config",
                resource_id=tenant_id,
                diff=diff,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    new_status = await _read_status(conn, tenant_id=tenant_id)
    logger.info(
        "llm_config_updated",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "fields_changed": sorted(diff.keys()),
        },
    )
    return new_status


@router.post(
    "/test",
    response_model=LLMConfigTestResult,
    summary="Verify the stored Anthropic BYOK key with a 5-token Claude Sonnet ping",
)
async def test_llm_config(
    response: Response,
    user_id: CurrentUserId,
    settings: SettingsDep,
    conn: Annotated[asyncpg.Connection, Depends(get_db)],
    rate_check: Annotated[
        RateLimitDecision, Depends(rate_limit(LLM_CONFIG_TEST))
    ],
) -> LLMConfigTestResult:
    """Run one minimal Claude Sonnet call with the tenant's stored BYOK key.

    No ``tenant_usage_log`` row is written — this is a connectivity probe,
    not a billable call. The decrypted key is held only on the local stack
    for the duration of the provider call.

    Rate-limited per docs/09 §8 — tighter than the saga endpoints because
    each call makes a direct provider round-trip with no caching.

    Status codes:
    - 200: key reachable. Body's ``valid`` field tells the UI the verdict.
      ``valid=False, reason="no_key_set"`` means the tenant hasn't stored
      one yet (still 200 — informational).
    - 401: stored key was rejected by Anthropic (bad / revoked).
    - 429: rate limit exceeded (5 / 60s); ``Retry-After`` header present.
    - 502: transient upstream error (rate limit, timeout, 5xx).
    - 503: server has no master encryption key configured.
    """

    attach_rate_limit_headers(response, rate_check)

    tenant_id = await _resolve_tenant_id(conn, user_id=user_id)

    master_secret = settings.llm_master_encryption_key
    if master_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM_MASTER_ENCRYPTION_KEY not configured on server",
        )

    byok_key = await key_vault.get_byok_key(
        conn,
        tenant_id=tenant_id,
        kind="anthropic",
        master_key=master_secret.get_secret_value(),
    )
    if byok_key is None:
        logger.info(
            "llm_config_test_skipped",
            extra={
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "reason": "no_key_set",
            },
        )
        return LLMConfigTestResult(valid=False, reason="no_key_set")

    provider = ClaudeProvider()
    probe = LLMRequest(
        task="rerank",
        tenant_id=tenant_id,
        user_id=user_id,
        system="ping",
        messages=[LLMMessage(role="user", content="hi")],
        cache_system=False,
        temperature=0.0,
        max_tokens=5,
    )

    try:
        await provider.complete(probe, model=_TEST_MODEL, api_key=byok_key)
    except LLMUnrecoverableError:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        logger.info(
            "llm_config_test_invalid",
            extra={"tenant_id": str(tenant_id), "user_id": str(user_id)},
        )
        return LLMConfigTestResult(valid=False, reason="invalid_key")
    except LLMRetryableError:
        response.status_code = status.HTTP_502_BAD_GATEWAY
        logger.warning(
            "llm_config_test_transient",
            extra={"tenant_id": str(tenant_id), "user_id": str(user_id)},
        )
        return LLMConfigTestResult(valid=False, reason="transient_error")

    logger.info(
        "llm_config_test_ok",
        extra={
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "model": _TEST_MODEL,
        },
    )
    return LLMConfigTestResult(valid=True, model=_TEST_MODEL)


__all__ = [
    "LLMConfigStatus",
    "LLMConfigTestResult",
    "LLMConfigUpdate",
    "router",
]
