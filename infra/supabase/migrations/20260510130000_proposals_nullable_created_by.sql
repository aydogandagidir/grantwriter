-- Migration 012: proposals.created_by nullable + ON DELETE SET NULL
-- Source of truth: docs/09 §3.2 (KVKK / GDPR self-service deletion)
--
-- The original migration 005 declared ``proposals.created_by`` as
-- ``not null references public.users(id)`` — fine for the happy path
-- but blocks the hard-delete grace-window purger:
-- ``apps/api/scripts/purge_deleted_accounts.py`` needs to anonymise the
-- column to NULL before deleting the user, otherwise the FK forbids
-- the delete and the row stays around past the 30-day window.
--
-- This migration relaxes the column AND switches the FK to ``ON DELETE
-- SET NULL``, so even if a hard-delete happens out-of-band (e.g.
-- Supabase Auth admin API directly) the proposal stays intact with
-- ``created_by = NULL`` instead of either failing the cascade or
-- vanishing with the user.
--
-- Idempotent — running twice is a no-op (every step uses IF EXISTS /
-- conditional reformulation).

-- 1. Drop the existing FK so we can re-attach with ON DELETE SET NULL.
alter table proposals
  drop constraint if exists proposals_created_by_fkey;

-- 2. Drop the NOT NULL constraint (the column itself stays).
alter table proposals
  alter column created_by drop not null;

-- 3. Re-attach the FK with the new on-delete behaviour. Same target
--    column, same nullability semantics — the only change is what
--    happens when the referenced user row is hard-deleted.
alter table proposals
  add constraint proposals_created_by_fkey
    foreign key (created_by) references public.users(id) on delete set null;

comment on column proposals.created_by is
  'Author user id. NULL when the original author has been hard-deleted '
  '(see docs/09 §3.2). RLS still scopes by tenant_id, so authorship '
  'erasure does not leak content cross-tenant.';
