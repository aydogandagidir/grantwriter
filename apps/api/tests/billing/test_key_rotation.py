"""Tests for the master-key rotation library.

Covers the security-critical invariants of the runbook in
``infra/supabase/migrations/20260510120000_byok_hardening.sql``:

- After rotation, the new master decrypts back to the original
  plaintext (round-trip preserved).
- The OLD master can no longer decrypt — a stolen old key is now
  useless against the rotated ciphertext.
- Both anthropic and openai columns rotate in lockstep; NULL columns
  stay NULL.
- A tenant with no BYOK keys is skipped (counted in
  ``tenants_skipped``), no audit row.
- Each successful rotation writes one ``tenant.master_key_rotated``
  audit row with no key material in the diff.
- Same old/new key raises ValueError before touching the DB.
- Wrong old key fails the per-tenant transaction without aborting
  the run; the report carries the error.
- Dry-run reports the would-rotate count without mutating anything.

Skips when ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from src.billing.key_rotation import RotationReport, rotate_all
from src.llm import key_vault


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


_OLD_MASTER = "old-master-key-32-bytes-padding!"
_NEW_MASTER = "new-master-key-32-bytes-padding!"


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping rotation tests")
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


async def _make_tenant(
    pool: asyncpg.Pool,
    *,
    anthropic: str | None = None,
    openai: str | None = None,
) -> uuid.UUID:
    """Create a tenant + (optionally) seed BYOK keys with the OLD master."""

    tenant_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            "Rotation Test",
            f"rot-{tenant_id}",
        )
        if anthropic is not None:
            await key_vault.store_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="anthropic",
                plaintext_key=anthropic,
                master_key=_OLD_MASTER,
            )
        if openai is not None:
            await key_vault.store_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="openai",
                plaintext_key=openai,
                master_key=_OLD_MASTER,
            )
    return tenant_id


async def _cleanup(pool: asyncpg.Pool, tenant_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute("delete from audit_log where tenant_id = $1", tenant_id)
        await conn.execute(
            "delete from tenant_llm_config where tenant_id = $1", tenant_id
        )
        await conn.execute("delete from tenants where id = $1", tenant_id)


# ── Pre-flight argument validation ─────────────────────────────────────


async def test_rotate_all_rejects_identical_keys(pool: asyncpg.Pool) -> None:
    with pytest.raises(ValueError, match="must differ"):
        await rotate_all(pool, old_master="same", new_master="same")


async def test_rotate_all_rejects_empty_keys(pool: asyncpg.Pool) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        await rotate_all(pool, old_master="", new_master=_NEW_MASTER)


# ── Happy path round-trip ──────────────────────────────────────────────


async def test_round_trip_preserves_plaintext_through_rotation(
    pool: asyncpg.Pool,
) -> None:
    plaintext = "sk-ant-original-byok-after-rotation-must-still-decrypt"
    tenant_id = await _make_tenant(pool, anthropic=plaintext)

    try:
        report = await rotate_all(
            pool,
            old_master=_OLD_MASTER,
            new_master=_NEW_MASTER,
            only_tenant_id=tenant_id,
        )

        assert report.tenants_processed == 1
        assert report.tenants_rotated == 1
        assert report.errors == []

        async with pool.acquire() as conn:
            recovered = await key_vault.get_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="anthropic",
                master_key=_NEW_MASTER,
            )
        assert recovered == plaintext
    finally:
        await _cleanup(pool, tenant_id)


async def test_old_master_fails_after_rotation(pool: asyncpg.Pool) -> None:
    """A stolen OLD master is now useless against the rotated ciphertext."""

    tenant_id = await _make_tenant(pool, anthropic="sk-ant-victim-key")
    try:
        await rotate_all(
            pool,
            old_master=_OLD_MASTER,
            new_master=_NEW_MASTER,
            only_tenant_id=tenant_id,
        )

        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.PostgresError):
                # Trying the old master against the rotated ciphertext
                # raises (pgcrypto reports a decrypt failure).
                await key_vault.get_byok_key(
                    conn,
                    tenant_id=tenant_id,
                    kind="anthropic",
                    master_key=_OLD_MASTER,
                )
    finally:
        await _cleanup(pool, tenant_id)


async def test_both_columns_rotate_in_lockstep(pool: asyncpg.Pool) -> None:
    anth = "sk-ant-rotated-anth"
    oai = "sk-proj-rotated-oai"
    tenant_id = await _make_tenant(pool, anthropic=anth, openai=oai)
    try:
        await rotate_all(
            pool,
            old_master=_OLD_MASTER,
            new_master=_NEW_MASTER,
            only_tenant_id=tenant_id,
        )
        async with pool.acquire() as conn:
            recovered_anth = await key_vault.get_byok_key(
                conn, tenant_id=tenant_id, kind="anthropic", master_key=_NEW_MASTER
            )
            recovered_oai = await key_vault.get_byok_key(
                conn, tenant_id=tenant_id, kind="openai", master_key=_NEW_MASTER
            )
        assert recovered_anth == anth
        assert recovered_oai == oai
    finally:
        await _cleanup(pool, tenant_id)


async def test_null_column_stays_null_through_rotation(
    pool: asyncpg.Pool,
) -> None:
    """A tenant with only the anthropic key (no openai) keeps the openai
    column NULL after the rotation — no spurious encryption of NULL."""

    tenant_id = await _make_tenant(pool, anthropic="sk-ant-only")
    try:
        await rotate_all(
            pool,
            old_master=_OLD_MASTER,
            new_master=_NEW_MASTER,
            only_tenant_id=tenant_id,
        )
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select anthropic_api_key_encrypted is not null as has_anth,
                       openai_api_key_encrypted is null as openai_null
                  from tenant_llm_config where tenant_id = $1
                """,
                tenant_id,
            )
        assert row["has_anth"] is True
        assert row["openai_null"] is True
    finally:
        await _cleanup(pool, tenant_id)


