-- Migration 011: BYOK Hardening
-- Source of truth: docs/09-security-compliance.md §5
--
-- The pgcrypto extension and the tenant_llm_config table both already exist
-- (migrations 001 and 002). This migration is purely additive hardening:
--   - re-asserts pgcrypto so a partial restore can re-run from this point
--   - adds an updated_at index to support the audit-timeline view (Faz 2)
--   - documents column intent so DBAs grepping the schema see it inline
--   - documents the master-key rotation runbook in this comment block
--     (only place in the repo where the SQL DBA will look first)
--
-- Master key handling — IMPORTANT:
--   The encryption key (LLM_MASTER_ENCRYPTION_KEY) is a 32-byte random string
--   stored ONLY in Railway secrets. It is never persisted in this database,
--   never committed to git, never logged. pgp_sym_encrypt / pgp_sym_decrypt
--   accept it as a runtime parameter from src/llm/key_vault.py — see that
--   module for the parameterised SQL.
--
-- ── Master key rotation procedure (every 6 months — runbook in Notion) ──
--
--   1. Generate NEW_MASTER:  openssl rand -base64 32
--   2. Add LLM_MASTER_ENCRYPTION_KEY_NEW in Railway alongside the old var.
--   3. Run apps/api/scripts/rotate_master_key.py — for every row in
--      tenant_llm_config:
--        SELECT pgp_sym_decrypt(col, OLD_MASTER) AS plaintext
--        UPDATE col = pgp_sym_encrypt(plaintext, NEW_MASTER)
--      one transaction per tenant; plaintext never touches a log line.
--   4. Deploy backend with LLM_MASTER_ENCRYPTION_KEY = NEW_MASTER.
--   5. Smoke test POST /api/v1/tenant/llm-config/test for ≥2 paying tenants.
--   6. Remove the old var from Railway.
--   7. audit_log: write one tenant.master_key_rotated event per tenant
--      (action only, no key material).

create extension if not exists "pgcrypto";

create index if not exists idx_tenant_llm_config_updated
  on tenant_llm_config(updated_at desc);

comment on column tenant_llm_config.anthropic_api_key_encrypted is
  'pgp_sym_encrypt-encoded BYOK key. Never stored or returned in plaintext. '
  'Decrypted only at runtime via src/llm/key_vault.py. '
  'Master key in env LLM_MASTER_ENCRYPTION_KEY (Railway secret), NEVER in DB.';

comment on column tenant_llm_config.openai_api_key_encrypted is
  'pgp_sym_encrypt-encoded BYOK key. Never stored or returned in plaintext. '
  'Decrypted only at runtime via src/llm/key_vault.py. '
  'Master key in env LLM_MASTER_ENCRYPTION_KEY (Railway secret), NEVER in DB.';

comment on table tenant_llm_config is
  'Per-tenant BYOK / managed-key configuration. RLS denies all access to '
  'authenticated users by design — only service_role (the FastAPI pool) '
  'reads/writes this table, with tenant scoping enforced in app code by '
  'src/api/routes/llm_config.py and src/llm/key_vault.py.';
