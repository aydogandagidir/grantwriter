"""BYOK (Bring Your Own Key) security tests.

Three groups, each with a different concern:

A. **Round-trip smoke** — store + read a key via :mod:`src.llm.key_vault`.
   The deeper round-trip + wrong-master-key + router-uses-BYOK tests live
   in ``tests/llm/test_db_integration.py``; this file adds one redundant
   smoke test so the security suite is self-contained.

B. **HTTP endpoint behaviour** — GET / PUT / POST /test exercised via
   httpx ASGITransport. The routes use real Postgres for encryption (the
   whole point of the feature) but stub :class:`ClaudeProvider` so no
   network call leaves the box.

C. **No-leak invariant** — the most important assertion in the file: the
   plaintext canary key MUST NOT appear in any captured log record after
   any sequence of BYOK operations.

All three groups skip when ``TEST_DATABASE_URL`` is unset (the security
suite's existing convention — local fast lane has no Postgres).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.auth import get_current_user_id
from src.core.config import get_settings
from src.core.db import get_db
from src.llm import key_vault
from src.llm.base import (
    LLMRequest,
    LLMResponse,
    LLMRetryableError,
    LLMUnrecoverableError,
    LLMUsage,
)
from src.llm.claude_provider import ClaudeProvider
from src.main import create_app

# 32-byte master key for the test fixture. Plaintext on purpose — no
# real Anthropic key is involved anywhere in this file.
_MASTER_KEY = "test-master-key-32-bytes-padding!"

# The canary key. The Group C invariant scans every log record for this
# substring; if any handler logs it, the test catches the regression.
_CANARY_KEY = "sk-ant-CANARY-do-not-leak-7b3e1c-aaaaaa"


# ── Setup fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def _master_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set ``LLM_MASTER_ENCRYPTION_KEY`` for the duration of the test.

    The autouse ``_reset_settings_cache`` fixture (in ``tests/conftest.py``)
    invalidates the cached :class:`Settings` instance on either side of
    every test, so the next ``get_settings()`` call picks up this env.
    """

    monkeypatch.setenv("LLM_MASTER_ENCRYPTION_KEY", _MASTER_KEY)
    get_settings.cache_clear()
    return _MASTER_KEY


@pytest.fixture
async def _byok_tenant(
    pool: asyncpg.Pool,
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """Create one tenant + one user; clean up everything we touched.

    This fixture intentionally does NOT reuse ``fixture_data`` from the
    security conftest because the BYOK suite needs ``audit_log`` and
    ``tenant_llm_config`` cleanup that ``fixture_data`` doesn't do. The
    foreign keys would block the tenants delete on teardown otherwise.
    """

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            "BYOK Test",
            f"byok-{tenant_id}",
        )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"byok-{user_id}@test",
        )
        await conn.execute(
            """
            insert into public.users (id, tenant_id, role)
            values ($1, $2, 'owner')
            """,
            user_id,
            tenant_id,
        )

    try:
        yield tenant_id, user_id
    finally:
        async with pool.acquire() as conn:
            # Order matters — every FK reference to tenant_id must clear
            # before the tenants row itself can be deleted.
            await conn.execute(
                "delete from audit_log where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from tenant_llm_config where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from tenant_usage_log where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from public.users where id = $1", user_id
            )
            await conn.execute(
                "delete from auth.users where id = $1", user_id
            )
            await conn.execute("delete from tenants where id = $1", tenant_id)


def _build_app(
    *,
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
) -> FastAPI:
    """Build the FastAPI app with auth + DB overridden onto the test pool."""

    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = _fake_db
    return app