async def test_tenant_without_keys_is_skipped(pool: asyncpg.Pool) -> None:
    """A tenant with no BYOK config is skipped — counted but no audit."""

    tenant_id = await _make_tenant(pool)  # no keys
    try:
        report = await rotate_all(
            pool,
            old_master=_OLD_MASTER,
            new_master=_NEW_MASTER,
            only_tenant_id=tenant_id,
        )
        assert report.tenants_processed == 1
        assert report.tenants_skipped == 1
        assert report.tenants_rotated == 0

        async with pool.acquire() as conn:
            audit_count = await conn.fetchval(
                "select count(*) from audit_log "
                "where tenant_id = $1 and action = 'tenant.master_key_rotated'",
                tenant_id,
            )
        assert audit_count == 0
    finally:
        await _cleanup(pool, tenant_id)


async def test_each_rotation_writes_one_audit_row_with_no_key_material(
    pool: asyncpg.Pool,
) -> None:
    plaintext = "sk-ant-AUDIT-CHECK-must-not-leak"
    tenant_id = await _make_tenant(pool, anthropic=plaintext)
    try:
        await rotate_all(
            pool,
            old_master=_OLD_MASTER,
            new_master=_NEW_MASTER,
            only_tenant_id=tenant_id,
        )
        async with pool.acquire() as conn:
            audits = await conn.fetch(
                """
                select action, diff::text as diff
                  from audit_log where tenant_id = $1
                """,
                tenant_id,
            )
        assert len(audits) == 1
        assert audits[0]["action"] == "tenant.master_key_rotated"
        # No key material in the diff — the canary substring proves it.
        assert "AUDIT-CHECK" not in audits[0]["diff"]
        assert "sk-ant" not in audits[0]["diff"]
    finally:
        await _cleanup(pool, tenant_id)


async def test_wrong_old_key_fails_one_tenant_but_continues_run(
    pool: asyncpg.Pool,
) -> None:
    """Tenant A is encrypted with OLD; tenant B with a DIFFERENT key.
    A run with --old=OLD rotates A but errors on B. The run completes
    and the report names the failing tenant."""

    plaintext_a = "sk-ant-A-good-key"
    plaintext_b = "sk-ant-B-encrypted-with-different-master"
    other_master = "different-master-key-32-bytes-pad"

    tenant_a = await _make_tenant(pool, anthropic=plaintext_a)
    tenant_b = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_b,
            "Tenant B",
            f"rot-b-{tenant_b}",
        )
        await key_vault.store_byok_key(
            conn,
            tenant_id=tenant_b,
            kind="anthropic",
            plaintext_key=plaintext_b,
            master_key=other_master,
        )

    try:
        report = await rotate_all(
            pool,
            old_master=_OLD_MASTER,
            new_master=_NEW_MASTER,
        )
        # Other test runs may have left tenants in the DB — only check
        # that A succeeded and B errored among our own ids.
        assert tenant_a not in {tid for tid, _ in report.errors}
        assert tenant_b in {tid for tid, _ in report.errors}

        # A: usable with the new master.
        async with pool.acquire() as conn:
            recovered = await key_vault.get_byok_key(
                conn,
                tenant_id=tenant_a,
                kind="anthropic",
                master_key=_NEW_MASTER,
            )
        assert recovered == plaintext_a

        # B: still readable with its original (different) master — the
        # transaction rolled back, so the row is unchanged.
        async with pool.acquire() as conn:
            still_b = await key_vault.get_byok_key(
                conn,
                tenant_id=tenant_b,
                kind="anthropic",
                master_key=other_master,
            )
        assert still_b == plaintext_b
    finally:
        await _cleanup(pool, tenant_a)
        await _cleanup(pool, tenant_b)


# ── Dry-run ────────────────────────────────────────────────────────────


async def test_dry_run_reports_would_rotate_count_without_writing(
    pool: asyncpg.Pool,
) -> None:
    plaintext = "sk-ant-dry-run-must-not-mutate"
    tenant_id = await _make_tenant(pool, anthropic=plaintext)
    try:
        report = await rotate_all(
            pool,
            old_master=_OLD_MASTER,
            new_master=_NEW_MASTER,
            only_tenant_id=tenant_id,
            dry_run=True,
        )
        assert report.dry_run is True
        assert report.tenants_processed == 1

        # Original master still decrypts → nothing happened in the DB.
        async with pool.acquire() as conn:
            recovered = await key_vault.get_byok_key(
                conn,
                tenant_id=tenant_id,
                kind="anthropic",
                master_key=_OLD_MASTER,
            )
        assert recovered == plaintext

        # No audit row was written.
        async with pool.acquire() as conn:
            audit_count = await conn.fetchval(
                "select count(*) from audit_log where tenant_id = $1",
                tenant_id,
            )
        assert audit_count == 0
    finally:
        await _cleanup(pool, tenant_id)


# ── Report serialisation sanity ────────────────────────────────────────


def test_report_as_dict_shape() -> None:
    """The CLI dumps this dict with json.dumps — keys must be stable."""

    report = RotationReport(
        tenants_processed=5,
        tenants_rotated=4,
        tenants_skipped=1,
        errors=[(uuid.uuid4(), "bang")],
        dry_run=False,
    )
    body = report.as_dict()
    assert set(body.keys()) == {
        "tenants_processed",
        "tenants_rotated",
        "tenants_skipped",
        "error_count",
        "errors",
        "dry_run",
    }
    assert body["error_count"] == 1
    assert isinstance(body["errors"][0]["tenant_id"], str)
