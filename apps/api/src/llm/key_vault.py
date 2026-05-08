"""Symmetric encryption of tenant BYOK API keys via Postgres pgcrypto.

Plaintext keys are NEVER returned in logs, error messages, or API responses
visible to the frontend. The frontend only sees a boolean
``configured/not-configured`` flag (returned by a future endpoint, not here).

The master key lives in ``settings.llm_master_encryption_key`` (Railway env
var); rotation procedure is documented in docs/09-security-compliance.md §5.

Public surface:
- :func:`store_byok_key`     — encrypt + persist a plaintext key
- :func:`get_byok_key`       — decrypt + return the plaintext key (in-memory only)
- :func:`clear_byok_key`     — null out the encrypted column
- :func:`prefer_managed_keys` — read tenant_llm_config.use_managed_keys
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

KeyKind = Literal["anthropic", "openai"]

_COLUMN_FOR: dict[KeyKind, str] = {
    "anthropic": "anthropic_api_key_encrypted",
    "openai": "openai_api_key_encrypted",
}


def _column(kind: KeyKind) -> str:
    """Return the encrypted-column name for a given provider.

    The keys come from a closed ``Literal`` so this never sees user input,
    but we still go through this function so SQL strings stay grep-friendly.
    """

    return _COLUMN_FOR[kind]


async def store_byok_key(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    kind: KeyKind,
    plaintext_key: str,
    master_key: str,
) -> None:
    """Encrypt and upsert a tenant's BYOK API key.

    Uses ``pgp_sym_encrypt`` so plaintext is never stored or returned.
    """

    if not plaintext_key:
        raise ValueError("plaintext_key is empty")
    if not master_key:
        raise ValueError("master_key is empty")

    col = _column(kind)
    # Parameterized SQL: $1, $2 bound by asyncpg — no interpolation of secrets.
    query = f"""
        insert into tenant_llm_config (tenant_id, {col}, updated_at)
        values ($1, pgp_sym_encrypt($2, $3), now())
        on conflict (tenant_id) do update
          set {col} = pgp_sym_encrypt($2, $3),
              updated_at = now()
    """
    await conn.execute(query, tenant_id, plaintext_key, master_key)
    logger.info(
        "byok_key_stored",
        extra={"tenant_id": str(tenant_id), "kind": kind},
    )


async def get_byok_key(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    kind: KeyKind,
    master_key: str,
) -> str | None:
    """Decrypt and return the tenant's BYOK key, or ``None`` if not set.

    The returned string MUST NOT be logged. Callers should pass it directly
    to the provider client and let it go out of scope immediately.
    """

    if not master_key:
        raise ValueError("master_key is empty")

    col = _column(kind)
    query = f"""
        select pgp_sym_decrypt({col}, $2) as key
          from tenant_llm_config
         where tenant_id = $1 and {col} is not null
    """
    row = await conn.fetchrow(query, tenant_id, master_key)
    if row is None:
        return None
    return str(row["key"])


async def clear_byok_key(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    kind: KeyKind,
) -> None:
    """Null out the encrypted column. Used on key rotation / removal."""

    col = _column(kind)
    query = f"update tenant_llm_config set {col} = null, updated_at = now() where tenant_id = $1"
    await conn.execute(query, tenant_id)
    logger.info(
        "byok_key_cleared",
        extra={"tenant_id": str(tenant_id), "kind": kind},
    )


async def prefer_managed_keys(conn: asyncpg.Connection, *, tenant_id: UUID) -> bool:
    """True if the tenant has opted into Bluedev-managed keys (the default).

    A row may not exist yet — treat that as "use managed" so plan upgrades
    don't have to pre-seed the row.
    """

    row = await conn.fetchrow(
        "select use_managed_keys from tenant_llm_config where tenant_id = $1",
        tenant_id,
    )
    if row is None:
        return True
    return bool(row["use_managed_keys"])


__all__ = [
    "KeyKind",
    "clear_byok_key",
    "get_byok_key",
    "prefer_managed_keys",
    "store_byok_key",
]