@pytest.fixture
async def byok_client(
    pool: asyncpg.Pool,
    _master_env: str,
    _byok_tenant: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[dict[str, Any]]:
    """Wired-up httpx client + tenant/user ids for endpoint tests."""

    tenant_id, user_id = _byok_tenant
    app = _build_app(pool=pool, user_id=user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield {
            "client": ac,
            "app": app,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "pool": pool,
        }


def _make_success_response() -> LLMResponse:
    return LLMResponse(
        text="hi",
        model="claude-sonnet-4-6",
        provider="claude",
        usage=LLMUsage(input_tokens=5, output_tokens=2),
        cost_usd=0.0,
    )


# ── Group A: encrypt → decrypt round-trip ──────────────────────────────


async def test_encrypt_decrypt_round_trip(
    pool: asyncpg.Pool,
    _byok_tenant: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """Smoke: pgcrypto + key_vault recover the exact plaintext."""

    tenant_id, _ = _byok_tenant

    async with pool.acquire() as conn:
        await key_vault.store_byok_key(
            conn,
            tenant_id=tenant_id,
            kind="anthropic",
            plaintext_key=_CANARY_KEY,
            master_key=_MASTER_KEY,
        )
        recovered = await key_vault.get_byok_key(
            conn,
            tenant_id=tenant_id,
            kind="anthropic",
            master_key=_MASTER_KEY,
        )

    assert recovered == _CANARY_KEY


# ── Group B: HTTP endpoints ────────────────────────────────────────────


async def test_get_returns_only_booleans_when_unset(
    byok_client: dict[str, Any],
) -> None:
    response = await byok_client["client"].get("/api/v1/tenant/llm-config")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "anthropic_configured": False,
        "openai_configured": False,
        "use_managed_keys": True,
        "preferred_provider": "claude",
        "monthly_budget_usd": None,
        "alert_threshold_usd": None,
    }


async def test_put_sets_monthly_budget_and_alert(
    byok_client: dict[str, Any],
) -> None:
    client = byok_client["client"]
    response = await client.put(
        "/api/v1/tenant/llm-config",
        json={"monthly_budget_usd": "50.00", "alert_threshold_usd": "40.00"},
    )
    assert response.status_code == 200
    body = response.json()
    # Pydantic v2 serialises Decimal as JSON string; both values round-trip.
    assert body["monthly_budget_usd"] == "50.00"
    assert body["alert_threshold_usd"] == "40.00"


async def test_put_clear_monthly_budget_nulls_the_column(
    byok_client: dict[str, Any],
) -> None:
    client = byok_client["client"]
    await client.put(
        "/api/v1/tenant/llm-config", json={"monthly_budget_usd": "100"}
    )
    response = await client.put(
        "/api/v1/tenant/llm-config", json={"clear_monthly_budget": True}
    )
    assert response.status_code == 200
    assert response.json()["monthly_budget_usd"] is None


async def test_put_stores_anthropic_key_and_does_not_return_it(
    byok_client: dict[str, Any],
) -> None:
    client = byok_client["client"]
    pool: asyncpg.Pool = byok_client["pool"]
    tenant_id: uuid.UUID = byok_client["tenant_id"]

    response = await client.put(
        "/api/v1/tenant/llm-config",
        json={"anthropic_api_key": _CANARY_KEY},
    )
    assert response.status_code == 200
    body = response.json()

    # Response surface advertises that a key is set, never the key itself.
    assert body["anthropic_configured"] is True
    assert _CANARY_KEY not in response.text

    # The stored bytes are the encrypted ciphertext, not the plaintext.
    async with pool.acquire() as conn:
        ciphertext = await conn.fetchval(
            "select anthropic_api_key_encrypted from tenant_llm_config "
            "where tenant_id = $1",
            tenant_id,
        )
    assert ciphertext is not None
    assert _CANARY_KEY.encode() not in bytes(ciphertext)

    # The decrypted key still round-trips back to the canary.
    async with pool.acquire() as conn:
        recovered = await key_vault.get_byok_key(
            conn, tenant_id=tenant_id, kind="anthropic", master_key=_MASTER_KEY
        )
    assert recovered == _CANARY_KEY


async def test_put_writes_audit_log_without_key_value(
    byok_client: dict[str, Any],
) -> None:
    client = byok_client["client"]
    pool: asyncpg.Pool = byok_client["pool"]
    tenant_id: uuid.UUID = byok_client["tenant_id"]

    await client.put(
        "/api/v1/tenant/llm-config",
        json={"anthropic_api_key": _CANARY_KEY, "use_managed_keys": False},
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select action, resource_type, diff::text as diff_text
              from audit_log where tenant_id = $1
              order by created_at desc
            """,
            tenant_id,
        )
    assert len(rows) == 1
    assert rows[0]["action"] == "tenant.llm_config_updated"
    assert rows[0]["resource_type"] == "tenant_llm_config"
    diff_text = rows[0]["diff_text"]
    assert "set" in diff_text
    # The ABSOLUTE invariant: no part of the plaintext key is in the diff.
    assert _CANARY_KEY not in diff_text
    assert "sk-ant" not in diff_text


async def test_put_clear_anthropic_drops_the_stored_key(
    byok_client: dict[str, Any],
) -> None:
    client = byok_client["client"]
    pool: asyncpg.Pool = byok_client["pool"]
    tenant_id: uuid.UUID = byok_client["tenant_id"]

    await client.put(
        "/api/v1/tenant/llm-config",
        json={"anthropic_api_key": _CANARY_KEY},
    )
    response = await client.put(
        "/api/v1/tenant/llm-config",
        json={"clear_anthropic": True},
    )
    assert response.status_code == 200
    assert response.json()["anthropic_configured"] is False

    async with pool.acquire() as conn:
        ciphertext = await conn.fetchval(
            "select anthropic_api_key_encrypted from tenant_llm_config "
            "where tenant_id = $1",
            tenant_id,
        )
    assert ciphertext is None


async def test_put_rejects_set_and_clear_in_same_request(
    byok_client: dict[str, Any],
) -> None:
    response = await byok_client["client"].put(
        "/api/v1/tenant/llm-config",
        json={"anthropic_api_key": _CANARY_KEY, "clear_anthropic": True},
    )
    assert response.status_code == 422


async def test_test_endpoint_returns_no_key_set_when_unset(
    byok_client: dict[str, Any],
) -> None:
    response = await byok_client["client"].post(
        "/api/v1/tenant/llm-config/test"
    )
    assert response.status_code == 200
    assert response.json() == {
        "valid": False,
        "model": None,
        "reason": "no_key_set",
    }


async def test_test_endpoint_returns_valid_for_a_good_key(
    byok_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_keys: list[str] = []

    async def fake_complete(
        self: ClaudeProvider,
        request: LLMRequest,
        *,
        model: str,
        api_key: str,
    ) -> LLMResponse:
        captured_keys.append(api_key)
        assert model == "claude-sonnet-4-6"
        assert request.max_tokens == 5
        return _make_success_response()

    monkeypatch.setattr(ClaudeProvider, "complete", fake_complete)

    client = byok_client["client"]
    await client.put(
        "/api/v1/tenant/llm-config", json={"anthropic_api_key": _CANARY_KEY}
    )
    response = await client.post("/api/v1/tenant/llm-config/test")

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "model": "claude-sonnet-4-6",
        "reason": None,
    }
    # Confirm the provider received the decrypted plaintext (not the ciphertext)
    # exactly once — but never logged it.
    assert captured_keys == [_CANARY_KEY]


async def test_test_endpoint_returns_401_for_an_invalid_key(
    byok_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete(
        self: ClaudeProvider,
        request: LLMRequest,
        *,
        model: str,
        api_key: str,
    ) -> LLMResponse:
        raise LLMUnrecoverableError("anthropic 401: invalid api key")

    monkeypatch.setattr(ClaudeProvider, "complete", fake_complete)

    client = byok_client["client"]
    await client.put(
        "/api/v1/tenant/llm-config", json={"anthropic_api_key": _CANARY_KEY}
    )
    response = await client.post("/api/v1/tenant/llm-config/test")

    assert response.status_code == 401
    assert response.json() == {
        "valid": False,
        "model": None,
        "reason": "invalid_key",
    }


async def test_test_endpoint_returns_502_for_a_transient_failure(
    byok_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete(
        self: ClaudeProvider,
        request: LLMRequest,
        *,
        model: str,
        api_key: str,
    ) -> LLMResponse:
        raise LLMRetryableError("rate limit")

    monkeypatch.setattr(ClaudeProvider, "complete", fake_complete)

    client = byok_client["client"]
    await client.put(
        "/api/v1/tenant/llm-config", json={"anthropic_api_key": _CANARY_KEY}
    )
    response = await client.post("/api/v1/tenant/llm-config/test")

    assert response.status_code == 502
    assert response.json() == {
        "valid": False,
        "model": None,
        "reason": "transient_error",
    }


# ── Group C: the no-leak invariant ─────────────────────────────────────


def _record_strings(record: logging.LogRecord) -> list[str]:
    """All free-text surfaces of a log record that a leak could land in."""

    parts: list[str] = [record.getMessage()]
    for arg in record.args or ():
        parts.append(str(arg))
    # Structured logging often goes through `extra=` and ends up as record
    # attributes — sweep every attribute value defensively. Skip the fixed
    # LogRecord internals so we don't false-positive on the message twice.
    builtins = {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "taskName", "asctime",
    }
    for attr_name, attr_value in record.__dict__.items():
        if attr_name in builtins or attr_name.startswith("_"):
            continue
        parts.append(str(attr_value))
    return parts


async def test_canary_key_never_appears_in_any_log_record(
    byok_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end leak check across PUT, GET, /test, and clear paths."""

    async def fake_complete(
        self: ClaudeProvider,
        request: LLMRequest,
        *,
        model: str,
        api_key: str,
    ) -> LLMResponse:
        return _make_success_response()

    monkeypatch.setattr(ClaudeProvider, "complete", fake_complete)

    caplog.set_level(logging.DEBUG)
    client = byok_client["client"]

    # A sequence that touches the secret on every code path:
    #   1. PUT with key — encrypts + audits + logs "llm_config_updated"
    #   2. GET — reads booleans, logs "llm_config_read"
    #   3. POST /test — decrypts + provider call, logs "llm_config_test_ok"
    #   4. PUT with clear — clears + audits + logs "llm_config_updated"
    await client.put(
        "/api/v1/tenant/llm-config", json={"anthropic_api_key": _CANARY_KEY}
    )
    await client.get("/api/v1/tenant/llm-config")
    await client.post("/api/v1/tenant/llm-config/test")
    await client.put(
        "/api/v1/tenant/llm-config", json={"clear_anthropic": True}
    )

    # The records list is the source of truth: every logger call after
    # caplog.set_level lands here. If a future regression logs the key in
    # a message, an arg, or an extra dict — this assertion catches it.
    leaks: list[tuple[str, str]] = []
    for record in caplog.records:
        for chunk in _record_strings(record):
            if _CANARY_KEY in chunk:
                leaks.append((record.name, chunk))

    assert not leaks, (
        f"Canary key leaked into {len(leaks)} log record(s): "
        f"{[name for name, _ in leaks]}"
    )
